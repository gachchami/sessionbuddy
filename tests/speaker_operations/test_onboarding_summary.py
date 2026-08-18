"""Onboarding summary tiles must agree with the task table, over HTTP.

Pins the eval-run P1: the tiles counted SPEAKERS (accepted-only) while the
table lists TASK rows for every speaker, so "Complete 0 / Incomplete 1"
could sit above a table showing 3 completed and 5 outstanding rows. The
tiles now count tasks with the same scope as the table and honour BOTH
filters — task type and state.
"""

import sqlite3

from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401 - pytest fixture
)
from tests.speaker_operations.test_speaker_attribution import (
    _admin,
    _seed_speaker_with_two_submissions,
)

DAY_MS = 86_400_000


def _seed_tasks(connection: sqlite3.Connection, organization_id: str, event_id: str) -> None:
    """Extend the attribution seed: a second (submitted, not accepted)
    speaker plus a spread of task states across both speakers."""
    connection.execute(
        """INSERT INTO people (id,organization_id,display_name,created_at_ms,updated_at_ms)
           VALUES ('person-2',?,'Marcus Okafor',1000,1000)""",
        (organization_id,),
    )
    connection.execute(
        """INSERT INTO event_speakers
           (id,organization_id,event_id,person_id,status,selection_status,
            accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
           VALUES ('speaker-2',?,?,'person-2','onboarding','submitted',
                   3000,3000,1000,1000)""",
        (organization_id, event_id),
    )
    import time

    now = int(time.time() * 1000)
    rows = (
        # id, speaker, state, due offset from now (None = no due date)
        ("task-completed-1", "speaker-1", "completed", None),
        ("task-completed-2", "speaker-1", "completed", None),
        ("task-waived", "speaker-1", "waived", None),
        ("task-open-overdue", "speaker-1", "open", -DAY_MS),
        ("task-open-due-soon", "speaker-1", "open", 2 * DAY_MS),
        ("task-open-later", "speaker-2", "open", 30 * DAY_MS),
    )
    for task_id, speaker, state, offset in rows:
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,task_type,title,
                state,due_at_ms,completed_at_ms,waived_at_ms,
                created_at_ms,updated_at_ms)
               VALUES (?,?,?,?,'custom',?,?,?,?,?,3000,3000)""",
            (
                task_id,
                organization_id,
                event_id,
                speaker,
                task_id,
                state,
                None if offset is None else now + offset,
                now if state == "completed" else None,
                now if state == "waived" else None,
            ),
        )
    connection.commit()


async def test_summary_tiles_agree_with_the_task_table(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        _seed_tasks(connection, organization_id, event_id)

        dashboard = await client.get(
            f"/api/v1/admin/events/{event_id}/onboarding", params={"limit": 50}
        )
        assert dashboard.status_code == 200, dashboard.text
        body = dashboard.json()
        rows = body["data"]
        summary = body["summary"]

        # The tiles describe the SAME task population the table lists.
        listed_complete = sum(1 for r in rows if r["state"] in ("completed", "waived"))
        listed_incomplete = sum(
            1 for r in rows if r["state"] in ("open", "overdue", "due_soon")
        )
        assert summary["complete"] == listed_complete == 3
        # attribution seed adds one open task (task-1) => 4 outstanding
        assert summary["incomplete"] == listed_incomplete == 4
        assert summary["overdue"] == 1
        assert summary["due_soon"] == 1
        # Non-accepted speakers' tasks are in the table, so they are in the
        # tiles too (speaker-2's open task counts).
        assert any(r["event_speaker_id"] == "speaker-2" for r in rows)


async def test_summary_tiles_honour_the_task_type_filter(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        _seed_tasks(connection, organization_id, event_id)

        filtered = await client.get(
            f"/api/v1/admin/events/{event_id}/onboarding",
            params={"task_type": "profile", "limit": 50},
        )
        assert filtered.status_code == 200, filtered.text
        body = filtered.json()
        # Only the attribution seed's open profile task matches the filter.
        assert [row["task_type"] for row in body["data"]] == ["profile"]
        assert body["summary"]["complete"] == 0
        assert body["summary"]["incomplete"] == 1
        assert body["summary"]["overdue"] == 0


async def test_summary_tiles_honour_the_state_filter(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        _seed_tasks(connection, organization_id, event_id)

        completed = await client.get(
            f"/api/v1/admin/events/{event_id}/onboarding",
            params={"state": "completed", "limit": 50},
        )
        assert completed.status_code == 200, completed.text
        body = completed.json()
        assert all(row["state"] == "completed" for row in body["data"])
        # The cards describe exactly the filtered population: only completed
        # rows exist here, so nothing can be incomplete or overdue.
        assert body["summary"]["complete"] == len(body["data"]) == 2
        assert body["summary"]["incomplete"] == 0
        assert body["summary"]["overdue"] == 0
        assert body["summary"]["due_soon"] == 0

        open_only = await client.get(
            f"/api/v1/admin/events/{event_id}/onboarding",
            params={"state": "open", "limit": 50},
        )
        body = open_only.json()
        assert body["summary"]["complete"] == 0
        assert body["summary"]["incomplete"] == 4
        assert body["summary"]["overdue"] == 1
        assert body["summary"]["due_soon"] == 1

        overdue = await client.get(
            f"/api/v1/admin/events/{event_id}/onboarding",
            params={"state": "overdue", "limit": 50},
        )
        body = overdue.json()
        assert [row["state"] for row in body["data"]] == ["overdue"]
        assert body["summary"]["incomplete"] == 1
        assert body["summary"]["overdue"] == 1
        assert body["summary"]["due_soon"] == 0

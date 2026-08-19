"""Event edits cannot silently reinterpret an existing agenda's local times."""

import pytest

from tests.agenda.test_session_content_history import EVENT_PAYLOAD, _admin
from tests.scheduling.test_publish_content_approval import _scheduled_draft
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)


def _snapshot(connection):
    return {
        table: [tuple(row) for row in connection.execute(
            f"SELECT * FROM {table} ORDER BY id"  # noqa: S608 - fixed table whitelist below
        )]
        for table in (
            "events", "agenda_items", "schedule_revisions", "call_for_speaker_forms",
            "evaluation_rounds", "audit_events",
        )
    }


@pytest.mark.parametrize("published", [False, True])
async def test_timezone_change_with_agenda_rejected_without_partial_writes(
    production_environment, published,  # noqa: F811
):
    connection, _, environment = production_environment
    async with _client(environment) as client:
        csrf, org_id, event_id = await _admin(client, connection)
        _, revision = await _scheduled_draft(client, connection, csrf, org_id, event_id)
        connection.execute(
            "UPDATE call_for_speaker_forms SET opens_at_ms=1800000000000,"
            "closes_at_ms=1800086400000 WHERE event_id=?", (event_id,),
        )
        connection.execute(
            "UPDATE evaluation_rounds SET review_opens_at_ms=1800086400000,"
            "review_closes_at_ms=1800172800000 WHERE event_id=?", (event_id,),
        )
        connection.commit()
        if published:
            connection.execute(
                "UPDATE schedule_revisions SET status='published',published_at_ms=1000 WHERE id=?",
                (revision["id"],),
            )
            connection.commit()
        before = _snapshot(connection)
        result = await client.patch(
            f"/api/v1/admin/events/{event_id}",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={**EVENT_PAYLOAD, "name": "Must not persist", "version": 1, "time_zone": "UTC"},
        )
        assert result.status_code == 409, result.text
        assert result.headers["x-conflict-type"] == "event_timezone_has_agenda"
        assert "Keep the current time zone" in result.json()["error"]["message"]
        assert _snapshot(connection) == before

        same_zone = await client.patch(
            f"/api/v1/admin/events/{event_id}",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={**EVENT_PAYLOAD, "name": "Safe name edit", "version": 1},
        )
        assert same_zone.status_code == 200, same_zone.text
        assert same_zone.json()["name"] == "Safe name edit"
        assert same_zone.json()["version"] == 2
        after = _snapshot(connection)
        for table in (
            "agenda_items", "schedule_revisions", "call_for_speaker_forms", "evaluation_rounds",
        ):
            assert after[table] == before[table]


async def test_timezone_change_without_agenda_is_allowed(production_environment):  # noqa: F811
    connection, _, environment = production_environment
    async with _client(environment) as client:
        csrf, _, event_id = await _admin(client, connection)
        result = await client.patch(
            f"/api/v1/admin/events/{event_id}",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={**EVENT_PAYLOAD, "version": 1, "time_zone": "UTC"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["time_zone"] == "UTC"


async def test_timezone_guard_catches_agenda_inserted_after_event_read(
    production_environment, monkeypatch,  # noqa: F811
):
    connection, _, environment = production_environment
    async with _client(environment) as client:
        csrf, org_id, event_id = await _admin(client, connection)
        await _scheduled_draft(client, connection, csrf, org_id, event_id)
        item = tuple(connection.execute("SELECT * FROM agenda_items").fetchone())
        connection.execute("DELETE FROM agenda_items")
        connection.commit()
        before = _snapshot(connection)
        original_prepare = environment.DB.prepare
        inserted = False

        def concurrent_prepare(sql):
            nonlocal inserted
            if "UPDATE events SET name=" in sql:
                # A real persisted insert at the last boundary before the event
                # UPDATE, after authorization and any preflight queries.
                connection.execute(
                    "INSERT INTO agenda_items VALUES ("  # noqa: S608 - placeholders only
                    + ",".join("?" for _ in item) + ")",
                    item,
                )
                connection.commit()
                inserted = True
            return original_prepare(sql)

        monkeypatch.setattr(environment.DB, "prepare", concurrent_prepare)
        result = await client.patch(
            f"/api/v1/admin/events/{event_id}",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={**EVENT_PAYLOAD, "version": 1, "time_zone": "UTC"},
        )
        assert inserted
        assert result.status_code == 409, result.text
        after = _snapshot(connection)
        assert after["agenda_items"] == [item]
        for table in before.keys() - {"agenda_items"}:
            assert after[table] == before[table]

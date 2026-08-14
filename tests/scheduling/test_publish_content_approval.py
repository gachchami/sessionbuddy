"""Agenda publication makes scheduled draft visibility an explicit choice."""

import json

from tests.agenda.test_session_content_history import _admin, _seed_accepted_session
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401 - pytest fixture
)


def _headers(csrf: str, key: str) -> dict[str, str]:
    return {
        "origin": "https://test",
        "x-csrf-token": csrf,
        "idempotency-key": key,
    }


async def _scheduled_draft(client, connection, csrf, organization_id, event_id):
    session_id = _seed_accepted_session(connection, organization_id, event_id)
    setup = await client.post(
        f"/api/v1/admin/events/{event_id}/agenda/setup",
        headers=_headers(csrf, "publish-content-setup"),
        json={"room_names": ["Main stage"], "track_names": []},
    )
    assert setup.status_code == 201, setup.text
    revision = setup.json()["revision"]
    connection.execute(
        """INSERT INTO agenda_items
           (id,organization_id,event_id,revision_id,accepted_session_id,room_id,
            track_id,event_date,event_time_zone,starts_at_ms,ends_at_ms,version,
            created_at_ms,updated_at_ms)
           VALUES ('publish-content-item',?,?,?, ?,?,NULL,'2030-03-17','Asia/Kolkata',
                   1900000000000,1900003600000,1,1000,1000)""",
        (
            organization_id,
            event_id,
            revision["id"],
            session_id,
            setup.json()["rooms"][0]["id"],
        ),
    )
    connection.commit()
    return session_id, revision


async def test_publish_only_preserves_scheduled_draft_content(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        session_id, revision = await _scheduled_draft(
            client, connection, csrf, organization_id, event_id
        )

        response = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/publish",
            headers=_headers(csrf, "publish-content-only"),
            json={
                "revision_id": revision["id"],
                "version": revision["version"],
                "approve_draft_sessions": False,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["newly_approved_session_ids"] == []
        assert response.json()["hidden_session_count"] == 1
        content = connection.execute(
            "SELECT content_status,version FROM accepted_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        assert tuple(content) == ("draft", 1)
        assert connection.execute(
            "SELECT COUNT(*) FROM session_content_versions WHERE accepted_session_id=?",
            (session_id,),
        ).fetchone()[0] == 0


async def test_publish_can_approve_scheduled_drafts_with_history_and_exact_replay(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        session_id, revision = await _scheduled_draft(
            client, connection, csrf, organization_id, event_id
        )
        connection.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,source_type,organizer_title,
                organizer_abstract,content_status,version,created_at_ms)
               VALUES ('unscheduled-draft',?,?,'organizer_created','Later session',
                       'Not on this revision','draft',1,1000)""",
            (organization_id, event_id),
        )
        connection.commit()
        url = f"/api/v1/admin/events/{event_id}/agenda/publish"
        headers = _headers(csrf, "publish-content-approve")
        payload = {
            "revision_id": revision["id"],
            "version": revision["version"],
            "approve_draft_sessions": True,
        }

        response = await client.post(url, headers=headers, json=payload)
        replay = await client.post(url, headers=headers, json=payload)

        assert response.status_code == replay.status_code == 200
        assert response.json() == replay.json()
        assert response.json()["newly_approved_session_ids"] == [session_id]
        assert response.json()["hidden_session_count"] == 0
        content = connection.execute(
            "SELECT content_status,version FROM accepted_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        assert tuple(content) == ("approved", 2)
        unscheduled = connection.execute(
            "SELECT content_status,version FROM accepted_sessions WHERE id='unscheduled-draft'"
        ).fetchone()
        assert tuple(unscheduled) == ("draft", 1)
        history = connection.execute(
            """SELECT version,title,abstract,content_status
               FROM session_content_versions WHERE accepted_session_id=?""",
            (session_id,),
        ).fetchall()
        assert [tuple(row) for row in history] == [
            (2, "Original title", "Original abstract", "approved")
        ]
        audit = connection.execute(
            """SELECT metadata_json FROM audit_events
               WHERE action='session.content.update' AND target_id=?""",
            (session_id,),
        ).fetchone()
        assert json.loads(audit[0]) == {
            "content_status": "approved",
            "source": "agenda_publish",
            "version": 2,
        }
        changed_choice = await client.post(
            url,
            headers=headers,
            json={**payload, "approve_draft_sessions": False},
        )
        assert changed_choice.status_code == 409


async def test_concurrent_content_edit_aborts_approval_and_publication(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        session_id, revision = await _scheduled_draft(
            client, connection, csrf, organization_id, event_id
        )
        original_batch = environment.DB.batch
        raced = False

        async def batch_after_concurrent_edit(statements):
            nonlocal raced
            approval_batch = any(
                "UPDATE accepted_sessions SET content_status='approved'" in statement.sql
                for statement in statements
            )
            if approval_batch and not raced:
                raced = True
                connection.execute(
                    """UPDATE submissions SET proposal_title='Concurrent title'
                       WHERE id='submission-1'"""
                )
                connection.execute(
                    "UPDATE accepted_sessions SET version=version+1 WHERE id=?",
                    (session_id,),
                )
                connection.commit()
            return await original_batch(statements)

        environment.DB.batch = batch_after_concurrent_edit
        response = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/publish",
            headers=_headers(csrf, "publish-content-race"),
            json={
                "revision_id": revision["id"],
                "version": revision["version"],
                "approve_draft_sessions": True,
            },
        )

        assert response.status_code == 409, response.text
        schedule = connection.execute(
            "SELECT status FROM schedule_revisions WHERE id=?", (revision["id"],)
        ).fetchone()
        assert schedule[0] == "draft"
        session = connection.execute(
            "SELECT content_status,version FROM accepted_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        assert tuple(session) == ("draft", 2)
        assert connection.execute(
            "SELECT proposal_title FROM submissions WHERE id='submission-1'"
        ).fetchone()[0] == "Concurrent title"

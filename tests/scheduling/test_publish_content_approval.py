"""Agenda publication makes scheduled draft visibility an explicit choice."""

import json

import sessionbuddy.scheduling.router as scheduling_router_module
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
    monkeypatch,
) -> None:
    connection, _queue, environment = production_environment
    calendar_changes = []
    reconciliations = []

    async def record_calendar_change(_db, change, _speakers, *, now_ms):
        calendar_changes.append((change, now_ms))
        return []

    async def record_reconciliation(_db, **values):
        reconciliations.append(values)
        return []

    monkeypatch.setattr(scheduling_router_module, "queue_calendar_changes", record_calendar_change)
    monkeypatch.setattr(
        scheduling_router_module,
        "reconcile_calendar_projection",
        record_reconciliation,
    )
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
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM session_content_versions WHERE accepted_session_id=?",
                (session_id,),
            ).fetchone()[0]
            == 0
        )
        attendee_schedule = await client.get(f"/api/v1/events/{event_id}/schedule")
        assert attendee_schedule.status_code == 200, attendee_schedule.text
        assert attendee_schedule.json()["items"] == []
        token = await client.post(
            f"/api/v1/admin/events/{event_id}/integrations/accelevents/tokens",
            headers=_headers(csrf, "draft-content-integration-token"),
            json={"label": "Draft visibility regression"},
        )
        assert token.status_code == 201, token.text
        integration = await client.post(
            f"/v1/event/{event_id}/sessions",
            headers={"x-access-token": token.json()["token"]},
        )
        assert integration.status_code == 200, integration.text
        assert integration.json()["results"] == []
        assert calendar_changes == []
        assert len(reconciliations) == 1
        assert reconciliations[0]["public_session_ids"] == set()


async def test_committed_publish_records_calendar_reconciliation_degradation(
    production_environment,  # noqa: F811 - pytest fixture
    monkeypatch,
) -> None:
    connection, _queue, environment = production_environment
    degradations = []

    async def fail_reconciliation(_db, **_values):
        raise RuntimeError("provider read failed")

    def record_degradation(_request, code):
        degradations.append(code)

    monkeypatch.setattr(
        scheduling_router_module,
        "reconcile_calendar_projection",
        fail_reconciliation,
    )
    monkeypatch.setattr(
        scheduling_router_module,
        "record_degradation",
        record_degradation,
    )
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        _session_id, revision = await _scheduled_draft(
            client, connection, csrf, organization_id, event_id
        )
        response = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/publish",
            headers=_headers(csrf, "calendar-reconciliation-degradation"),
            json={
                "revision_id": revision["id"],
                "version": revision["version"],
                "approve_draft_sessions": False,
            },
        )

    assert response.status_code == 200, response.text
    assert degradations == ["calendar_projection_failed"]
    assert (
        connection.execute(
            "SELECT status FROM schedule_revisions WHERE id=?", (revision["id"],)
        ).fetchone()[0]
        == "published"
    )


async def test_publish_can_approve_scheduled_drafts_with_history_and_exact_replay(
    production_environment,  # noqa: F811 - pytest fixture
    monkeypatch,
) -> None:
    connection, _queue, environment = production_environment
    calendar_changes = []
    reconciliations = []

    async def record_calendar_change(_db, change, _speakers, *, now_ms):
        calendar_changes.append((change, now_ms))
        return []

    async def record_reconciliation(_db, **values):
        reconciliations.append(values)
        return []

    monkeypatch.setattr(scheduling_router_module, "queue_calendar_changes", record_calendar_change)
    monkeypatch.setattr(
        scheduling_router_module,
        "reconcile_calendar_projection",
        record_reconciliation,
    )
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
        attendee_schedule = await client.get(f"/api/v1/events/{event_id}/schedule")
        assert attendee_schedule.status_code == 200, attendee_schedule.text
        assert [item["session_id"] for item in attendee_schedule.json()["items"]] == [session_id]
        token = await client.post(
            f"/api/v1/admin/events/{event_id}/integrations/accelevents/tokens",
            headers=_headers(csrf, "approved-content-integration-token"),
            json={"label": "Approved visibility regression"},
        )
        assert token.status_code == 201, token.text
        integration = await client.post(
            f"/v1/event/{event_id}/sessions",
            headers={"x-access-token": token.json()["token"]},
        )
        assert integration.status_code == 200, integration.text
        assert [item["id"] for item in integration.json()["results"]] == [session_id]
        assert len(calendar_changes) == 1
        assert calendar_changes[0][0].agenda_item_id == "publish-content-item"
        assert calendar_changes[0][0].published is True
        assert len(reconciliations) == 1
        assert reconciliations[0]["public_session_ids"] == {session_id}
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
        assert (
            connection.execute(
                "SELECT proposal_title FROM submissions WHERE id='submission-1'"
            ).fetchone()[0]
            == "Concurrent title"
        )


async def test_unschedule_then_publish_sends_calendar_cancellation(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        session_id, revision = await _scheduled_draft(
            client, connection, csrf, organization_id, event_id
        )
        connection.execute(
            "UPDATE accepted_sessions SET content_status='approved' WHERE id=?",
            (session_id,),
        )
        connection.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,source_type,organizer_title,
                organizer_abstract,content_status,version,created_at_ms)
               VALUES ('calendar-keep',?,?,'organizer_created','Keep scheduled',
                       'Keeps the next agenda publishable','approved',1,1000)""",
            (organization_id, event_id),
        )
        room_id = connection.execute(
            "SELECT room_id FROM agenda_items WHERE id='publish-content-item'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO agenda_items
               (id,organization_id,event_id,revision_id,accepted_session_id,room_id,
                event_date,event_time_zone,starts_at_ms,ends_at_ms,version,
                created_at_ms,updated_at_ms)
               VALUES ('calendar-keep-item',?,?,?,'calendar-keep',?,'2030-03-17',
                       'Asia/Kolkata',1900007200000,1900010800000,1,1000,1000)""",
            (organization_id, event_id, revision["id"], room_id),
        )
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,display_name,status,authorization_version,
                version,created_at_ms,updated_at_ms)
               VALUES ('calendar-speaker','calendar@example.com','calendar@example.com',
                       'Calendar Speaker','active',1,1,1000,1000)"""
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES ('calendar-org-member',?,'calendar-speaker','member','active',1000,1000)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES ('calendar-event-member',?,?,'calendar-speaker','speaker','active',
                       1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO calendar_invitations
               (id,organization_id,event_id,agenda_item_id,recipient_user_id,
                calendar_uid,sequence,last_content_hash,updated_at_ms)
               VALUES ('prior-calendar',?,?, 'publish-content-item',?,
                       'prior-calendar@example.test',0,X'00',1000)""",
            (organization_id, event_id, "calendar-speaker"),
        )
        connection.commit()

        first_publish = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/publish",
            headers=_headers(csrf, "calendar-first-publish"),
            json={"revision_id": revision["id"], "version": revision["version"]},
        )
        assert first_publish.status_code == 200, first_publish.text
        next_revision_id = first_publish.json()["draft_revision_id"]
        cloned = connection.execute(
            """SELECT id,version FROM agenda_items
               WHERE revision_id=? AND accepted_session_id=?""",
            (next_revision_id, session_id),
        ).fetchone()
        unscheduled = await client.delete(
            f"/api/v1/admin/events/{event_id}/agenda/items/{cloned['id']}",
            params={"version": cloned["version"]},
            headers={
                **_headers(csrf, "calendar-unschedule"),
                "content-type": "application/json",
            },
        )
        assert unscheduled.status_code == 204, unscheduled.text
        next_revision = connection.execute(
            "SELECT version FROM schedule_revisions WHERE id=?",
            (next_revision_id,),
        ).fetchone()
        second_publish = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/publish",
            headers=_headers(csrf, "calendar-second-publish"),
            json={"revision_id": next_revision_id, "version": next_revision["version"]},
        )

        assert second_publish.status_code == 200, second_publish.text
        cancellation = connection.execute(
            """SELECT ics_content FROM calendar_invitation_versions
               WHERE invitation_id='prior-calendar' ORDER BY sequence DESC LIMIT 1"""
        ).fetchone()
        assert cancellation is not None
        assert "METHOD:CANCEL\r\n" in cancellation["ics_content"]
        assert "STATUS:CANCELLED\r\n" in cancellation["ics_content"]

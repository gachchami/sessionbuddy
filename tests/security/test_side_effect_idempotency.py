"""Idempotency contracts for fanout and one-time-secret operations."""

from tests.agenda.test_session_content_history import (
    EVENT_PAYLOAD,
    _admin,
    _seed_accepted_session,
)
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401 - pytest fixture
)


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    headers = {"origin": "https://test", "x-csrf-token": csrf}
    if key is not None:
        headers["idempotency-key"] = key
    return headers


async def test_agenda_publish_replays_one_revision_without_duplicate_fanout(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        queued_before_publish = len(queue.messages)
        accepted_session_id = _seed_accepted_session(
            connection, organization_id, event_id
        )
        setup = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/setup",
            headers=_headers(csrf, "publish-setup-idempotency-2026"),
            json={"room_names": ["Main stage"], "track_names": []},
        )
        assert setup.status_code == 201, setup.text
        revision = setup.json()["revision"]
        room_id = setup.json()["rooms"][0]["id"]
        connection.execute(
            "UPDATE accepted_sessions SET content_status='approved' WHERE id=?",
            (accepted_session_id,),
        )
        connection.execute(
            """INSERT INTO agenda_items
               (id,organization_id,event_id,revision_id,accepted_session_id,room_id,
                track_id,event_date,event_time_zone,starts_at_ms,ends_at_ms,version,
                created_at_ms,updated_at_ms)
               VALUES ('publish-item',?,?,?, ?,?,NULL,'2030-03-17','Asia/Kolkata',
                       1900000000000,1900003600000,1,1000,1000)""",
            (
                organization_id,
                event_id,
                revision["id"],
                accepted_session_id,
                room_id,
            ),
        )
        connection.commit()
        payload = {"revision_id": revision["id"], "version": revision["version"]}
        url = f"/api/v1/admin/events/{event_id}/agenda/publish"

        missing = await client.post(url, headers=_headers(csrf), json=payload)
        assert missing.status_code == 400
        first = await client.post(
            url, headers=_headers(csrf, "publish-agenda-idempotency-2026"), json=payload
        )
        replay = await client.post(
            url, headers=_headers(csrf, "publish-agenda-idempotency-2026"), json=payload
        )

        assert first.status_code == replay.status_code == 200
        assert first.json() == replay.json()
        assert connection.execute(
            "SELECT COUNT(*) FROM schedule_revisions WHERE event_id=?", (event_id,)
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='agenda.publish' AND event_id=?",
            (event_id,),
        ).fetchone()[0] == 1
        assert len(queue.messages) == queued_before_publish
        changed = await client.post(
            url,
            headers=_headers(csrf, "publish-agenda-idempotency-2026"),
            json={**payload, "version": payload["version"] + 1},
        )
        assert changed.status_code == 409


async def test_round_submission_replay_creates_one_assignment_and_email(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        queued_before_assignment = len(queue.messages)
        user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        round_id = "20000000-0000-4000-8000-000000000001"
        seed_submission = "20000000-0000-4000-8000-000000000002"
        added_submission = "20000000-0000-4000-8000-000000000003"
        connection.execute(
            """INSERT INTO call_for_speaker_forms
               (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
                published_at_ms,created_at_ms,updated_at_ms)
               VALUES ('idempotency-form',?,?,1,'idempotency-form','Welcome','{}',
                       'published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        for submission_id, public_id in (
            (seed_submission, "idempotency-public-1"),
            (added_submission, "idempotency-public-2"),
        ):
            connection.execute(
                """INSERT INTO submissions
                   (id,organization_id,event_id,form_id,public_session_id,
                    proposal_title,proposal_abstract,speaker_name,speaker_email,status,
                    submitted_at_ms,created_at_ms,updated_at_ms)
                   VALUES (?,?,?,'idempotency-form',?,'Proposal','Abstract','Speaker',
                           'speaker@example.com','submitted',1000,1000,1000)""",
                (submission_id, organization_id, event_id, public_id),
            )
        connection.execute(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES ('idempotency-evaluator',?,?,?,'evaluator','active',1000,1000)""",
            (organization_id, event_id, user_id),
        )
        connection.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms)
               VALUES (?,?,?,'Review','{"assignment_strategy":"balanced"}',
                       'open',1000,1000)""",
            (round_id, organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                status,created_at_ms,updated_at_ms)
               VALUES ('idempotency-seed-assignment',?,?,?,?,?,'assigned',1000,1000)""",
            (organization_id, event_id, round_id, seed_submission, user_id),
        )
        connection.commit()
        url = f"/api/v1/admin/evaluation-rounds/{round_id}/submissions"
        payload = {"submission_ids": [added_submission]}
        headers = _headers(csrf, "round-submissions-idempotency-2026")

        assert (await client.post(url, headers=_headers(csrf), json=payload)).status_code == 400
        first = await client.post(url, headers=headers, json=payload)
        replay = await client.post(url, headers=headers, json=payload)

        assert first.status_code == replay.status_code == 200
        assert first.json() == replay.json() == {
            "round_id": round_id,
            "submission_count": 1,
            "assignment_count": 1,
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM evaluation_assignments WHERE round_id=?", (round_id,)
        ).fetchone()[0] == 2
        assert connection.execute(
            """SELECT COUNT(*) FROM communication_messages
               WHERE deterministic_key LIKE 'evaluation-assignment:%'"""
        ).fetchone()[0] == 1
        assert len(queue.messages) == queued_before_assignment + 1
        changed = await client.post(
            url,
            headers=headers,
            json={"submission_ids": [seed_submission]},
        )
        assert changed.status_code == 409


async def test_token_retry_never_creates_or_replays_a_second_secret(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        url = f"/api/v1/admin/events/{event_id}/integrations/accelevents/tokens"
        payload = {"label": "Accelevents production"}
        headers = _headers(csrf, "accelevents-token-idempotency-2026")

        assert (await client.post(url, headers=_headers(csrf), json=payload)).status_code == 400
        first = await client.post(url, headers=headers, json=payload)
        replay = await client.post(url, headers=headers, json=payload)

        assert first.status_code == 201, first.text
        assert replay.status_code == 409, replay.text
        assert "cannot be replayed" in replay.json()["error"]["message"]
        token = first.json()["token"]
        assert connection.execute(
            "SELECT COUNT(*) FROM event_integration_tokens WHERE event_id=?", (event_id,)
        ).fetchone()[0] == 1
        record = connection.execute(
            """SELECT request_fingerprint,response_resource_id
               FROM idempotency_records WHERE event_id=?
                 AND response_resource_type='event_integration_token'""",
            (event_id,),
        ).fetchone()
        assert record[1] == first.json()["id"]
        assert token not in repr(tuple(record))
        changed = await client.post(
            url,
            headers=headers,
            json={"label": "Different token request"},
        )
        assert changed.status_code == 409
        second_event = await client.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_headers(csrf),
            json={**EVENT_PAYLOAD, "name": "Second integration event"},
        )
        assert second_event.status_code == 201, second_event.text
        cross_event = await client.post(
            f"/api/v1/admin/events/{second_event.json()['id']}/integrations/accelevents/tokens",
            headers=headers,
            json=payload,
        )
        assert cross_event.status_code == 409
        assert connection.execute(
            "SELECT COUNT(*) FROM event_integration_tokens"
        ).fetchone()[0] == 1

from tests.agenda.test_session_content_history import _admin
from tests.security.test_production_identity_flow import (
    _client,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)


async def test_direct_rejection_needs_no_round(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO call_for_speaker_forms
               (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
                published_at_ms,created_at_ms,updated_at_ms)
               VALUES ('direct-form',?, ?,1,'direct','Welcome','{"fields":[]}',
                       'published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('direct-submission',?,?,'direct-form','direct-public','No review needed',
                       'Abstract','Priya Raman','priya@example.test',?,'submitted',
                       1000,1000,1000)""",
            (organization_id, event_id, user_id),
        )
        response = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/direct-submission/reject",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "direct-rejection-test",
            },
            json={
                "decision": "rejected",
                "internal_reason": "Outside this event's scope",
                "send_email": False,
                "speaker_message": "",
                "override_incomplete_reviews": False,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["round_id"] is None
    decision = connection.execute(
        "SELECT decision,round_id FROM submission_decisions WHERE submission_id='direct-submission'"
    ).fetchone()
    assert tuple(decision) == ("rejected", None)
    audit = connection.execute(
        """SELECT metadata_json FROM audit_events
           WHERE action='submission.decision.record' AND target_id='direct-submission'"""
    ).fetchone()[0]
    assert '"direct_rejection":true' in audit


async def test_round_rejection_revokes_outstanding_assignment(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO call_for_speaker_forms
               (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
                published_at_ms,created_at_ms,updated_at_ms)
               VALUES ('round-form',?, ?,1,'round-form','Welcome','{"fields":[]}',
                       'published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('round-submission',?,?,'round-form','round-public','Rejected talk',
                       'Abstract','Priya Raman','priya@example.test',?,'submitted',
                       1000,1000,1000)""",
            (organization_id, event_id, user_id),
        )
        connection.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms)
               VALUES ('rejection-round',?,?,'Review','{}','open',1000,1000)""",
            (organization_id, event_id),
        )
        invitation = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={"email": "decision-reviewer@example.com", "role": "evaluator"},
        )
        assert invitation.status_code == 201, invitation.text
        async with _client(environment) as reviewer:
            accepted = await reviewer.post(
                "/auth/verify",
                data={"token": _token(connection, "decision-reviewer@example.com")},
                follow_redirects=False,
            )
            assert accepted.status_code == 303
        reviewer_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='decision-reviewer@example.com'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                status,created_at_ms,updated_at_ms)
               VALUES ('unfinished-assignment',?,?,'rejection-round','round-submission',?,
                       'assigned',1000,1000)""",
            (organization_id, event_id, reviewer_user_id),
        )
        response = await client.post(
            "/api/v1/admin/evaluation-rounds/rejection-round/submissions/round-submission/decision",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "round-rejection-test",
            },
            json={
                "decision": "rejected",
                "internal_reason": "Organizer override",
                "send_email": False,
                "speaker_message": "",
                "override_incomplete_reviews": True,
            },
        )

    assert response.status_code == 200, response.text
    status = connection.execute(
        "SELECT status FROM evaluation_assignments WHERE id='unfinished-assignment'"
    ).fetchone()[0]
    assert status == "revoked"

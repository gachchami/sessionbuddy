from tests.agenda.test_session_content_history import _admin
from tests.security.test_production_identity_flow import (
    _client,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)


async def _accepted_reviewer(client, connection, environment, csrf, event_id, email):
    invitation = await client.post(
        f"/api/v1/admin/events/{event_id}/invitations",
        headers={"origin": "https://test", "x-csrf-token": csrf},
        json={"email": email, "role": "evaluator", "display_name": "Draft Reviewer"},
    )
    assert invitation.status_code == 201, invitation.text
    async with _client(environment) as reviewer:
        accepted = await reviewer.post(
            "/auth/verify",
            data={"token": _token(connection, email)},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
    return connection.execute(
        "SELECT id FROM users WHERE normalized_email=?", (email,)
    ).fetchone()[0]


async def test_direct_rejection_needs_no_round(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, queue, environment = production_environment
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
        queued_before_rejection = len(queue.messages)
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
                "send_email": True,
                "speaker_message": "Thank you, but this proposal is outside our program scope.",
                "override_incomplete_reviews": False,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["round_id"] is None
    assert response.json()["communication_queued"] is True
    decision = connection.execute(
        "SELECT decision,round_id FROM submission_decisions WHERE submission_id='direct-submission'"
    ).fetchone()
    assert tuple(decision) == ("rejected", None)
    audit = connection.execute(
        """SELECT metadata_json FROM audit_events
           WHERE action='submission.decision.record' AND target_id='direct-submission'"""
    ).fetchone()[0]
    assert '"direct_rejection":true' in audit
    communication = connection.execute(
        """SELECT recipient_email,subject,html_body,status FROM communication_messages
           WHERE deterministic_key LIKE 'submission-decision:%'"""
    ).fetchone()
    assert communication[0] == "priya@example.test"
    assert communication[1].endswith("proposal not selected")
    assert "outside our program scope" in communication[2]
    assert communication[3] == "queued"
    assert len(queue.messages) == queued_before_rejection + 1


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


async def test_draft_round_assignment_does_not_block_direct_rejection(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Preparing a draft round must not close the direct-rejection path.

    A draft round is invisible to reviewers, so a proposal sitting in one has had no
    review and can still be rejected without review. Treating a draft assignment like a
    live one reinstated the very "you must open a round to reject" trap direct rejection
    exists to avoid -- and reported it as the misleading "already in an evaluation round".
    """
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
               VALUES ('draft-form',?, ?,1,'draft-form','Welcome','{"fields":[]}',
                       'published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        reviewer_user_id = await _accepted_reviewer(
            client,
            connection,
            environment,
            csrf,
            event_id,
            "draft-reviewer@example.test",
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('draft-submission',?,?,'draft-form','draft-public','Off topic talk',
                       'Abstract','Priya Raman','priya@example.test',?,'submitted',
                       1000,1000,1000)""",
            (organization_id, event_id, user_id),
        )
        connection.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms)
               VALUES ('draft-round',?,?,'Final review','{}','draft',1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                status,created_at_ms,updated_at_ms)
               VALUES ('draft-assignment',?,?,'draft-round','draft-submission',?,
                       'assigned',1000,1000)""",
            (organization_id, event_id, reviewer_user_id),
        )
        response = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/draft-submission/reject",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "draft-round-direct-rejection",
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
    # The draft assignment is cleaned up, so opening the round later cannot resurrect it.
    status = connection.execute(
        "SELECT status FROM evaluation_assignments WHERE id='draft-assignment'"
    ).fetchone()[0]
    assert status == "revoked"


async def test_draft_round_cannot_record_decisions(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """A draft round is not a decision surface.

    Allowing decisions before the round opens would let an organizer skip review entirely
    while the audit trail still shows a round, and it offered a confusing way around the
    direct-rejection block rather than fixing it.
    """
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
               VALUES ('draft-decision-form',?, ?,1,'draft-dec','Welcome','{"fields":[]}',
                       'published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('draft-decision-submission',?,?,'draft-decision-form','draft-dec-public',
                       'Pending talk','Abstract','Priya Raman','priya@example.test',?,'submitted',
                       1000,1000,1000)""",
            (organization_id, event_id, user_id),
        )
        connection.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms)
               VALUES ('draft-decision-round',?,?,'Final review','{}','draft',1000,1000)""",
            (organization_id, event_id),
        )
        reviewer_user_id = await _accepted_reviewer(
            client,
            connection,
            environment,
            csrf,
            event_id,
            "draft-decision-reviewer@example.test",
        )
        connection.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                status,created_at_ms,updated_at_ms)
               VALUES ('draft-decision-assignment',?,?,'draft-decision-round',
                       'draft-decision-submission',?,'assigned',1000,1000)""",
            (organization_id, event_id, reviewer_user_id),
        )
        response = await client.post(
            "/api/v1/admin/evaluation-rounds/draft-decision-round"
            "/submissions/draft-decision-submission/decision",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "draft-round-decision-blocked",
                },
                json={
                    "decision": "accepted",
                    "internal_reason": "Attempted organizer override",
                "send_email": False,
                "speaker_message": "",
                "override_incomplete_reviews": True,
            },
        )

    assert response.status_code == 409, response.text
    assert "still a draft" in response.json()["error"]["message"]
    assert (
        connection.execute(
            """SELECT COUNT(*) FROM submission_decisions
               WHERE submission_id='draft-decision-submission'"""
        ).fetchone()[0]
        == 0
    )

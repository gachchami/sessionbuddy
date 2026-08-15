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


async def test_final_decision_corrections_are_append_only_and_manage_session_lifecycle(
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
               VALUES ('correction-form',?, ?,1,'correction','Welcome','{"fields":[]}',
                       'published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('corrected-submission',?,?,'correction-form','correction-public',
                       'Recovered proposal','Abstract','Priya Raman','priya@example.test',
                       ?,'submitted',1000,1000,1000)""",
            (organization_id, event_id, user_id),
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('already-accepted-submission',?,?,'correction-form','accepted-public',
                       'Existing accepted proposal','Abstract','Priya Raman',
                       'priya@example.test',?,'submitted',900,900,900)""",
            (organization_id, event_id, user_id),
        )
        connection.execute(
            """INSERT INTO people
               (id,organization_id,display_name,created_at_ms,updated_at_ms)
               VALUES ('correction-person',?,'Priya Raman',800,800)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,selection_status,
                accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
               VALUES ('correction-speaker',?,?,'correction-person','complete','accepted',
                       800,800,800,800)""",
            (organization_id, event_id),
        )
        for link_id, linked_submission in (
            ("correction-primary", "corrected-submission"),
            ("accepted-primary", "already-accepted-submission"),
        ):
            connection.execute(
                """INSERT INTO submission_speakers
                   (id,organization_id,event_id,submission_id,event_speaker_id,role,
                    snapshot_name,created_at_ms)
                   VALUES (?,?,?,?,?,'primary','Priya Raman',800)""",
                (
                    link_id,
                    organization_id,
                    event_id,
                    linked_submission,
                    "correction-speaker",
                ),
            )
        connection.execute(
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
                version,decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES ('existing-acceptance',?,?,NULL,'already-accepted-submission','accepted',
                       'Existing program decision',1,?,900,900)""",
            (organization_id, event_id, user_id),
        )
        connection.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,submission_id,decision_id,lifecycle_status,
                created_at_ms)
               VALUES ('existing-session',?,?,'already-accepted-submission',
                       'existing-acceptance','active',900)""",
            (organization_id, event_id),
        )
        for task_id, task_type in (
            ("existing-profile-task", "profile"),
            ("existing-headshot-task", "headshot"),
        ):
            connection.execute(
                """INSERT INTO speaker_tasks
                   (id,organization_id,event_id,event_speaker_id,submission_id,task_type,
                    title,destination_type,state,created_at_ms,updated_at_ms)
                   VALUES (?,?,?,?,?,?,?,?, 'open',900,900)""",
                (
                    task_id,
                    organization_id,
                    event_id,
                    "correction-speaker",
                    "already-accepted-submission",
                    task_type,
                    f"Existing {task_type} task",
                    task_type,
                ),
            )
        rejected = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/corrected-submission/reject",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "correction-original",
            },
            json={
                "decision": "rejected",
                "internal_reason": "Original program decision",
                "send_email": False,
                "speaker_message": "",
                "override_incomplete_reviews": False,
            },
        )
        assert rejected.status_code == 200, rejected.text
        accepted = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/corrected-submission/decision-corrections",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "correction-accept",
            },
            json={
                "corrected_decision": "accepted",
                "reason": "The program committee resolved a classification error.",
            },
        )
        assert accepted.status_code == 200, accepted.text
        rejected_again = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/corrected-submission/decision-corrections",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "correction-reject",
            },
            json={
                "corrected_decision": "rejected",
                "reason": "A later eligibility review reversed the correction.",
            },
        )
        assert rejected_again.status_code == 200, rejected_again.text
        rejected_session = connection.execute(
            """SELECT lifecycle_status,withdrawn_at_ms
               FROM accepted_sessions WHERE submission_id=?""",
            ("corrected-submission",),
        ).fetchone()
        assert rejected_session[0] == "withdrawn"
        assert rejected_session[1] is not None
        connection.execute(
            """UPDATE event_speakers SET status='withdrawn',withdrawn_at_ms=1200
               WHERE id='correction-speaker'"""
        )
        connection.commit()
        accepted_after_withdrawal = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/corrected-submission/decision-corrections",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "correction-accept-withdrawn",
            },
            json={
                "corrected_decision": "accepted",
                "reason": "The program result changed, but the speaker withdrawal remains.",
                "send_email": False,
            },
        )

    assert accepted_after_withdrawal.status_code == 200, accepted_after_withdrawal.text
    original = connection.execute(
        "SELECT decision,internal_reason FROM submission_decisions WHERE submission_id=?",
        ("corrected-submission",),
    ).fetchone()
    assert tuple(original) == ("rejected", "Original program decision")
    corrections = connection.execute(
        """SELECT previous_decision,corrected_decision,reason
           FROM submission_decision_corrections WHERE submission_id=?
           ORDER BY corrected_at_ms,id""",
        ("corrected-submission",),
    ).fetchall()
    assert [tuple(row) for row in corrections] == [
        (
            "rejected",
            "accepted",
            "The program committee resolved a classification error.",
        ),
        (
            "accepted",
            "rejected",
            "A later eligibility review reversed the correction.",
        ),
        (
            "rejected",
            "accepted",
            "The program result changed, but the speaker withdrawal remains.",
        ),
    ]
    session = connection.execute(
        """SELECT lifecycle_status,withdrawn_at_ms,decision_correction_id
           FROM accepted_sessions WHERE submission_id=?""",
        ("corrected-submission",),
    ).fetchone()
    assert session[0] == "active"
    assert session[1] is None
    assert session[2] == accepted_after_withdrawal.json()["id"]
    audit_count = connection.execute(
        """SELECT COUNT(*) FROM audit_events
           WHERE action='submission.decision.correct' AND target_id='corrected-submission'"""
    ).fetchone()[0]
    assert audit_count == 3
    speaker = connection.execute(
        """SELECT status,selection_status,withdrawn_at_ms
           FROM event_speakers WHERE id='correction-speaker'"""
    ).fetchone()
    assert tuple(speaker) == ("withdrawn", "accepted", 1200)
    task_counts = dict(
        connection.execute(
            """SELECT task_type,COUNT(*) FROM speaker_tasks
               WHERE event_speaker_id='correction-speaker' GROUP BY task_type"""
        ).fetchall()
    )
    assert task_counts == {"headshot": 1, "profile": 1, "slides": 1}


async def test_unreviewed_proposal_can_be_accepted_with_an_audited_reason(
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
               VALUES ('direct-accept-form',?, ?,1,'direct-accept','Welcome','{"fields":[]}',
                       'published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('direct-accept-submission',?,?,'direct-accept-form','direct-accept-public',
                       'Invited program session','Abstract','Priya Raman','priya@example.test',
                       ?,'submitted',1000,1000,1000)""",
            (organization_id, event_id, user_id),
        )
        response = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/direct-accept-submission/accept",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "direct-acceptance-test",
            },
            json={
                "decision": "accepted",
                "internal_reason": "Invited program content does not require scoring.",
                "send_email": False,
                "speaker_message": "",
                "override_incomplete_reviews": False,
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["round_id"] is None
    session = connection.execute(
        """SELECT lifecycle_status FROM accepted_sessions
           WHERE submission_id='direct-accept-submission'"""
    ).fetchone()
    assert tuple(session) == ("active",)
    audit = connection.execute(
        """SELECT metadata_json FROM audit_events
           WHERE action='submission.decision.record'
             AND target_id='direct-accept-submission'"""
    ).fetchone()[0]
    assert '"direct_decision":true' in audit
    assert '"direct_rejection":false' in audit


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
        # Round membership must exist before an assignment can reference it: the
        # assignment foreign keys are composite against these tables.
        connection.execute(
            """INSERT INTO evaluation_round_submissions
               (round_id,submission_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES ('rejection-round','round-submission',?,?,'active',1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO evaluation_round_evaluators
               (round_id,evaluator_user_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES ('rejection-round',?,?,?,'active',1000,1000)""",
            (reviewer_user_id, organization_id, event_id),
        )
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
        # Round membership must exist before an assignment can reference it: the
        # assignment foreign keys are composite against these tables.
        connection.execute(
            """INSERT INTO evaluation_round_submissions
               (round_id,submission_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES ('draft-round','draft-submission',?,?,'active',1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO evaluation_round_evaluators
               (round_id,evaluator_user_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES ('draft-round',?,?,?,'active',1000,1000)""",
            (reviewer_user_id, organization_id, event_id),
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
            """INSERT INTO evaluation_round_submissions
               (round_id,submission_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES ('draft-decision-round','draft-decision-submission',
                       ?,?,'active',1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO evaluation_round_evaluators
               (round_id,evaluator_user_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES ('draft-decision-round',?,?,?,'active',1000,1000)""",
            (reviewer_user_id, organization_id, event_id),
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

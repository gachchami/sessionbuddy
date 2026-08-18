import asyncio

import pytest
from fastapi import HTTPException

from sessionbuddy.evaluation import router as evaluation_router_module
from sessionbuddy.platform.upload_contracts import task_form_schema_json
from tests.agenda.test_session_content_history import _admin
from tests.security.test_production_identity_flow import (
    _client,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)


def test_legacy_decision_body_does_not_fabricate_an_override() -> None:
    assert evaluation_router_module._decision_speaker_message(
        "<p>Thank you for your proposal. It was not selected for this event.</p>"
    ) == ""
    assert evaluation_router_module._decision_speaker_message(
        '<p data-message-source="custom">First line<br>Second line</p>'
    ) == "First line\nSecond line"


def test_decision_message_tokens_resolve_before_preview_and_delivery() -> None:
    composition = evaluation_router_module._decision_composition(
        event_name="DevFlow",
        speaker_name="Priya Raman",
        proposal_title="Reliable systems",
        decision="accepted",
        correction=False,
        subject_override="{{speaker.name}}: {{submission.title}}",
        message_override=(
            "Hi {{speaker.name}}, your session '{{submission.title}}' is accepted for "
            "{{event.name}}."
        ),
    )

    assert composition.subject == "Priya Raman: Reliable systems"
    assert composition.body == (
        "Hi Priya Raman, your session 'Reliable systems' is accepted for DevFlow."
    )

    legacy = evaluation_router_module._decision_composition(
        event_name="DevFlow",
        speaker_name="Priya Raman",
        proposal_title="Reliable systems",
        decision="accepted",
        correction=False,
        subject_override="",
        message_override=(
            "Hi {speaker_name}, congratulations! Your session '{talk_title}' "
            "has been accepted for {event_name}."
        ),
    )
    assert legacy.body == (
        "Hi Priya Raman, congratulations! Your session 'Reliable systems' "
        "has been accepted for DevFlow."
    )


def test_decision_message_rejects_unknown_tokens_but_preserves_literal_braces() -> None:
    with pytest.raises(HTTPException) as invalid:
        evaluation_router_module._decision_composition(
            event_name="DevFlow",
            speaker_name="Priya Raman",
            proposal_title="Reliable systems",
            decision="accepted",
            correction=False,
            subject_override="",
            message_override="Hi {{speaker.first_name}}",
        )
    assert invalid.value.status_code == 422

    literal = evaluation_router_module._decision_composition(
        event_name="DevFlow",
        speaker_name="Priya Raman",
        proposal_title="Reliable systems",
        decision="accepted",
        correction=False,
        subject_override="Notes {for the speaker}",
        message_override="Use {curly braces} in your example.",
    )
    assert literal.subject == "Notes {for the speaker}"
    assert literal.body == "Use {curly braces} in your example."


def test_decision_message_substitutions_do_not_expand_replacement_text() -> None:
    composition = evaluation_router_module._decision_composition(
        event_name="DevFlow",
        speaker_name="Speaker named {talk_title}",
        proposal_title="Reliable systems",
        decision="accepted",
        correction=False,
        subject_override="",
        message_override="Hi {{speaker.name}}",
    )
    assert composition.body == "Hi Speaker named {talk_title}"


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


async def _round_decision_fixture(
    client, connection, environment, csrf, organization_id, event_id, user_id, prefix
):
    form_id = f"{prefix}-form"
    submission_id = f"{prefix}-submission"
    round_id = f"{prefix}-round"
    assignment_id = f"{prefix}-assignment"
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
            published_at_ms,created_at_ms,updated_at_ms)
           VALUES (?, ?, ?, 1, ?, 'Welcome', '{"fields":[]}', 'published', 1000,1000,1000)""",
        (form_id, organization_id, event_id, form_id),
    )
    connection.execute(
        """INSERT INTO submissions
           (id,organization_id,event_id,form_id,public_session_id,proposal_title,
            proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
            submitted_at_ms,created_at_ms,updated_at_ms)
           VALUES (?, ?, ?, ?, ?, 'Concurrent round decision', 'Abstract',
                   'Priya Raman','priya@example.test',?,'submitted',1000,1000,1000)""",
        (submission_id, organization_id, event_id, form_id, f"{prefix}-public", user_id),
    )
    connection.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms)
           VALUES (?, ?, ?, ?, '{}','open',1000,1000)""",
        (round_id, organization_id, event_id, f"Review {prefix}"),
    )
    reviewer_user_id = await _accepted_reviewer(
        client,
        connection,
        environment,
        csrf,
        event_id,
        f"{prefix}-reviewer@example.com",
    )
    connection.execute(
        """INSERT INTO evaluation_round_submissions
           (round_id,submission_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
           VALUES (?, ?, ?, ?, 'active',1000,1000)""",
        (round_id, submission_id, organization_id, event_id),
    )
    connection.execute(
        """INSERT INTO evaluation_round_evaluators
           (round_id,evaluator_user_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
           VALUES (?, ?, ?, ?, 'active',1000,1000)""",
        (round_id, reviewer_user_id, organization_id, event_id),
    )
    connection.execute(
        """INSERT INTO evaluation_assignments
           (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
            status,created_at_ms,updated_at_ms)
           VALUES (?, ?, ?, ?, ?, ?, 'assigned',1000,1000)""",
        (
            assignment_id,
            organization_id,
            event_id,
            round_id,
            submission_id,
            reviewer_user_id,
        ),
    )
    connection.commit()
    return round_id, submission_id


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
        preview = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/direct-submission/decision-message-preview",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={"decision": "rejected", "correction": False},
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
                "send_email": True,
                "speaker_message": (
                    "Thank you, but this proposal is outside our program scope.\n"
                    "Please submit again next year."
                ),
                "override_incomplete_reviews": False,
            },
        )

    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "resolved_subject": "Content Summit: proposal not selected",
        "resolved_body": "Thank you for your proposal. It was not selected for this event.",
        "proposal_title": "No review needed",
        "recipient_available": True,
    }
    assert response.status_code == 200, response.text
    assert response.json()["round_id"] is None
    assert response.json()["communication_queued"] is True
    assert response.json()["speaker_subject"] == ""
    assert response.json()["speaker_message"] == (
        "Thank you, but this proposal is outside our program scope.\nPlease submit again next year."
    )
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
    assert "scope.<br>Please submit again next year." in communication[2]
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
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES ('active-co-user','active-co@example.test','active-co@example.test',
                       'active',800,800)"""
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES ('active-co-member',?,'active-co-user','member','active',800,800)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO people
               (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
               VALUES ('active-co-person',?,'active-co-user','Active Co Speaker',800,800)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,selection_status,
                accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
               VALUES ('active-co-speaker',?,?,'active-co-person','complete','submitted',
                       800,800,800,800)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO people
               (id,organization_id,display_name,created_at_ms,updated_at_ms)
               VALUES ('correction-co-person',?,'Co Speaker',800,800)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,selection_status,
                accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms,
                withdrawn_at_ms)
               VALUES ('correction-co-speaker',?,?,'correction-co-person','withdrawn',
                       'accepted',800,800,800,800,1200)""",
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
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,
                snapshot_name,created_at_ms)
               VALUES ('correction-co-link',?,?,'corrected-submission',
                       'correction-co-speaker','co_speaker','Co Speaker',800)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,
                snapshot_name,created_at_ms)
               VALUES ('active-co-link',?,?,'corrected-submission',
                       'active-co-speaker','co_author','Active Co Speaker',800)""",
            (organization_id, event_id),
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
                        title,destination_type,state,created_at_ms,updated_at_ms,form_schema_json)
                       VALUES (?,?,?,?,?,?,?,?, 'open',900,900,?)""",
                (
                    task_id,
                    organization_id,
                    event_id,
                    "correction-speaker",
                    "already-accepted-submission",
                    task_type,
                        f"Existing {task_type} task",
                        task_type,
                        task_form_schema_json(task_type),
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
        assert accepted.json()["communication_queued"] is False
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
        assert tuple(connection.execute(
            """SELECT status,selection_status FROM event_speakers
               WHERE id='active-co-speaker'"""
        ).fetchone()) == ("complete", "rejected")
        assert connection.execute(
            """SELECT COUNT(*) FROM speaker_tasks
               WHERE event_speaker_id='active-co-speaker' AND state='waived'"""
        ).fetchone()[0] == 2
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
                "send_email": True,
                "speaker_subject": "Your corrected conference result",
                "speaker_message": (
                    "Your proposal result changed; your withdrawal remains in effect."
                ),
            },
        )
        accepted_after_withdrawal_replay = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/corrected-submission/decision-corrections",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "correction-accept-withdrawn",
            },
            json={
                "corrected_decision": "accepted",
                "reason": "The program result changed, but the speaker withdrawal remains.",
                "send_email": True,
                "speaker_subject": "Your corrected conference result",
                "speaker_message": (
                    "Your proposal result changed; your withdrawal remains in effect."
                ),
            },
        )
        changed_correction_retry = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/corrected-submission/decision-corrections",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "correction-accept-withdrawn",
            },
            json={
                "corrected_decision": "accepted",
                "reason": "The program result changed, but the speaker withdrawal remains.",
                "send_email": True,
                "speaker_subject": "A changed correction subject",
                "speaker_message": (
                    "Your proposal result changed; your withdrawal remains in effect."
                ),
            },
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('withdrawn-new-submission',?,?,'correction-form','withdrawn-new-public',
                       'Accepted after withdrawal','Abstract','Priya Raman',
                       'priya@example.test',?,'submitted',1300,1300,1300)""",
            (organization_id, event_id, user_id),
        )
        connection.execute(
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,
                snapshot_name,created_at_ms)
               VALUES ('withdrawn-new-primary',?,?,'withdrawn-new-submission',
                       'correction-speaker','primary','Priya Raman',1300)""",
            (organization_id, event_id),
        )
        connection.commit()
        rejected_without_session = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/withdrawn-new-submission/reject",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "withdrawn-new-reject",
            },
            json={
                "decision": "rejected",
                "internal_reason": "Original decision before speaker returned",
                "send_email": False,
                "speaker_message": "",
                "override_incomplete_reviews": False,
            },
        )
        assert rejected_without_session.status_code == 200
        accepted_without_existing_session = await client.post(
            f"/api/v1/admin/events/{event_id}/submissions/withdrawn-new-submission/decision-corrections",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "withdrawn-new-accept",
            },
            json={
                "corrected_decision": "accepted",
                "reason": "The proposal qualifies, while the speaker remains withdrawn.",
            },
        )

    assert accepted_after_withdrawal.status_code == 200, accepted_after_withdrawal.text
    assert accepted_after_withdrawal_replay.status_code == 200
    assert accepted_after_withdrawal_replay.json()["speaker_message"] == (
        "Your proposal result changed; your withdrawal remains in effect."
    )
    assert accepted_after_withdrawal_replay.json()["speaker_subject"] == (
        "Your corrected conference result"
    )
    assert changed_correction_retry.status_code == 409
    assert changed_correction_retry.headers["x-conflict-type"] == "decision-correction"
    assert "changed after an earlier attempt" in changed_correction_retry.json()["error"]["message"]
    assert accepted_without_existing_session.status_code == 200, (
        accepted_without_existing_session.text
    )
    withdrawn_new_session = connection.execute(
        """SELECT lifecycle_status,withdrawn_at_ms FROM accepted_sessions
           WHERE submission_id='withdrawn-new-submission'"""
    ).fetchone()
    assert tuple(withdrawn_new_session) == ("withdrawn", 1200)
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
    assert session[0] == "withdrawn"
    assert session[1] == 1200
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

    async with _client(environment) as restore_client:
        requested = await restore_client.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin"},
        )
        assert requested.status_code == 202
        verified = await restore_client.post(
            "/auth/verify",
            data={"token": _token(connection, "admin@example.com")},
            follow_redirects=False,
        )
        assert verified.status_code == 303
        restore_session = (await restore_client.get("/api/v1/auth/session")).json()
        restored_co_speaker = await restore_client.post(
            f"/api/v1/admin/events/{event_id}/speakers/correction-co-speaker/restore",
            headers={
                "origin": "https://test",
                "x-csrf-token": restore_session["csrf_token"],
            },
            json={"participation_version": 1},
        )
        assert restored_co_speaker.status_code == 200, restored_co_speaker.text
        assert restored_co_speaker.json()["reactivated_session_count"] == 0
        session_after_co_restore = connection.execute(
            """SELECT lifecycle_status FROM accepted_sessions
               WHERE submission_id='corrected-submission'"""
        ).fetchone()[0]
        assert session_after_co_restore == "withdrawn"
        restored_response = await restore_client.post(
            f"/api/v1/admin/events/{event_id}/speakers/correction-speaker/restore",
            headers={
                "origin": "https://test",
                "x-csrf-token": restore_session["csrf_token"],
            },
            json={"participation_version": 1},
        )
        repeated_restore = await restore_client.post(
            f"/api/v1/admin/events/{event_id}/speakers/correction-speaker/restore",
            headers={
                "origin": "https://test",
                "x-csrf-token": restore_session["csrf_token"],
            },
            json={"participation_version": 1},
        )

    assert restored_response.status_code == 200, restored_response.text
    assert restored_response.json() == {
        "status": "onboarding",
        "participation_version": 2,
        "reactivated_session_count": 2,
    }
    assert repeated_restore.status_code == 409
    restored_speaker = connection.execute(
        "SELECT status,withdrawn_at_ms FROM event_speakers WHERE id='correction-speaker'"
    ).fetchone()
    assert tuple(restored_speaker) == ("onboarding", None)
    restored_sessions = connection.execute(
        """SELECT submission_id,lifecycle_status,withdrawn_at_ms FROM accepted_sessions
           WHERE submission_id IN ('corrected-submission','withdrawn-new-submission')
           ORDER BY submission_id"""
    ).fetchall()
    assert [tuple(row) for row in restored_sessions] == [
        ("corrected-submission", "active", None),
        ("withdrawn-new-submission", "active", None),
    ]
    resumed_tasks = dict(
        connection.execute(
            """SELECT task_type,COUNT(*) FROM speaker_tasks
               WHERE event_speaker_id='correction-speaker' AND state='open'
               GROUP BY task_type"""
        ).fetchall()
    )
    assert resumed_tasks == {"headshot": 1, "profile": 1, "slides": 2}
    co_speaker_tasks = {
        row[0]
        for row in connection.execute(
            """SELECT task_type FROM speaker_tasks
               WHERE event_speaker_id='correction-co-speaker'"""
        ).fetchall()
    }
    assert co_speaker_tasks == {"profile", "headshot"}
    restore_audits = connection.execute(
        """SELECT COUNT(*) FROM audit_events
           WHERE action='speaker.participation.restore' AND target_id='correction-speaker'"""
    ).fetchone()[0]
    assert restore_audits == 1


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


async def test_concurrent_matching_acceptance_requests_reconcile_to_one_decision(
    production_environment,  # noqa: F811 - pytest fixture
    monkeypatch,
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
               VALUES ('concurrent-accept-form',?, ?,1,'concurrent-accept','Welcome',
                       '{"fields":[]}','published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,submitter_user_id,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES ('concurrent-accept-submission',?,?,'concurrent-accept-form',
                       'concurrent-accept-public','Concurrent acceptance','Abstract',
                       'Priya Raman','priya@example.test',?,'submitted',1000,1000,1000)""",
            (organization_id, event_id, user_id),
        )

        original_execute = evaluation_router_module._execute
        both_ready = asyncio.Event()
        ready = 0

        async def execute_together(request, batch):
            nonlocal ready
            ready += 1
            if ready == 2:
                both_ready.set()
            await asyncio.wait_for(both_ready.wait(), timeout=2)
            await original_execute(request, batch)

        monkeypatch.setattr(evaluation_router_module, "_execute", execute_together)
        path = (
            f"/api/v1/admin/events/{event_id}/submissions/"
            "concurrent-accept-submission/accept"
        )
        payload = {
            "decision": "accepted",
            "internal_reason": "Matching concurrent organizer intent",
            "send_email": True,
            "speaker_subject": "A custom acceptance subject",
            "speaker_message": "Your proposal was accepted.",
            "override_incomplete_reviews": False,
        }
        first, second = await asyncio.gather(
            client.post(
                path,
                headers={
                    "origin": "https://test",
                    "x-csrf-token": csrf,
                    "idempotency-key": "concurrent-acceptance-a",
                },
                json=payload,
            ),
            client.post(
                path,
                headers={
                    "origin": "https://test",
                    "x-csrf-token": csrf,
                    "idempotency-key": "concurrent-acceptance-b",
                },
                json=payload,
            ),
        )

    assert [first.status_code, second.status_code] == [200, 200]
    assert first.json()["id"] == second.json()["id"]
    assert connection.execute(
        "SELECT COUNT(*) FROM submission_decisions WHERE submission_id=?",
        ("concurrent-accept-submission",),
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM accepted_sessions WHERE submission_id=?",
        ("concurrent-accept-submission",),
    ).fetchone()[0] == 1
    assert connection.execute(
        """SELECT COUNT(*) FROM communication_messages
           WHERE deterministic_key LIKE 'submission-decision:%'"""
    ).fetchone()[0] == 1


async def test_round_decision_replays_same_key_without_duplicate_side_effects(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        round_id, submission_id = await _round_decision_fixture(
            client,
            connection,
            environment,
            csrf,
            organization_id,
            event_id,
            user_id,
            "round-replay",
        )
        path = f"/api/v1/admin/evaluation-rounds/{round_id}/submissions/{submission_id}/decision"
        headers = {
            "origin": "https://test",
            "x-csrf-token": csrf,
            "idempotency-key": "round-decision-replay",
        }
        payload = {
            "decision": "accepted",
            "internal_reason": "Organizer override",
            "send_email": True,
            "speaker_subject": "A custom acceptance subject",
            "speaker_message": "Your proposal was accepted.",
            "override_incomplete_reviews": True,
        }
        first = await client.post(path, headers=headers, json=payload)
        second = await client.post(path, headers=headers, json=payload)
        connection.execute(
            """UPDATE communication_messages SET status='failed'
               WHERE deterministic_key LIKE 'submission-decision:%'"""
        )
        connection.commit()
        failed_delivery_replay = await client.post(path, headers=headers, json=payload)
        changed = await client.post(
            path,
            headers=headers,
            json={**payload, "speaker_message": "A changed retry message."},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.content == second.content
    assert failed_delivery_replay.json()["send_email"] is True
    assert failed_delivery_replay.json()["communication_queued"] is False
    assert first.json()["override_incomplete_reviews"] is True
    assert first.json()["speaker_subject"] == "A custom acceptance subject"
    assert first.json()["speaker_message"] == "Your proposal was accepted."
    assert changed.status_code == 409, changed.text
    assert changed.headers["x-conflict-type"] == "final-decision"
    assert "changed after an earlier attempt" in changed.json()["error"]["message"]
    plan = [
        str(row[3])
        for row in connection.execute(
            f"EXPLAIN QUERY PLAN {evaluation_router_module.DECISION_VIEW_SQL}",
            (first.json()["id"],),
        ).fetchall()
    ]
    assert any("SEARCH a USING INDEX idx_audit_target" in detail for detail in plan)
    # The projection reads delivery/provenance columns from the single row found
    # through the unique deterministic-key index. Keeping the index narrow costs
    # one bounded table-row fetch here and avoids enlarging every message write.
    assert any("SEARCH cm USING INDEX" in detail for detail in plan)
    assert not any(
        "SCAN a" in detail or "SCAN cm" in detail or "TEMP B-TREE" in detail
        for detail in plan
    )
    assert connection.execute(
        "SELECT COUNT(*) FROM submission_decisions WHERE submission_id=?", (submission_id,)
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM communication_messages WHERE deterministic_key LIKE ?",
        ("submission-decision:%",),
    ).fetchone()[0] == 1


async def test_acceptance_onboards_participant_who_accepted_invitation_first(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        round_id, submission_id = await _round_decision_fixture(
            client,
            connection,
            environment,
            csrf,
            organization_id,
            event_id,
            user_id,
            "participant-before-decision",
        )
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES ('early-participant','early@example.test','early@example.test',
                       'active',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES ('early-membership',?,'early-participant','member','active',1000,1000)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO people
               (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
               VALUES ('early-person',?,'early-participant','Early Participant',1000,1000)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,selection_status,
                accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
               VALUES ('early-speaker',?,?,'early-person','complete','submitted',
                       1000,1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,
                snapshot_name,created_at_ms)
               VALUES ('early-link',?,?,?,'early-speaker','co_author',
                       'Early Participant',1000)""",
            (organization_id, event_id, submission_id),
        )
        response = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/submissions/{submission_id}/decision",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "participant-before-decision-accept",
            },
            json={
                "decision": "accepted",
                "internal_reason": "Selected",
                "send_email": False,
                "speaker_message": "",
                "override_incomplete_reviews": True,
            },
        )

    assert response.status_code == 200, response.text
    assert tuple(connection.execute(
        """SELECT status,selection_status FROM event_speakers
           WHERE id='early-speaker'"""
    ).fetchone()) == ("onboarding", "accepted")
    tasks = connection.execute(
        """SELECT task_type FROM speaker_tasks
           WHERE event_speaker_id='early-speaker' ORDER BY task_type"""
    ).fetchall()
    assert [row[0] for row in tasks] == ["headshot", "profile"]


async def test_concurrent_matching_round_decisions_reconcile_to_one_result(
    production_environment,  # noqa: F811 - pytest fixture
    monkeypatch,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        round_id, submission_id = await _round_decision_fixture(
            client,
            connection,
            environment,
            csrf,
            organization_id,
            event_id,
            user_id,
            "round-race",
        )
        original_execute = evaluation_router_module._execute
        both_ready = asyncio.Event()
        ready = 0

        async def execute_together(request, batch):
            nonlocal ready
            ready += 1
            if ready == 2:
                both_ready.set()
            await asyncio.wait_for(both_ready.wait(), timeout=2)
            await original_execute(request, batch)

        monkeypatch.setattr(evaluation_router_module, "_execute", execute_together)
        path = f"/api/v1/admin/evaluation-rounds/{round_id}/submissions/{submission_id}/decision"
        payload = {
            "decision": "accepted",
            "internal_reason": "Organizer override",
            "send_email": True,
            "speaker_message": "Your proposal was accepted.",
            "override_incomplete_reviews": True,
        }
        first, second = await asyncio.gather(
            client.post(
                path,
                headers={
                    "origin": "https://test",
                    "x-csrf-token": csrf,
                    "idempotency-key": "round-race-request-a",
                },
                json=payload,
            ),
            client.post(
                path,
                headers={
                    "origin": "https://test",
                    "x-csrf-token": csrf,
                    "idempotency-key": "round-race-request-b",
                },
                json=payload,
            ),
        )

    assert [first.status_code, second.status_code] == [200, 200]
    assert first.json()["id"] == second.json()["id"]
    assert connection.execute(
        "SELECT COUNT(*) FROM submission_decisions WHERE submission_id=?", (submission_id,)
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM communication_messages WHERE deterministic_key LIKE ?",
        ("submission-decision:%",),
    ).fetchone()[0] == 1


async def test_concurrent_divergent_round_decisions_report_the_recorded_outcome(
    production_environment,  # noqa: F811 - pytest fixture
    monkeypatch,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        round_id, submission_id = await _round_decision_fixture(
            client,
            connection,
            environment,
            csrf,
            organization_id,
            event_id,
            user_id,
            "round-divergent",
        )
        original_execute = evaluation_router_module._execute
        both_ready = asyncio.Event()
        ready = 0

        async def execute_together(request, batch):
            nonlocal ready
            ready += 1
            if ready == 2:
                both_ready.set()
            await asyncio.wait_for(both_ready.wait(), timeout=2)
            await original_execute(request, batch)

        monkeypatch.setattr(evaluation_router_module, "_execute", execute_together)
        path = f"/api/v1/admin/evaluation-rounds/{round_id}/submissions/{submission_id}/decision"
        common = {
            "internal_reason": "Organizer override",
            "send_email": False,
            "speaker_message": "",
            "override_incomplete_reviews": True,
        }
        first, second = await asyncio.gather(
            client.post(
                path,
                headers={
                    "origin": "https://test",
                    "x-csrf-token": csrf,
                    "idempotency-key": "round-divergent-accept",
                },
                json={**common, "decision": "accepted"},
            ),
            client.post(
                path,
                headers={
                    "origin": "https://test",
                    "x-csrf-token": csrf,
                    "idempotency-key": "round-divergent-reject",
                },
                json={**common, "decision": "rejected"},
            ),
        )

    responses = [first, second]
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response for response in responses if response.status_code == 200)
    loser = next(response for response in responses if response.status_code == 409)
    assert loser.headers["x-conflict-type"] == "final-decision"
    assert "Another final decision was recorded" in loser.json()["error"]["message"]
    recorded = connection.execute(
        "SELECT decision FROM submission_decisions WHERE submission_id=?", (submission_id,)
    ).fetchall()
    assert [tuple(row) for row in recorded] == [(winner.json()["decision"],)]


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

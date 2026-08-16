"""The round dashboard reports reviewer membership, not only assignment activity."""

from tests.evaluation.test_round_selection_persistence import (
    SAM_USER_ID,
    A,
    _admin_user_id,
    _client,
    _create_round,
    _round_body,
    _setup,
    production_environment,  # noqa: F401 - pytest fixture
)


async def test_results_keep_active_unassigned_reviewers_and_count_each_assignment_once(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _setup(client, connection)
        created = await _create_round(
            client,
            csrf,
            event_id,
            _round_body(assignments=[A]),
            label="results-membership",
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]
        assignment_id = connection.execute(
            "SELECT id FROM evaluation_assignments WHERE round_id=? AND submission_id=?",
            (round_id, A),
        ).fetchone()[0]
        # An evaluation and a conflict both join through this assignment. DISTINCT keeps
        # those child rows from multiplying the assignment's progress count.
        connection.execute(
            """INSERT INTO evaluations
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,
                rating,recommendation,internal_comment,state,created_at_ms,updated_at_ms,
                finalized_at_ms,criterion_responses_json)
               VALUES ('evaluation-1',?,?,?,?,?,4,'accept','Draft note','draft',1000,1000,NULL,
                       '{"notes":"Private until final"}')""",
            (organization_id, event_id, round_id, assignment_id, SAM_USER_ID),
        )
        connection.commit()
        draft_results = await client.get(
            f"/api/v1/admin/evaluation-rounds/{round_id}/results"
        )
        assert draft_results.status_code == 200, draft_results.text
        reviewed_submission = next(
            submission
            for submission in draft_results.json()["submissions"]
            if submission["submission_id"] == A
        )
        assert reviewed_submission["reviews"][0]["criterion_responses"] == {}

        connection.execute(
            """UPDATE evaluations
               SET state='final', internal_comment='Done', finalized_at_ms=1000
               WHERE id='evaluation-1'"""
        )
        connection.execute(
            """INSERT INTO evaluation_conflicts
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,
                conflict_type,explanation,declared_at_ms)
               VALUES ('conflict-1',?,?,?,?,?,'other','Needs review',1000)""",
            (organization_id, event_id, round_id, assignment_id, SAM_USER_ID),
        )
        connection.commit()

        results = await client.get(f"/api/v1/admin/evaluation-rounds/{round_id}/results")
        assert results.status_code == 200, results.text
        (reviewer,) = results.json()["evaluators"]
        assert reviewer == {
            "evaluator_user_id": SAM_USER_ID,
            "display_name": "Sam Whitfield",
            "assigned_count": 1,
            "completed_count": 1,
            "conflict_count": 1,
            "late_assignment_count": 0,
            "late_completed_count": 0,
        }

        # Revoking the only pair leaves Sam attached to the draft. Progress must retain
        # the membership and name while excluding the revoked pair, its final review, and
        # its conflict — otherwise the dashboard hides "Remove" for a reviewer who has
        # nothing live and cannot be re-added either.
        connection.execute(
            "UPDATE evaluation_assignments SET status='revoked' WHERE id=?", (assignment_id,)
        )
        connection.commit()
        unassigned = await client.get(f"/api/v1/admin/evaluation-rounds/{round_id}/results")
        (reviewer,) = unassigned.json()["evaluators"]
        assert reviewer["display_name"] == "Sam Whitfield"
        assert reviewer["assigned_count"] == 0
        assert reviewer["completed_count"] == 0
        assert reviewer["conflict_count"] == 0



async def test_remove_hides_membership_and_add_reactivates_it(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup(client, connection)
        created = await _create_round(
            client,
            csrf,
            event_id,
            _round_body(assignments=[A]),
            label="remove-membership",
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]
        # Leave an attached reviewer with no live work: this is the state that exposed
        # whether Remove updates membership or only revokes assignments.
        connection.execute(
            """UPDATE evaluation_assignments SET status='revoked'
               WHERE round_id=? AND evaluator_user_id=?""",
            (round_id, SAM_USER_ID),
        )
        connection.commit()

        removed = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/evaluators/{SAM_USER_ID}/remove",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={},
        )
        assert removed.status_code == 200, removed.text
        assert connection.execute(
            """SELECT status FROM evaluation_round_evaluators
               WHERE round_id=? AND evaluator_user_id=?""",
            (round_id, SAM_USER_ID),
        ).fetchone()[0] == "removed"
        after_remove = await client.get(f"/api/v1/admin/evaluation-rounds/{round_id}/results")
        assert after_remove.json()["evaluators"] == []

        added = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/evaluators",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={"evaluator_user_id": SAM_USER_ID, "submission_ids": [A]},
        )
        assert added.status_code == 200, added.text
        assert connection.execute(
            """SELECT status FROM evaluation_round_evaluators
               WHERE round_id=? AND evaluator_user_id=?""",
            (round_id, SAM_USER_ID),
        ).fetchone()[0] == "active"
        after_add = await client.get(f"/api/v1/admin/evaluation-rounds/{round_id}/results")
        (reviewer,) = after_add.json()["evaluators"]
        assert reviewer["assigned_count"] == 1


async def test_decided_member_remains_in_open_round_without_a_live_assignment(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _setup(client, connection)
        created = await _create_round(
            client,
            csrf,
            event_id,
            _round_body(status="open", assignments=[A], submission_ids=[A]),
            label="decided-open-membership",
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]
        user_id = _admin_user_id(connection)
        connection.execute(
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
                version,decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES ('membership-decision',?,?,?,?,'accepted','Original reason',
                       1,?,1000,1000)""",
            (organization_id, event_id, round_id, A, user_id),
        )
        connection.execute(
            """INSERT INTO submission_decision_corrections
               (id,organization_id,event_id,submission_id,original_decision_id,
                previous_decision,corrected_decision,reason,corrected_by_user_id,corrected_at_ms)
               VALUES ('membership-correction',?,?,?,'membership-decision','accepted','rejected',
                       'Latest correction reason',?,1100)""",
            (organization_id, event_id, A, user_id),
        )
        connection.execute(
            """UPDATE evaluation_assignments SET status='revoked'
               WHERE round_id=? AND submission_id=?""",
            (round_id, A),
        )
        connection.commit()

        inbox = await client.get(f"/api/v1/admin/events/{event_id}/submissions")
        assert inbox.status_code == 200, inbox.text
        proposal = next(item for item in inbox.json()["data"] if item["id"] == A)
        assert proposal["status"] == "rejected"
        assert proposal["evaluation_round_id"] == round_id
        assert proposal["reassessment_state"] == "under_review"

        results = await client.get(f"/api/v1/admin/evaluation-rounds/{round_id}/results")
        assert results.status_code == 200, results.text
        (proposal_result,) = results.json()["submissions"]
        assert proposal_result["decision"] == "rejected"
        assert proposal_result["decision_round_id"] == round_id
        assert proposal_result["internal_reason"] == "Original reason"
        assert proposal_result["correction_reason"] == "Latest correction reason"

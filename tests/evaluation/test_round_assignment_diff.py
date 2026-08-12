"""The round assignment diff: the rules that keep explicit pairs correct.

plan_round_diff is deliberately pure -- it takes the current database state and the desired
state and returns the writes. That keeps the rules that matter testable without a database,
because they are rules about intent, not about SQL:

* A pair may only be added or revived while BOTH memberships end up active. The schema
  enforces that membership EXISTS, never that it is ACTIVE, so this is the only guard.
* A conflict-revoked pair is never revived -- an organizer resubmitting the same desired
  state must not silently undo a reviewer's recusal.
* A finalized evaluation is never revoked.
* Identical desired state produces an empty diff, so the caller skips the batch, the
  version bump and the audit record entirely.
"""

from sessionbuddy.evaluation.router import AssignmentState, plan_round_diff

A, B, C = "proposal-a", "proposal-b", "proposal-c"
SAM, KIM = "sam", "kim"
ACTIVE_THREE = {A: "active", B: "active", C: "active"}


def plan(
    current_assignments,
    desired_pairs,
    *,
    current_submissions=None,
    current_evaluators=None,
    desired_submissions=(A, B, C),
    desired_evaluators=(SAM,),
):
    return plan_round_diff(
        current_submissions=dict(
            ACTIVE_THREE if current_submissions is None else current_submissions
        ),
        current_evaluators=dict(
            {SAM: "active"} if current_evaluators is None else current_evaluators
        ),
        current_assignments=current_assignments,
        desired_submissions=list(desired_submissions),
        desired_evaluators=list(desired_evaluators),
        desired_pairs=list(desired_pairs),
    )


BOTH = {(A, SAM): AssignmentState("a1", "assigned"), (B, SAM): AssignmentState("a2", "assigned")}


def test_assigns_two_of_three_proposals_and_leaves_the_third_a_member():
    """ABS-05. Sam reviews A and B; C stays in the round with nobody on it."""
    diff = plan({}, [(A, SAM), (B, SAM)], current_submissions={}, current_evaluators={})
    assert diff.add_assignments == [(A, SAM), (B, SAM)]
    assert diff.activate_submissions == [A, B, C]
    assert not any(pair[0] == C for pair in diff.add_assignments)


def test_repeating_the_same_desired_state_is_a_true_no_op():
    diff = plan(BOTH, [(A, SAM), (B, SAM)])
    assert diff.changed is False
    assert not (
        diff.activate_submissions
        or diff.add_assignments
        or diff.revive_assignments
        or diff.revoke_assignments
        or diff.deactivate_submissions
        or diff.deactivate_evaluators
    )


def test_conflict_revoked_pairs_are_never_revived():
    current = {**BOTH, (C, SAM): AssignmentState("a3", "revoked", has_conflict=True)}
    diff = plan(current, [(A, SAM), (B, SAM), (C, SAM)])
    assert diff.revive_assignments == []
    assert any("conflict-revoked" in reason for reason in diff.refused)


def test_organizer_revoked_pairs_are_revived_not_reinserted():
    """A second INSERT would hit UNIQUE (round_id, submission_id, evaluator_user_id)."""
    current = {**BOTH, (C, SAM): AssignmentState("a4", "revoked")}
    diff = plan(current, [(A, SAM), (B, SAM), (C, SAM)])
    assert diff.revive_assignments == ["a4"]
    assert diff.add_assignments == []


def test_finalized_evaluations_are_never_revoked():
    current = {**BOTH, (B, SAM): AssignmentState("a2", "completed", has_final_evaluation=True)}
    diff = plan(current, [(A, SAM)])
    assert diff.revoke_assignments == []
    assert any("finalized" in reason for reason in diff.refused)


def test_removing_a_reviewer_revokes_pairs_then_deactivates_membership():
    diff = plan(BOTH, [], desired_evaluators=())
    assert [(i, r) for i, r, _ in diff.revoke_assignments] == [
        ("a1", "organizer_removed"),
        ("a2", "organizer_removed"),
    ]
    assert diff.deactivate_evaluators == [SAM]
    # the proposals are still in the round; they have simply lost their reviewer
    assert diff.deactivate_submissions == []


def test_discarded_draft_metadata_is_whitelisted_not_the_evaluation_row():
    summary = {"criteria_answered": 2, "has_comment": True, "updated_at_ms": 9}
    current = {(A, SAM): AssignmentState("a1", "assigned", draft_summary=summary)}
    diff = plan(
        current,
        [],
        current_submissions={A: "active"},
        desired_submissions=(A,),
        desired_evaluators=(),
    )
    assert diff.revoke_assignments[0][2] == summary
    assert "internal_comment" not in diff.revoke_assignments[0][2]


def test_a_pair_losing_its_membership_in_the_same_save_is_refused():
    diff = plan(BOTH, [(A, SAM)], desired_evaluators=())
    assert diff.add_assignments == []
    assert any("membership not active" in reason for reason in diff.refused)

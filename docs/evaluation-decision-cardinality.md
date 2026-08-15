# Evaluation decision cardinality

Status: accepted

Date: 2026-08-22

## Context

SessionBuddy supports multiple first-class evaluation rounds. Each round owns its
review window, scorecard, blind-review setting, reviewer pool, submission
membership, and explicit reviewer-to-submission assignments.

Final proposal decisions have different cardinality. The released schema stores
the round that originated a decision as provenance, while enforcing one final
decision per submission through both `UNIQUE (submission_id)` and
`uq_submission_decisions_final`. Corrections are append-only records that preserve
the original decision and reconcile the accepted-session lifecycle.

The proposal inbox currently hides round-selection controls after a final
decision. This couples a round's input membership to the existence of a global
decision and prevents organizers from gathering another set of reviews without
discarding decision history.

## Decision

SessionBuddy retains one immutable global final decision per submission.
Evaluation rounds remain independent evidence-gathering stages and may include a
submission that already has a final decision.

A later round for a decided proposal is advisory:

- the existing accepted or rejected decision remains effective while the round
  is open;
- the proposal may receive fresh assignments and evaluations in that round;
- a later-round recommendation does not insert a second
  `submission_decisions` row;
- changing the effective result requires the existing audited decision-correction
  workflow;
- a recommendation matching the effective decision requires no correction;
- correction side effects must reconcile accepted sessions, speaker status,
  onboarding tasks, agenda placement, notification state, and audit history.

Decision status and evaluation activity are separate response concepts. APIs and
clients must keep `status` as the effective decision or submission state and
represent later-round activity separately, for example:

```json
{
  "status": "accepted",
  "evaluation_state": "under_review",
  "evaluation_round_id": "round-id",
  "evaluation_round_name": "Final Review"
}
```

The organizer UI may render this as `Accepted · Under review in Final Review`.
It must not add `under_review` to the decision-status vocabulary.

## Consequences

- Round membership must no longer be gated solely by the existence of a final
  decision.
- Membership in the same active round remains unavailable and must be explained
  separately from a prior decision.
- Reviewer queues continue to derive from active assignments and open review
  windows, not from decision state.
- Decision controls on a later-round results page must not call the ordinary
  decision-creation endpoint for an already-decided proposal. A differing result
  must enter the correction workflow.
- Proposal-content locking, participant-attribution editing, round eligibility,
  and decision correction are distinct policies. Relaxing round eligibility does
  not automatically permit proposal-content edits.
- Moving to one final decision per round would require a new ADR and an ordered,
  non-destructive migration covering effective-decision ordering and all
  downstream lifecycle semantics.

## Rejected alternatives

### Remove the existing decision before another round

Rejected because final decisions are immutable and must remain auditable.

### Store an additional final decision for every round

Rejected for the current implementation because it conflicts with the released
schema and leaves the effective decision, notifications, onboarding, and accepted
session ownership ambiguous.

### Treat `under_review` as a proposal status

Rejected because it destroys the distinction between the effective decision and
current evaluation activity and breaks clients that derive correction direction
and styling from the accepted/rejected status vocabulary.

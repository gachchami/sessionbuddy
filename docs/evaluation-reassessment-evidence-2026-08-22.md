# Evaluation reassessment UX evidence

Date: 2026-08-22

Areas: ABS-S1, ABS-S2, ABS-S3, AIA-S1

## Observed behavior

### Decided proposals disappear from future-round selection

The proposal inbox renders an inclusion checkbox only for an undecided submitted
proposal that is not already associated with a round. A proposal with a final
decision is instead labelled `Already decided`, even when the organizer is
creating a different later round.

This prevents the organizer from assigning the proposal for a second review and
blocks reviewer-queue evidence that depends on those assignments.

### Decision locking also disables participant attribution

After a final decision, the speaker-facing proposal editor reports that the
proposal is read-only and disables `Add participant`. The organizer proposal
surface does not provide an equivalent participant editor. Proposal-content
immutability and participant attribution are therefore controlled by one broad
lock even though they have different lifecycle and synchronization requirements.

Participant editing remains a separate product-policy decision. If enabled for
accepted proposals, it must synchronize accepted-session and agenda speaker
attribution rather than modifying only the proposal record.

### Rejected-to-accepted correction produced opaque conflicts

AIA-S1 offered `Record correction to accepted`, but repeated attempts left the
effective result rejected and surfaced only `Conflict Reference: <request-id>`.
The evaluation-round Reviews page also attempted the ordinary decision path for
an already-decided proposal. That path reaches the global decision uniqueness
guard and raises a bare HTTP 409, which the shared client renders with exactly
that opaque conflict-reference message.

The two entry points must be reproduced independently. The Reviews-page conflict
is explained by the global decision guard; the correction-dialog failure still
requires request-id and database-state evidence to identify its failing batch
statement.

## Correction-side coverage gap

The existing bidirectional correction test creates a submission without a
`submission_speakers` or `event_speakers` row. Its correction context therefore
has no `event_speaker_id` and skips all speaker-side effects:

- restoring the event speaker to accepted/onboarding state;
- creating guarded profile, headshot, and slides tasks;
- reconciling those tasks with another already-accepted proposal for the same
  speaker.

A production-shaped regression fixture must use one primary event speaker with:

1. proposal A already accepted;
2. open system profile and headshot tasks from proposal A;
3. proposal B rejected;
4. a correction of proposal B to accepted.

The test must assert the correction record, accepted-session lifecycle, speaker
state, task counts, audit event, notification replay, and idempotent retry. The task inserts contain
`WHERE NOT EXISTS` predicates aligned with the partial unique indexes, so task
uniqueness is a correctness assertion rather than the presumed root cause.

The fixture should also cover a withdrawn event speaker. A program-decision
correction must not silently reverse a person's independent withdrawal from the
event. It may update selection status, but it preserves the `withdrawn` /
`withdrawn_at_ms` lifecycle pair and does not create new onboarding work until
the speaker is explicitly restored through the speaker workflow. Its accepted
session remains withdrawn as well, preventing a session with no participating
speaker from silently becoming schedulable.

## Agreed implementation scope

1. Add the production-shaped correction regression and identify the exact
   correction-dialog batch failure.
2. Fix correction side effects without duplicating system tasks or implicitly
   reversing an unrelated speaker withdrawal.
3. Permit explicitly decided proposals to join a different draft or open round,
   while continuing to explain and prevent duplicate membership in the same
   round.
4. Expose later-round activity as a separate `reassessment_state`; preserve the
   effective accepted/rejected status.
5. Keep reviewer queues assignment-driven and add an assertion that a decided
   proposal assigned to a later open round appears exactly where assigned.
6. On later-round results, preserve a matching effective decision and route a
   differing result through audited correction instead of attempting a second
   final-decision insert.

## Expected UX

- A decided proposal can be selected for a later round.
- The inbox distinguishes `Already in this round` from a prior final decision.
- Accepted and rejected badges remain stable while a secondary badge communicates
  active re-evaluation.
- Reviewers see exactly their new assignments.
- Organizers receive a specific, persistent explanation when correction fails;
  confirmation and cancellation controls remain operable.
- A successful differing later-round outcome preserves the original decision and
  appends an audited correction with coherent session and speaker side effects.

## Validation

- correction endpoint tests with a real primary event speaker and pre-existing
  system tasks;
- withdrawn-speaker correction test;
- organizer inbox and later-round selection browser test;
- evaluator assignment API assertion for the decided proposal;
- results-page test proving matching outcomes are no-ops and differing outcomes
  invoke correction;
- accepted-session, speaker-task, and audit assertions;
- generated OpenAPI and embedded-asset checks after contract and UI changes.

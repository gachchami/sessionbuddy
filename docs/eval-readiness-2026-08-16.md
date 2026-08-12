# SessionBuddy evaluator readiness

Date: 16 August 2026  
Evaluator: `killmysaas-evals`  
Areas: Call for Papers, Abstract Management, Speaker Management, Content
Management, AI Agenda, Public Widgets

## Decision

Do not run the complete paid six-area evaluation until the hard blockers below
are fixed or explicitly accepted. Use a new run directory; do not resume either
failed run because the evaluator reuses terminal `evidence.json` files.

## Hard blockers

| Area / scenario | Current limitation | Expected effect | Required fix |
| --- | --- | --- | --- |
| ABS-S2 | Only one evaluation round may remain open. CFP-S3 can leave one open before ABS-S2 asks for two persisted rounds. | The second round cannot be created. ABS-S3 then lacks its required assignments. | Permit independent concurrent rounds with explicitly scoped assignments and results. |
| ABS-S2 / ABS-S3 | Scorecard criteria are numeric-only. | Dropdown and free-text criteria required by the evaluator cannot be configured, answered, or persisted. | Add typed criteria, choice configuration, typed answers, reviewer controls, and exports. |
| CNT-S1 / CNT-S2 | Organizer-created general tasks exist, but are stored as `custom`. Upload authorization requires task types matching `slides` or `supporting_document`. | A file-request task cannot own the requested upload, blocking the content chain. | Add organizer-created file-request task types and bind upload authorization to them. |
| AIA-S1 / AIA-S2 | Accepted decisions create sessions, but there is no clear direct session-creation path and final rejected decisions are immutable. | The evaluator may fail to produce three schedulable sessions, including two sharing a speaker. | Add direct audited session creation or an explicit audited decision-correction workflow. |
| All authenticated scenarios | A recreated database invalidates saved evaluator sessions. | Personas can land in onboarding or lack the expected resource assignments. | Reprovision accounts/memberships and recapture organizer, speaker, and reviewer storage states after every reset. |
| All areas | The development Worker has produced intermittent 1101/CPU-limit failures, including password authentication and authenticated routes. | Scenarios may be blocked before reaching product functionality. | Stabilize Worker CPU/runtime behavior and require repeated deployed authenticated-navigation smokes. |

## Fixed since the stopped run

- CFP session format is now organizer-configurable and defaults to the five exact
  evaluator formats: Keynote (45 min), Talk (30 min), Lightning Talk (10 min),
  Workshop (120 min), and Panel (45 min).
- The separate preferred-duration field was removed to avoid contradictory data.
- CFP display rules now group earlier proposal fields and custom questions, and
  choice-backed sources render a valid-answer select rather than a free-text
  value.
- The account menu now has an explicit application-shell layer above workflow
  heroes, with desktop/mobile sign-out hit-testing coverage.
- The speaker portal contains inline proposal entry for published open calls.
- Organizer-created general tasks support multi-speaker assignment.
- The event workspace exposes public embed URLs and copyable iframe snippets.

## Likely score losses, not whole-run blockers

| Criterion | Gap |
| --- | --- |
| SPK-03 | No bulk speaker CSV import workflow. |
| CNT-05 | No cross-role append-only comment thread on uploaded asset versions. |
| CNT-14 | No multi-select latest-version ZIP download. |
| EMB-15 | Basic snippets exist, but embeds are not persisted and lack enable state, output-format selection, filters, configurable fields, and custom CSS. |
| EMB-01 / EMB-09 | Public session projections lack some structured speaker job-title/company and format metadata. |
| ABS-10 | Aggregate results expose average ratings but lack an explicit score-sort control/table contract. |
| ABS-11 | Co-speakers are not clearly projected with role labels in evaluation results. |
| SPK-15 | No dedicated travel-preference or event-defined logistics fields. |
| EMB-02 / EMB-03 | Public filtering is mostly keyword/group based rather than full Track/Format/Location facets. |
| EMB-06 / EMB-08 | Public agenda is chronological grouping rather than a full time-grid/detail round trip. |

## Scenario prerequisites

- CFP-S1 must publish a discoverable public CFP and persist its event state.
- CFP-S2 requires that open call plus a complete speaker profile/session.
- CFP-S3 requires two submitted proposals and an active reviewer assignment.
- CFP-S4 requires completed reviews and valid decision-notification delivery.
- ABS-S2 requires three submitted proposals and two independent rounds.
- Speaker and content scenarios require accepted speakers; content additionally
  requires typed file-request tasks.
- Agenda scenarios require at least three schedulable sessions, with two sharing
  a speaker, plus rooms and tracks.
- Public-widget scenarios require at least three approved, published sessions
  across two days and populated speaker profiles.

## Clean rerun gate

- [ ] Back up and recreate the development D1 resource; do not use a reset migration.
- [ ] For a local eval, stop `worker`, `activity-worker`, and `activity-poller`,
      run `docker compose run --rm --no-deps worker npm run
      worker:reset-data:local -- --confirm sessionbuddy-local`, then restart them
      with `docker compose up --detach worker activity-worker activity-poller`.
- [ ] Confirm the reset reports exactly one canonical migration,
      `0001_baseline.sql`, and refuses any additional baseline SQL file.
- [ ] Confirm the fresh local state has one organization/admin/password and zero
      events, sessions, submissions, challenges, and activity rows.
- [ ] Reprovision Organizer, Speaker, and Reviewer and recapture `.auth`; the
      bootstrap-only reset intentionally removes the Speaker and Reviewer.
- [ ] Apply only the canonical baseline and prove the second application is a no-op.
- [ ] Deploy the verified Worker package with no fixture material.
- [ ] Bootstrap and reprovision organizer, speaker, and reviewer identities.
- [ ] Recapture all three authenticated browser states after provisioning.
- [ ] For the scored run, leave `.auth/` empty so scenarios use the configured
  persona email/password credentials and can sign in again after a persona
  switch or dropped session. Saved storage states are useful only for targeted
  session-restore diagnostics.
- [ ] Confirm repeated authenticated `/admin`, `/speaker`, and `/reviews` navigation without 1101/500 responses.
- [ ] Confirm two simultaneous evaluation rounds and typed scorecards.
- [ ] Confirm organizer-created file requests authorize the expected uploads.
- [ ] Confirm at least three sessions can be scheduled and published.
- [ ] Run evaluator list, smoke, auth checks, and six-area dry-run.
- [ ] Start a brand-new paid run without `--resume`.

The current evaluator checkout is configured for password mode. Its four saved
storage-state files were moved, recoverably, to the ignored
`.auth-session-backup-2026-08-16/` directory. The required six-area dry run
passes with no active `.auth/*.json` files.

## Run-specific evidence

Do not merge these histories:

- Current stopped run:
  `/private/tmp/sessionbuddy-evals.MDdll1/repo/runs/2026-08-16T07-26-56/`
- Older completed, heavily pre-seeded CFP run:
  `/private/tmp/sessionbuddy-evals.R3v5sG/repo/runs/2026-08-11T23-40-47/`

The older run contains the create-event validation, public CFP, multi-event
isolation, and sign-out behavior discussed in the review. The stopped run used a
different database and deployment and must be assessed independently.

# Independent eval-failure estimate vs. `eval-readiness-2026-08-16.md`

Date: 16 August 2026
Method: seven independent code-tracing passes (one per spec in `sessionbuddy-evals`),
each mapping every rubric item in `specs/*.yaml` to actual implementation in
`src/sessionbuddy/**`. The repo's own readiness report was **not** read until after
all seven passes completed.

Evaluator kit: `/private/tmp/sessionbuddy-evals.MDdll1/repo`
Product: `/Users/superman/playground/projects/sessionbuddy`

---

## 1. Independent estimate, per area

| Area | Items | Weight | Pass | Partial | Fail | Est. weight earned | Area score |
|---|---:|---:|---:|---:|---:|---:|---|
| Call for Papers | 18 | 38 | 15 | 3 | 0 | ~34.5 (91%) | ~18 / 20 |
| Abstract Management | 14 | 28 | 1 | 5 | 8 | ~6.5 (23%) | ~4.6 / 20 |
| Speaker Management | 16 | 33 | 8 | 4 | 4 | ~21 (64%) | ~9.5 / 15 |
| Content Management | 14 | 31 | 3 | 9 | 2 | ~18 (58%) | ~8.7 / 15 |
| AI Agenda | 8 | 18 | 6 | 2 | 0 | ~15 (83%) | ~8.3 / 10 |
| Public Widgets | 16 | 32 | 8 | 5 | 3 | ~22 (69%) | ~13.8 / 20 |
| **Required total** | **86** | **180** | **41** | **28** | **17** | | **≈ 63 / 100** |
| Speaker CRM (extra credit) | 12 | 19 | 1 | 4 | 7 | ~6 (32%) | ~3 / 10 |

Partials credited at 0.5 for the estimate.

**≈63/100 as-is. ≈76/100 if the reviewer-membership blocker (§3) is fixed.**

---

## 2. Comparison with the repo report

| Finding | Independent analysis | Repo report | Verdict |
|---|---|---|---|
| Only one open evaluation round (409) | Found — kills ABS-01, cascades | Hard blocker, ABS-S2 | Agree |
| Scorecard criteria numeric-only | Found; plus weights must total 100 and weighted mean is rounded to int | Hard blocker | Agree (+ rounding bug) |
| Organizer tasks stored as `custom`; uploads need `slides`/`supporting_document` | Found — CNT-02, drags CNT-07 | Hard blocker CNT-S1/S2 | Agree |
| No direct session creation; rejected decisions immutable | Found — AIA-04 setup unreachable; CFP-12 decisions one-shot | Hard blocker AIA-S1/S2 | Agree |
| No speaker CSV import | SPK-03 zero | Score loss | Agree |
| No cross-role comment thread on assets | CNT-05 fail | Listed | Agree |
| No ZIP bulk export | CNT-14 fail | Listed | Agree |
| Embed builder iframe-only, unpersisted | EMB-15 partial | Listed | Agree |
| Public cards lack job title/company/format | EMB-01/09 partial | Listed | Agree |
| No score-sort control/table | ABS-10 fail | Listed | Agree |
| Co-speaker role labels in results | Scored PASS (organizer detail shows "Co-speaker") | Listed as a gap in the *results* view | Report is stricter and likely right — round dashboard shows only `speaker_name` |
| Facets vs. grouping; no time grid; no detail view | EMB-03, 07, 08 **fail** | Soft "score losses" | Harsher here — EMB-08 (no session detail at all) and EMB-07 (no day nav) are zeros |
| **Reviewer has no event membership** | **#1 blocker** — blocks round creation *and* all of ABS-S3 | Only implied under "reprovision accounts/memberships" | **Under-weighted in the report** |
| **Speaker profile split-brain** — portal writes `users.description`/`user_headshots`, organizer reads `people.biography`/`speaker_assets`; no portal headshot upload | SPK-08 + SPK-10 fail (5 weight) | Not mentioned | **Missing** |
| **`content_status` defaults to `draft`** — published agenda still renders empty public widgets | Risk across AIA-07, CNT-12, EMB-14 | Only as a scenario prerequisite | Understated |
| **Session content edit / approval / history welded to the agenda scheduling dialog** (needs rooms + conflict-free slot) | Drags CNT-09, 11, 12 | Not mentioned | **Missing** |
| **CFP accept/reject requires the submission be in an evaluation round** (404 otherwise) | CFP-12/13 partial | Not mentioned | **Missing** |
| **Accepted → agenda handoff is title-only** (no speaker/track on the card) | CFP-15 partial | Not mentioned | **Missing** |
| Worker 1101/CPU-limit instability | Not visible to static analysis | Hard blocker | Report-only, correct |
| Saved storage states invalid after DB recreate | Not visible to static analysis | Hard blocker | Report-only, correct |

---

## 3. The reviewer-membership blocker (highest-value fix)

### Two role vocabularies, one concept

| | `user_roles` | `event_memberships` |
|---|---|---|
| Scope | global, per user | per (org, event, user) |
| Values | `CHECK(role IN ('organizer','reviewer','speaker'))` | `CHECK(role IN ('event_admin','evaluator','speaker'))` |
| Drives | `/reviews` access, persona switcher | reviewer picker, assignments, permissions |

`access.py:4571` maps between them deliberately: `"evaluator" -> "reviewer"`. This is
**not** a naming bug — nothing is misspelled; a row is simply missing.

### What is and isn't provisioned

`/.local/provision_eval.py` writes only `users`, `user_roles`, `password_credentials`.
It never writes `organization_memberships` or `event_memberships`.
`cloudflare-reset-2026-08-16-eval-rerun/sessionbuddy-development-clean.sql` contains
zero `event_memberships` rows.

| | Status |
|---|---|
| Reviewer user exists | yes |
| Reviewer password sign-in works | yes |
| Global `user_roles('reviewer')` | yes |
| `event_memberships(role='evaluator', status='active')` on DevFlow Conf 2027 | **no** |

Only two code paths ever insert `event_memberships`:
`access.py:4554` (emailed invitation acceptance) and `cfp/router.py:2374`
(auto-adds a **speaker** on CFP submission — which is why the speaker persona needs
no provisioning and the reviewer does).

### The cascade

`GET /admin/events/{id}/evaluators` (`evaluation/router.py:539`) selects
`event_memberships WHERE role='evaluator' AND status='active'` -> empty.
Then `admin_submissions.js:47` pushes *"invite at least one reviewer"* and disables
**Open round**; `EvaluationRoundCreate.evaluator_user_ids` has `min_length=1`, so the
API refuses too. **No evaluation round can be created at all.**

Everything below hangs off a round existing — 17 weight of code that is implemented
and correct but can never be photographed by the eval agent:

| Item | W | Lives on |
|---|---:|---|
| ABS-02 | 2 | `POST /evaluation-rounds/{id}/evaluators` |
| ABS-03 | 3 | reviewer queue = round assignments |
| ABS-05 | 3 | `evaluation_assignments` |
| ABS-06 | 2 | round creation form |
| ABS-07 | 2 | round's `blind_review` flag |
| ABS-08 | 2 | `/admin/evaluation-rounds/{id}` |
| ABS-09 | 1 | per-evaluator row on that page |
| ABS-12 | 1 | reviewer portal |
| ABS-13 | 2 | round card |

ABS-S3 step 1 instructs the agent to **stop the scenario** if reviewer access requires
an unverifiable emailed link — so this weight is forfeited by fixture setup, not by
product quality.

### Fix

Add `organization_memberships` + `event_memberships(role='evaluator', status='active')`
inserts to `provision_eval.py`, run after the organizer's event exists.

This is fixture setup, not cheating: ABS-02's pass criterion is that pools are
*per-round*, and the agent still performs the attach in-app via
`POST /evaluation-rounds/{id}/evaluators`. Seeding only makes Sam eligible to appear in
the dropdown — exactly what clicking the emailed link would do, and the same category
of setup as seeding the organizer's password row.

Seeding does **not** fix ABS-01 (one-open-round 409), ABS-04 (weights rounded to int),
ABS-10 (no sortable table), or ABS-14 (no AI override) — those stay partial. Hence 86%,
not 100%.

### Loose end

`evalconfig.json` uses `namohh.namaha+reviewer1@gmail.com`; the spec's fixture identity
is `sam.reviewer@sbek-test.example.com`. The `personaEmails` override should win, but
the agent is told to sign in as Sam — confirm the harness substitutes rather than
passing the spec string through.

---

## 4. Per-area failure causes

**Call for Papers (~91%)** — decisions coupled to evaluation rounds
(`record_submission_decision` 404s without an assignment); accepted -> agenda handoff is
title-only; public form gated behind sign-in; email-only reviewer/speaker provisioning.

**Abstract Management (~23%, ~86% unblocked)** — reviewer membership (§3); one open round
per event; round list omits dates/scorecards; results are cards with no sort; weights must
total 100 and the mean is rounded to int; AI triage has no override.

**Speaker Management (~64%)** — profile split-brain (portal writes `users.description` /
`user_headshots`, organizer reads `people.biography` / `speaker_assets`); no portal headshot
upload at all; no CSV import; everything gated on `selection_status='accepted'`, which only a
review decision can set; no status control on the speaker record; no travel/logistics fields;
no automated due-date reminders.

**Content Management (~58%)** — no file comments (only a per-version "what changed?" string,
no author, no thread, invisible to the speaker); no ZIP export; content editing/approval/history
welded to the agenda scheduling dialog; organizer tasks are `custom` question tasks, not file
requests; speaker portal hides version metadata it already receives; no organizer-side headshot
upload; files library has no session association.

**AI Agenda (~83%)** — board is a grouped card list, not a time grid (no time axis, no day
switcher, empty agenda renders nothing); conflict messages never name the speaker or clashing
session; no organizer-editable speaker assignment, so the shared-speaker overlap may be
unreachable; conflicts are prevented rather than displayed; `content_status` defaults to draft.

**Public Widgets (~69%)** — no session detail view anywhere; no day navigation; view-grouping
buttons are not facets; speaker job title/company missing from schedule cards; no "Show more"
truncation; embed builder is iframe-only with no formats/branding/filters/saved list; ordering
is by first name, not surname.

**Speaker CRM (~32%)** — no CRM module exists; the area is served incidentally by an
event-speaker aggregation page. No notes, tags, custom fields, segments, merge, or pipeline
tables. No ingestion path (no CSV import, no manual org-level contact creation). Bulk email and
dashboard exist but at event scope, not org scope.

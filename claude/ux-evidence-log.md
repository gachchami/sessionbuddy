# UX Evidence Log

> **Reconstructed 2026-08-17.** The original `claude/ux-evidence-log.md` was not present in the
> working tree, in `git log --all`, or anywhere under the granted folders. This file is rebuilt
> from primary evidence that *does* exist: the harness run directories under
> `/private/tmp/sessionbuddy-evals.MDdll1/repo/runs/`, `git log`, and the source itself.
> Entries marked **[from briefing]** are carried over from the session handoff and have **not**
> been re-verified against an artifact — treat them as claims, not evidence, until re-run.

---

## 2026-08-17 — CFP-S2 speaker journey: clean pass, no product defects in scope

**Artifact:** `runs/2026-08-17T03-05-21/CFP-S2/` — outcome `completed`, 79 turns, 27 screenshots.
Target: `https://sessionbuddy-development.shiny-cloud-dd47.workers.dev`.
Agent `claude-sonnet-5`, judge `claude-opus-5`.

This is the only recent run that produced usable product signal. The judge's own summary:

> "Within that scope the app behaved correctly with no errors, broken flows, or data loss
> observed; the two defects listed are usability/coverage gaps rather than failures."

### Verified working (screenshot-backed)

| Behaviour | Evidence |
|---|---|
| Draft save with title only, then resume on return | `004-draft-saved-banner`, `008-draft-restored-title-prefilled` — "Your saved draft has been restored." |
| Required-field validation blocks review | `009` — "Complete the highlighted required fields before reviewing your proposal." |
| Conditional fields show **and** hide | `011` Workshop → "Workshop prerequisites" appears; `012` Talk → it disappears |
| Track / format / audience dropdowns populated | `010` — `Platform & Infra`, `AI Engineering`, `Talk (30 min)`, `Workshop (120 min)` |
| Submit → confirmation + durable receipt | `015` — "Submission confirmed", receipt `7d15ffa5-…` |
| Dashboard status labels | `017` one proposal "Submitted"; `026` both proposals listed |
| Edit persists across a hard reload | `019` → `020` "Proposal updated" → `021` appended sentence still present |

### Rubric outcome for the Call for Papers area

`earned 7.5 / judgeable 13 / total weight 38` — coverage **34.2%**.
Verdicts: **2 pass** (CFP-02, CFP-07), **4 partial** (CFP-01, CFP-03, CFP-05, CFP-09),
**11 cannot_judge**.

Every `cannot_judge` and three of the four `partial`s trace to one cause: CFP-S1, CFP-S3 and
CFP-S4 were excluded by the `--scenarios` filter and ran 0 turns. The judge states this
explicitly — *"a coverage limitation of this run, not an app defect."*

### Defects actually found (both MINOR)

1. **Drafts are invisible on the speaker dashboard.** With a saved draft present, `/speaker`
   showed "No proposals yet." and "Your speaker workspace is ready". The draft is recoverable
   only by returning to the CFP form URL. *(`006`, turns 18–21.)*
   → This is the item the `GET /api/v1/speaker/proposal-drafts` +
   `loadProposalDrafts()` work addresses. Note it is scored as a **usability wrinkle inside a
   passing item (CFP-07)**, not a failing criterion.

2. **No bio / headshot / social fields anywhere in the submission path.** The CFP form collects
   name, email (readonly), title, abstract, format, full description, one dynamic question,
   audience level, track, co-speakers. No bio, Twitter, LinkedIn or headshot. Sample-data
   fields for Priya Raman (bio, twitter, linkedin, tshirt, dietary) have nowhere to go.
   → **These fields already exist on the account profile model** (`platform/auth/access.py`
   L829–837: `biography`, `website_url`, `linkedin_url`, `x_url`, `headshot_url`). The gap is
   surfacing, not storage. This is the same surface the `users`/`people` consolidation touches.

### Scoring artefact worth knowing about

CFP-03 lost points on the *deadline* requirement for a harness reason, not a product reason —
the judge wrote that it was "scored down not because a deadline was shown to be missing, but
because the captured page outlines are truncated before any deadline text." That is the
500-character tool-result clip (`agent.ts:643`, `judge.ts:65`) costing a real point.

---

## 2026-08-17 — CFP-builder occlusion thread **[from briefing, unverified]**

Reported closed: 19 blocked eval clicks → 0, and CFP-S1 published for the first time.
No run directory in `runs/` corroborates a successful CFP-S1: the most recent CFP-S1 record is
`runs/2026-08-17T03-04-57/CFP-S1/evidence.json`, outcome `agent_error` on turn 1.
**Needs a clean CFP-S1 run to become evidence.**

---

## Open items

1. **Full-suite runs produce no signal at all.** Every 18-scenario run since 2026-08-16T06:51
   died on turn 1. See `identity-model-and-eval-2026-08-17.md` for the root cause. This is the
   single highest-leverage item — nothing else in this log can be re-verified until it is fixed.
2. **Drafts not listed on the speaker dashboard** (defect 1 above). Endpoint and loader are
   committed in `a3e86b1`; deployment status to the dev worker is **unconfirmed** — neither the
   cloud sandbox (workers.dev is not on its egress allowlist) nor `device_bash` (no network) can
   reach the worker to check.
3. **Speaker profile fields have no surface in the submission path** (defect 2 above).
4. **Track field in the CFP builder when the event has no tracks** — `admin_programs.js:123`
   splices it out silently; tracks are only creatable on the Agenda page, which the builder never
   mentions. *Not reproduced in the 03-05-21 run* — DevFlow Conf 2027 has tracks, so Track
   rendered correctly there. This is an organizer-side (CFP-S1) issue and remains unverified.
5. **500-char judge clip** (`agent.ts:643`, `judge.ts:65`) marks on-screen things absent.
   Harness-side; costs points on every scenario. Cost CFP-03 in this run specifically.

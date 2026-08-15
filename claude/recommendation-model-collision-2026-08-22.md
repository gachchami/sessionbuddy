# Recommendation model collision — analysis and remediation sequence

**Date:** 2026-08-22 · **Reconciled into the repository record:** 2026-08-23
**Source:** SBek run `2026-08-22T04-14-23`, scenario **ABS-S3**, step 5 (observation 4), plus a
read-only trace of the working tree.
**Status:** analysed and sequenced. **Not scheduled. No code changed by this analysis.**

> **Line anchors are as of commit `5ab5636` (2026-08-23 05:25 IST).** They were first taken against
> the pre-commit tree and are rebased here — `main.tsx` shifted by roughly +38 lines. Server-side
> anchors are unchanged. Re-verify before acting; this tree moves.

---

## 1. What was observed

Screenshot `009-reviewer-scorecard-filled.jpg` shows the reviewer form for *Docs That Answer Back*
carrying **two duplicated pairs**, not one duplicated control:

| | Inside the Scorecard fieldset | Below it, in `.review-fields` |
|---|---|---|
| Recommendation | `Recommendation*` → `Accept` (title case) | `Recommendation*` → `accept` (snake case) |
| Comment | `Comments*` → the 139-char review text | `Internal comment` → empty |

The run flagged the recommendation pair. The comment pair is the more damaging of the two.

## 2. Mechanism — a product-model collision, not a renderer bug

A round's `rubric_json` carries two independent field systems, and the reviewer form renders both
unconditionally.

**Built-ins**, constructed at `router.py:1171-1178`: `rating`, `recommendation.choices`,
`internal_comment`. Rendered around `main.tsx:625-650`, stored as columns on `evaluations`.

**Organizer-authored `criteria[]`** of type `score` / `select` / `text`. Rendered as the Scorecard
around `main.tsx:592-605`, stored in `criterion_responses_json`.

Nothing mediates between them:

- **The built-in cannot be switched off.** `recommendations: list[str] = Field(min_length=2, …)` is
  mandatory on `EvaluationRoundCreate` (`models.py:60`). An organizer wanting their own
  recommendation scale can only *add* a second control, never replace the first.
- **Nothing rejects the shadowing.** Label "Recommendation" → key `recommendation`
  (`admin_submissions.js:1468`). The `usedKeys` set dedupes criteria against each other only; there
  is no reserved-name check against `rating` / `recommendation` / `internal_comment` on either
  side. `valid_rubric` (`models.py:95-99`) checks intra-criteria uniqueness and stops.
- **The server independently requires both at finalize** and permits contradictory answers —
  scorecard `Accept` alongside overall `reject` is a valid save.

No data collision: `rubric["recommendation"]` and `rubric["criteria"][*].key` are separate
namespaces, and the form field names are `recommendation` vs `criterion_recommendation`. This is
semantic duplication, not corruption.

## 3. The severe defect: required reviewer work is write-only

`get_round_results` selects `criterion_responses_json` (`router.py:3261`) and uses it for **exactly
one thing** — computing `weighted_score` (`router.py:3279`). `EvaluationDetail` (`models.py:442-450`)
has no field for non-scored responses. The summary CSV export has seven columns
(`router.py:3500-3528`) and includes neither recommendation nor criterion responses.

Consequences on the ABS-S3 review:

- Scorecard `Recommendation = Accept` — persisted, never rendered anywhere.
- Scorecard `Comments` = the reviewer's actual 139-character assessment — persisted, never rendered
  anywhere.
- Built-in `Internal comment`, the field organizers *do* see (`main.tsx:1410`), was left blank
  because it was optional.

The requiredness is inverted against visibility: the criteria carried `required: true`, while the
built-in comment is gated by `comment_required`, which was off. The form steered the reviewer's
effort into the channel nobody can read. From the organizer's chair the review is `3.34 · accept`
and no words at all.

This generalises: **every** `select` and `text` criterion is a collection sink, not only these two.
That is why organizer visibility is the urgent fix, ahead of removing the duplicate controls.

The data is recoverable — this is a read-model and presentation gap, not missing persistence.

## 4. The formatting inconsistency has a separate cause

- Criterion options render exactly as configured: `Accept`, `Maybe`, `Reject`.
- The built-in default is `strong_accept, accept, reject, strong_reject` — the **shipped default**
  of the round form's Recommendations input (`admin_submissions.html:20`), also seeded by
  `fixtures/day_n/ui/05-reviews-and-decisions.json`.
- `main.tsx:634` renders `<option key={choice}>{choice}</option>` verbatim, with no presentation
  layer. (The criterion select at `main.tsx:601` uses the same no-`value` pattern, but its options
  are already human labels.)

So **every reviewer on a default round sees raw snake_case tokens**, duplicate control or not.
ABS-S3 only made it visible by placing a nicely-labelled control beside it.

## 5. Provenance

The product's own reference fixtures create score-only criteria. The `Recommendation` / `Comments`
select+text criteria came from the ABS eval seed — ABS-S2 explicitly expects typed criteria of that
shape. That is a priority argument, not an exoneration: the product permits the configuration
silently, and the write-only criterion responses plus the raw-token default are live in shipped
code regardless.

---

## 6. Decisions

**Criteria become canonical, not the built-in.** The criteria system already supports typed
responses, custom choices, requiredness, rubric ordering and per-round configuration. The built-in
is structurally older and less expressive. A reserved-key guard is cheaper but would make the valid
ABS-S2 rubric impossible, and adding the validator directly to `EvaluationRoundCreate` could block
existing shadowed drafts from saving. **The reserved-key guard is rejected as a final design.**

**Organizer visibility is the urgent fix**, independent of which recommendation wins. It prevents
silent loss of review evidence for every non-scored criterion.

**Humanize at render, preserve stored values.** Changing defaults helps only future rounds and
splits historical values into two conventions. Display-only humanisation fixes every existing round
without touching validation, persistence or API values. Longer term, choices become
`{value, label}` rather than one string doing both jobs.

**Export shape:** keep the existing submission-summary CSV **byte-stable** and **add a second
detailed review export** — one row per evaluation, deterministic rubric-ordered criterion columns.
Converting the current export to per-review rows would silently break its aggregate meaning; a JSON
column preserves the data but is unpleasant to consume.

---

## 7. Constraints found in the code

1. **Legacy writes are validated against the round's own choices.** `router.py:2816-2820` rejects
   any `recommendation` not in `rubric["recommendation"]["choices"]`. Mirroring `Accept` from a
   designated criterion 422s whenever the criterion options differ from the Recommendations input —
   the ABS case exactly (`Accept/Maybe/Reject` vs `strong_accept/accept/reject/strong_reject`,
   disjoint). Resolved by deriving the legacy choices from the designated criterion **before**
   mirroring, not by bypassing the validator.
2. **The legacy envelope is smaller than the criterion envelope.** `recommendation` is
   `max_length=80` (`models.py:325, 372`); criterion `options` allow 120 characters
   (`models.py:28`). `recommendations` caps at 8 choices; `select` options allow 20. A legal
   designated criterion can therefore be unmirrorable, and a lossy mirror corrupts the column
   organizers read. Designated recommendation criteria must be held to the legacy envelope during
   compatibility; the constraint retires at step 7.
3. **Comment mirroring needs no envelope work.** `internal_comment` is `max_length=5000` on both
   sides and free-text criteria are capped at 5000 (`router.py:2785`).
4. **`_round_criteria` filters to scored criteria only** (`router.py:325-330`,
   `criterion.get("weight")`). Reusing it as the label/type map yields an empty map for exactly the
   criteria being surfaced, and fails silently rather than erroring. A separate full-criteria
   accessor is required.
5. **Response order is alphabetical, not rubric order.** `criterion_responses_json` is stored with
   `sort_keys=True` (`router.py:2887`). Deterministic rendering and export must iterate the
   rubric's `criteria` list.
6. **The rubric dict is constructed twice** — `router.py:1172` (create) and `router.py:1566` (draft
   edit) — with identical literal shapes. Derivation logic landing in one and not the other means a
   draft-saved round and a directly-opened round get different `recommendation.choices` from the
   same rubric. Extract one builder.
7. **`EvaluationRoundResults` has no home for rubric metadata** (`models.py:473-489`). Criteria
   labels and types belong there once, not repeated on every `EvaluationDetail`.
8. **Schema surface:** `EvaluationDetail` is `extra="forbid"` and appears in
   `openapi/openapi.json`, `src/sessionbuddy/api/openapi_contract.py` and
   `tests/test_openapi_contract.py` — a field addition is a four-file change. Commit `5ab5636` has
   just done exactly this for `weighted_score`; follow its shape.
9. **`humanize()` needs no cross-bundle global.** Both affected surfaces — the reviewer control
   (`main.tsx:634`) and the organizer results line (`main.tsx:1410`) — live in
   `frontend/src/main.tsx`. The round builder's only contact with stored tokens is
   `admin_submissions.js:649`, `recommendations.join(", ")` into an authoring input that round-trips
   to the API, where raw values are correct. A shared presentation module under `frontend/src/` is
   sufficient.

---

## 8. Sequence

### 1. Read-only reconciliation audit — **first**

Find finalized reviews whose `recommendation` differs from a recommendation-like criterion
response. Compare normalised meanings, so `Accept` and `accept` agree while `Reject` and `accept`
conflict. **Auto-correct nothing.**

Report three buckets:

- **agree** — normalised values match. Eligible for automatic migration.
- **conflict** — the two channels state opposing judgments. Preserve both values, flag for human
  review, **never choose one automatically**.
- **unmappable** — the scorecard value has no counterpart in the legacy choice set at all. `Maybe`
  against `strong_accept/accept/reject/strong_reject` is the canonical case: the reviewer was
  forced to record something more definite than they meant, in the only channel organizers read.
  Preserve both values, flag separately, **do not coerce** `Maybe` into accept or reject.

The audit's detection heuristic does not need to be sound — it only reports, so false positives
cost a human glance.

**Policy, decided in advance so the result does not sit waiting on a judgment call:**

- Finalized evaluations are **never** silently reopened or rewritten. Any correction goes through
  an explicit, audited correction workflow.
- **Step 7 cannot remove legacy readability while unresolved conflict or unmappable records
  remain.** Either resolve them explicitly, or retain an archival compatibility view.

This must run before any UI consolidation: once one channel is hidden, contradictory finalized data
becomes far harder to notice or reason about.

### 2. Expose all criterion responses

- Add a full-criteria accessor; do **not** reuse `_round_criteria` (constraint 4).
- Iterate rubric order, never JSON key order (constraint 5).
- Add typed criterion responses to `EvaluationDetail`; put criteria metadata on
  `EvaluationRoundResults` (constraint 7).
- Render them in organizer results.
- Add the separate detailed-review CSV; keep the summary export byte-stable.
- Label both channels honestly during the transition — e.g. "Scorecard response" and "Legacy
  overall recommendation".

**Accepted interim ugliness:** until step 6, existing rounds show both recommendations (`Accept` and
`accept`) on the organizer results page. This is chosen, not accidental — it surfaces the
contradiction as evidence.

### 3. Humanize legacy recommendation tokens

- One helper shared by the reviewer control and the organizer results line, in a `frontend/src/`
  presentation module (constraint 9).
- `<option value={choice}>{humanize(choice)}</option>` — the explicit `value` is required:
  `main.tsx:634` currently omits it, so value === text, and changing the text alone would send
  `"Strong accept"` to the server and 422 against the rubric choices.
- Both surfaces in the same change. Humanising the reviewer control alone recreates a cross-surface
  disagreement one entry after the `3` vs `3.34` one was closed.
- API and stored values stay raw. Historical data is not rewritten for presentation. The summary
  CSV will carry `accept` while the UI shows `Accept` — expected, and the argument for
  `{value, label}` landing sooner.

### 4. Introduce explicit criterion purposes

- `purpose: "recommendation" | "comment" | null`.
- At most one criterion of each purpose per rubric.
- Recommendation purpose requires `response_type == "select"`; comment purpose requires `"text"`.
- During compatibility, recommendation criteria must fit the legacy envelope: 2–8 choices, each ≤80
  characters (constraint 2).

### 5. Make the round builder honest

- When a designated criterion exists, remove or disable the legacy Recommendations control with an
  explanation.
- `recommendations` becomes conditionally derivable rather than unconditionally required by
  Pydantic.
- The server derives the legacy rubric choices from the designated criterion — in **one** extracted
  rubric builder, not both call sites (constraint 6).
- Without a designated criterion, legacy recommendations remain required.

Without this step, the organizer keeps filling a field nothing uses — the same defect relocated
from reviewer to organizer.

### 6. Render one reviewer control

- Designated recommendation/comment criteria replace the built-in controls visually.
- Save writes the canonical criterion response and mirrors it into the legacy column.
- Because legacy choices were derived at step 5, recommendation validation stays coherent
  (constraint 1).
- Comment mirroring needs no envelope change (constraint 3).

### 7. Migrate and retire

- Classify existing exact recommendation/comment criteria **only after** the audit.
- Preserve ambiguous custom criteria without guessing.
- Subject to the audit policy in step 1: legacy readability stays until conflicts and unmappables
  are resolved or archived.
- Once consumers read designated criteria, retire the duplicate legacy authoring path.

---

## 9. Browser coverage — `harness/e2e/reviews.spec.ts`

Steps 3 and 6 are pure frontend behaviour that server tests cannot reach. The spec must prove:

1. A purpose-designated rubric renders **exactly one** Recommendation control.
2. A purpose-designated comment renders **exactly one** comment control.
3. Humanized labels retain raw submitted values — display "Strong accept", submit `strong_accept`.
4. Submission writes **both** the canonical criterion response and the compatibility mirror.
5. A legacy rubric without purposes still renders its built-in controls correctly.
6. An ordinary `select` criterion remains visible alongside the built-in recommendation when it is
   *not* designated as the recommendation purpose.

**Counting controls is insufficient — intercept and assert the submitted payload.** That is what
catches a hidden-but-still-submitted duplicate, or a broken compatibility mirror.

The idiom already exists in this file. The weighted-score test added by `5ab5636` captures
`savedPayload = route.request().postDataJSON()` and asserts with `toMatchObject`. These six extend
an established pattern rather than introducing one.

## 10. Rejected alternatives

- **Rename the second field to "Overall recommendation."** Reduces confusion, preserves
  contradictory data.
- **Hide it in the frontend.** Fails server validation at finalize (`router.py:2821`).
- **Reserved-key rejection as the final design.** Cheaper, but forbids the more capable half of the
  model and invalidates the ABS-S2 rubric; would also need migration or grandfathering for existing
  shadowed drafts.
- **Change the shipped `recommendations` default to human labels.** Helps only future rounds and
  splits historical values into two conventions.
- **Convert the summary CSV to per-review rows.** Silently breaks its existing aggregate meaning.

## 11. Rebase against the current tree

Commit `5ab5636` (`fix(evaluation): preserve weighted score precision`, 2026-08-23 05:25 IST,
`gachchami <me@dbhanushali.com>`, 9 files, +225/−37) landed while this analysis was being written.
It closes three of the four residuals from the weighted-mean entry and changes what remains here:

| Planned concern | Status after `5ab5636` |
|---|---|
| Shared score formatting on organizer aggregates | **done** — `formatScore` at `main.tsx:1097` and `main.tsx:1397` |
| Partial-score wording | **done** — "Partial weighted score preview" at `main.tsx:661` |
| Browser submission coverage / payload interception idiom | **done** — 84-line Playwright test |
| `EvaluationDetail` field addition across 4 files | **precedent set** — `weighted_score` did exactly this |
| Server-side half-point rounding | **still open** — `router.py` retains `round(…)` |
| Everything in §8 (steps 1–7) | **untouched** — the collision work is entirely ahead |

Nothing in the sequence is invalidated. Steps 2 and 3 gain a working precedent to copy, and step 9
gains an established test idiom. **Do not implement from any line reference in this document
without re-verifying it first** — every anchor here was taken against a tree that moved twice
during the analysis.

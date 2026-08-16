# Recommendation model collision — analysis and remediation sequence

**Date:** 2026-08-22 · **Reconciled into the repository record:** 2026-08-23
**Source:** SBek run `2026-08-22T04-14-23`, scenario **ABS-S3**, step 5 (observation 4), plus a
read-only trace of the working tree.
**Status:** analysed, sequenced, and **largely implemented** — see §13 for what shipped and what
remains. Steps 1 and 7 are still open.

> **Two pin points, deliberately.** §§1–5 and §12 describe the code **as the defect existed** and
> stay pinned to `5ab5636` — re-anchoring them to the fix would point at lines that no longer
> contain the defect. §§7, 8 and 13 describe the **shipped** code and are pinned to
> **`80cbea8` — `fix(evaluation): unify recommendation and comment criteria`, 2026-08-23 06:50 IST,
> 19 files, +1010/−97**. Both commits are immutable, so these anchors no longer drift with the
> working tree.

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

## 4. The shipped default contributed directly to the collision

- Criterion options render exactly as configured: `Accept`, `Maybe`, `Reject`.
- The built-in default is `strong_accept, accept, reject, strong_reject` — the **shipped default**
  of the round form's Recommendations input (`admin_submissions.html:20`), also seeded by
  `fixtures/day_n/ui/05-reviews-and-decisions.json`.
- `main.tsx:634` renders `<option key={choice}>{choice}</option>` verbatim, with no presentation
  layer. (The criterion select at `main.tsx:601` uses the same no-`value` pattern, but its options
  are already human labels.)

So **every reviewer on a default round sees raw snake_case tokens**, duplicate control or not.
Remote inspection showed that the ABS-S3 author added the human-labelled custom scale because the
mandatory built-in still carried that shipped default. Formatting was therefore a contributing
cause of the duplicate control, not merely a cosmetic defect beside it.

## 5. Provenance

The product's own reference fixtures create score-only criteria. The `Recommendation` / `Comments`
select+text criteria came from the ABS eval seed — ABS-S2 explicitly expects typed criteria of that
shape. That is a priority argument, not an exoneration: the product permits the configuration
silently, and the write-only criterion responses plus the raw-token default are live in shipped
code regardless.

---

## 5a. Classification and priority — what actually closes ABS-S3

Remote dev inspection resolved the ambiguity: the built-in carried
`strong_accept / accept / reject / strong_reject`, while the custom criterion carried
`Accept / Maybe / Reject`. The author was expressing a scale the shipped default did not offer;
the product then rendered both independent systems.

That reclassifies the work without reducing any of it:

| | Closes what | Depends on the deployed rubric? |
|---|---|---|
| **Step 0** — seed correction + non-blocking builder warning | Prevent accidental semantic duplicates | No |
| **Steps 2–3** — expose non-score responses; replace and humanize raw defaults | Preserve review content and remove a contributing cause | No |
| **Steps 4–6** — purposes, one control, mirroring | **The observed ABS-S3 scenario, directly** | No |
| **Step 7** — retire legacy fields | — | Gated on remote audits |

The deployed rubric has now been inspected; steps 4–6 are remediation, not optional enablement.

### Remote evidence retrieved

1. The affected round's complete `rubric_json`.
2. Its criterion responses, and the legacy `recommendation` / `internal_comment` values.
3. The eval seed definition that created the round.

The different scales prove the need for purpose-designated replacement. The finalized review's
139-character prose existed only in the custom criterion while legacy `internal_comment` was
empty, independently confirming the write-only response defect. `Maybe` was available but not
chosen, proving the audit's unmappable category was reachable even though this review did not
exercise it.

**Step 0 is not held for this query.** Correct the seed, add the warning, and add a regression test
proving the corrected seed produces exactly one Recommendation control.

---

## 6. Decisions

**Criteria become canonical, not the built-in.** The criteria system already supports typed
responses, custom choices, requiredness, rubric ordering and per-round configuration. The built-in
is structurally older and less expressive. The built-in nevertheless stays canonical **during
compatibility**, because finalization and organizer results already depend on it.

**Never reject a criterion because of its label or key.** A reserved-name guard is too heuristic:
"Recommendation" may be an intentional custom scale, and ABS-S2 legitimately needs expressive
`select` / `text` criteria. **Names must not determine data semantics.** The rule is:

- **Now, until purposes ship:** the round builder shows a non-blocking warning. It must not prevent
  saving.

  > "This criterion may duplicate the built-in Recommendation field. Reviewers will see both
  > controls. Use the built-in field or designate this criterion after purpose-based fields are
  > available."

- **Later, as the final rule:** validation is purpose-based, never name-based.
  - `purpose=None` — ordinary criterion; **any label is allowed**, including "Recommendation".
  - `purpose="recommendation"` — replaces the visible built-in Recommendation control.
  - `purpose="comment"` — replaces the visible built-in Internal comment control.
  - Reject **multiple criteria sharing the same non-null purpose**. That is the only rejection.
  - During compatibility, mirror designated criterion values into the legacy fields.

**Correct the eval seed** to use the built-ins — or, once purposes ship, to designate its custom
criteria — so ABS-S2 keeps exercising `select` and `text` criteria without creating accidental
duplication.

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

1. **Legacy writes are validated against the round's own choices.** `router.py:2906` (`80cbea8`)
   rejects any `recommendation` not in `rubric["recommendation"]["choices"]`, and `router.py:2909`
   refuses a final save with none. Mirroring `Accept` from a
   designated criterion 422s whenever the criterion options differ from the Recommendations input —
   the ABS case exactly (`Accept/Maybe/Reject` vs `strong_accept/accept/reject/strong_reject`,
   disjoint). Resolved by deriving the legacy choices from the designated criterion **before**
   mirroring, not by bypassing the validator.
2. **The legacy envelope is smaller than the criterion envelope.** `recommendation` is
   `max_length=80` (`models.py:341, 388`); criterion `options` allow 120 characters
   (`models.py:30`). `recommendations` caps at 8 choices; `select` options allow 20. A legal
   designated criterion can therefore be unmirrorable, and a lossy mirror corrupts the column
   organizers read. Designated recommendation criteria must be held to the legacy envelope during
   compatibility; the constraint retires at step 7.
   **Enforced in `80cbea8`** at `models.py:36-45` — recommendation purpose requires `select`,
   requires `required=True`, and caps at 8 choices of ≤80 characters.
3. **Comment mirroring needs no envelope work.** `internal_comment` is `max_length=5000` on both
   sides (`models.py:389`) and free-text criteria are capped at 5000 (`router.py:2873`).
4. **The scoring accessor filters to scored criteria only** (`criterion.get("weight")`). Reusing
   it as the label/type map yields an empty map for exactly the criteria being surfaced, and fails
   silently rather than erroring. A separate full-criteria accessor is required.
   **Resolved in `80cbea8`:** `_round_criteria` was renamed `_round_criteria_for_historical_scores`
   (`router.py:318`, sole input to `_weighted_review_score` at `router.py:3369`) and remains
   the sole input to `_weighted_review_score`, deliberately *unvalidated* so that tightening
   `EvaluationCriterion` later cannot retroactively drop a weighted criterion and move historical
   averages. `_full_round_criteria` (`router.py:339`) is the separate, model-valid metadata
   accessor. Two accessors, named for their jobs, neither substitutable for the other.
5. **Response order is alphabetical, not rubric order.** `criterion_responses_json` is stored with
   `sort_keys=True` (`router.py:2981`). Deterministic rendering and export must iterate the
   rubric's `criteria` list.
6. **The rubric dict was constructed twice** — create and draft-edit, with identical literal
   shapes. Derivation logic landing in one and not the other means a draft-saved round and a
   directly-opened round get different `recommendation.choices` from the same rubric.
   **Resolved in `80cbea8`:** one `_build_round_rubric` (`router.py:376`) called from both
   `router.py:1270` (create) and `router.py:1656` (draft update).
7. **`EvaluationRoundResults` had no home for rubric metadata.** Criteria labels and types belong
   there once, not repeated on every `EvaluationDetail`. **Resolved in `80cbea8`:** a `criteria`
   field on `EvaluationRoundResults` (`models.py:491`), populated at `router.py:3571`;
   `EvaluationDetail` (`models.py:459`) carries only keyed responses.
8. **Schema surface:** `EvaluationDetail` is `extra="forbid"` and appears in
   `openapi/openapi.json`, `src/sessionbuddy/api/openapi_contract.py` and
   `tests/test_openapi_contract.py` — a field addition is a four-file change. Commit `5ab5636` has
   just done exactly this for `weighted_score`; follow its shape.
9. **`humanize()` needs no cross-bundle global.** Both affected surfaces — the reviewer control
   (`main.tsx:698` at `80cbea8`) and the organizer results line (`main.tsx:1478`) — live in
   `frontend/src/main.tsx`. The round builder's only contact with stored tokens is
   `admin_submissions.js:715`, `recommendations.join(", ")` into an authoring input that round-trips
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

**Scope of the blocker (see §12).** The duplication does not exist in any local dataset, so the
audit currently has no reachable data to run against. **That blocks legacy-field retirement (step
7), not implementation.** Steps 2–6 and §9 proceed without it. Before removing legacy fields, run
the audit against remote dev D1 and any production dataset; **any dataset that cannot be reached
remains a migration blocker.**

### 2. Expose all criterion responses

- Add a full-criteria accessor; do **not** reuse the scoring accessor (constraint 4).
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
  `main.tsx` then omitted it, so value === text, and changing the text alone would send
  `"Strong accept"` to the server and 422 against the rubric choices.
- Both surfaces in the same change. Humanising the reviewer control alone recreates a cross-surface
  disagreement one entry after the `3` vs `3.34` one was closed.
- API and stored values stay raw. Historical data is not rewritten for presentation. The summary
  CSV will carry `accept` while the UI shows `Accept` — expected, and the argument for
  `{value, label}` landing sooner.

### 4. Introduce explicit criterion purposes

- `purpose: "recommendation" | "comment" | null`.
- At most one criterion of each non-null purpose per rubric — **this is the only rejection**.
  Labels and keys are never grounds for refusal.
- Recommendation purpose requires `response_type == "select"`; comment purpose requires `"text"`.
- During compatibility, recommendation criteria must fit the legacy envelope: 2–8 choices, each ≤80
  characters (constraint 2).
- Old rubrics default to `purpose=None` for free — stored criteria dicts already omit optional
  keys, and pydantic fills defaults on read (verified, §12).

**Step 0, shippable immediately and not held for the remote query (§5a):** the non-blocking builder
warning from §6, the eval-seed correction, and a regression test proving the corrected seed renders
**exactly one** Recommendation control. Neither depends on anything else in this sequence.

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

0. **(Step 0, ships first)** The corrected eval seed renders **exactly one** Recommendation control.
   This is the regression guard for the observed ABS-S3 scenario and does not wait on purposes.
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
- **Reserved-name rejection, at any stage.** Rejected outright, not merely as a final design.
  "Recommendation" may be an intentional custom scale; ABS-S2 legitimately needs expressive
  `select`/`text` criteria; and it would need grandfathering for existing shadowed drafts. Names
  must not determine data semantics. A non-blocking warning covers the compatibility window; a
  `purpose` collision is the only thing ever refused.
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

## 12. What the audit found in local data (2026-08-23, read-only)

Every local D1 database was copied to scratch and queried read-only. **Fourteen** contain an
`evaluation_rounds` table. Across all of them:

- **Zero** rounds define a `select` or `text` criterion.
- **Zero** criteria are keyed `recommendation`, `comments`, `comment` or `internal_comment`.
- Every criterion in every local rubric is a scored one.

**The duplication does not exist in any local dataset.** A later read-only inspection of deployed
dev D1 found the affected round: the built-in retained the shipped snake-case choices, while the
custom Recommendation criterion supplied `Accept / Maybe / Reject`. The custom required Comments
response held the reviewer's 139-character assessment and legacy `internal_comment` was empty.

Consequences:

1. **Deployed dev is now auditable.** Production datasets must still be audited before step 7.
2. **The write-only channel has zero local instances** — not because it is safe, but because no
   local rubric authors a `select` or `text` criterion. Severity rests on the deployed dataset and
   on the code path, not on accumulated damage.
3. **The raw-token installed base is one closed round.** Only the closed `Initial review` carries
   `strong_accept/accept/reject/strong_reject`, with a stored value of `accept` that would render
   raw. The shipped default is still wrong; its footprint is small.

**Verified, not assumed:** stored criteria dicts carry only `key`, `label` and `weight` — no
`response_type`, `required` or `options`. `router.py:2666` passes those raw dicts into
`EvaluationAssignmentView.criteria: list[EvaluationCriterion]`, so pydantic supplies the defaults on
read, and the server's own `criterion.get("response_type", "score")` agrees. There is no rendering
bug in the legacy shape, and `purpose` will default to `None` on old rubrics by the same mechanism.

## 13. Implementation status (2026-08-23, reviewed over three passes)

Shipped as **`80cbea8` — `fix(evaluation): unify recommendation and comment criteria`**, 2026-08-23
06:50 IST: 19 files, +1010/−97, including the new `frontend/src/presentation.ts`.

| Step | State |
|---|---|
| **0** — builder warning | **Shipped.** Advisory `.criterion-duplicate-warning`, name-matched, never blocking. Eval-seed correction lives in the evals repo and is **not** in this change. |
| **1** — reconciliation audit | **Open.** dev2 inspected by hand (one round, `agree`); production uninspected. |
| **2** — expose criterion responses | **Shipped.** `_full_round_criteria`, `EvaluationDetail.criterion_responses`, `EvaluationRoundResults.criteria`, organizer `<dl>` in rubric order, and a separate `reviews.csv` export. Summary export byte-stable. |
| **3** — humanize legacy tokens | **Shipped.** `formatRecommendationChoice` in `frontend/src/presentation.ts`, applied to the reviewer control and the organizer results line, with explicit `value={choice}`. Stored and API values stay raw. |
| **4** — criterion purposes | **Shipped.** `purpose: "recommendation" \| "comment" \| null`; one per rubric; type-constrained; recommendation forced `required=True` and held to the 2–8 / 80-char legacy envelope. |
| **5** — honest round builder | **Shipped.** Legacy Recommendations control hidden, de-`required`, and its custom validity suppressed when a purpose criterion exists; choices derived server-side in one shared `_build_round_rubric` used by both create and draft-update. |
| **6** — one reviewer control | **Shipped.** Designated criteria replace the built-ins; `_canonical_evaluation_fields` mirrors into the legacy columns. |
| **7** — migrate and retire | **Open**, gated on step 1. |

### Decisions taken during implementation

- **The shipped default changed** to `Strong accept, Accept, Reject, Strong reject`. This is the
  §10 "rejected alternative" deliberately reopened: remote evidence showed the raw default was what
  drove the ABS-S3 author to add a second control, so it was treated as a contributing cause.
  Accepted consequence: existing rounds keep `strong_accept`, new rounds store `Strong accept`, and
  the summary CSV now carries both conventions. `{value, label}` is what actually retires this.
- **Draft review content is not exposed.** `criterion_responses` is returned only for `state ==
  "final"`, proven end-to-end in `tests/evaluation/test_round_results_membership.py`.
- **`EvaluationSave` no longer declares the recommendation requirement**, because the payload lacks
  the rubric metadata needed to identify the canonical key. The save route enforces it after
  loading the rubric. The rationale is recorded in the model to stop it being "restored" later.
- **Detailed-export columns are `Label [key]`**, so duplicate criterion labels cannot collide.
- **Two criteria accessors, not one** — see constraint 4 above.

### Coverage

Server: purpose typing/uniqueness/envelope, `_canonical_evaluation_fields` both directions, the
optional-comment asymmetry, the unvalidated historical-scoring path (`weight: 150` still counts),
detailed-CSV rubric order with a deliberate duplicate label, and CSV formula-prefix quoting.
Browser: exactly one Recommendation and one comment control for a designated rubric; built-in
controls intact for a legacy rubric; an undesignated `select` criterion still rendering alongside
the built-in; humanized option text with the raw value in the payload; the mirror present in the
submitted body; and the previously silent Finalize now showing a pending, disabled state.

The new endpoint is registered in `observability/manifest.json` with owner, runbook and SLO. Served
copies verified in sync: `reviews.js` newer than `main.tsx`, `embedded_assets.py` newer than
`admin_submissions.js`, cache token at `?v=23`.

### Still open

1. The reconciliation audit against production (step 1), which gates step 7.
2. The eval-seed correction, in the evals repository.
3. `router.py` still stores `round(…)` for the legacy integer — the half-point divergence recorded
   as residual 3 in the evidence log.

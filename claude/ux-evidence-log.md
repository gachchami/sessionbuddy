# UX Evidence Log — sessionbuddy

**This file is the canonical record.** The `claude.ai` project holds working notes; a copy of this
log there is reference material only and points here. Do not edit both independently.

> **Reconciled 2026-08-23.** Two documents named `claude/ux-evidence-log.md` existed
> independently: this git-tracked file, *reconstructed 2026-08-17* from primary evidence after the
> original could not be found in the tree or in `git log --all`; and a longer copy in the claude.ai
> project — which was, in fact, that missing original, invisible to the Aug-17 session because it
> lived in the project rather than on disk. They are not two edits of one document. Neither was a
> superset. This file merges both and resolves the one place they contradicted each other
> (see *CFP-S1 publish: citation corrected*).

Observed friction from real usage sessions (SBek eval runs, manual walkthroughs, user reports).
Each entry records what happened, where, why it matters, and candidate fixes — so UX work can be
prioritized from evidence rather than guesses. Append new entries at the top; graduate an entry
into `claude/review-backlog-2026-08-13.md` (or its successor) when it becomes a scheduled fix task.

Entry template:

```
## YYYY-MM-DD — <short title>
- Source: <how observed>
- Page / files: <route + static files>
- Observation: <what happened>
- Interpretation: <why it matters>
- Candidate fixes: <options, cheapest first>
- Measure of success: <how we'd know a fix worked>
- Status: open | fix scheduled (link) | fixed (link)
```

---

## 2026-08-22 — Reviewer preview labelled a rounded integer "weighted mean"; organizer showed 3.34 — fixed, committed `5ab5636`

- **Source:** SBek run `2026-08-22T04-14-23`, scenario **ABS-S3** (`outcome: completed`, 85 turns).
  Observations 4 (step 5) and 9 (step 8). Confirmed by a read-only trace of `frontend/src/main.tsx`
  and `src/sessionbuddy/evaluation/router.py`.

  Seventeen runs carry an `ABS-S3` directory, so this is not the first ABS attempt — but of the
  nine since 2026-08-17, eight ended `agent_error` (seven at turn 1, one at turn 120) and one
  `blocked` at turn 3. `2026-08-22T04-14-23` is the **first to reach step 5**, and the only run
  whose evidence contains the string `Overall rating preview` at all. The defect was reachable for
  months; nothing had got far enough to see it.

- **Page / files:** `/reviews` (reviewer workspace) and `/admin/evaluation-rounds/{round-id}` —
  `frontend/src/main.tsx`, `src/sessionbuddy/evaluation/router.py`,
  `src/sessionbuddy/evaluation/models.py`.

- **Observation, verbatim from the run.** Step 5, reviewer, Originality 4 / Relevance 2 at weights
  67/33: *"the app's live readout says 'Overall rating preview: 3 (weighted mean, submitted on
  finalize)'."* Step 8, organizer, same single review: *"'1/1 reviews finalized', '3.34 round
  average'"* and the proposal card *"'3.34 mean · 1/1 complete'"*. The agent carried the arithmetic
  between steps itself and flagged the contradiction.

- **One number, four computation sites — three of them rounded.** Line numbers are **as of the
  run**; `main.tsx` was rewritten by the fix, so they no longer resolve.

  | Where | Code at the time of the run | Value |
  |---|---|---|
  | Reviewer live preview | `computePreview` — `Math.round(Σ score×weight / Σweights)` | **3** |
  | Finalize payload `rating` | `Math.round(Σ score×weight / 100)` | **3** |
  | Server save | `router.py:2800` — `round(…)` into `evaluations.rating` (int) | **3** |
  | Organizer aggregate | `router.py:333 _weighted_review_score` → `_weighted_mean` | **3.34** |

  The 2026-08-17 ABS-04 fix was **deliberately read-time only** — its own docstring says so
  ("rather than change the stored column, which would ripple through
  `EvaluationSave`/`EvaluationView`"). Everything upstream of `get_round_results` still rounded.
  ABS-S3 walked straight into that seam.

- **Interpretation: a presentation-contract mismatch, not corrupt stored data.**
  `criterion_responses_json` held 4 and 2 at full fidelity and the aggregate was right.
  `evaluations.rating` is *intentionally* an integer. The defect was that the reviewer-facing
  string called that integer a "weighted mean" — and showed it in the moment immediately before an
  **irreversible finalize**. Severity: moderate UI correctness.

- **Correction to the run's own diagnosis.** Observation 4 concludes *"it shows the UNWEIGHTED
  value (or truncates/rounds to an integer)"*. The first branch is **wrong**. All three rounding
  paths did multiply by weight. It only looked unweighted because 4/2 at 67/33 rounds to 3, which
  is *also* the unweighted mean — the scenario's sample values cannot tell the two hypotheses
  apart. **Discriminating case: Originality 5 / Relevance 1 at 67/33 → weighted 3.68; unweighted
  3.** Anyone chasing "the preview ignores weights" would have hunted a missing multiplication that
  was not missing.

- **Four things found alongside it:**

  1. **JS/Python rounding disagree at half points.** `Math.round(2.5) === 3`; Python `round(2.5) ==
     2` (banker's rounding). The server *overrides* the client's `rating` whenever scored responses
     are present, so preview and stored integer could differ by one. Reachable with the most
     natural two-criterion setup there is: **50/50 weights, scores 3 and 2 → 2.5 → preview 3,
     stored 2.**
  2. **The client's `rating` computation was already dead for scorecard rounds** — computed,
     transmitted, discarded server-side. The wire format already carried `criterion_responses`, and
     `models.py:380` permits a final save with `rating: None` when responses are present, so "let
     the server derive it" cost nothing.
  3. **Draft reload resurfaced the integer.** The render read `previews[id] ?? assignment.rating`;
     after a reload with no local preview, the stored **3** came back. Fixing the preview function
     alone would not have fixed this path — and `??` would have swallowed a deliberate `null` for a
     blank scorecard.
  4. **Partial-draft denominator inconsistency.** The server divided by the weights of *answered*
     scored criteria; the client required all of them before computing anything.

- **Where the contradiction was visible.** Both values render on the *organizer's own* round
  dashboard: the card header showed `3.34 mean` while `EvaluationDetail.rating` passed the stored
  **3** straight through. It sits inside a `<details>` collapsed by default, so it was one click
  away rather than in frame in screenshot 016 — the same page, not a cross-persona discrepancy.

### Fix landed 2026-08-22 23:18, committed 2026-08-23 05:25 as `5ab5636` — reviewed, and it is a good one

Reviewed against the working tree after the edit, before the commit. All four recommendations
implemented, including the contract refinement. Line numbers below are **post-commit**.

- **`weightedScore(criteria, responses)`** (`main.tsx:133`) is now the single client-side
  definition: filters to `response_type === "score"`, skips blanks, accumulates `answered` weight,
  returns `Math.round((total / answered) * 100) / 100`. `computePreview` delegates to it;
  `formatScore()` (`main.tsx:184`) renders `toFixed(2)`.
- **The client no longer computes a rating for scorecard rounds** — the finalize payload sends
  `rating: null` plus the criterion responses, filtered to non-blank. The server is now the sole
  authority, which removes finding 1 from every visible surface.
- **`EvaluationDetail.rating` was not widened.** A distinct `weighted_score: float | None` was
  added (`models.py:448`, populated at `router.py:3292`) and rendered with a fallback to the bare
  integer for legacy and non-scorecard reviews (`main.tsx:1410`). The integer keeps meaning the
  integer.
- **The reload path is properly fixed** (`main.tsx:652`): `hasOwnProperty(previews, id)` rather
  than `??`, falling back to a full-precision recompute from `assignment.criterion_responses`. A
  deliberate `null` preview still renders `—`.
- **Copy is now honest:** *"Weighted score preview: … (calculated from the scorecard)"*, switching
  to *"Partial weighted score preview"* when the scorecard is incomplete (`main.tsx:661`).
- **The aggregate was not disturbed.** `weighted_score` is initialized per-iteration inside the
  loop and computed only for `state == "final"`, so drafts still cannot leak into the round
  average.
- **Server tests cover all three cases** (`tests/evaluation/test_evaluation.py:341`): 4/2 at 67/33
  → 3.34, the **5/1 → 3.68** discriminator, and **50/50 3+2 → 2.5**.
- **Browser coverage exists** — an 84-line Playwright test in `harness/e2e/reviews.spec.ts` asserts
  the preview renders `3.34`, `3.68` and `2.50`, and intercepts the PUT to assert
  `toMatchObject({ rating: null, criterion_responses: { originality: 3, relevance: 2 }, state:
  "final" })`.
- **The bundle was rebuilt** — `static/app/assets/reviews.js` contains the new string. No repeat of
  the "fixed locally, invisible in the deployed build" trap from 2026-08-17.
- **Finding 4 is resolved rather than papered over:** client and server now both divide by
  *answered* weight. Finalize with a blank **required** criterion is still blocked client-side by
  an explicit `state === "final" && !form.checkValidity()` guard.
- **A divergence I expected to find is not reachable.** `EvaluationCriterion` forbids a weight on
  `select` and `text` criteria, so the client's `response_type === "score"` filter and the server's
  `_round_criteria` weight filter select the same set. No third definition to drift.

### Residual — three of four now closed by `5ab5636`

| # | Item | Status |
|---|---|---|
| 1 | Two aggregate displays bypassed `formatScore` (round average, per-proposal card mean) | **closed** — `formatScore(results.average_rating)` at `main.tsx:1097` and `formatScore(submission.average_rating)` at `main.tsx:1397` |
| 2 | No client-side or browser-level test | **closed** — see the Playwright test above |
| 3 | JS/Python half-point divergence latent server-side | **OPEN** — `router.py` still stores `round(…)`, so a review whose weighted score is 2.5 stores 2. Nothing displays that integer as a mean today, so it is invisible — but it will look wrong the moment anything renders `rating` beside `weighted_score`. |
| 4 | Partial scorecard showed a confident two-decimal number | **closed** — copy switches to "Partial weighted score preview" |

- **Architectural warning this entry should preserve.** `_weighted_review_score` recomputes against
  the round's **current** `rubric_json`. Edit a round's criterion weights after reviews are
  finalized and every historical aggregate silently changes — the read-time approach bought
  correctness at the cost of historical stability. **The UI fix removed the visible contradiction
  but does not touch this; immutable rubric snapshots per round are still needed for historically
  stable evaluation results.** With the client no longer storing anything meaningful, the
  recomputed value is now the *only* record of a review's score, which makes the snapshot gap more
  load-bearing than it was, not less.

- **Measure of success:** re-run ABS-S3 and confirm step 5 reads `3.34` where it read `3`. Then
  **4/2 at 67/33 → 3.34**, **5/1 → 3.68** (separates a real fix from a rounding tweak), **50/50 3+2
  → 2.5**.

- **Status:** fixed and committed (`5ab5636`), **not yet deployed or re-run.** Reopen if a fresh
  ABS-S3 does not show `3.34` at step 5.

### Also from this run

**Recommendation model collision — analyzed, not scheduled.** Two differently-labelled
Recommendation controls on the same review form (title-case `Accept/Maybe/Reject` from a scorecard
`select` criterion, and raw `strong_accept/accept/reject/strong_reject` from the round's mandatory
built-in), plus the same duplication for comments. This is **not** two UI defects: it is a product-
model collision — two independent field systems rendered unconditionally, both required at
finalize, permitting contradictory answers — compounded by **write-only review content**: every
`select` and `text` criterion response is persisted and never surfaced to organizers or exported.
The reviewer's actual 139-character assessment is unreadable by anyone. Full analysis, constraints,
audit policy and a seven-step remediation sequence in
`claude/recommendation-model-collision-2026-08-22.md`.

**Fixture drift, untriaged.** The ABS-S3 fixture no longer matches the scenario script and cost
real coverage: Riley Reviewer had **one** assignment, not two (step 6 unperformable); all three
proposals are attributed to `Sasha Speaker` with **no co-speaker Marcus Okafor anywhere** (ABS-11
untestable); the two proposals the reviewer was meant to score were **already decided**; the event
lists three rounds including `Initial Review` and `Initial review` differing only by case; and the
sort control (ABS-10) worked but had **n=1**, so reordering could not be proven.

**No feedback on Finalize** for ~3s — the page appeared unchanged before the submission went
through. A user could reasonably click Finalize repeatedly believing it failed. Untriaged.

---

## 2026-08-17 — CFP-S1 blocked clicks 19 → 0; CFP-S2 speaker journey clean

Outcome entry for the occlusion thread below.

- **Source:** three consecutive SBek runs of the same scenario, plus a fourth for CFP-S2.

| | `22:26:55` | `00:14:15` | `01:15:47` |
|---|---|---|---|
| `"covering it"` blocked clicks | **19** | 0 | **0** |
| overlay mentions | 24 | 0 | **0** |
| `select "Session format"` | — | **16** | **1** |
| Session format configured | ✗ | ✓ | ✓ |
| custom questions added | ✗ | partial | **all four** |
| availability set | ✗ | ✗ | **✓** |

### CFP-S1 publish: citation corrected (2026-08-23)

This entry previously claimed run `01:15:47` published a CFP for the first time, quoting a public
portal URL. **That citation does not hold.** `runs/2026-08-17T01-15-47/CFP-S1/` exists but contains
only an empty `screenshots/` directory and **no `evidence.json`**; no 2026-08-17 CFP-S1 run
contains the string `published successfully`. The Aug-17 reconstruction of this log was right to
mark the thread *"[from briefing, unverified] … Needs a clean CFP-S1 run to become evidence."*

**The claim is nevertheless true, two days later than recorded.** The earliest CFP-S1 evidence
containing `CFP published successfully` is **`runs/2026-08-19T09-55-03`** (`outcome: completed`, 88
turns, `covering it` count **0**), verbatim:

> "CFP published successfully. Status shows 'Live' with confirmation message 'Your CFP was
> published successfully.' Public portal URL:
> `…/cfp/b55ab2/devflow-conf-2027` (visible as a copyable link…)"

Reproduced on `2026-08-19T10-52-33` (78 turns) and `2026-08-19T11-11-57` (81 turns). CFP-S1 has
since completed on `2026-08-21T01-21-06` (99), `2026-08-21T13-55-54` (33), `2026-08-22T03-09-27`
(64) and `2026-08-22T04-14-23` (112). **Lesson: a briefing claim carried into a log without a run
directory behind it survived six days and two rewrites. Cite the run, always.**

- **The second defect, found only because the first was fixed.** With the occlusion gone, run
  `00:14:15` still died at the turn cap — the agent set the display-rule trigger correctly on its
  first attempt and then re-selected it **15 more times**, because every snapshot reported the
  control empty. Cause: the trigger `<select>` wrapped its options in `<optgroup>`, and
  Playwright's `ariaSnapshot()` — which `browser.ts:276` uses to build the page outline — **drops
  every option nested in an optgroup**. Isolated to six lines of HTML:

  ```
  GROUPED (optgroup):        FLAT:
  - combobox "Grouped":      - combobox "Flat":
    - option "Choose…"         - option "Choose…"
                               - option "X" [selected]
                               - option "Y"
  ```

  The agent narrated it exactly: *"The trigger field dropdown (e1618) is unselected while the
  Answer value (e1620) is already 'Workshop (120 min)'."* The DOM said `selectedIndex: 3`. Fixed by
  appending options directly (`admin_programs.js:569`), event fields before custom questions;
  option text, value and `data-source-key` all unchanged. Selects on that dropdown went 16 → 1.

- **Layout change that closed the occlusion for good.** The builder is now a pane, not a document:
  `.cfp-builder` pins below the shell chrome at `calc(100dvh - var(--sb-chrome-top))`, `.cfp-editor`
  is the one scrollport, and the outline nav sits above it rather than over it. Full scroll sweep
  at five viewports went from **12–21 blocked positions to 0** (`covered by: {}`). One trade-off:
  the publish button sits just below the fold at the very top of the page, then pins once you
  scroll.

- **Regression guards (all executed both ways):**
  - `tests/release_readiness/test_cfp_builder_sticky_controls.py` — 9 assertions; 9 pass patched, 6
    fail on reconstructed pre-patch CSS.
  - `tests/release_readiness/test_select_options_stay_snapshot_visible.py` — new; bans `<optgroup>`
    across all console scripts and pages. 3 pass patched, 2 fail pre-patch.
  - `harness/e2e/cfp-builder-chrome-occlusion.spec.ts` — new; loads the real shell CSS *and*
    `app_shell.js`, hit-tests the row and both reorder buttons. 4 pass, 4 fail pre-patch.

- **Status:** occlusion **fixed and confirmed in product behaviour**; publish confirmed from
  2026-08-19. Full pytest suite later run by the team: 904 passed / 3 failed, all three
  unrelated-to-behaviour drift (stale asset token, observability registration, OpenAPI) and since
  resolved.

---

## 2026-08-17 — CFP-S2 speaker journey: clean pass, no product defects in scope

**Artifact:** `runs/2026-08-17T03-05-21/CFP-S2/` — outcome `completed`, 79 turns, 27 screenshots.
Target: `https://sessionbuddy-development.shiny-cloud-dd47.workers.dev`.
Agent `claude-sonnet-5`, judge `claude-opus-5`.

At the time this was the only recent run that produced usable product signal. The judge's own
summary:

> "Within that scope the app behaved correctly with no errors, broken flows, or data loss observed;
> the two defects listed are usability/coverage gaps rather than failures."

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

Every `cannot_judge` and three of the four `partial`s trace to one cause: CFP-S1, CFP-S3 and CFP-S4
were excluded by the `--scenarios` filter and ran 0 turns. The judge states this explicitly —
*"a coverage limitation of this run, not an app defect."*

### Defects found (both MINOR)

1. **Drafts are invisible on the speaker dashboard.** With a saved draft present, `/speaker` showed
   "No proposals yet." and "Your speaker workspace is ready". The draft is recoverable only by
   returning to the CFP form URL. *(`006`, turns 18–21.)* Addressed by
   `GET /api/v1/speaker/proposal-drafts` (`cfp/router.py:921`) + `loadProposalDrafts()` in
   `speaker_portal.js`, committed in `a3e86b1`. Scored as a **usability wrinkle inside a passing
   item (CFP-07)**, not a failing criterion.

2. **No bio / headshot / social fields anywhere in the submission path.** The CFP form collects
   name, email (readonly), title, abstract, format, full description, one dynamic question,
   audience level, track, co-speakers. No bio, Twitter, LinkedIn or headshot. Sample-data fields
   for Priya Raman (bio, twitter, linkedin, tshirt, dietary) have nowhere to go.
   → **These fields already exist on the account profile model** (`platform/auth/access.py`
   L829–837: `biography`, `website_url`, `linkedin_url`, `x_url`, `headshot_url`). The gap is
   surfacing, not storage. Same surface the `users`/`people` consolidation touches.

### Scoring artefact worth knowing about

CFP-03 lost points on the *deadline* requirement for a harness reason, not a product reason — the
judge wrote that it was "scored down not because a deadline was shown to be missing, but because
the captured page outlines are truncated before any deadline text." That is the 500-character
tool-result clip (`agent.ts:643`, `judge.ts:65`) costing a real point.

---

## 2026-08-16 (later) — Session-format row unclickable: 19 blocked clicks

*Superseded by the outcome entry above. Kept for the diagnosis.*

- **Source:** SBek run `2026-08-16T22-26-55`, scenario **CFP-S1**, `CFP-S1/evidence.json`.
  Reproduced independently in headless Chromium against the real shipped stylesheets and the real
  `app_shell.js`.
- **Page / files:** `/admin/events/{event-id}/cfp`, **Proposal details** screen —
  `src/sessionbuddy/static/product.css`, `app_shell.css`, and every page that loads them.
- **Observation:** the agent tried to open the "Session format" system field's `<summary>`
  disclosure **19 consecutive times**, across refs `e273 … e795`, at scroll positions **y=0, y=602
  and y=690**, with `Escape` presses and fresh snapshots in between. Every attempt was refused by
  the harness with *"could not be clicked because X is covering it"*. The covering element
  alternated between `<div#cfp-editor-actions.cfp-editor-actions>` — the **bottom** sticky
  Draft/Publish/Discard bar — and `<span.cfp-outline-label> "Confirmation"` / `<div#cfp-outline-items>`,
  the sticky form-outline nav.

  The agent correctly diagnosed it as a layout/z-index defect (Escape does not dismiss either),
  recorded it as a confirmed bug, and abandoned the sub-step. The same `#cfp-outline-items` layer
  later blocked `+ Add custom question` four times (`e1031 … e1112`).
- **Correcting two earlier claims:** (1) the blocker is **not** primarily the topbar/event nav — it
  is the builder's own two sticky bars, top and bottom; (2) the harness **does** hit-test before
  clicking, so this is not Playwright's retry logic masking the defect.
- **Replay of the agent's exact sequence** (`scrollTo(y)` → `scrollIntoViewIfNeeded()` →
  `elementFromPoint`), pre-patch:
  ```
  1024x768 | y=0: BLOCKED by #cfp-editor-actions | y=602: BLOCKED by .cfp-outline-label | y=690: BLOCKED
  1280x720 | y=0: clickable                      | y=602: BLOCKED by .cfp-outline-item  | y=690: BLOCKED
  1440x900 | y=0: clickable                      | y=602: BLOCKED by .cfp-outline-item  | y=690: BLOCKED by .sb-event-nav
  ```
- **Root causes found and fixed:**
  1. **`.question-card { overflow: hidden }`** — exactly the shape the entry below warned to look
     for, one level further in. `hidden` makes each question card a scroll container, so
     `scrollIntoView` on a control inside it resolves against *the card* and the target's
     `scroll-margin-top` is discarded. Proof by isolation: with `hidden` the ↓ button lands at
     viewport y=20 despite a computed `scroll-margin-top: 144px`; with `clip` it lands at y=144.
     → `overflow: clip`.
  2. **The shell's scroll offsets were literals smaller than the chrome they had to clear** —
     `scroll-padding-top` / `scroll-margin-top: 9rem` against `--sb-chrome-top: 9.5rem`, the exact
     "hardcoded guess that drifts from the shell" the previous fix's own test docstring names. →
     new `--sb-scroll-offset` (defaults to `var(--sb-chrome-top)`); the offsets derive from it,
     which **deletes three breakpoint-specific literal pairs** (5/9/12rem) in favour of one rule.
  3. **`.cfp-question-actions` swallowed clicks through its transparent tail** — its background
     fades to transparent over its lower third, so rows scrolling beneath it stayed visible while
     it took their clicks. → `pointer-events: none` with `> * { pointer-events: auto }`.
  4. **The add-question bar was on a screen that lists nothing to add** — scoped to the proposal
     screen only via `.cfp-editor-section--proposal`. (An earlier attempt scoped it to
     `--single-question`, which also hid the button on the screen you land on right after adding a
     question — caught and corrected before it shipped.)
  5. Cache-bust tokens unified across **26 pages** — they had drifted to `app-shell.css?v=19/20/21`
     and `product.css?v=58/59/62/63/65`, so an `app_shell.css` fix would have reached almost no
     page.
- **Status:** closed — see the outcome entry above.

---

## 2026-08-16 — Root cause of the CFP-builder scroll-thrash: every sticky rule in the builder was inert

- **Source:** Investigation of the 2026-08-13 entry below, before deciding the parked builder
  redesign. Reproduced in headless Chromium against the real shipped stylesheets.
- **Page / files:** `/admin/events/{event-id}/cfp` — `src/sessionbuddy/static/product.css`,
  `app_shell.css`, `admin_programs.html`.
- **Observation:** The builder was **already written to keep its controls on screen** —
  `.cfp-editor-actions` (publish button, draft/live label, publish result) declares
  `position: sticky; bottom: 0`, and `.cfp-question-actions` (+ Add custom question) declares
  `position: sticky; top: 5.75rem`. Neither ever stuck. `.cfp-builder` carried `overflow: hidden`,
  which makes it the nearest scrollport for every sticky descendant; that box never scrolls, so
  sticky resolved to static and the controls scrolled away with the form. Measured on the Questions
  screen with 12 custom questions (document 5040px at 1440×900): the publish button's viewport
  position tracked scroll 1:1 — 4501 → 3501 → 2501 → 1501 — i.e. off screen at every scroll depth
  except the very bottom. The form outline (`.cfp-section-nav`) was not sticky at all and left the
  viewport after ~450px of scroll.
- **Interpretation:** The eval agent's scroll-thrash was not a missing feature; it was a
  **one-declaration CSS defect** silently disabling three controls designed to stay pinned. That
  also explains why the earlier "make the anchor nav sticky" candidate fix looked necessary — the
  intended design was already there and dead. Worth checking for the same shape elsewhere: any
  `overflow: hidden` ancestor of a sticky element.
- **Fix applied (2026-08-16):** `.cfp-builder` → `overflow: clip`; `app_shell.css` publishes
  `--sb-chrome-top`; `.cfp-section-nav` and `.cfp-question-actions` pinned against it.
- **Evidence:** headless Chromium, real stylesheets, before vs after at 1440×900, 1280×720,
  900×800, 390×844. Before: publish, form outline and + Add-question all leave the viewport by
  1200px of scroll and never return. After: all three stay visible **and hit-testable** at every
  scroll depth on every viewport.
- **Regression guard:** `tests/release_readiness/test_cfp_builder_sticky_controls.py`.
- **Status:** fixed. **Follow-up that mattered:** making these bars genuinely sticky is what put
  them *in front of* the question rows — see the entries above. The `overflow: hidden` shape this
  entry warns about was found again on `.question-card`. The warning was right; it just needed
  acting on.

---

## 2026-08-13 — CFP builder: agent scroll-thrash relocating availability/publish controls

- **Source:** SBek bounded eval scenario (Playwright agent driving the real console). The agent
  spent many actions scrolling up and down to relocate the availability and publish controls while
  building/publishing a CFP. Scenario was allowed to finish naturally — no intervention, no code
  changed at time of capture.
- **Page / files:** `/admin/events/{event-id}/cfp` — `src/sessionbuddy/static/admin_programs.html`
  (+ `admin_programs.js`).
- **Observation:** Availability and publish live deep inside one long single-column form: section
  `#publish-settings` contains the whole `#publish-form`, with `#cfp-availability` (open/close
  window) and the publish action far down the page below Basics/Questions/Confirmation/Routing
  content. The page *does* have an in-page anchor nav and a `#cfp-state` publish-status badge near
  the top, but the agent did not use the anchors — it scrolled repeatedly to re-find the controls
  after each edit elsewhere in the form.
- **Interpretation:** Agent scroll-thrash is a decent proxy for human wayfinding cost. Two signals:
  (1) the most consequential controls on the page — when the CFP opens/closes, and whether it is
  live — are the hardest to reach and to keep in view; (2) the existing anchor nav is evidently not
  salient or not sticky enough to serve as the recovery mechanism (it scrolls away with the page).
  A human organizer editing questions and then wanting to publish faces the same hunt.
- **Measure of success:** Re-run the same bounded SBek scenario and compare total action count and
  scroll-action count.
- **Status:** root-caused and fixed across 2026-08-16 and 2026-08-17 — see the entries above.
  Confirmed by CFP-S1 publishing a CFP (2026-08-19, see citation correction).

---

## Open items

1. **Full-suite runs produced no signal for a period.** Every 18-scenario run between
   2026-08-16T06:51 and mid-August died on turn 1. See `identity-model-and-eval-2026-08-17.md` for
   the root cause. Runs from 2026-08-19 onward complete, so this is substantially resolved — but
   ABS-S3 still shows eight `agent_error` outcomes in nine attempts since 2026-08-17, so the
   failure mode is not fully gone.
2. **Drafts not listed on the speaker dashboard.** Endpoint and loader committed in `a3e86b1`;
   deployment status to the dev worker was **unconfirmed** at the time of writing — neither the
   cloud sandbox (workers.dev is not on its egress allowlist) nor `device_bash` (no network) could
   reach the worker to check.
3. **Speaker profile fields have no surface in the submission path** (CFP-S2 defect 2 above).
4. **Track field in the CFP builder when the event has no tracks** — `admin_programs.js:123`
   splices it out silently; tracks are only creatable on the Agenda page, which the builder never
   mentions (`grep -c agenda` on `admin_programs.html` → 0). Both CFP-S1 runs concluded "there is
   NO built-in Track system field" and rebuilt it as a custom question, losing routing and
   blind-review semantics. *Not reproduced in the 03-05-21 run* — DevFlow Conf 2027 has tracks.
   Cheapest fix: render the row disabled with "Add tracks in Agenda to offer this question".
5. **500-char judge clip** (`agent.ts:643`, `judge.ts:65`) marks on-screen things absent.
   Harness-side; costs points on every scenario. Cost CFP-03 in the 03-05-21 run specifically.
6. **The harness loop-breaker only counts failures.** `MAX_REPEATED_TOOL_FAILURES` keys on
   `isError`, but the optgroup loop was 16 *successful* calls the agent could not verify.
7. **Server-side half-point rounding** (`router.py`, residual 3 above) — open.
8. **Recommendation model collision** — analyzed, not scheduled. See
   `claude/recommendation-model-collision-2026-08-22.md`.
9. **Immutable rubric snapshots per round** — `_weighted_review_score` recomputes against the
   round's current `rubric_json`, so editing criterion weights after finalization silently rewrites
   history.

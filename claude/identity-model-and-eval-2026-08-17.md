# Identity model & eval scoring — 2026-08-17

> **Reconstructed 2026-08-17.** The original was not in the working tree or in `git log --all`.
> Rebuilt from source, `git log`, and the harness run corpus. The **decision** below is carried
> from the session handoff; the **evidence** and **eval analysis** are re-derived from artifacts.
> The original's ranked risk list and rubric weight table could not be recovered — the sections
> below are re-derived and should be reviewed rather than trusted.

---

## Part 1 — Identity model

### Decision (carried from handoff, not re-derived)

`users` is the single source of truth for a person. `account`, `people`, `speaker` and
`reviewer` are **UI-facing names**, not separate stores.

### What the code currently shows

`people` is referenced across 20+ modules — it is not a thin alias today:

```
migrations_baseline/0001_baseline.sql          3
src/sessionbuddy/cfp/router.py                 9
src/sessionbuddy/platform/auth/access.py      21
src/sessionbuddy/competition/router.py        21
src/sessionbuddy/console/embedded_assets.py    9
src/sessionbuddy/speaker_operations/router.py  5
src/sessionbuddy/static/speaker_directory.html 13
… plus communications/, evaluation/, scheduling/, static/access_admin.*
```

The profile surface already carries the fields the CFP form is missing
(`platform/auth/access.py` L829–837):

```
user_id, display_name, job_title, company, biography,
website_url, linkedin_url, x_url, headshot_url
```

### Blocking prerequisite — confirmed in source

`AccountProfileUpdate.description` is capped at **1000** characters:

```python
# src/sessionbuddy/platform/auth/access.py:847
description: str | None = Field(default=None, max_length=1000)
```

**Widen 1000 → 5000 before any backfill**, or imported bios truncate silently. Neighbouring
constraints for reference: `event_description` 2000 (L353), another `description` 2000 (L540).
Widening L847 alone leaves those two inconsistent — decide deliberately whether they follow.

### Risks (re-derived — original ranking lost)

- **Truncation on backfill.** The 1000-char cap above. Mitigated by widening first. *Highest.*
- **`people` is load-bearing, not cosmetic.** 21 references in `competition/router.py` and 21 in
  `access.py` alone. A rename-only pass will miss semantics.
- **`cfp/router.py` and `access.py` contain enormous single lines.** Never `grep` whole lines
  from them, nor from `console/embedded_assets.py` or `.local/*.sql`. Use `grep -c/-o/-l` or
  bounded Python slices.
- **`embedded_assets.py` duplicates static assets.** It carries its own copies of
  `loadProposalDrafts` and `people` references — any edit to `static/*.js` must be mirrored there
  or the console and the portal drift.
- **No eval safety net right now.** Until full-suite runs work (Part 2), a regression introduced
  by this consolidation would not be caught.

---

## Part 2 — Why the evals produce no score

### Headline

The evals are not scoring the product badly. **They are barely scoring the product at all.**

Latest full run, `runs/2026-08-17T03-04-57`: all 18 scenarios `agent_error` on **turn 1**,
whole run over in **3 seconds**, rubric coverage **0%**, score withheld, 86 manual items pending.

Latest scenario run, `runs/2026-08-17T03-05-21` (CFP-S2 only): overall **57.7% over a 6.8%
coverage slice** — still withheld, since the threshold is `MIN_COVERAGE_PCT = 60`
(`src/config.ts:14`).

| Area | earned | judgeable | total weight | coverage |
|---|---|---|---|---|
| call-for-papers | 7.5 | 13 | 38 | 34.2% |
| abstract-management | 0 | 0 | 28 | 0% |
| ai-agenda | 0 | 0 | 18 | 0% |
| content-management | 0 | 0 | 31 | 0% |
| public-widgets | 0 | 0 | 35 | 0% |
| speaker-management | 0 | 0 | 33 | 0% |

### Root cause of the full-run collapse

`src/model_client.ts:232`:

```ts
export function createModelClient(): ModelClient {
  if (process.env.SBEK_PROVIDER === "claude-cli") {
    return new ClaudeCliClient() as unknown as ModelClient;
  }
  return new Anthropic();          // ← needs ANTHROPIC_API_KEY
}
```

`SBEK_PROVIDER` appears in exactly two places in the whole harness: this line, and a line of CLI
help text (`src/cli.ts:55`). It is **not** in `.env` — which defines only `SBEK_TARGET_URL`,
`SBEK_ROOT`, `SBEK_CLAUDE_AUTH_VOLUME` — and `ANTHROPIC_API_KEY` is unset in the shell.

So any run launched without `SBEK_PROVIDER=claude-cli` exported inline falls through to
`new Anthropic()` and every scenario dies instantly with:

> `Could not resolve authentication method. Expected one of apiKey, authToken, credentials,
> config, or profile to be set.`

Single-scenario runs are typically launched by hand with the variable prefixed, which is exactly
why they complete while full-suite runs do not.

### Full run history — three separate harness failure modes, zero product regressions

| Run | Outcomes | Mode |
|---|---|---|
| 2026-08-16T05-17-33 | 16 error / 1 pass / 1 not-found | Codex bridge: `fetch failed` ×12 |
| 2026-08-16T06-51-24 | 18 error | **auth unresolved** |
| 2026-08-16T08-28-16 | 17 error / 1 pass | `Claude CLI exited 1` ×15 |
| 2026-08-16T12-24-13 | 18 error | **auth unresolved** |
| 2026-08-16T22-18-24 | 18 error | **auth unresolved** |
| 2026-08-17T01-15-05 | 18 error | **auth unresolved** |
| 2026-08-17T03-04-57 | 18 error | **auth unresolved** |

**No full 18-scenario run has ever produced a score.** Single-scenario runs, by contrast, mostly
reach 70–90 turns and complete.

Note `.auth/` is **empty**; the four saved persona sessions (organizer, reviewer, speaker,
speaker2) live in `.auth-session-backup-2026-08-16/`. The `Claude CLI exited 1` run at 08-28-16
lines up with `.auth` being emptied at 08:16 — worth confirming before the next full run.

### Fix, in leverage order

1. **Make the provider resolve.** Add `SBEK_PROVIDER=claude-cli` to `.env`, **and** add a
   preflight in `cli.ts` that resolves the provider once and aborts with a clear message before
   any scenario runs. A run must never again burn 18 scenarios silently.
2. **Restore `.auth/`** from `.auth-session-backup-2026-08-16/` so personas do not re-auth.
3. **Then re-run the full suite.** Coverage should clear 60% and produce the first real score.
   Until this happens every product conclusion below is provisional.
4. **Raise the 500-char clip** (`agent.ts:643`, `judge.ts:65`). Demonstrated cost: CFP-03 was
   marked down for a missing deadline the judge could not see because the page outline was
   truncated before it.
5. **Turn-limit exhaustion.** Six single-scenario runs ended at the 70/90-turn cap, which scores
   as `agent_error`. Worth checking whether scenarios are over-scoped or the agent is looping.

### Consequences for the planned work

- **Deploying `proposal-drafts` will not move a failing item to passing.** CFP-07 already
  **passes**; dashboard invisibility is logged as a MINOR usability note inside it. Still worth
  shipping — just not the eval unblocker it was assumed to be.
- **Track was not broken in this run.** CFP-03 recorded `Platform & Infra` and `AI Engineering`
  rendering correctly. The `admin_programs.js:123` splice only bites on events with **no** tracks
  and is organizer-side (CFP-S1), which has not executed successfully. Fix it on its merits, not
  on eval evidence — there is none yet.
- **The bio/headshot gap is the one eval defect that overlaps the identity work.** Those fields
  already exist on the profile model; they are simply not surfaced.

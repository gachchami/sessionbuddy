# SessionBuddy

## Description and goals

SessionBuddy is an open-source, performance-first replacement for the subset of
Sessionboard used to run speaker-driven events. It covers the full path from a
public call-for-speakers through review, acceptance, speaker onboarding,
scheduling, and event operations. The backend is Python/FastAPI compiled to a
Cloudflare Python Worker; Cloudflare D1 is the transactional source of truth,
R2 stores private assets, and Queues/Workflows run asynchronous effects. The
console is dependency-free same-origin HTML/JS/CSS with one React/Vite island
(the evaluator workspace).

The product exists so an event team can, without spreadsheet re-entry:

1. Publish a routed call-for-speakers form.
2. Collect a complete speaker and proposal record.
3. Review and score submissions.
4. Accept speakers, collect outstanding assets through tasks, and communicate
   automatically.
5. Build a conflict-free agenda quickly.
6. See onboarding progress in real time.

Trade-offs follow a fixed priority order: correctness, authorization, and no
cross-event data leakage first; perceived and measured speed second; completion
of the six journeys third; accessibility and operational reliability fourth;
additional features and polish last. The full implementation and
acceptance-testing contract is `docs/requirements.md`.

## How to install

### Local development (recommended: Docker)

The supported environment is Docker. It pins Node.js 24, Wrangler, uv, Python,
and the Debian base rather than depending on host-installed runtimes.

Run the complete foundation gate in an isolated container:

```bash
docker compose run --rm --service-ports worker sh scripts/cloudflare_gate.sh
```

The gate installs locked dependencies, runs lint and tests, resolves the real
Pyodide/WASM package lock, applies local D1 migrations, starts Workerd, checks
the API and review console, and writes the Worker benchmark under `.local/`.

For an interactive development server:

```bash
docker compose up --build worker
```

A fresh instance opens `http://localhost:8787/setup` and contains no
organizations, events, speakers, or synthetic identities. The one-time setup
creates the first organization and named administrator through the guarded
bootstrap API; passwordless email links handle every subsequent sign-in.

Key local URLs once set up:

- `http://localhost:8787/admin` — organizer event ledger and recent changes.
- `http://localhost:8787/admin/events/new` — create or duplicate an event.
- `http://localhost:8787/admin/events/{event-id}/settings` — edit event details,
  branding, and lifecycle.
- `http://localhost:8787/admin/events/{event-id}/cfp` — build and publish the
  event's Call for Proposals.
- `http://localhost:8787/cfp/{published-slug}` — the public proposal form; use
  the exact link shown after publishing. First-time submitters verify their
  email at submission time; file answers are staged before any speaker record
  exists and attached atomically when the submission succeeds.
- `http://localhost:8787/admin/events/{event-id}/submissions` — submission
  review and evaluation rounds.
- `http://localhost:8787/reviews` — the evaluator workspace (assignment-scoped
  reads, resumable drafts, rubric validation, immutable finalization).
- `http://localhost:8787/speaker` — the speaker portal (tasks, profile,
  quarantined asset uploads with malware scanning).
- `http://localhost:8787/admin/events/{event-id}/onboarding` — live onboarding
  progress for organizers.
- `http://localhost:8787/admin/events/{event-id}/agenda` and
  `http://localhost:8787/events/{event-id}/schedule` — agenda editor and the
  published schedule.

Focused development commands inside the container:

```bash
cp .dev.vars.example .dev.vars
npm ci
uv sync
uv run ruff check .
uv run pytest
uv run python scripts/generate_openapi.py
npm run worker:sync      # Pyodide dependency compatibility gate; commit pylock.toml
npm run worker:migrate   # apply local D1 migrations
npm run worker:dev
```

`pywrangler sync` resolves against the Pyodide index selected by
`compatibility_date` and installs generated Worker packages into ignored local
directories. Commit `pylock.toml`; do not commit `python_modules/` or
`.venv-workers/`.

### Deploying the isolated Cloudflare development environment

Authenticate Wrangler without exposing host credentials to the container:

```bash
docker compose run --rm --no-deps worker npx wrangler login --device --browser=false
```

Then migrate, deploy, and verify:

```bash
docker compose run --rm --no-deps worker npm run worker:migrate:dev
docker compose run --rm --no-deps worker npm run worker:deploy:dev
docker compose run --rm --no-deps worker npm run worker:preflight:dev
```

The deployment preflight is read-only. The stricter
`worker:activation:preflight:dev` command remains non-zero until email, direct
R2 upload credentials, and the initial bootstrap are complete. Perform the
one-time bootstrap without exposing or retaining its token:

```bash
docker compose run --rm --no-deps worker npm run worker:bootstrap:dev -- \
  --organization-name "Example Events" \
  --admin-email "admin@example.com"
```

This creates a valid organization with no events; the administrator creates the
first event from `/admin`. See
[deployment configuration](docs/deployment-configuration.md) for Cloudflare
variables, secrets, bindings (D1, R2, Queues/DLQs, Workflow, rate limiters),
and the development malware-scan bypass (`MALWARE_SCAN_MODE`), which staging
and production reject.

### Release rehearsal

The complete local release-hardening gate runs entirely through containers:

```bash
./scripts/release_gate.sh
```

It runs the full suite, a large isolated test seed, query-plan checks,
backup/restore rehearsal, desktop/mobile Axe checks, local API benchmarks,
Lighthouse, and a Wrangler dry run. No remote deployment is performed.

## Results

### Verification status (2026-08-13)

- Full Python suite: **535 tests passing** (HTTP-level journeys for identity,
  multi-organizer administration, staged CFP uploads, evaluation, scheduling,
  and release readiness), with `ruff` clean and canonical baseline validation
  enforced (`scripts/validate_baseline_migration.py`).
- Browser/Axe: 44 checks across desktop Chrome and Pixel 7 profiles, including
  authenticated admin, access, evaluator, speaker, onboarding, and agenda pages.
- Large-database release smoke: 10,000 submissions, 2,000 speakers, 50,000
  tasks, and 2,000 agenda items with integrity, foreign-key, schema-hash,
  query-plan, backup, and restore checks.
- Cloudflare development rehearsal: 23/23 activation preflight checks; live
  passwordless sign-in, invitation acceptance/revocation, conditional
  draft/submission, speaker ownership, and a direct R2 upload promoted clean.
  Worker bundle approximately 8.9 MiB (2.28 MiB gzip); dry-run deploy passes.
- Observability: every API route (122), document route (32 pages + 3 documented
  exclusions), and asynchronous handler (cron, queue consumers, workflow) is
  registered in `observability/manifest.json` with an owner, budget/SLO, and
  runbook — enforced by tests so new surfaces cannot ship unregistered.

Point-in-time acceptance evidence lives in
[the nine-area delivery audit](docs/delivery-completion-audit.md); the current
capability boundary is summarized in [product status](docs/product-status.md).

### What is implemented

Routed CFP forms with conditional questions and staged pre-submission file
uploads; versioned drafts; idempotent submissions; evaluation rounds with
balanced or full assignment, blind review, conflict handling, and immutable
audited decisions; speaker onboarding tasks, quarantined asset pipeline with
fixed-length R2-to-scanner streaming, communications with a self-recovering
dispatch queue; conflict-checked agenda building and published schedules with
embeds and calendar invitations; multi-organizer administration (invitable
organization administrators and per-event administrators); and a fail-closed
security model — opaque sessions, live D1 membership checks, CSRF/origin
guards, per-(organization, event) authorization, and 404-style denials.

### Known gaps

Tracked openly rather than hidden: evaluation-round surfaces cap at 100 rows
and compute completion from the capped page (top of the current backlog);
several admin lists truncate silently (25–5,000 row caps); expired operational
records (sessions, challenges, idempotency rows) have no scheduled purge yet;
queue consumers emit no structured logs (recorded as a known gap in the
observability manifest); browser-local CFP drafts have no TTL; and there is no
staging/production environment split in `wrangler.jsonc` yet. Benchmarks:
`scripts/benchmark_api.py` measures any route against local ASGI, Workerd, or
a deployed environment (see `docs/debugging-runbook.md`).

## Documentation map

Architecture and delivery requirements live under `docs/`: start with
`requirements.md` (the contract), `architecture.md` (design intent; see its
implementation-status note), `product-status.md` (capability boundary),
`delivery-completion-audit.md` (evidence), `deployment-configuration.md`,
`api-security.md`, `data-architecture.md`, and `debugging-runbook.md`
(incident response, including the async-handler section).

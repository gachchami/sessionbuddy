# Sessionbuddy

Sessionbuddy is a performance-first event program-management application. The backend is Python/FastAPI on Cloudflare Python Workers; D1 is the transactional source of truth.

The Worker uses Smart Placement so database-backed handlers can execute near
the D1 primary. Keep client, `app`, and `db` timings separate when evaluating
the result; placement may need traffic and up to 15 minutes before it decides.

## Engine Room and local development

The supported development and compatibility environment is Docker. It pins
Node.js 24, Wrangler, uv, Python, and the Debian base rather than depending on
host-installed runtimes.

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

Review the product-shaped CFP management flow at these local URLs:

- `http://localhost:8787/admin/programs` — administrator sign-in, program creation,
  and configurable public-form publication.
- `http://localhost:8787/cfp/{published-slug}` — public proposal submission; use
  the exact link shown after publishing.
- `http://localhost:8787/admin/programs/{program-id}/submissions` — authorized
  submission review; use the link shown after publishing.

Fresh instances open `/setup` and contain no organizations, events, speakers, or
synthetic identities. The one-time setup creates the first organization and named
administrator through the guarded bootstrap API. Passwordless email challenges
handle subsequent sign-in. Invitations provision event administrators,
evaluators, or speakers only after email verification; public-form registration
provisions only the owning speaker. Every authenticated mutation uses the same
opaque HTTP-only session, live D1 membership, origin, and session-bound CSRF checks.

Evaluation workflow starts from a program's submission-review page. Open the
initial round there, then use `http://localhost:8787/reviews` for the React/Vite
evaluator workspace. The current vertical slice supports assignment-scoped
reads, resumable drafts, rubric validation, and immutable finalization. Evaluator
access in deployed environments is granted through an administrator invitation.

After a round is opened, its submission page links to
`/admin/evaluation-rounds/{round-id}`. That admin dashboard shows completion and
the documented arithmetic mean of finalized ratings. Decisions require all
assignments for that submission to be final, are audited independently, and do
not enqueue or send communication. Once recorded, a decision is permanently
locked by both the API and UI; the product has no decision-change workflow.

Round setup supports a configurable numeric range, recommendation choices,
evaluator guidance, multiple active event evaluators, and two deterministic
assignment strategies: `balanced` distributes submissions round-robin, while
`all` assigns every selected submission to every selected evaluator. The
server validates evaluator membership and submission scope before creating the
atomic assignment batch.

Speaker operations starts at `http://localhost:8787/speaker`. A speaker signs in
after an administrator invitation or after
registering through a published proposal form. The portal is derived from the
verified user's explicit ownership records, never from a browser-supplied email.

Scheduling is available after an administrator has accepted sessions and created
the agenda from the event workspace:

- Admin editor: `http://localhost:8787/admin/events/{event-id}/agenda`
- Staff/speaker schedule: `http://localhost:8787/events/{event-id}/schedule`

The editor includes list/day/week/track/room views, Firefox-compatible drag/drop,
a keyboard scheduling form, transactional conflict rejection, visible stale-write
rollback, publication, and per-speaker calendar update planning.

See `docs/product-status.md` for the implemented boundary and remaining provider
activation work.

Release readiness provides the complete local release-hardening gate. The shell
launcher runs on the host only to orchestrate Docker Compose; Node, Python,
Pywrangler, Wrangler, tests, builds, and benchmarks execute in containers:

```bash
./scripts/release_gate.sh
```

It runs the full suite, large isolated test seed, query-plan checks,
backup/restore rehearsal, desktop/mobile Axe checks, local API
benchmarks, Lighthouse, and a Wrangler dry run entirely through containers. See
`docs/product-status.md` for accepted local evidence and the remaining
Cloudflare staging promotion gates.

The admin operational view for an event is available at
`http://localhost:8787/admin/events/{event-id}/onboarding`.
It refreshes a bounded D1 snapshot every five seconds, pauses while hidden, and
shows completion, overdue/due-soon speakers, submission states, evaluation
progress, and filterable task rows. The explicit event path is lookup input;
the server independently resolves its organization and enforces dashboard RBAC.

The speaker portal supports private asset upload at `http://localhost:8787/speaker`.
Headshots, slides, and supporting documents use
kind-specific MIME/size limits, browser SHA-256, a signed upload intent, the
local R2 binding, and an explicit completion step.

The local Worker streams quarantined bytes from R2 to the authenticated ClamAV
container through a fixed-length body; it does not copy a full 50 MiB object into
Worker memory. Only the scanner's signed clean result promotes the exact
generation. Deployed environments issue a direct R2 SigV4 PUT and publish a
versioned Queue job for the replay-safe scanner consumer, which uses the same
streaming adapter.

Evaluators may declare a conflict before finalization; the assignment is revoked
and exposed for admin reassignment. A round closes only after every selected
submission remains covered and every active assignment is final. The accepted
calculation, tie, immutability, and lifecycle rules are recorded in
`docs/product-status.md`.

Authenticate Wrangler without exposing host credentials to the container:

```bash
docker compose run --rm --no-deps worker npx wrangler login --device --browser=false
```

The OAuth credentials are stored in the Docker-managed `wrangler-config`
volume, not in the repository or host configuration directory.

Apply migrations and deploy the isolated Cloudflare development environment:

```bash
docker compose run --rm --no-deps worker npm run worker:migrate:dev
docker compose run --rm --no-deps worker npm run worker:deploy:dev
docker compose run --rm --no-deps worker npm run worker:preflight:dev
```

The deployment preflight is read-only. The stricter
`worker:activation:preflight:dev` command remains non-zero until email, direct
R2 upload credentials, and initial bootstrap are complete. Perform the one-time
bootstrap without exposing or retaining its token:

```bash
docker compose run --rm --no-deps worker npm run worker:bootstrap:dev -- \
  --organization-name "Example Events" \
  --admin-email "admin@example.com"
```

This creates a valid organization with no events. The administrator creates the
first event from `/admin/events`. Event name, start, end, and time zone can
still be supplied together to the bootstrap command when desired.

The commands below are available inside the container for focused development:

```bash
cp .dev.vars.example .dev.vars
npm ci
uv sync
uv run ruff check .
uv run pytest
uv run python scripts/generate_openapi.py
npm run worker:sync
npm run worker:dev
```

See [deployment configuration](docs/deployment-configuration.md) for Cloudflare variables, secrets, the one-time administrator bootstrap, and the development malware-scan bypass.
See [the nine-area delivery audit](docs/delivery-completion-audit.md) for current
local/live evidence and the exact remaining authenticated rehearsal.

`pywrangler sync` is the dependency compatibility gate. It resolves against the
Pyodide index selected by `compatibility_date`, writes the reviewed `pylock.toml`,
and installs generated Worker packages into ignored local directories. Commit
`pylock.toml`; do not commit `python_modules/` or `.venv-workers/`.

Apply the local D1 migration after Pywrangler is available:

```bash
npm run worker:migrate
```

Run the fast ASGI benchmark inside the worker container:

```bash
docker compose run --rm --no-deps worker uv run python scripts/benchmark_api.py \
  --output .local/benchmarks/engine-room.json
```

With the local Worker running, exercise Pyodide, Workerd, and the HTTP boundary:

```bash
docker compose run --rm --no-deps worker uv run python scripts/benchmark_api.py \
  --base-url http://worker:8787 \
  --output .local/benchmarks/engine-room-worker.json
```

Any API route can use the same benchmark contract. For example, benchmark the
deployed D1 probe from inside the container:

```bash
uv run python scripts/benchmark_api.py \
  --base-url https://sessionbuddy-development.shiny-cloud-dd47.workers.dev \
  --route /api/v1/engine-room/database \
  --output .local/benchmarks/engine-room-d1-cloudflare-dev.json
```

This host benchmark catches application-level regressions quickly. Release measurements must also run against `pywrangler dev` and an isolated deployed preview because only those environments exercise Pyodide, `workerd`, and real Cloudflare bindings.

Architecture and delivery requirements are documented under `docs/`. The
accepted platform gate and the contracts every feature must reuse are summarized
in `docs/product-status.md`.

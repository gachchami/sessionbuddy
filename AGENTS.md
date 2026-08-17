# SessionBuddy agent instructions

These instructions apply to the entire repository. More specific `AGENTS.md`
files may extend them within a subdirectory, but must not weaken the security,
data-isolation, or release requirements below.

## Product vocabulary and boundaries

Organize work by capability, not delivery waves:

- `platform`: authentication, authorization, persistence, storage, security,
  observability, and shared API contracts
- `console`: the operator-facing Engine Room at `/engine-room`
- `cfp`: programs, forms, and submissions
- `evaluation`: reviewer assignments, conflicts, evaluations, and decisions
- `speaker_operations`: onboarding, assets, communications, and reminders
- `scheduling`: agenda management, publication, and calendar delivery

`/engine-room` is an internal operational console, not the customer-facing
homepage. Do not add tenant, identity, submission, speaker, incident, or secret
data to its public response models.

SessionBuddy supports both fresh installations and upgrades of data-bearing
installations.

`migrations_baseline/0001_baseline.sql` is the canonical schema for a new
database. Never apply a changed baseline directly to an existing populated
database.

Schema changes after the current baseline must be delivered as ordered,
incremental migrations. Incremental migrations must:

- preserve existing tenant and application data;
- add nullable columns or safe defaults before enforcing stricter constraints;
- backfill new required values deterministically;
- resolve incompatible or duplicate historical rows before creating unique
  indexes;
- preserve foreign-key integrity throughout the transition;
- be safe under migration-runner locking and transactions;
- succeed on first application and report no migrations to apply on a repeat
  run;
- include tests covering both a fresh installation and an upgrade from the
  preceding supported schema;
- include backup, validation, rollback, and recovery instructions;
- never log, expose, or copy secrets into migration artifacts.

Do not edit an already released incremental migration. Add a new migration for
subsequent corrections.

The baseline may be periodically rebased to incorporate accumulated
incremental migrations, but only during an explicitly planned maintenance
window. A baseline rebase requires:

1. a verified backup of every affected database;
2. a successful restore rehearsal;
3. a documented downtime and rollback plan;
4. a fresh-install schema test;
5. an upgrade equivalence test proving that:
   `previous baseline + incremental migrations` produces the same schema and
   data semantics as the new baseline;
6. foreign-key, row-count, tenant-isolation, and critical-record validation;
7. retention of the previous database or backup until post-release validation
   is complete.

After a baseline rebase, archive the incorporated migration history according
to the migration runner’s documented procedure. Never delete migration
bookkeeping from a live database merely to make it resemble a fresh install.

## Working model

- One primary agent owns architecture, integration, and final verification.
- Use at most three parallel implementation agents after the shared foundation
  exists. Give each agent a bounded capability and non-overlapping files.
- Agents share one worktree. Inspect existing changes before editing and never
  overwrite unrelated user or agent work.
- Do not create branches, commits, deployments, paid resources, or external
  messages unless the user has authorized that action.

## Workspace conduct

The checkout is a shared working directory, not scratch space. These rules
govern agent conduct; they do not describe the current application
architecture.

- Inspect the current worktree before editing and re-check it during long
  tasks. Files may change while another agent or the user is working.
- Treat existing modifications and untracked files as someone else's work
  unless their ownership is established. Preserve them and avoid overlapping
  edits.
- Never use `git stash`, `git reset`, `git checkout --`, or `git clean` to make
  the tree convenient unless the user explicitly authorizes that exact action.
- Prefer read-only Git commands while investigating. Do not run Git mutations
  concurrently with another process in the same worktree.
- Never delete another process's Git lock. It may belong to an active process;
  report it and wait for its owner to finish. If this task created a stale lock,
  first prove that its owning process has ended before removing it.
- Do not leave archives, patch bundles, backup copies, throwaway scripts,
  caches, or generated operating-system files in the checkout. Gitignored
  directories are not general-purpose dumping grounds.
- Put necessary temporary work outside the checkout. Use `.local/` only for
  local evidence and manifests explicitly assigned there by repository tooling
  or runbooks, including benchmark, Lighthouse, and deployment evidence.
- Before finishing, remove artifacts created solely for the task. If removal
  is impossible, report every remaining path exactly.
- Verify changes in this checkout with the checked-in toolchain. Do not copy or
  mirror the repository elsewhere to manufacture verification evidence.
- If a required check cannot run, name the skipped check and the actual reason.

## Review discipline

- Establish what is being reviewed before drawing conclusions. For a commit,
  inspect the committed blob with `git show <sha>:<path>` and inspect its diff;
  `cat`, `sed`, editors, and ordinary file reads show the live worktree instead.
  For uncommitted work, inspect the live worktree and state that it may move
  underneath the review.
- Separate observed facts, inferences, resolved findings, and remaining risks.
  Do not present a hypothesis as a reproduced defect.
- Re-read files after unexpected movement in HEAD or the worktree. Do not
  attribute another task's in-flight edits to the change under review.
- Verify framework and runtime behavior rather than arbitrating from assumed
  defaults. Cloudflare Workerd, D1, Pyodide, FastAPI, browser, and test-double
  behavior may differ from local intuition.
- Test the test: confirm that a regression test reaches the behavior it names,
  fails when the defect is restored, and does not pass from string presence,
  an inert fake, or an unreachable branch.
- Prefer assertions that identify routes, states, or outcomes over unexplained
  event counts and other brittle magic numbers.
- Review generated artifacts and served assets when they are part of the
  runtime contract; source changes alone are not proof of deployed behavior.

## Design and design documents

- Design for people using plain language, obvious next actions, progressive
  disclosure, accessible semantics, and truthful system state. Do not expose
  implementation machinery merely because it exists.
- A durable tracked design document must declare its purpose, lifecycle status
  (`proposal`, `approved`, `in-progress`, `implemented`, `superseded`, or
  `archived`), and authority. It must use a stable descriptive filename and
  must not depend on dates, commit hashes, worktree state, delivery schedules,
  or another agent's conversation to remain understandable.
- Keep durable decisions separate from current observations, implementation
  steps, and historical evidence. Coalesce lasting product constraints into the
  appropriate design, contract, test, or product-status document instead of
  retaining a chronological review log in the repository.
- A task brief is temporary execution guidance, not a design record. It must
  begin by inspecting the current tree and must not claim that recorded file
  locations, implementation status, or verification results remain current.
- Do not put mutable stash references, dirty-worktree instructions, absolute
  machine paths, environment-specific target URLs, or conversational context
  into portable task instructions.
- Task briefs must state scope, non-goals, owned files or capabilities,
  dependencies, forbidden actions, acceptance criteria, verification commands,
  and stop conditions.
- Before following any historical design, verify the current tree, schema,
  routes, tests, and product status. Stop and report when the requested outcome
  is already implemented or the recorded contract has drifted.
- Repository instructions, current schema and API contracts, and executable
  tests outrank historical plans. More specific instructions may add context
  but may not weaken security, tenant isolation, migration, or release rules.

## Code quality

- Keep one authoritative normalization or derivation point for a concept.
  Repeated near-identical rules must be extracted or guarded by tests that
  prevent their behavior from drifting.
- Validate the content and semantics that make an operation safe, not merely
  the presence or shape of an argument.
- Fail closed at authorization, tenant, configuration, and persistence
  boundaries. Degraded UX must not silently broaden access or claim a write
  succeeded when persistence is uncertain.
- Before an irreversible operation, validate every identity, source, target,
  authorization fact, and configuration input needed to make it safe. Perform
  that validation before invoking the destructive dependency, and test that no
  destructive call occurs when preflight validation rejects the operation.
- Place error handling and observability at the layer that can still see the
  failure before completion is emitted. Preserve actionable structured errors
  without logging sensitive content.
- Use distinct stable error or telemetry codes for causes that require
  different operator or user responses.
- Declare every runtime and tooling dependency in the repository's supported
  dependency system. Do not rely on packages installed incidentally on a host.
- Prefer the smallest coherent change. Do not include formatter churn,
  generated-file noise, or unrelated cleanup in a functional change.
- Comments should record invariants, non-obvious constraints, and reasons that
  prevent recurrence. Do not preserve stale line references or restate code.

## Container-first development

Run project tooling in the checked-in Docker Compose environment. Do not rely on
host Python or Node installations as release evidence.

Common commands:

```sh
docker compose up --build --detach worker
docker compose run --rm --no-deps worker npm run worker:migrate
docker compose run --rm --no-deps worker npm run frontend:check
docker compose run --rm --no-deps worker npm run frontend:build
docker compose run --rm --no-deps worker uv run ruff check .
docker compose run --rm --no-deps worker uv run pytest -q
docker compose run --rm e2e
```

Use `scripts/release_gate.sh` for a complete local release rehearsal. It must
remain non-deploying. Keep generated benchmark and Lighthouse artifacts under
ignored `.local/` directories.

After changing packaged HTML, CSS, or JavaScript, regenerate and verify embedded
assets:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/embed_console_assets.py
```

After changing API routes or response models, regenerate `openapi/openapi.json`.

## Cloudflare compatibility

- The deployed application is a Python Worker running through Workerd/Pyodide.
- For any D1 task, check transaction support first. D1's Wrangler/SQL execution
  path does not accept raw SQL `BEGIN TRANSACTION`, `COMMIT`, or `SAVEPOINT`;
  use the runtime storage transaction APIs when atomic transactions are needed,
  or design Wrangler-operated maintenance as dependency-ordered, restartable
  statements with backup and post-operation verification.
- Treat `wrangler.jsonc`, `pylock.toml`, and the pinned compatibility date as
  reviewed release inputs.
- Verify packaging with a Wrangler/pywrangler dry run before deployment.
- Keep D1, R2, Queue, Workflow, rate-limit, and secret bindings isolated by
  environment. Never point local or staging code at production resources.
- Store local secrets only in ignored `.dev.vars`. Store deployed secrets with
  Cloudflare secret bindings; never commit them in Wrangler variables.
- A staging or production deploy requires successful migrations, workflow
  smokes, accessibility checks, and rollback/backup evidence.

## Security and data rules

- Default an absent or unknown `APP_ENV` to production-safe, fail-closed
  behavior. Demo sessions, local delivery, and synthetic privileged identities
  must require an explicit local environment.
- Enforce authentication, tenant scope, and RBAC on the server for every
  protected operation. UI visibility is not authorization.
- Preserve CSRF/origin checks, secure session cookies, rate limits, structured
  error envelopes, request IDs, audit records, and idempotency conventions.
- Never log or expose credentials, tokens, cookies, form content, private asset
  URLs, malware samples, or personal data.
- Uploads remain quarantined until type/size validation and malware scanning
  succeed. Private downloads require short-lived grants.
- Final evaluation decisions are immutable. Changing a final decision requires
  an explicit audited correction workflow, not an ordinary update endpoint.

## Performance, observability, and accessibility

- Every new API must emit the shared request ID, error envelope, route-level
  timing, and safe structured telemetry.
- Benchmark by route template and record environment, deployment version,
  dataset version, concurrency, cold/warm state, response size, failure classes,
  and p50/p75/p95/p99 latency.
- Add query-plan or large-dataset evidence for new list, dashboard, assignment,
  or scheduling queries.
- New user-facing pages must pass desktop and mobile Playwright/Axe checks,
  keyboard navigation, focus visibility, reduced-motion behavior, semantic
  labels, and responsive layout review.
- Do not call a performance regression fixed without comparable measurements.

## Completion standard

Before handing off a change:

1. Run the narrowest relevant tests while iterating.
2. Run Ruff, frontend type checking when applicable, and the full Python suite.
3. Run relevant capability smoke tests through the local Worker.
4. Run browser tests for UI changes.
5. Regenerate embedded assets and OpenAPI artifacts when their sources change.
6. Run `git diff --check` and report any verification that could not be run.
7. Update `docs/product-status.md` when capability or release status changes.

After any schema change:

1. validate a fresh database created from the canonical baseline;
2. validate an upgrade from the immediately preceding supported schema;
3. run the migration twice and prove the second run is a no-op;
4. run `PRAGMA foreign_key_check`;
5. compare expected table, index, trigger, and critical row counts;
6. record backup and rollback evidence before deployment.

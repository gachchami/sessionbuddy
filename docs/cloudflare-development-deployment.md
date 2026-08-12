# Deploying SessionBuddy to the Cloudflare development environment

This runbook describes the checked-in `dev` deployment only. It deploys the
main SessionBuddy Worker and the independent activity-projection Worker to the
isolated Cloudflare development resources configured in
[`wrangler.jsonc`](../wrangler.jsonc) and
[`wrangler.activity.jsonc`](../wrangler.activity.jsonc).

Run all commands from the repository root:

```sh
cd /Users/superman/playground/projects/sessionbuddy
```

This procedure changes remote Cloudflare resources. It is separate from the
non-deploying local release gate and requires explicit deployment authority.

## 1. Development deployment inventory

The checked-in `dev` environment currently targets:

| Resource | Development value |
| --- | --- |
| Main Worker | `sessionbuddy-development` |
| Activity Worker | `sessionbuddy-activity-development` |
| Public origin | `https://sessionbuddy-development.shiny-cloud-dd47.workers.dev` |
| D1 database | `sessionbuddy-development-clean` |
| D1 database ID | `84c13579-447f-4794-b3d3-674af424c17a` |
| R2 bucket | `sessionbuddy-assets-development` |
| Reminder Workflow | `sessionbuddy-reminders-development` |
| Activity queue | `sessionbuddy-activity-development` |
| Activity dead-letter queue | `sessionbuddy-activity-development-dlq` |

The main Worker also binds the asset-scan and communication queues and their
dead-letter queues as declared in `wrangler.jsonc`. Rate-limit bindings,
scheduled triggers, Workers AI, D1, R2, Queue, and Workflow configuration are
part of the checked-in Wrangler files and must be reviewed as release inputs.

The development environment intentionally sets:

```text
APP_ENV=development
MALWARE_SCAN_MODE=disabled
SKIP_PROFILE_ONBOARDING=true
```

The scanning bypass is acceptable only for this isolated development Worker.
Do not copy it to preview, staging, production, or an unknown environment.

## 2. Required local prerequisites

- Docker Engine with Docker Compose.
- Authorization to deploy to the Cloudflare account referenced by
  `wrangler.jsonc`.
- Existing development D1, R2, Queue/DLQ, Workflow, rate-limit, and Workers AI
  resources matching the checked-in bindings.
- An active verified Resend sender when real email delivery is required.
- No unreviewed change to `migrations_baseline/0001_baseline.sql` on a
  data-bearing development database.

Wrangler is pinned by `package-lock.json` and runs inside the `worker`
container. Do not substitute an unpinned host Wrangler installation as release
evidence.

## 3. Authenticate Wrangler

The Compose volume `sessionbuddy_wrangler-config` retains the CLI login without
copying host credentials into the container.

Start device login:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler login --device --browser=false
```

Open the URL printed by Wrangler, approve the login, then verify the active
account:

```sh
docker compose run --rm --no-deps worker npx wrangler whoami
```

Stop if the displayed account is not the intended development account.

## 4. Install or rotate development secrets

Secrets belong in Cloudflare's encrypted Worker secret store. Never place them
in `wrangler.jsonc`, `.dev.vars`, shell history, command arguments, source, or
chat.

Set each secret interactively for the main Worker:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler secret put SESSION_HMAC_KEY --env dev
docker compose run --rm --no-deps worker \
  npx wrangler secret put CSRF_HMAC_KEY --env dev
docker compose run --rm --no-deps worker \
  npx wrangler secret put RATE_LIMIT_HMAC_KEY --env dev
docker compose run --rm --no-deps worker \
  npx wrangler secret put UPLOAD_HMAC_KEY --env dev
docker compose run --rm --no-deps worker \
  npx wrangler secret put RESEND_API_KEY --env dev
docker compose run --rm --no-deps worker \
  npx wrangler secret put R2_ACCESS_KEY_ID --env dev
docker compose run --rm --no-deps worker \
  npx wrangler secret put R2_SECRET_ACCESS_KEY --env dev
```

Each HMAC value must be independent and contain at least 32 bytes of random
material. The R2 credentials must be scoped to the development bucket. A
`SCANNER_HMAC_KEY` is unnecessary while the checked-in development environment
uses `MALWARE_SCAN_MODE=disabled`; it becomes mandatory if scanning is enabled.

List secret names without revealing their values:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler secret list --env dev
```

Secret installation is an initialization/rotation step, not something to repeat
for every code deployment.

## 5. Run the local release gate first

```sh
scripts/release_gate.sh
```

Do not continue unless it ends with:

```text
Release readiness local release gate passed. No remote deployment was performed.
```

The complete local sequence is documented in
[`local-development-and-release-gate.md`](local-development-and-release-gate.md).

## 6. Prepare generated artifacts

Run these commands in order:

```sh
docker compose run --rm --no-deps worker npm run frontend:check
docker compose run --rm --no-deps worker npm run frontend:build
docker compose run --rm --no-deps worker \
  uv run python scripts/embed_console_assets.py
docker compose run --rm --no-deps worker \
  uv run python scripts/embed_console_assets.py --check
docker compose run --rm --no-deps worker \
  uv run python scripts/generate_openapi.py
docker compose run --rm --no-deps worker \
  npm run worker:migrations:baseline:check
git diff --check
```

Commit or otherwise preserve reviewed generated changes before deployment.

## 7. Produce non-deploying package dry runs

Validate both Worker packages before changing remote state:

```sh
docker compose run --rm --no-deps worker \
  uv run pywrangler deploy --env dev --dry-run \
  --outdir /workspace/.local/package-dry-run-main
```

```sh
docker compose run --rm --no-deps worker \
  uv run pywrangler deploy --config wrangler.activity.jsonc --env dev \
  --dry-run --outdir /workspace/.local/package-dry-run-activity
```

Validate the main package with the checked-in validator:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/validate_worker_package.py \
  /workspace/.local/package-dry-run-main
```

These commands build deployment packages but do not deploy them.

## 8. Apply the D1 baseline safely

SessionBuddy supports fresh installations only. The sole canonical schema is
`migrations_baseline/0001_baseline.sql`; there is no incremental compatibility
upgrade path.

Before applying it, inspect the remote migration state:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler d1 migrations list DB --remote --env dev
```

If the baseline file changed and the remote database contains business data,
stop. Do not run the changed baseline against that database. Provision a fresh
development D1 database, update the same new database ID in both Wrangler
files, review the replacement, and only then continue.

For a new database, or when the checked-in baseline is unchanged and Wrangler
reports the expected migration state, apply it with:

```sh
docker compose run --rm --no-deps worker npm run worker:migrate:dev
```

Confirm that nothing remains pending:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler d1 migrations list DB --remote --env dev
```

## 9. Apply development R2 CORS

The checked-in policy permits browser `PUT` uploads only from the exact
development Worker origin, permits `Content-Type`, and exposes `ETag`.

```sh
docker compose run --rm --no-deps worker npm run worker:r2-cors:dev
```

Do not replace the exact origin with `*` for authenticated speaker uploads.

## 10. Deploy both Workers

Deploy the activity projector first so the independent consumer is available
before the main application emits new activity work:

```sh
docker compose run --rm --no-deps worker npm run activity:deploy:dev
```

Then deploy the main Worker:

```sh
docker compose run --rm --no-deps worker npm run worker:deploy:dev
```

These are the first commands in this procedure that publish application code.

## 11. Run deployment preflight

The deployment-only preflight is read-only. It checks configuration, bindings,
remote migration state, Worker reachability, secrets inventory, queues, R2
CORS, and the deployed origin without changing them.

```sh
docker compose run --rm --no-deps worker npm run worker:preflight:dev
```

Resolve every `FAIL` before continuing. `PENDING` activation items can be
expected only on an intentionally fresh environment.

## 12. Bootstrap a fresh development database once

Skip this section when the environment is already bootstrapped.

The D1 baseline generates a one-time setup key. For an interactive setup,
retrieve it in a private terminal:

```sh
docker compose run --rm --no-deps worker npm run worker:setup-key:dev
```

Open the deployed `/setup` page, enter that key, then provide the organization
and initial administrator details. Successful setup permanently consumes the
key.

For controlled non-interactive automation, use:

```sh
docker compose run --rm --no-deps worker npm run worker:bootstrap:dev -- \
  --organization-name "Example Events" \
  --admin-first-name "Example" \
  --admin-last-name "Administrator" \
  --admin-email "admin@example.com"
```

Replace every example value. The bootstrap command reads the setup key without
printing it and refuses to create a second organization. It intentionally does
not create a sample event.

## 13. Run the strict activation preflight

```sh
docker compose run --rm --no-deps worker \
  npm run worker:activation:preflight:dev
```

Do not call the environment fully activated until this reports zero failed and
zero pending checks. It includes real-email readiness, direct R2 upload
credentials, R2 CORS, remote bindings, and completed bootstrap state.

## 14. Post-deployment smoke checks

Confirm the public health route first:

```sh
curl --fail --silent \
  https://sessionbuddy-development.shiny-cloud-dd47.workers.dev/health
```

Then verify these user journeys against the development origin:

1. Request and redeem a delivered passwordless sign-in link.
2. Open `/admin` and create or open an event.
3. Publish a CFP, save a draft, and submit a proposal.
4. Invite an organizer or reviewer and exercise acceptance/revocation.
5. Open `/speaker` with the correct speaker identity and event isolation.
6. Upload a development asset directly to R2 and verify promotion.
7. Confirm that the corresponding activity appears after the activity Worker
   processes it.

Do not paste magic-link fragments, session cookies, setup keys, upload grants,
or provider responses into logs or deployment notes.

## 15. Observe and recover

### Reset eval-created development data

To return an already bootstrapped development environment to a clean eval
state while retaining its sole administrator, organization, membership,
organizer role, profile, password credential, and completed setup marker, run:

```sh
docker compose run --rm --no-deps worker \
  npm run worker:reset-data:dev -- \
  --confirm sessionbuddy-development-clean
```

To reset the equivalent Wrangler-managed local D1 state, with the same backup,
confirmation, bootstrap-shape, and post-reset verification safeguards, run:

```sh
docker compose stop worker activity-worker activity-poller
docker compose run --rm --no-deps worker \
  npm run worker:reset-data:local -- \
  --confirm sessionbuddy-local
docker compose up --build --detach worker
```

The local command never contacts or mutates the remote development D1. Local
backups include `-local-before-reset-` in their ignored `.local/backups/`
filename; remote backups include `-remote-before-reset-`.
Stop the local Worker processes first so no request or activity projection can
race the schema replacement. Starting `worker` afterward also starts the
activity dependencies declared by Compose.

Both commands require one exact active bootstrap administrator and an exact
database-name confirmation. Before destructive work they export a complete SQL
backup and a second, explicit-column bootstrap bundle beneath ignored
`.local/backups/`; both files are mode `0600` and reported with SHA-256 hashes.
The bundle contains only the organization, administrator user, complete active
password credential row, organization-admin membership, organizer role,
optional matching person/headshot rows, organization ownership row, and the
completed setup marker. It never contains sessions, challenges, invitations,
events, proposals, or evaluation data.

Local reset drops every application table including `d1_migrations`, reapplies
the canonical baseline, proves the repeat apply is a no-op, deletes the newly
generated unused setup credential, restores the bootstrap bundle, and verifies
that all operational tables are empty. It leaves local R2, rate-limit, workflow,
and secret state alone.

Remote reset deletes and recreates the development D1 in APAC by default
(`--location` can override the location hint), updates the exact old UUID once
in both `wrangler.jsonc` and `wrangler.activity.jsonc`, applies and verifies the
baseline, restores and verifies the bootstrap bundle, and deploys both Workers
so neither continues using the deleted binding. A failure after remote deletion
is intentionally loud: use the printed full backup and bootstrap bundle to
complete the roll-forward; never point either Worker back to the deleted UUID.

This is destructive for event, CFP, proposal, evaluation, speaker, scheduling,
audit, activity, invitation, and session data. It does not rotate application
secrets or recompute the password verifier; the complete credential row retains
its existing PHC salt and pepper version, while the pepper itself remains in
the deployed secret binding.

Stream main Worker logs:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler tail sessionbuddy-development --format json
```

Stream activity Worker logs in a second terminal:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler tail sessionbuddy-activity-development --format json
```

Inspect deployed versions before any rollback:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler versions list --env dev
```

Rollback is a separate destructive production-control decision. Confirm the
exact Worker, version, schema compatibility, and current impact before running
it. Because the schema has no downgrade path, recover schema problems by an
explicit reviewed roll-forward or by replacing the disposable development D1
database; never improvise a destructive down migration.

## 16. Exact serial command summary

For an already provisioned and activated development environment whose baseline
schema has not changed, the deployment sequence is:

```sh
docker compose run --rm --no-deps worker npx wrangler whoami
scripts/release_gate.sh
docker compose run --rm --no-deps worker npm run frontend:check
docker compose run --rm --no-deps worker npm run frontend:build
docker compose run --rm --no-deps worker \
  uv run python scripts/embed_console_assets.py
docker compose run --rm --no-deps worker \
  uv run python scripts/embed_console_assets.py --check
docker compose run --rm --no-deps worker \
  uv run python scripts/generate_openapi.py
docker compose run --rm --no-deps worker \
  npm run worker:migrations:baseline:check
git diff --check
docker compose run --rm --no-deps worker \
  uv run pywrangler deploy --env dev --dry-run \
  --outdir /workspace/.local/package-dry-run-main
docker compose run --rm --no-deps worker \
  uv run pywrangler deploy --config wrangler.activity.jsonc --env dev \
  --dry-run --outdir /workspace/.local/package-dry-run-activity
docker compose run --rm --no-deps worker \
  uv run python scripts/validate_worker_package.py \
  /workspace/.local/package-dry-run-main
docker compose run --rm --no-deps worker \
  npx wrangler d1 migrations list DB --remote --env dev
docker compose run --rm --no-deps worker npm run worker:migrate:dev
docker compose run --rm --no-deps worker \
  npx wrangler d1 migrations list DB --remote --env dev
docker compose run --rm --no-deps worker npm run worker:r2-cors:dev
docker compose run --rm --no-deps worker npm run activity:deploy:dev
docker compose run --rm --no-deps worker npm run worker:deploy:dev
docker compose run --rm --no-deps worker npm run worker:preflight:dev
docker compose run --rm --no-deps worker \
  npm run worker:activation:preflight:dev
curl --fail --silent \
  https://sessionbuddy-development.shiny-cloud-dd47.workers.dev/health
```

Re-run the focused authenticated smoke journeys after the health check. Record
the deployed Worker versions, migration result, preflight totals, smoke results,
and rollback reference in the release evidence.

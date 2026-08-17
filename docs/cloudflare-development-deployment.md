# Deploying SessionBuddy to the Cloudflare development environment

This runbook deploys either `dev1` or `dev2` using the same workflow. The
checked-in [`wrangler.jsonc`](../wrangler.jsonc) and
[`wrangler.activity.jsonc`](../wrangler.activity.jsonc) are local-development
templates only. Concrete Cloudflare resources are rendered from an ignored
target manifest; no deployment command may default to a target.

Run all commands from the repository root:

```sh
cd /Users/superman/playground/projects/sessionbuddy
```

This procedure changes remote Cloudflare resources. It is separate from the
non-deploying local release gate and requires explicit deployment authority.

## 1. Select and render one deployment target

```sh
TARGET=dev1 # or dev2; required
test -f ".local/deployments/$TARGET.local"
docker compose run --rm --no-deps worker \
  uv run python scripts/render_private_cloudflare_config.py \
  --manifest ".local/deployments/$TARGET.local" \
  --main-output "wrangler.$TARGET.private.jsonc" \
  --activity-output "wrangler.activity.$TARGET.private.jsonc" \
  --cors-output "r2-cors.$TARGET.private.json"
MAIN_CONFIG="wrangler.$TARGET.private.jsonc"
ACTIVITY_CONFIG="wrangler.activity.$TARGET.private.jsonc"
CORS_CONFIG="r2-cors.$TARGET.private.json"
PUBLIC_BASE_URL=$(docker compose run --rm --no-deps worker \
  uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["vars"]["PUBLIC_BASE_URL"])' \
  "$MAIN_CONFIG")
R2_BUCKET_NAME=$(docker compose run --rm --no-deps worker \
  uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["vars"]["R2_BUCKET_NAME"])' \
  "$MAIN_CONFIG")
```

Read the rendered configs and record the main Worker, activity Worker, public
origin, D1 name and UUID, R2 bucket, queues/DLQs, Workflow, rate-limit namespace
IDs, and target commit. Compare the D1 UUID with `wrangler versions view` for
the currently deployed main Worker. Abort before backup if any identity differs.

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
- Authorization to deploy to the Cloudflare account referenced by the selected
  private manifest.
- Existing development D1, R2, Queue/DLQ, Workflow, rate-limit, and Workers AI
  resources matching the selected rendered configs.
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
  npx wrangler secret put SESSION_HMAC_KEY --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  npx wrangler secret put CSRF_HMAC_KEY --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  npx wrangler secret put RATE_LIMIT_HMAC_KEY --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  npx wrangler secret put UPLOAD_HMAC_KEY --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  npx wrangler secret put RESEND_API_KEY --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  npx wrangler secret put R2_ACCESS_KEY_ID --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  npx wrangler secret put R2_SECRET_ACCESS_KEY --config "$MAIN_CONFIG"
```

Each HMAC value must be independent and contain at least 32 bytes of random
material. The R2 credentials must be scoped to the development bucket. A
`SCANNER_HMAC_KEY` is unnecessary while the checked-in development environment
uses `MALWARE_SCAN_MODE=disabled`; it becomes mandatory if scanning is enabled.

List secret names without revealing their values:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler secret list --config "$MAIN_CONFIG"
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
  uv run pywrangler deploy --config "$MAIN_CONFIG" --dry-run \
  --outdir /workspace/.local/package-dry-run-main
```

```sh
docker compose run --rm --no-deps worker \
  uv run pywrangler deploy --config "$ACTIVITY_CONFIG" \
  --dry-run --outdir /workspace/.local/package-dry-run-activity
```

Validate the main package with the checked-in validator:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/validate_worker_package.py \
  /workspace/.local/package-dry-run-main
```

These commands build deployment packages but do not deploy them.

Before any remote mutation, run the deployment-only preflight against the
selected config:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/cloudflare_preflight.py \
  --config "$MAIN_CONFIG" --deployment-only --allow-pending-migrations
```

Stop here on every `FAIL`. This validates the selected account/resource
identity, static configuration, remote migration state, queues, secrets, R2
policy, and currently deployed origin before migrations, CORS, or deployment
can change that target. A deliberately fresh target may retain documented
`PENDING` activation items.

## 8. Apply D1 migrations safely

`migrations_baseline/0001_baseline.sql` is immutable. Later numbered migrations
upgrade data-bearing databases in order and must preserve existing data.

Before applying it, inspect the remote migration state:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler d1 migrations list DB --remote --config "$MAIN_CONFIG"
```

If `0001_baseline.sql` changed, stop: do not run it against that database. For a
new incremental migration, export and checksum a backup, rehearse restoration,
then apply only after fresh-chain and preceding-schema upgrade tests pass.

For a new database, or when the checked-in baseline is unchanged and Wrangler
reports the expected migration state, apply it with:

```sh
docker compose run --rm --no-deps worker uv run pywrangler d1 migrations apply DB --remote --config "$MAIN_CONFIG"
```

Confirm that nothing remains pending:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler d1 migrations list DB --remote --config "$MAIN_CONFIG"
```

## 9. Apply development R2 CORS

The rendered private policy permits browser `PUT` uploads only from the exact
selected Worker origin, permits `Content-Type`, and exposes `ETag`. Read
`R2_BUCKET_NAME` from `MAIN_CONFIG`, verify it against the selected manifest,
then apply the generated policy:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler r2 bucket cors set "$R2_BUCKET_NAME" \
  --file "$CORS_CONFIG" --config "$MAIN_CONFIG" --force
```

Do not replace the exact origin with `*` for authenticated speaker uploads.

## 10. Deploy both Workers

Deploy the activity projector first so the independent consumer is available
before the main application emits new activity work:

```sh
docker compose run --rm --no-deps worker uv run pywrangler deploy --config "$ACTIVITY_CONFIG"
```

Then deploy the main Worker:

```sh
docker compose run --rm --no-deps worker uv run pywrangler deploy --config "$MAIN_CONFIG"
```

These are the first commands in this procedure that publish application code.

## 11. Rerun deployment preflight

Rerun the same read-only deployment preflight after both Workers deploy. It
checks that the selected bindings, migration state, Worker reachability,
secrets inventory, queues, R2 CORS, and deployed origin still agree.

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/cloudflare_preflight.py \
  --config "$MAIN_CONFIG" --deployment-only
```

Resolve every `FAIL` before continuing. `PENDING` activation items can be
expected only on an intentionally fresh environment.

## 12. Bootstrap a fresh development database once

Skip this section when the environment is already bootstrapped.

The D1 baseline generates a one-time setup key. Read it only through the
selected config:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/setup_key.py --config "$MAIN_CONFIG"
```

Open the deployed `/setup` page, enter that key, then provide the organization
and initial administrator details. Successful setup permanently consumes the
key.

Replace every example value. The bootstrap command reads the setup key without
printing it and refuses to create a second organization. It intentionally does
not create a sample event.

## 13. Run the strict activation preflight

Run the strict activation checks against the selected origin and bindings:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/cloudflare_preflight.py --config "$MAIN_CONFIG"
```

Do not call the environment fully activated until this reports zero failed and
zero pending checks. It includes real-email readiness, direct R2 upload
credentials, R2 CORS, remote bindings, and completed bootstrap state.

## 14. Post-deployment smoke checks

Confirm the public health route first:

```sh
curl --fail --silent "${PUBLIC_BASE_URL}/health"
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

The remote reset helper is not target-aware and must not be used with private
rendered configs. A reset requires a separate reviewed procedure that resolves
the exact D1 UUID from `MAIN_CONFIG`, takes and checksums a backup, rehearses
recovery, and verifies the retained bootstrap records. Never infer the target
from a `dev` label.

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

Remote reset must never edit tracked Wrangler templates. It must update the
selected ignored manifest, re-render both private configs, and deploy both
Workers so neither continues using a deleted binding.

This is destructive for event, CFP, proposal, evaluation, speaker, scheduling,
audit, activity, invitation, and session data. It does not rotate application
secrets or recompute the password verifier; the complete credential row retains
its existing PHC salt and pepper version, while the pepper itself remains in
the deployed secret binding.

Stream main Worker logs:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler tail --config "$MAIN_CONFIG" --format json
```

Stream activity Worker logs in a second terminal:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler tail --config "$ACTIVITY_CONFIG" --format json
```

Inspect deployed versions before any rollback:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler versions list --config "$MAIN_CONFIG"
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
  uv run pywrangler deploy --config "$MAIN_CONFIG" --dry-run \
  --outdir /workspace/.local/package-dry-run-main
docker compose run --rm --no-deps worker \
  uv run pywrangler deploy --config "$ACTIVITY_CONFIG" \
  --dry-run --outdir /workspace/.local/package-dry-run-activity
docker compose run --rm --no-deps worker \
  uv run python scripts/validate_worker_package.py \
  /workspace/.local/package-dry-run-main
docker compose run --rm --no-deps worker \
  uv run python scripts/cloudflare_preflight.py --config "$MAIN_CONFIG" \
  --deployment-only --allow-pending-migrations
docker compose run --rm --no-deps worker \
  npx wrangler d1 migrations list DB --remote --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker uv run pywrangler d1 migrations apply DB --remote --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  npx wrangler d1 migrations list DB --remote --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  npx wrangler r2 bucket cors set "$R2_BUCKET_NAME" \
  --file "$CORS_CONFIG" --config "$MAIN_CONFIG" --force
docker compose run --rm --no-deps worker uv run pywrangler deploy --config "$ACTIVITY_CONFIG"
docker compose run --rm --no-deps worker uv run pywrangler deploy --config "$MAIN_CONFIG"
docker compose run --rm --no-deps worker \
  uv run python scripts/cloudflare_preflight.py --config "$MAIN_CONFIG" --deployment-only
curl --fail --silent "${PUBLIC_BASE_URL}/health"
```

Re-run the focused authenticated smoke journeys after the health check. Record
the deployed Worker versions, migration result, preflight totals, smoke results,
and rollback reference in the release evidence.

# Deployment configuration

SessionBuddy keeps only local-development templates in `wrangler.jsonc` and
`wrangler.activity.jsonc`. Concrete deployed resource names, UUIDs, account
identifiers, origins, and sender configuration belong in ignored private
manifests under `.local/deployments/`. API credentials remain in Cloudflare's
encrypted Worker secret store and never belong in either manifest or Wrangler
file. The initial administrator email is supplied once to the guarded bootstrap
API and then stored as a verified user.

Copy [`deployment.private.example`](../deployment.private.example) to either
`.local/deployments/dev1.local` or `.local/deployments/dev2.local`, fill every
value, and render concrete ignored configs:

```sh
TARGET=dev1 # or dev2; never default this value
docker compose run --rm --no-deps worker \
  uv run python scripts/render_private_cloudflare_config.py \
  --manifest ".local/deployments/$TARGET.local" \
  --main-output "wrangler.$TARGET.private.jsonc" \
  --activity-output "wrangler.activity.$TARGET.private.jsonc" \
  --cors-output "r2-cors.$TARGET.private.json"
```

Set these for the rest of the workflow:

```sh
MAIN_CONFIG="wrangler.$TARGET.private.jsonc"
ACTIVITY_CONFIG="wrangler.activity.$TARGET.private.jsonc"
CORS_CONFIG="r2-cors.$TARGET.private.json"
```

Before any backup, migration, or deployment, inspect both rendered files and
compare their Worker names and D1 UUID with the currently deployed Worker's
bindings. Stop on any mismatch. Never use a generic `--env dev` deployment.

## Required settings

Set these non-secret but private deployment values in the target manifest:

- `APP_ENV`: `development`, `staging`, or `production`.
- `PUBLIC_BASE_URL`: the exact HTTPS origin used in email links.
- `ALLOWED_ORIGINS`: the same origin, or a comma-separated allowlist for a separate frontend.
- `MALWARE_SCAN_MODE`: `disabled` works only in explicit `local` or
  `development` environments. Preview, staging, production, and unknown
  environments fail closed and require scanning.
- `RESEND_FROM_ADDRESS`: the verified sender shown to recipients.
- `SCANNER_URL`: required when malware scanning is enabled.
- `SKIP_PROFILE_ONBOARDING`: when `true`, local and development magic links
  complete immediately and sessions bypass the first-login profile redirect,
  without changing stored profile data. The setting is ignored in staging and
  production. Use it only for disposable automated-evaluation environments.
- `CLOUDFLARE_ACCOUNT_ID` and `R2_BUCKET_NAME`: non-secret identifiers used to
  generate direct-upload URLs.

Every deployed environment must also bind the checked-in Cloudflare rate-limit
namespaces. `SPEAKER_UPLOAD_AUTH_RATE_LIMITER` permits three new speaker upload
authorizations per user/event each minute, and `HEADSHOT_UPLOAD_RATE_LIMITER`
permits three synchronous account-headshot scans per user each minute. Missing
bindings fail closed with 503.
Magic-link delivery uses two independent namespaces:
`MAGIC_LINK_RECIPIENT_RATE_LIMITER` permits three requests per normalized
recipient each minute regardless of source address, while
`MAGIC_LINK_SOURCE_RATE_LIMITER` permits ten requests per source each minute
regardless of recipient. Password sign-in continues to use `AUTH_RATE_LIMITER`.

Install secrets through the selected rendered main config:

```sh
docker compose run --rm --no-deps worker \
  npx wrangler secret put NAME --config "$MAIN_CONFIG"
```

- `SESSION_HMAC_KEY`, `CSRF_HMAC_KEY`, `RATE_LIMIT_HMAC_KEY`, and `UPLOAD_HMAC_KEY`: independent random values of at least 32 bytes.
- `RESEND_API_KEY`: required for real magic-link and notification email.
- `SCANNER_HMAC_KEY`: required only when the scanner is enabled.
- `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`: scoped R2 S3 credentials required
  for browser-to-R2 upload authorization.

`RESEND_API_KEY` and `RESEND_FROM_ADDRESS` are site-level settings owned by the
SessionBuddy operator. Organization administrators never enter provider secrets.
When creating or editing an event, they can set an optional sender display name
and reply-to email. Event messages combine that identity with the site's verified
sending address; blank event fields inherit the site defaults.

Never put real deployment identifiers, secrets, or the administrator email in
tracked Wrangler files, and never commit `.dev.vars`, `.local/deployments/`, or
rendered `*.private.jsonc` files.

Keep the selected manifest's Resend domain verification active, or replace
`RESEND_FROM_ADDRESS` with another address on a verified domain.

## Initial administrator bootstrap

Fresh deployments start with the immutable
`migrations_baseline/0001_baseline.sql`, then apply every later numbered SQL
migration in order. The baseline contains no organizations, users, events,
submissions, evaluation data, or `d1_migrations` bookkeeping. Verify the complete
chain inside Docker after every schema change:

```sh
docker compose run --rm --no-deps worker npm run worker:migrations:baseline:check
```

Every rendered Wrangler config points its database at the ordered ledger in
`migrations_baseline/`. Released migration files are runtime inputs and must not
be edited or removed. `migrations_baseline/checksums.sha256` pins every released
migration and CI rejects changed, missing, or unrecorded files. Existing
databases apply only migrations not already recorded by D1; fresh databases
apply the whole chain.

Before upgrading a data-bearing database, export a D1 backup and record its
checksum. After migration, verify `PRAGMA foreign_key_check`, critical row
counts, and the new schema objects. Rollback means restoring that verified
backup to the previous application version; do not reverse a partially applied
schema with ad-hoc SQL. Keep the backup until post-release validation completes.

Applying the D1 baseline creates a random 256-bit, instance-specific setup key.
Retrieve or rotate the private setup key only through the selected target config:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/setup_key.py --config "$MAIN_CONFIG"
```

Open `/setup`, enter that same key, and provide the organization name plus the
administrator's first name, last name, and email. The setup creates no sample events or
speakers. To invalidate a key before setup and generate a replacement:

Retrieval and regeneration are refused after setup completes. Successful setup
deletes the key in the same D1 transaction, and database triggers prevent another
key from being inserted or rotated while the permanent completion marker exists.
The key is never returned by the Worker API.

The endpoint refuses a second organization. The command reads the migration-generated
key without printing it. Browser setup requests the administrator's first magic
link automatically and remains on a check-your-email confirmation instead of
sending the administrator through the homepage or another sign-in request. An
event is intentionally optional; the initial administrator
creates the first event from the empty-state UI.

Sign-in and invitation emails place the single-use bearer token in the URL
fragment (`/auth/verify#token=...`). Fragments are not sent to the Worker or in
HTTP referrers. The confirmation page removes the fragment from browser history,
copies it into a hidden same-origin form field, and waits for an explicit click;
the token is redeemed only from the POST body after exact-Origin validation.

Completion is also recorded by an atomic, singleton D1 marker. The marker is
claimed in the same transaction as the first organization and administrator,
so concurrent setup attempts cannot both succeed. It is independent of business
records: deleting or archiving organizations does not reopen first-time setup.
The database rejects changing or deleting this marker through normal SQL.

## Private development deployments with scanning disabled

Each private development manifest renders `APP_ENV=development` and
`MALWARE_SCAN_MODE=disabled`, so development uploads bypass malware scanning.
This is an accepted risk for the isolated development Worker and must never be
copied into preview, staging, or production. Those environments require a real,
reachable scanner endpoint and `SCANNER_HMAC_KEY`.

Confirm the rendered `PUBLIC_BASE_URL`, `ALLOWED_ORIGINS`, Worker name, and D1
UUID are the exact selected target, then run the target-specific workflow in
[`cloudflare-development-deployment.md`](cloudflare-development-deployment.md).

The target-specific runbook contains the release checks, backup, migration,
deployment, and validation commands. After activation, check a delivered
passwordless sign-in, a CFP draft/submission, an invitation, and an R2 upload.
The code intentionally does not invent or commit provider/account values.

The renderer emits a private R2 policy that allows browser `PUT` uploads only
from the selected Worker's exact origin with `Content-Type`, and exposes only
`ETag`. Never use a wildcard origin for authenticated speaker uploads.

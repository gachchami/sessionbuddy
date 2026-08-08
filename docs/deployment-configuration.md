# Deployment configuration

SessionBuddy keeps public deployment settings in `wrangler.jsonc` and secrets in Cloudflare's encrypted Worker secret store. The initial administrator email is not an environment variable: it is supplied once to the guarded bootstrap API and then stored as a verified user.

## Required settings

Set these non-secret values under the target Wrangler environment:

- `APP_ENV`: `development`, `staging`, or `production`.
- `PUBLIC_BASE_URL`: the exact HTTPS origin used in email links.
- `ALLOWED_ORIGINS`: the same origin, or a comma-separated allowlist for a separate frontend.
- `MALWARE_SCAN_MODE`: `disabled` works only in `local` or `development`; staging and production fail closed.
- `RESEND_FROM_ADDRESS`: the verified sender shown to recipients.
- `SCANNER_URL`: required when malware scanning is enabled.
- `CLOUDFLARE_ACCOUNT_ID` and `R2_BUCKET_NAME`: non-secret identifiers used to
  generate direct-upload URLs.

Install secrets through the Docker-managed Wrangler environment (change `dev` for another environment):

```sh
docker compose run --rm --no-deps worker npx wrangler secret put NAME --env dev
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

Never put real secrets or the administrator email in `wrangler.jsonc`, and never commit `.dev.vars`.

The development environment currently uses the operator-owned verified address
`notifications@mail.noneli.com`. Keep its Resend domain verification active, or
replace `RESEND_FROM_ADDRESS` with another address on a verified domain.

## Initial administrator bootstrap

Applying the D1 migrations creates a random 256-bit, instance-specific
setup key. Retrieve it in a private terminal without putting it in source or chat:

```sh
docker compose run --rm --no-deps worker npm run worker:setup-key:dev
```

Open `/setup`, enter that same key, and provide the organization name plus the
administrator's full name and email. The setup creates no sample events or
speakers. To invalidate a key before setup and generate a replacement:

```sh
docker compose run --rm --no-deps worker npm run worker:setup-key:dev -- --regenerate
```

Retrieval and regeneration are refused after setup completes. Successful setup
deletes the key in the same D1 transaction, and database triggers prevent another
key from being inserted or rotated while the permanent completion marker exists.
The key is never returned by the Worker API.

For non-interactive automation, the guarded command remains available:

```sh
docker compose run --rm --no-deps worker npm run worker:bootstrap:dev -- \
  --organization-name "Example Events" \
  --admin-name "Example Administrator" \
  --admin-email "admin@example.com"
```

The endpoint refuses a second organization. The command reads the migration-generated
key without printing it. Browser setup requests the administrator's first magic
link automatically. An event is intentionally optional; the initial administrator
creates the first event from the empty-state UI.

Completion is also recorded by an atomic, singleton D1 marker. The marker is
claimed in the same transaction as the first organization and administrator,
so concurrent setup attempts cannot both succeed. It is independent of business
records: deleting or archiving organizations does not reopen first-time setup.
The database rejects changing or deleting this marker through normal SQL.

## Development deployment with scanning disabled

The checked-in `dev` environment sets `MALWARE_SCAN_MODE` to `disabled`. Uploads are marked `clean` with reason `development_bypass`, and an audit event records the bypass. The same value is rejected in staging and production.

Confirm `PUBLIC_BASE_URL` and `ALLOWED_ORIGINS` contain the exact Worker origin,
then run:

```sh
docker compose run --rm --no-deps worker npm run frontend:check
docker compose run --rm --no-deps worker npm run frontend:build
docker compose run --rm --no-deps worker uv run python scripts/embed_console_assets.py
docker compose run --rm --no-deps worker npm run worker:migrate:dev
docker compose run --rm --no-deps worker npm run worker:r2-cors:dev
docker compose run --rm --no-deps worker npm run worker:deploy:dev
docker compose run --rm --no-deps worker npm run worker:preflight:dev
```

`worker:preflight:dev` is read-only and passes when deployment/configuration is
sound even if provider activation is pending. Run the strict inventory with:

```sh
docker compose run --rm --no-deps worker npm run worker:activation:preflight:dev
```

That command remains non-zero until verified email delivery, direct R2 upload
credentials, and initial bootstrap are ready. After activation, check a delivered
passwordless sign-in, a CFP draft/submission, an invitation, and an R2 upload.
The code intentionally does not invent or commit provider/account values.

The checked-in `r2-cors.dev.json` policy allows only browser `PUT` uploads from
the exact development Worker origin with `Content-Type`, and exposes only
`ETag`. Keep each environment's origin-specific policy separate; do not use a
wildcard origin for authenticated speaker uploads.

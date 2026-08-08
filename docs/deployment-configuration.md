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

`BOOTSTRAP_TOKEN` is also a Worker secret, but do not create or retain it
manually. The bootstrap command below generates, streams, uses, and removes it.

Never put real secrets or the administrator email in `wrangler.jsonc`, and never commit `.dev.vars`.

## Initial administrator bootstrap

Apply every D1 migration first. Then supply the approved administrator/event
details to the guarded command:

```sh
docker compose run --rm --no-deps worker npm run worker:bootstrap:dev -- \
  --organization-name "Example Events" \
  --event-name "Example Conference" \
  --admin-email "admin@example.com" \
  --starts-at "2026-11-01T09:00:00+05:30" \
  --ends-at "2026-11-01T18:00:00+05:30" \
  --time-zone "Asia/Kolkata"
```

The endpoint refuses a second organization. The command always attempts to
remove `BOOTSTRAP_TOKEN`, never prints it, and exits non-zero if removal cannot
be confirmed. After success, open `/sign-in?redirect=/admin/events` and request
a magic link for that administrator email.

## Development deployment with scanning disabled

The checked-in `dev` environment sets `MALWARE_SCAN_MODE` to `disabled`. Uploads are marked `clean` with reason `development_bypass`, and an audit event records the bypass. The same value is rejected in staging and production.

Confirm `PUBLIC_BASE_URL` and `ALLOWED_ORIGINS` contain the exact Worker origin,
then run:

```sh
docker compose run --rm --no-deps worker npm run frontend:check
docker compose run --rm --no-deps worker npm run frontend:build
docker compose run --rm --no-deps worker uv run python scripts/embed_console_assets.py
docker compose run --rm --no-deps worker npm run worker:migrate:dev
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

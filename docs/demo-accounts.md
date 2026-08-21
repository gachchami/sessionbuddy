# Demo accounts and one-click sign-in

SessionBuddy can offer three password-free demo controls on `/sign-in` and the
landing page — one per account persona. This document covers what they are, how
to enable them, and how to take them away.

## What it is

`GET /api/v1/auth/demo-personas` reports which personas are available.
`POST /api/v1/auth/demo-sign-in` accepts **a role and nothing else**, resolves it
to a server-owned user id, and creates an ordinary session. The browser never
sends an email, a user id, or a credential, so the endpoint cannot be turned
into arbitrary account impersonation.

Demo sign-in is an addition, not a replacement. The three demo accounts also
hold real password credentials, so signing in with an email and password works
exactly as before.

## Personas

| Role | Lands on | Identity |
| --- | --- | --- |
| Organizer | `/admin` | `demo-organizer@sessionbuddy.demo`, created by the seed |
| Reviewer | `/reviews` | `demo-reviewer@sessionbuddy.demo`, created by the seed |
| Speaker | `/speaker` | `demo-speaker@sessionbuddy.demo`, created by the seed |

Personas are always resolved by **user id**, never by display name. Names are
mutable and not unique, so a different database's unrelated "Sam Whitfield"
can never become a demo login by accident.

The demo organizer receives a `manage` grant on the demo organization rather
than ownership. Resource ownership cannot be revoked through the product, so a
demo identity must never hold it.

## Enabling it

Demo access requires both `APP_ENV=local` or `APP_ENV=development` and an
explicit `DEMO_LOGIN_ENABLED=true`. Anything else — a missing or unknown
environment, an absent flag, `1`, or `yes` — leaves it off. This preserves the
existing scanner and deployment meaning of `APP_ENV` while preventing a copied
flag from enabling password-free access in preview, staging, or production.

```
APP_ENV=development      # use local for the Docker environment
DEMO_LOGIN_ENABLED=true  # the literal string "true"; nothing else enables it
DEMO_ORGANIZER_USER_ID=<id>
DEMO_REVIEWER_USER_ID=<id>
DEMO_SPEAKER_USER_ID=<id>
```

At runtime a persona with an empty id is simply skipped, so a half-configured
environment offers only the personas it can actually deliver rather than a
control that fails on click.

The release gate mirrors the runtime. `scripts/cloudflare_preflight.py` FAILs
if demo login is enabled outside local/development, or if any
`DEMO_*_USER_ID` is empty while demo login is on. That check is a deploy-time
backstop against a copied `vars` block and does not alter `APP_ENV`'s other
runtime meaning it did not already have.

## Seeding

The seed is data, never a migration. It is idempotent: re-running it repairs
the demo organizer in place rather than creating duplicates.

Generate the ignored credential registry once, then reuse it for every demo
environment. It contains a separate password for each persona, is created with
mode `0600`, and must remain under `.local/`:

```sh
export PASSWORD_PEPPER="$(grep '^PASSWORD_PEPPER=' .dev.vars | cut -d= -f2-)"

docker compose run --rm --no-deps worker \
  uv run python scripts/seed_demo_accounts.py --local --create-credentials
```

Subsequent runs omit `--create-credentials`. The seed refuses a credential file
that is group/world accessible and verifies each configured user id against the
expected `demo-*@sessionbuddy.demo` email before changing a password. It rotates
only the three demo credentials, bumps their authorization versions to end old
sessions, and never prints a plaintext password.

The script prints the three ids to paste into `.dev.vars` (local) or the demo
environment's `vars` block, and reports whether every configured persona is
usable — active account, active role, and whether it still has a password
credential for ordinary sign-in.

Against a deployed database, name the wrangler environment and the tenant:

```sh
uv run python scripts/seed_demo_accounts.py --env dev --organization-id <id>
```

When the deployed environment's password pepper is intentionally unavailable,
rotate the three existing demo accounts through the running application. This
uses the gated demo sign-in endpoint, verifies each expected email, preserves
the profile, and lets that deployment hash the new password with its own
secret:

```sh
uv run python scripts/seed_demo_accounts.py \
  --app-url https://sessionbuddy-development.example.workers.dev
```

Use the same ignored credential registry for local, development, and a second
development deployment. The script never sends one environment's password
pepper to another environment and never prints a password.

For a private Wrangler configuration or a target-specific pepper file, pass
them explicitly without copying either into source control:

```sh
uv run python scripts/seed_demo_accounts.py --env dev2 \
  --config .cloudflare-private/dev2/wrangler.rendered.jsonc \
  --pepper-file .cloudflare-private/dev2/password-pepper.secret \
  --organization-id <id>
```

The script refuses to run unless `DEMO_LOGIN_ENABLED=true` is set for the
target environment, so seeding and sign-in agree on one switch. It exits
non-zero if any configured persona is missing, inactive, or lacks its role.
Use `--verify-only` to run that validation without writing anything.

## Repairing a broken demo

The demo accounts share one organization with real content, and demo visitors
have real permissions inside it. An organizer persona can revoke a reviewer's
assignment or delete unscheduled agenda content, and users cannot be deleted
and recreated through the product. When the demo tenant gets into a bad state:

```sh
uv run python scripts/seed_demo_accounts.py --local --reset
```

`--reset` removes only rows the seed owns — the synthetic organizer, its
membership, and its grant — then seeds again. It never touches submissions,
evaluations, events, or any real account.

## Disabling and purging

1. Set `DEMO_LOGIN_ENABLED=false`. The endpoints return 404 immediately and the
   controls disappear from both pages, because the section is built from the
   API response rather than hidden in the shipped HTML.
2. Run the seeder with `--reset` and do not re-seed, to remove the synthetic
   organizer.
3. Clear the three `DEMO_*_USER_ID` values.

Revoking `DEMO_LOGIN_ENABLED` does not end sessions that already exist. To cut
those off as well, bump `authorization_version` on the demo accounts, which
invalidates every live session for them on the next request.

## Why it must never share a production database

The gate is environment configuration, but the real boundary is the data. A
demo visitor gets genuine permissions inside whatever tenant the demo accounts
belong to. If those accounts existed in a production database, a public button
would hand every visitor real authority over real customer records. Keep demo
identities in a database whose contents you are willing to have edited by
anyone who can reach the page.

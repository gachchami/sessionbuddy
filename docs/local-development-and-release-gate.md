# Local development and release-gate runbook

The activity Worker topology, projection semantics, and local/development
bring-up sequence are documented in
[`activity-pipeline.md`](activity-pipeline.md).

This document records the container-first procedure used to launch SessionBuddy
locally and the exact verification sequence used before handing off changes.
Run every command from the repository root.

```sh
cd /Users/superman/playground/projects/sessionbuddy
```

## 1. Runtime defined by the Dockerfile

The root [`Dockerfile`](../Dockerfile) is the development and test image for the
Python Worker. It performs these steps in order:

1. Copies `uv` and `uvx` from `ghcr.io/astral-sh/uv:0.12.2`.
2. Uses `node:24.19.0-bookworm-slim` as the runtime image.
3. Installs only `ca-certificates` and `curl` with `apt`.
4. Uses `/workspace` as the working directory.
5. Installs the locked Node dependencies from `package-lock.json` with
   `npm ci`.
6. Installs the locked Python dependencies from `uv.lock` with
   `uv sync --frozen`.
7. Copies the repository and installs the SessionBuddy Python project with a
   second `uv sync --frozen`.
8. Exposes port `8787`.
9. Starts the Worker with:

   ```sh
   npm run worker:dev -- --ip 0.0.0.0 --port 8787
   ```

The host does not need its own Node or Python toolchain. Docker and Docker
Compose are the supported host prerequisites.

## 2. Services in the local stack

[`compose.yaml`](../compose.yaml) defines the following services:

| Service | Purpose | Host port |
| --- | --- | --- |
| `worker` | Main SessionBuddy Python Worker | `8787` |
| `activity-worker` | Independent activity projection Worker | `8788` |
| `activity-poller` | Calls the activity dispatch endpoint once per second | none |
| `mailpit` | Local email delivery and inbox UI | `8025` |
| `clamav` | Malware scanning engine | none |
| `scanner` | HTTP adapter between SessionBuddy and ClamAV | none |
| `https` | Optional local Caddy TLS proxy | `8443` |
| `tunnel` | Cloudflare quick tunnel giving the stack a public HTTPS origin | none |
| `tunnel-vars` | One-shot: writes the tunnel hostname into `.dev.vars` | none |
| `e2e` | Playwright test runner | none |

Starting `worker` also starts `mailpit`, `clamav`, `scanner`,
`activity-worker`, `activity-poller`, `tunnel`, and `tunnel-vars` because they
are dependencies. The `https` and `e2e` services start only when explicitly
requested.

### Why the tunnel is not optional

Email delivery is only useful locally if the magic link inside the message can
actually be opened. That requires a public HTTPS origin: `Secure` session
cookies are dropped on a plain-http origin, and the eval kit's containerized
Chromium rejects the `https` service's internal Caddy certificate. The `tunnel`
service provides that origin.

A quick tunnel needs no Cloudflare account, but its hostname is regenerated on
every start, so it must never be committed to `wrangler.jsonc`. `tunnel-vars`
resolves the assigned hostname from the cloudflared log and writes
`PUBLIC_BASE_URL` and `ALLOWED_ORIGINS` into `.dev.vars`, which is ignored by
Git and overrides `vars` in `wrangler dev`. It rewrites only those two keys and
preserves the local HMAC secrets in that file. `worker` waits for it with
`service_completed_successfully`, because both variables are read once at
startup.

If no hostname appears within `TUNNEL_TIMEOUT_SECONDS` (default 90),
`tunnel-vars` exits non-zero and the worker does not start. That is deliberate:
booting with an unroutable `PUBLIC_BASE_URL` produces magic links that 404,
which is harder to diagnose than a refused startup.

To read the current origin:

```sh
docker compose logs tunnel-vars
grep -E '^(PUBLIC_BASE_URL|ALLOWED_ORIGINS)=' .dev.vars
```

The source tree is bind-mounted at `/workspace`. Named volumes retain Node
modules, the Python virtual environment, tool caches, and Wrangler
configuration between runs.

## 3. First local launch, in serial order

### 3.1 Create the local environment file once

Do not overwrite an existing `.dev.vars` file.

```sh
test -f .dev.vars || cp .dev.vars.example .dev.vars
```

`.dev.vars` is ignored by Git and is the only appropriate place for local
secrets.

### 3.2 Build the application images

```sh
docker compose build worker activity-worker scanner
```

Rebuild after changing `Dockerfile`, `scanner/Dockerfile`, `package-lock.json`,
`uv.lock`, or other image-time dependencies. Ordinary source edits are visible
through the bind mount and do not require an image rebuild.

### 3.3 Validate the canonical baseline migration

```sh
docker compose run --rm --no-deps worker \
  npm run worker:migrations:baseline:check
```

### 3.4 Apply the local D1 migration

```sh
docker compose run --rm --no-deps worker npm run worker:migrate
```

SessionBuddy supports fresh installations only. If
`migrations_baseline/0001_baseline.sql` changes, do not apply it over a
data-bearing development database. Recreate the local database first, then
prove both the first application and a repeat no-op application through the
release gate described below.

### 3.5 Start the local stack

```sh
docker compose up --detach worker
```

Use `--build` when an image input changed:

```sh
docker compose up --build --detach worker
```

### 3.6 Confirm readiness

```sh
docker compose ps
curl --fail --silent http://127.0.0.1:8787/health
docker compose logs --tail=100 worker activity-worker activity-poller
```

The application is available at <http://127.0.0.1:8787>. A fresh database
opens the one-time setup flow at <http://127.0.0.1:8787/setup>. Mailpit is
available at <http://127.0.0.1:8025>.

To follow the main Worker logs during development:

```sh
docker compose logs --follow worker
```

To stop the stack without deleting its named volumes:

```sh
docker compose stop
```

## 4. Commands used while iterating

Run the narrowest relevant test first. The following examples all execute in
the checked-in container environment:

```sh
docker compose run --rm --no-deps worker \
  uv run pytest -q tests/speaker_operations/test_speaker_portal.py
```

```sh
docker compose run --rm --no-deps worker \
  uv run pytest -q tests/cfp/test_cfp.py
```

Run a focused Playwright specification against the running Worker with:

```sh
docker compose run --rm e2e sh -lc \
  "npm ci && npx playwright test e2e/speaker-portal-responsive.spec.ts --workers=4"
```

After changing packaged HTML, CSS, or JavaScript, regenerate the embedded
assets and verify that generation is clean:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/embed_console_assets.py
docker compose run --rm --no-deps worker \
  uv run python scripts/embed_console_assets.py --check
```

After changing API routes or response models, regenerate OpenAPI:

```sh
docker compose run --rm --no-deps worker \
  uv run python scripts/generate_openapi.py
```

Run the standard static and unit/integration checks in this order:

```sh
docker compose run --rm --no-deps worker npm run frontend:check
docker compose run --rm --no-deps worker npm run frontend:build
docker compose run --rm --no-deps worker npm run fixtures:check-assets
docker compose run --rm --no-deps worker \
  uv run python scripts/embed_console_assets.py --check
docker compose run --rm --no-deps worker uv run ruff check .
docker compose run --rm --no-deps worker uv run pytest -q
docker compose run --rm e2e sh -lc \
  "npm ci && npx playwright test --workers=4"
git diff --check
```

These commands are useful iteration evidence, but they do not replace the full
release rehearsal.

## 5. Full release gate

Run the checked-in gate exactly as follows:

```sh
scripts/release_gate.sh
```

The script is non-deploying. It creates a temporary directory beneath ignored
`.local/`, uses the isolated Compose project name
`sessionbuddy-release-gate`, and combines [`compose.yaml`](../compose.yaml)
with [`compose.release.yaml`](../compose.release.yaml). The ordinary local D1
state is not used.

The gate does not start `tunnel` or `tunnel-vars`.
[`compose.release.yaml`](../compose.release.yaml) replaces the worker's
`depends_on` with `!override` for that reason: a hermetic rehearsal must not
require internet or a live quick tunnel, and must not rewrite the developer's
`.dev.vars` as a side effect. Browser checks in the gate address the worker
directly at `http://worker:8787`, so no public origin is needed.

The gate performs these operations serially:

1. Builds the `worker` image.
2. Validates `migrations_baseline/0001_baseline.sql`.
3. Applies the baseline to a fresh temporary local D1 database.
4. Applies it a second time to prove repeatability/no-op behavior.
5. Marks the isolated instance setup as complete for browser testing.
6. Starts the isolated Worker and its activity/scanning/email dependencies.
7. Runs TypeScript checking with `npm run frontend:check`.
8. Runs the Vite production build with `npm run frontend:build`.
9. Verifies generated fixture assets.
10. Verifies that embedded console assets are current.
11. Runs Ruff across the repository.
12. Runs the complete Python test suite with `pytest -q`.
13. Runs the large-dataset database release smoke, including its integrity and
    query-plan checks.
14. Runs all Playwright browser and Axe checks with four workers.
15. Benchmarks `/api/v1/engine-room/status` with 100 measured requests, 20
    warmups, and concurrency 4. The JSON result is written under
    `.local/benchmarks/`.
16. Runs mobile Lighthouse performance and accessibility checks for
    `/engine-room`. The JSON result is written under `.local/lighthouse/`.
17. Produces a Wrangler development-environment package with `--dry-run`.
18. Validates the dry-run Worker package.
19. Stops the isolated stack and removes its temporary D1/package state, even
    when a command fails.

Success ends with this exact message:

```text
Release readiness local release gate passed. No remote deployment was performed.
```

## 6. What the gate does not do

The release gate does not deploy, modify a remote D1 database, send external
messages, or use the ordinary local development database. Deployment and
remote migration commands are deliberately outside this runbook and require
separate authorization and release evidence.

## 7. Final handoff checklist

After the gate succeeds, run:

```sh
git status --short
git diff --check
```

Report the focused tests, full gate result, browser coverage, and any command
that could not be run. Update `docs/product-status.md` when the capability or
release status changed.

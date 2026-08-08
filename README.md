# Sessionbuddy

Sessionbuddy is a performance-first event program-management application. The backend is Python/FastAPI on Cloudflare Python Workers; D1 is the transactional source of truth.

The Worker uses Smart Placement so database-backed handlers can execute near
the D1 primary. Keep client, `app`, and `db` timings separate when evaluating
the result; placement may need traffic and up to 15 minutes before it decides.

## Foundation development

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

Authenticate Wrangler without exposing host credentials to the container:

```bash
docker compose run --rm worker npx wrangler login --device --browser=false
```

The OAuth credentials are stored in the Docker-managed `wrangler-config`
volume, not in the repository or host configuration directory.

Apply migrations and deploy the isolated Cloudflare development environment:

```bash
docker compose run --rm worker npm run worker:migrate:dev
docker compose run --rm worker npm run worker:deploy:dev
```

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

`pywrangler sync` is the dependency compatibility gate. It resolves against the
Pyodide index selected by `compatibility_date`, writes the reviewed `pylock.toml`,
and installs generated Worker packages into ignored local directories. Commit
`pylock.toml`; do not commit `python_modules/` or `.venv-workers/`.

Apply the local D1 migration after Pywrangler is available:

```bash
npm run worker:migrate
```

Run the fast host-ASGI benchmark:

```bash
uv run python scripts/benchmark_api.py --output .local/benchmarks/foundation.json
```

With the local Worker running, exercise Pyodide, Workerd, and the HTTP boundary:

```bash
uv run python scripts/benchmark_api.py \
  --base-url http://127.0.0.1:8787 \
  --output .local/benchmarks/foundation-worker.json
```

Any API route can use the same benchmark contract. For example, benchmark the
deployed D1 probe from inside the container:

```bash
uv run python scripts/benchmark_api.py \
  --base-url https://sessionbuddy-development.shiny-cloud-dd47.workers.dev \
  --route /api/v1/foundation/database \
  --output .local/benchmarks/foundation-d1-cloudflare-dev.json
```

This host benchmark catches application-level regressions quickly. Release measurements must also run against `pywrangler dev` and an isolated deployed preview because only those environments exercise Pyodide, `workerd`, and real Cloudflare bindings.

Architecture and delivery requirements are documented under `docs/`. The
accepted Wave 0 gate and the contracts every feature must reuse are summarized
in `docs/wave-0-acceptance.md`.

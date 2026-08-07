# Sessionbuddy

Sessionbuddy is a performance-first event program-management application. The backend is Python/FastAPI on Cloudflare Python Workers; D1 is the transactional source of truth.

## Foundation development

Requirements: `uv`, Node.js required by Pywrangler/Wrangler, and Python 3.13.

```bash
cp .dev.vars.example .dev.vars
uv sync
uv run ruff check .
uv run pytest
uv run python scripts/generate_openapi.py
uv run pywrangler sync
uv run pywrangler dev
```

Apply the local D1 migration after Pywrangler is available:

```bash
uv run pywrangler d1 migrations apply DB --local
```

Run the fast host-ASGI benchmark:

```bash
uv run python scripts/benchmark_api.py --output .local/benchmarks/foundation.json
```

This host benchmark catches application-level regressions quickly. Release measurements must also run against `pywrangler dev` and an isolated deployed preview because only those environments exercise Pyodide, `workerd`, and real Cloudflare bindings.

Architecture and delivery requirements are documented under `docs/`.

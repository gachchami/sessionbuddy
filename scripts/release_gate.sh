#!/bin/sh
set -eu

# Container-first release rehearsal. Artifacts are written only beneath ignored
# .local directories; this command does not deploy or touch remote resources.
docker compose up --build --detach worker
docker compose run --rm --no-deps worker npm run worker:migrations:baseline:check
docker compose run --rm --no-deps worker npm run worker:migrate
docker compose run --rm --no-deps worker npm run frontend:check
docker compose run --rm --no-deps worker npm run frontend:build
docker compose run --rm --no-deps worker uv run python scripts/embed_console_assets.py --check
docker compose run --rm --no-deps worker uv run ruff check .
docker compose run --rm --no-deps worker uv run pytest -q
docker compose run --rm --no-deps worker uv run python scripts/release_db_smoke.py --large

docker compose run --rm e2e

docker compose run --rm --no-deps worker uv run python scripts/benchmark_api.py \
  --base-url http://worker:8787 --route /api/v1/engine-room/status \
  --requests 100 --warmup 20 --concurrency 4 --timeout-seconds 30 \
  --output .local/benchmarks/release-engine-room-worker.json
docker compose run --rm e2e sh -lc \
  "mkdir -p .local/lighthouse && \
   CHROME_PATH=/ms-playwright/chromium-1234/chrome-linux/chrome \
   npx --yes lighthouse@13.4.1 http://worker:8787/engine-room \
   --only-categories=performance,accessibility --output=json \
   --output-path=.local/lighthouse/engine-room-mobile.json \
   --chrome-flags='--headless --no-sandbox --disable-dev-shm-usage' --quiet"
docker compose run --rm --no-deps worker uv run pywrangler deploy \
  --env dev --dry-run --outdir /tmp/sessionbuddy-release-dry-run

echo "Release readiness local release gate passed. No remote deployment was performed."

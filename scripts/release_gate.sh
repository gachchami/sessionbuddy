#!/bin/sh
set -eu

# Container-first release rehearsal. Artifacts are written only beneath ignored
# .local directories; this command does not deploy or touch remote resources.
mkdir -p .local
RELEASE_GATE_STATE=$(mktemp -d .local/release-gate.XXXXXX)
export RELEASE_GATE_STATE
RELEASE_GATE_SUFFIX=$(printf '%s' "${RELEASE_GATE_STATE##*.}" | tr '[:upper:]' '[:lower:]')
RELEASE_GATE_PROJECT="sessionbuddy-release-gate-$RELEASE_GATE_SUFFIX"
export RELEASE_GATE_PROJECT
# Local D1 is owned by one Workerd process. Parallel browser contexts can begin
# overlapping local SQLite transactions and crash the release-rehearsal Worker;
# keep the default bounded while allowing an explicit concurrency probe.
PW_WORKERS=${PW_WORKERS:-2}
export PW_WORKERS

release_compose() {
  docker compose -p "$RELEASE_GATE_PROJECT" -f compose.yaml -f compose.release.yaml "$@"
}

cleanup() {
  release_compose down --remove-orphans >/dev/null 2>&1 || true
  rm -rf -- "$RELEASE_GATE_STATE"
}
trap cleanup EXIT INT TERM

release_compose build worker
release_compose run --rm --no-deps worker npm run worker:migrations:baseline:check
release_compose run --rm --no-deps worker npm run worker:migrate -- \
  --persist-to "/workspace/$RELEASE_GATE_STATE"
# Prove the ledger is repeatable against the same persistent D1 before the
# browser phase changes the instance from fresh-install to configured state.
release_compose run --rm --no-deps worker npm run worker:migrate -- \
  --persist-to "/workspace/$RELEASE_GATE_STATE"
release_compose run --rm --no-deps worker uv run pywrangler d1 execute DB \
  --local --persist-to "/workspace/$RELEASE_GATE_STATE" \
  --command "INSERT INTO instance_setup (singleton_key,completed_at_ms) VALUES ('primary',unixepoch() * 1000); DELETE FROM instance_setup_credentials WHERE singleton_key='primary';"
release_compose run --rm --no-deps worker npm run frontend:check
release_compose run --rm --no-deps worker npm run frontend:build
release_compose run --rm --no-deps worker npm run fixtures:check-assets
release_compose run --rm --no-deps worker uv run python scripts/embed_console_assets.py --check
release_compose run --rm --no-deps worker uv run ruff check .
release_compose run --rm --no-deps worker uv run pytest -q
release_compose run --rm --no-deps worker uv run python scripts/release_db_smoke.py --large

release_compose up --detach worker
release_compose run --rm -e PW_WORKERS e2e sh -lc '
  ready=0
  for attempt in $(seq 1 60); do
    if node -e "fetch(\"http://worker:8787/health\").then(response => process.exit(response.ok ? 0 : 1)).catch(() => process.exit(1))"; then
      ready=$((ready + 1))
      [ "$ready" -ge 3 ] && break
    else
      ready=0
    fi
    sleep 1
  done
  [ "$ready" -ge 3 ]
  npm ci
  npx playwright test --workers=${PW_WORKERS:-2} --max-failures=1
'

release_compose run --rm --no-deps worker uv run python scripts/benchmark_api.py \
  --base-url http://worker:8787 --route /api/v1/engine-room/status \
  --requests 100 --warmup 20 --concurrency 4 --timeout-seconds 30 \
  --output .local/benchmarks/release-engine-room-worker.json
release_compose run --rm e2e sh -lc \
  "mkdir -p .local/lighthouse && \
   CHROME_PATH=/ms-playwright/chromium-1234/chrome-linux/chrome \
   npx --yes lighthouse@13.4.1 http://worker:8787/engine-room \
   --only-categories=performance,accessibility --output=json \
   --output-path=.local/lighthouse/engine-room-mobile.json \
   --chrome-flags='--headless --no-sandbox --disable-dev-shm-usage' --quiet"
release_compose run --rm --no-deps worker uv run pywrangler deploy \
  --env dev --dry-run --outdir "/workspace/$RELEASE_GATE_STATE/package-dry-run"
release_compose run --rm --no-deps worker uv run python \
  scripts/validate_worker_package.py "/workspace/$RELEASE_GATE_STATE/package-dry-run"

echo "Release readiness local release gate passed. No remote deployment was performed."

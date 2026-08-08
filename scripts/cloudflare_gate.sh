#!/bin/sh
set -eu

mkdir -p .local/benchmarks .local/logs

npm ci
uv sync --frozen
uv run python scripts/embed_console_assets.py --check
uv run ruff check .
uv run pytest
npm run worker:sync
npm run worker:migrate

npm run worker:dev -- --ip 0.0.0.0 --port 8787 >.local/logs/worker.log 2>&1 &
worker_pid=$!
trap 'kill "$worker_pid" 2>/dev/null || true' EXIT INT TERM

attempt=0
until curl --fail --silent http://127.0.0.1:8787/health >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    cat .local/logs/worker.log
    exit 1
  fi
  sleep 1
done

curl --fail --silent http://127.0.0.1:8787/api/v1/health >/dev/null
curl --fail --silent http://127.0.0.1:8787/api/v1/openapi.json >/dev/null
curl --fail --silent http://127.0.0.1:8787/api/v1/engine-room/status >/dev/null
curl --fail --silent http://127.0.0.1:8787/api/v1/engine-room/database >/dev/null
curl --fail --silent http://127.0.0.1:8787/engine-room >/dev/null
curl --fail --silent http://127.0.0.1:8787/engine-room/assets/console.css >/dev/null
curl --fail --silent http://127.0.0.1:8787/engine-room/assets/console.js >/dev/null
curl --fail --silent http://127.0.0.1:8787/cfp-integration >/dev/null
uv run python scripts/smoke_cfp.py

uv run python scripts/benchmark_api.py \
  --base-url http://127.0.0.1:8787 \
  --output .local/benchmarks/engine-room-worker.json

#!/bin/sh
set -eu

marker="node_modules/.sessionbuddy-sbek-lock.sha256"
lock_digest=$(sha256sum pnpm-lock.yaml | cut -d ' ' -f 1)

if [ -f "$marker" ] && [ "$(sed -n '1p' "$marker")" = "$lock_digest" ]; then
  exit 0
fi

corepack pnpm install --frozen-lockfile --ignore-scripts \
  --store-dir=/pnpm-store --reporter=silent
printf '%s\n' "$lock_digest" >"$marker"

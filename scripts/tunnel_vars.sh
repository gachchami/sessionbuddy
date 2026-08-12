#!/bin/sh
set -eu

# Resolve the quick tunnel's generated hostname and publish it to .dev.vars.
#
# Local email delivery is only useful if the magic link inside the message is
# reachable over HTTPS: `Secure` session cookies are dropped on a plain-http
# origin, and the eval kit's containerized Chromium rejects Caddy's internal
# certificate. A trycloudflare quick tunnel is the cheapest publicly-resolvable
# HTTPS origin, but its hostname is regenerated on every start — so it must not
# be committed to wrangler.jsonc. `.dev.vars` is gitignored and overrides `vars`
# in `wrangler dev`, which makes it the correct home for a per-boot value.
#
# cloudflared prints the assigned hostname to its log. The metrics server only
# documents /metrics, so the log is the supported way to read it.

log_file="${TUNNEL_LOG_FILE:-/tunnel/tunnel.log}"
vars_file="${DEV_VARS_FILE:-/workspace/.dev.vars}"
timeout_seconds="${TUNNEL_TIMEOUT_SECONDS:-90}"

hostname=""
elapsed=0
while [ "$elapsed" -lt "$timeout_seconds" ]; do
  if [ -f "$log_file" ]; then
    hostname=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log_file" | head -n 1 || true)
    [ -n "$hostname" ] && break
  fi
  sleep 1
  elapsed=$((elapsed + 1))
done

if [ -z "$hostname" ]; then
  echo "No trycloudflare hostname appeared in $log_file within ${timeout_seconds}s." >&2
  echo "The worker would start with an unroutable PUBLIC_BASE_URL and every magic link would 404, so startup stops here." >&2
  exit 1
fi

# Local origins stay reachable so 127.0.0.1:8787 and the Caddy 8443 endpoint
# keep working for anyone not going through the tunnel.
allowed="http://127.0.0.1:8787,http://localhost:8787,http://worker:8787,https://localhost:8443,$hostname"

# Rewrite only the two managed keys. .dev.vars holds local HMAC secrets; a
# clobbering write would silently invalidate every existing session and upload
# grant, so preserve every other line exactly as it is.
tmp="$vars_file.tunnel.tmp"
touch "$vars_file"
grep -v -E '^(PUBLIC_BASE_URL|ALLOWED_ORIGINS)=' "$vars_file" > "$tmp" || true
printf 'PUBLIC_BASE_URL=%s\n' "$hostname" >> "$tmp"
printf 'ALLOWED_ORIGINS=%s\n' "$allowed" >> "$tmp"
mv "$tmp" "$vars_file"

echo "Tunnel origin: $hostname (written to $vars_file)"

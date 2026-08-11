#!/bin/sh
set -eu

# Stable launcher for the external SessionBoard Eval Kit. SessionBuddy browser
# credentials stay in the eval kit's ignored .auth path. Model requests cross a
# short-lived authenticated bridge to the already signed-in host Codex CLI;
# ChatGPT credentials are never copied into the evaluator container.

target_url="${SBEK_TARGET_URL:-https://sessionbuddy-development.shiny-cloud-dd47.workers.dev}"
eval_root="${SBEK_ROOT:-}"
dependencies_volume="${SBEK_NODE_MODULES_VOLUME:-sessionbuddy-sbek-node-modules-v2}"
store_volume="${SBEK_PNPM_STORE_VOLUME:-sessionbuddy-sbek-pnpm-store-v2}"
playwright_image="${SBEK_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v1.62.1-noble}"
codex_bin="${SBEK_CODEX_BIN:-/Applications/ChatGPT.app/Contents/Resources/codex}"
bridge_bind="${SBEK_CODEX_BRIDGE_BIND:-0.0.0.0}"
bridge_container_host="${SBEK_CODEX_BRIDGE_CONTAINER_HOST:-host.docker.internal}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_dir")
bridge_pid=""
bridge_state=""

cleanup_bridge() {
  if [ -n "$bridge_pid" ]; then
    kill "$bridge_pid" >/dev/null 2>&1 || true
    wait "$bridge_pid" >/dev/null 2>&1 || true
    bridge_pid=""
  fi
  case "$bridge_state" in
    */sessionbuddy-codex-bridge.*)
      rm -rf -- "$bridge_state"
      ;;
  esac
  bridge_state=""
}

start_codex_bridge() {
  case "$codex_bin" in
    /*) ;;
    *)
      echo "SBEK_CODEX_BIN must be an absolute path." >&2
      exit 2
      ;;
  esac
  if [ ! -x "$codex_bin" ]; then
    echo "Codex CLI is not executable at the configured path." >&2
    exit 2
  fi
  host_node=$(command -v node || true)
  if [ -z "$host_node" ]; then
    echo "Node.js is required on the host to run the Codex evaluation bridge." >&2
    exit 2
  fi
  if ! codex_status=$("$codex_bin" login status 2>&1); then
    echo "Codex login status could not be verified." >&2
    exit 2
  fi
  case "$codex_status" in
    *"Logged in using ChatGPT"*) ;;
    *)
      echo "Codex must be signed in with ChatGPT before a live evaluation." >&2
      exit 2
      ;;
  esac

  bridge_state=$(mktemp -d "${TMPDIR:-/tmp}/sessionbuddy-codex-bridge.XXXXXX")
  token_file="$bridge_state/token"
  ready_file="$bridge_state/ready.json"
  bridge_log="$bridge_state/bridge.log"
  trap cleanup_bridge EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  SBEK_CODEX_BIN="$codex_bin" "$host_node" "$project_root/scripts/codex_eval_bridge.mjs" \
    --eval-root "$eval_root" \
    --token-file "$token_file" \
    --ready-file "$ready_file" \
    --host "$bridge_bind" \
    --port 0 \
    >"$bridge_log" 2>&1 &
  bridge_pid=$!

  attempt=0
  while [ ! -f "$ready_file" ] && [ "$attempt" -lt 100 ]; do
    if ! kill -0 "$bridge_pid" >/dev/null 2>&1; then
      echo "Codex evaluation bridge failed to start." >&2
      exit 2
    fi
    attempt=$((attempt + 1))
    sleep 0.1
  done
  if [ ! -f "$ready_file" ] || [ ! -f "$token_file" ]; then
    echo "Codex evaluation bridge did not become ready." >&2
    exit 2
  fi
  bridge_port=$(
    "$host_node" -e \
      'const fs=require("node:fs");const value=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));if(!Number.isInteger(value.port))process.exit(2);process.stdout.write(String(value.port));' \
      "$ready_file"
  )
  bridge_url="http://$bridge_container_host:$bridge_port/v1/generate"
}

if [ -z "$eval_root" ]; then
  for candidate in /private/tmp/sessionbuddy-evals.*/repo; do
    if [ -f "$candidate/package.json" ] && [ -f "$candidate/src/cli.ts" ]; then
      eval_root="$candidate"
      break
    fi
  done
fi

if [ -z "$eval_root" ] || [ ! -f "$eval_root/src/cli.ts" ]; then
  echo "SessionBoard Eval Kit not found. Set SBEK_ROOT to its checkout." >&2
  exit 2
fi

host_name=$(printf '%s' "$target_url" | sed -E 's#^[a-z]+://([^/]+).*#\1#')

ensure_linux_dependencies() {
  docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -v "$store_volume:/pnpm-store" \
    -v "$project_root/scripts/install_sbek_dependencies.sh:/install-sbek-dependencies.sh:ro" \
    -w /eval \
    "$playwright_image" \
    sh /install-sbek-dependencies.sh
}

if [ "${1:-}" = "where" ]; then
  printf 'eval_root=%s\ntarget_url=%s\ndependencies_volume=%s\nstore_volume=%s\nplaywright_image=%s\n' \
    "$eval_root" "$target_url" "$dependencies_volume" "$store_volume" "$playwright_image"
  exit 0
fi

if [ "${1:-}" = "auth" ]; then
  shift
  persona=${1:-}
  if [ -z "$persona" ]; then
    echo "Usage: scripts/run_sbek.sh auth <organizer|speaker|reviewer|attendee>" >&2
    exit 2
  fi
  echo "Request a SessionBuddy sign-in link for the $persona account at:" >&2
  echo "  $target_url/sign-in" >&2
  echo "Then save that browser session with:" >&2
  echo "  scripts/run_sbek.sh auth-link $persona '<magic-link-url>'" >&2
  exit 0
fi

# Complete a pasted magic link directly, handling the /auth/verify confirmation
# page (GET renders a Continue button; the POST consumes the token). The current
# upstream eval CLI has no paste-link flag, so SessionBuddy captures that browser
# state with this narrow helper.
if [ "${1:-}" = "auth-link" ]; then
  shift
  persona=${1:-}
  link=${2:-}
  if [ -z "$persona" ] || [ -z "$link" ]; then
    echo "Usage: scripts/run_sbek.sh auth-link <persona> <magic-link-url>" >&2
    exit 2
  fi
  ensure_linux_dependencies
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -v "$project_root/scripts/sbek_auth_link.mjs:/sbek-auth-link.mjs:ro" \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    node /sbek-auth-link.mjs "$persona" "$host_name" "$link"
fi

if [ "${1:-}" = "list" ]; then
  ensure_linux_dependencies
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -w /eval \
    "$playwright_image" \
    corepack pnpm exec tsx src/cli.ts list
fi

if [ "${1:-}" = "smoke" ]; then
  ensure_linux_dependencies
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -w /eval \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    corepack pnpm run smoke
fi

dry_run=0
for argument in "$@"; do
  if [ "$argument" = "--dry-run" ]; then
    dry_run=1
  fi
done

missing_personas=""
required_personas="organizer speaker reviewer"
ensure_linux_dependencies
if [ "$dry_run" != "1" ]; then
  if ! docker run --rm \
    -v "$eval_root:/eval" \
    -v "$project_root/scripts/check_sbek_config.mjs:/check-sbek-config.mjs:ro" \
    "$playwright_image" \
    node /check-sbek-config.mjs /eval "$target_url" "$@"; then
    exit 2
  fi
  for persona in $required_personas; do
    session_file="$eval_root/.auth/$host_name.$persona.json"
    if [ ! -f "$session_file" ]; then
      missing_personas="$missing_personas $persona"
    fi
  done
  if [ -n "$missing_personas" ]; then
    echo "Missing saved eval persona session(s):$missing_personas" >&2
    echo "Save each SessionBuddy magic-link session with scripts/run_sbek.sh auth-link." >&2
    exit 2
  fi
fi

if [ "$dry_run" != "1" ] && [ "${SBEK_SKIP_SESSION_CHECK:-0}" != "1" ]; then
  echo "Checking saved persona sessions and role boundaries before starting a live Codex eval..."
  if ! docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -v "$project_root/scripts/check_sbek_sessions.mjs:/check-sessions.mjs:ro" \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    node /check-sessions.mjs "$target_url" "$host_name" $required_personas; then
    echo "A saved eval session is no longer authenticated. Refresh it before running the suite." >&2
    exit 3
  fi
fi

command_name="run"
if [ "${1:-}" = "resume" ]; then
  shift
  if [ -z "${1:-}" ]; then
    echo "Usage: scripts/run_sbek.sh resume <run-directory> [flags]" >&2
    exit 2
  fi
  resume_dir=$1
  shift
  set -- --resume "$resume_dir" "$@"
fi

if [ "$dry_run" = "1" ]; then
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -w /eval \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    corepack pnpm exec tsx src/cli.ts "$command_name" --url "$target_url" "$@"
fi

start_codex_bridge
docker run --rm \
  --add-host "$bridge_container_host:host-gateway" \
  -v "$eval_root:/eval" \
  -v "$dependencies_volume:/eval/node_modules" \
  -v "$token_file:/run/sessionbuddy-codex/token:ro" \
  -w /eval \
  -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  -e SBEK_CODEX_BRIDGE_URL="$bridge_url" \
  -e SBEK_CODEX_BRIDGE_TOKEN_FILE=/run/sessionbuddy-codex/token \
  "$playwright_image" \
  corepack pnpm exec tsx src/cli.ts "$command_name" --url "$target_url" "$@"

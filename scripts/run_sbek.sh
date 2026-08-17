#!/bin/sh
set -eu

# Stable launcher for the external SessionBoard Eval Kit. It records only paths
# and image names. Credentials stay in the eval kit's ignored .env/.auth paths
# or in an explicitly configured local authentication volume.

target_url="${SBEK_TARGET_URL:-}"
eval_root="${SBEK_ROOT:-}"
auth_volume="${SBEK_CLAUDE_AUTH_VOLUME:-sessionbuddy-claude-auth}"
dependencies_volume="${SBEK_NODE_MODULES_VOLUME:-sessionbuddy-sbek-node-modules-v2}"
store_volume="${SBEK_PNPM_STORE_VOLUME:-sessionbuddy-sbek-pnpm-store-v2}"
playwright_image="${SBEK_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v1.62.1-noble}"
provider="${SBEK_PROVIDER:-anthropic-api}"
openrouter_key_file="${SBEK_OPENROUTER_KEY_FILE:-$(pwd)/.local/openrouter_api_key}"

if [ -z "$target_url" ]; then
  echo "Set SBEK_TARGET_URL to the exact deployment under evaluation." >&2
  exit 2
fi

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
    -v "$(pwd)/scripts/install_sbek_dependencies.sh:/install-sbek-dependencies.sh:ro" \
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
    -v "$(pwd)/scripts/sbek_auth_link.mjs:/sbek-auth-link.mjs:ro" \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    node /sbek-auth-link.mjs "$persona" "$host_name" "$link"
fi

if [ "${1:-}" = "auth-password" ]; then
  shift
  persona=${1:-}
  if [ -z "$persona" ]; then
    echo "Usage: scripts/run_sbek.sh auth-password <persona>" >&2
    exit 2
  fi
  ensure_linux_dependencies
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -v "$(pwd)/scripts/sbek_password_auth.mjs:/sbek-password-auth.mjs:ro" \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    node /sbek-password-auth.mjs "$persona" "$host_name" "$target_url"
fi

if [ "${1:-}" = "list" ]; then
  ensure_linux_dependencies
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -w /eval \
    "$playwright_image" \
    corepack pnpm sbek list
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
    -v "$(pwd)/scripts/check_sbek_config.mjs:/check-sbek-config.mjs:ro" \
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
    echo "No saved session for:$missing_personas; using configured password credentials." >&2
  fi
fi

if [ "$dry_run" != "1" ] && [ -z "$missing_personas" ] && [ "${SBEK_SKIP_SESSION_CHECK:-0}" != "1" ]; then
  echo "Checking saved persona sessions and role boundaries before starting a paid eval..."
  if ! docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -v "$(pwd)/scripts/check_sbek_sessions.mjs:/check-sessions.mjs:ro" \
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
    corepack pnpm sbek "$command_name" --url "$target_url" "$@"
fi

# Claude Max authentication remains in the host keychain. Running the eval kit
# itself on the host lets its fixed-purpose adapter invoke `claude --print`
# without copying credentials or mounting the Claude config into a container.
if [ "$provider" = "claude-cli" ]; then
  if ! command -v claude >/dev/null 2>&1; then
    echo "Claude CLI was not found on the host PATH." >&2
    exit 2
  fi
  if [ ! -x "$eval_root/node_modules/.bin/tsx" ]; then
    echo "Host eval dependencies are missing. From the eval checkout, run: pnpm install" >&2
    exit 2
  fi
  cd "$eval_root"
  export SBEK_PROVIDER=claude-cli
  exec node --import tsx src/cli.ts "$command_name" --url "$target_url" "$@"
fi

if [ "$provider" = "openrouter" ]; then
  if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    if [ ! -s "$openrouter_key_file" ]; then
      echo "OpenRouter authentication is unavailable." >&2
      echo "Set OPENROUTER_API_KEY or save the key in the ignored file: $openrouter_key_file" >&2
      exit 2
    fi
    OPENROUTER_API_KEY=$(tr -d '\r\n' < "$openrouter_key_file")
    export OPENROUTER_API_KEY
  fi
  openrouter_model="${SBEK_OPENROUTER_MODEL:-stealth/ox-alpha}"
  docker run --rm \
    -v "$(pwd)/scripts/check_openrouter_model.mjs:/check-openrouter-model.mjs:ro" \
    -e OPENROUTER_API_KEY \
    "$playwright_image" \
    node /check-openrouter-model.mjs "$openrouter_model"
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -w /eval \
    -e OPENROUTER_API_KEY \
    -e SBEK_PROVIDER=openrouter \
    -e SBEK_REASONING_EFFORT="${SBEK_REASONING_EFFORT:-max}" \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    corepack pnpm sbek "$command_name" --url "$target_url" "$@"
fi

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -w /eval \
    -e ANTHROPIC_API_KEY \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    corepack pnpm sbek "$command_name" --url "$target_url" "$@"
fi

if [ -f "$eval_root/.env" ] && grep -Eq '^ANTHROPIC_API_KEY=.+$' "$eval_root/.env"; then
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -w /eval \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    corepack pnpm sbek "$command_name" --url "$target_url" "$@"
fi

if docker volume inspect "$auth_volume" >/dev/null 2>&1; then
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$dependencies_volume:/eval/node_modules" \
    -v "$auth_volume:/claude" \
    -w /eval \
    -e CLAUDE_CONFIG_DIR=/claude \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    corepack pnpm sbek "$command_name" --url "$target_url" "$@"
fi

echo "No Anthropic authentication is available to the eval container." >&2
echo "Set ANTHROPIC_API_KEY or add it to the eval checkout's ignored .env file." >&2
exit 2

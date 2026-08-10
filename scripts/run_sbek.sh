#!/bin/sh
set -eu

# Stable launcher for the external SessionBoard Eval Kit. It records only paths,
# image names, and Docker volume names; credentials remain inside Docker volumes
# and the eval kit's ignored .auth directory.

target_url="${SBEK_TARGET_URL:-https://sessionbuddy-development.shiny-cloud-dd47.workers.dev}"
eval_root="${SBEK_ROOT:-}"
auth_volume="${SBEK_CLAUDE_AUTH_VOLUME:-sessionbuddy-claude-auth}"
playwright_image="${SBEK_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v1.62.1-noble}"

if [ -z "$eval_root" ]; then
  for candidate in /private/tmp/sessionbuddy-evals.*/repo; do
    if [ -f "$candidate/package.json" ] && [ -x "$candidate/node_modules/.bin/tsx" ]; then
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

if [ "${1:-}" = "auth" ]; then
  shift
  persona=${1:-}
  if [ -z "$persona" ]; then
    echo "Usage: scripts/run_sbek.sh auth <organizer|speaker|reviewer|attendee>" >&2
    exit 2
  fi
  shift
  if [ "${1:-}" = "--reuse" ]; then
    shift
    source_persona=${1:-}
    if [ -z "$source_persona" ]; then
      echo "Usage: scripts/run_sbek.sh auth <persona> --reuse <saved-persona>" >&2
      exit 2
    fi
    exec docker run --rm \
      -v "$eval_root:/eval" \
      -w /eval \
      -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
      "$playwright_image" \
      ./node_modules/.bin/tsx src/cli.ts auth \
        --persona "$persona" --url "$target_url" --reuse "$source_persona"
  fi
  exec docker run --rm -it \
    -v "$eval_root:/eval" \
    -w /eval \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    ./node_modules/.bin/tsx src/cli.ts auth \
      --persona "$persona" --url "$target_url" --at /sign-in --paste-link "$@"
fi

# Complete a pasted magic link directly, handling the /auth/verify confirmation
# page (GET renders a Continue button; the POST consumes the token). Use this
# when the eval kit's own --paste-link flow stalls on the interstitial.
if [ "${1:-}" = "auth-link" ]; then
  shift
  persona=${1:-}
  link=${2:-}
  if [ -z "$persona" ] || [ -z "$link" ]; then
    echo "Usage: scripts/run_sbek.sh auth-link <persona> <magic-link-url>" >&2
    exit 2
  fi
  exec docker run --rm \
    -v "$eval_root:/eval" \
    -v "$(pwd)/scripts/sbek_auth_link.mjs:/sbek-auth-link.mjs:ro" \
    -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    "$playwright_image" \
    node /sbek-auth-link.mjs "$persona" "$host_name" "$link"
fi

missing_personas=""
required_personas="organizer speaker"
if [ -f "$eval_root/.auth/$host_name.reviewer.json" ]; then
  required_personas="$required_personas reviewer"
fi
for persona in $required_personas; do
  session_file="$eval_root/.auth/$host_name.$persona.json"
  if [ ! -f "$session_file" ]; then
    missing_personas="$missing_personas $persona"
  fi
done
if [ -n "$missing_personas" ]; then
  echo "Missing saved eval persona session(s):$missing_personas" >&2
  echo "Authenticate them with the eval kit before running the full suite." >&2
  exit 2
fi

if ! docker volume inspect "$auth_volume" >/dev/null 2>&1; then
  echo "Claude authentication volume '$auth_volume' does not exist." >&2
  exit 2
fi

if [ "${1:-}" = "where" ]; then
  printf 'eval_root=%s\ntarget_url=%s\nauth_volume=%s\nplaywright_image=%s\n' \
    "$eval_root" "$target_url" "$auth_volume" "$playwright_image"
  exit 0
fi

if [ "${SBEK_SKIP_SESSION_CHECK:-0}" != "1" ]; then
  echo "Checking saved persona sessions and role boundaries before starting a paid eval..."
  if ! docker run --rm \
    -v "$eval_root:/eval" \
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

exec docker run --rm \
  -v "$eval_root:/eval" \
  -v "$auth_volume:/claude" \
  -w /eval \
  -e CLAUDE_CONFIG_DIR=/claude \
  -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  "$playwright_image" \
  ./node_modules/.bin/tsx src/cli.ts "$command_name" --url "$target_url" "$@"

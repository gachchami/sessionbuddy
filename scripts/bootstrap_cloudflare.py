"""Perform SessionBuddy's guarded one-time Cloudflare administrator bootstrap."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from scripts.cloudflare_preflight import load_environment
except ModuleNotFoundError:  # Direct `python scripts/bootstrap_cloudflare.py` execution.
    from cloudflare_preflight import load_environment

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NPX = shutil.which("npx") or "/usr/local/bin/npx"


def timestamp_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset, for example +05:30")
    return int(parsed.timestamp() * 1_000)


def bootstrap_payload(arguments: argparse.Namespace) -> dict[str, str | int]:
    starts_at_ms = timestamp_ms(arguments.starts_at)
    ends_at_ms = timestamp_ms(arguments.ends_at)
    if ends_at_ms <= starts_at_ms:
        raise ValueError("--ends-at must be after --starts-at")
    if "@" not in arguments.admin_email or len(arguments.admin_email) > 320:
        raise ValueError("--admin-email must be a valid email address")
    return {
        "organization_name": arguments.organization_name,
        "event_name": arguments.event_name,
        "admin_email": arguments.admin_email,
        "starts_at_ms": starts_at_ms,
        "ends_at_ms": ends_at_ms,
        "time_zone": arguments.time_zone,
    }


def wrangler_secret(arguments: list[str], *, value: str | None = None) -> None:
    environment = {**os.environ, "CI": "1", "NO_COLOR": "1"}
    result = subprocess.run(  # noqa: S603 - arguments are assembled by this trusted CLI
        [NPX, "wrangler", "secret", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        input=f"{value}\n" if value is not None else None,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        safe_output = f"{result.stdout}\n{result.stderr}".strip()
        raise RuntimeError(f"Wrangler secret operation failed:\n{safe_output}")


def post_bootstrap(base_url: str, token: str, payload: dict[str, str | int]) -> dict:
    request = Request(  # noqa: S310 - base URL is validated as HTTPS before this call
        f"{base_url.rstrip('/')}/api/v1/bootstrap",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "user-agent": "SessionBuddy-Bootstrap/1",
            "x-bootstrap-token": token,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:  # noqa: S310 - validated HTTPS origin
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read(4_096).decode("utf-8", errors="replace")
        raise RuntimeError(f"bootstrap returned HTTP {error.code}: {body}") from error
    except URLError as error:
        raise RuntimeError(f"bootstrap request failed: {error.reason}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="dev", help="Wrangler environment name")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "wrangler.jsonc")
    parser.add_argument("--organization-name", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument(
        "--starts-at",
        required=True,
        help="ISO 8601 timestamp with UTC offset, such as 2026-11-01T09:00:00+05:30",
    )
    parser.add_argument(
        "--ends-at",
        required=True,
        help="ISO 8601 timestamp with UTC offset, such as 2026-11-01T18:00:00+05:30",
    )
    parser.add_argument("--time-zone", required=True, help="IANA name, such as Asia/Kolkata")
    arguments = parser.parse_args()
    try:
        _, variables = load_environment(arguments.config, arguments.env)
        base_url = variables.get("PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url.startswith("https://") or ".example." in base_url:
            raise ValueError("the selected environment needs an exact HTTPS PUBLIC_BASE_URL")
        payload = bootstrap_payload(arguments)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Bootstrap input error: {error}", file=sys.stderr)
        return 1

    token = secrets.token_urlsafe(48)
    installed = False
    cleanup_error: RuntimeError | None = None
    try:
        wrangler_secret(["put", "BOOTSTRAP_TOKEN", "--env", arguments.env], value=token)
        installed = True
        result = post_bootstrap(base_url, token, payload)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if installed:
            try:
                wrangler_secret(["delete", "BOOTSTRAP_TOKEN", "--env", arguments.env])
            except RuntimeError as error:
                cleanup_error = error
                print(
                    f"SECURITY ACTION REQUIRED: remove BOOTSTRAP_TOKEN manually. {error}",
                    file=sys.stderr,
                )

    if cleanup_error is not None:
        return 1
    print("Initial administrator bootstrap completed; BOOTSTRAP_TOKEN was removed.")
    print(f"Organization ID: {result['organization_id']}")
    print(f"Event ID: {result['event_id']}")
    print(f"Administrator user ID: {result['admin_user_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

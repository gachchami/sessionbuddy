"""Perform SessionBuddy's guarded one-time Cloudflare administrator bootstrap."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from scripts.cloudflare_preflight import load_environment
    from scripts.setup_key import SetupKeyError, read_setup_key
except ModuleNotFoundError:  # Direct `python scripts/bootstrap_cloudflare.py` execution.
    from cloudflare_preflight import load_environment
    from setup_key import SetupKeyError, read_setup_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def timestamp_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamps must include a UTC offset, for example +05:30")
    return int(parsed.timestamp() * 1_000)


def bootstrap_payload(arguments: argparse.Namespace) -> dict[str, str | int]:
    if "@" not in arguments.admin_email or len(arguments.admin_email) > 320:
        raise ValueError("--admin-email must be a valid email address")
    payload: dict[str, str | int] = {
        "organization_name": arguments.organization_name,
        "admin_name": arguments.admin_name,
        "admin_email": arguments.admin_email,
    }
    event_values = (
        arguments.event_name,
        arguments.starts_at,
        arguments.ends_at,
        arguments.time_zone,
        arguments.event_location,
        arguments.event_description,
        arguments.event_delivery_mode,
    )
    if not any(value is not None for value in event_values):
        return payload
    if not all(value is not None for value in event_values):
        raise ValueError("all event details must be supplied together")
    starts_at_ms = timestamp_ms(arguments.starts_at)
    ends_at_ms = timestamp_ms(arguments.ends_at)
    if ends_at_ms <= starts_at_ms:
        raise ValueError("--ends-at must be after --starts-at")
    payload.update(
        {
            "event_name": arguments.event_name,
            "starts_at_ms": starts_at_ms,
            "ends_at_ms": ends_at_ms,
            "time_zone": arguments.time_zone,
            "event_location": arguments.event_location,
            "event_description": arguments.event_description,
            "event_delivery_mode": arguments.event_delivery_mode,
        }
    )
    return payload


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
    parser.add_argument("--admin-name", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--event-name")
    parser.add_argument(
        "--starts-at",
        help="ISO 8601 timestamp with UTC offset, such as 2026-11-01T09:00:00+05:30",
    )
    parser.add_argument(
        "--ends-at",
        help="ISO 8601 timestamp with UTC offset, such as 2026-11-01T18:00:00+05:30",
    )
    parser.add_argument("--time-zone", help="IANA name, such as Asia/Kolkata")
    parser.add_argument("--event-location")
    parser.add_argument("--event-description")
    parser.add_argument(
        "--event-delivery-mode", choices=("in_person", "virtual", "hybrid")
    )
    arguments = parser.parse_args()
    try:
        _, variables = load_environment(arguments.config, arguments.env)
        base_url = variables.get("PUBLIC_BASE_URL", "").rstrip("/")
        if not base_url.startswith("https://") or ".example." in base_url:
            raise ValueError("the selected environment needs an exact HTTPS PUBLIC_BASE_URL")
        payload = bootstrap_payload(arguments)
        token = read_setup_key(arguments.env)
    except (OSError, SetupKeyError, ValueError, json.JSONDecodeError) as error:
        print(f"Bootstrap input error: {error}", file=sys.stderr)
        return 1

    try:
        result = post_bootstrap(base_url, token, payload)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 1
    print("Initial administrator bootstrap completed; the setup key was permanently consumed.")
    print(f"Organization ID: {result['organization_id']}")
    if result.get("event_id"):
        print(f"Event ID: {result['event_id']}")
    else:
        print("Event: none (create the first event from the administrator UI)")
    print(f"Administrator user ID: {result['admin_user_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

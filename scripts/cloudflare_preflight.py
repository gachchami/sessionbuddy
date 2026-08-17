"""Audit a deployed SessionBuddy Cloudflare environment without changing it."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from scripts.render_private_cloudflare_config import DEMO_USER_ID_VALUES, load_jsonc
except ModuleNotFoundError:  # Direct execution from scripts/.
    from render_private_cloudflare_config import DEMO_USER_ID_VALUES, load_jsonc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NPX = shutil.which("npx") or "/usr/local/bin/npx"
CORE_SECRETS = {
    "CSRF_HMAC_KEY",
    "RATE_LIMIT_HMAC_KEY",
    "SESSION_HMAC_KEY",
    "UPLOAD_HMAC_KEY",
}
EMAIL_SECRETS = {"RESEND_API_KEY"}
R2_UPLOAD_SECRETS = {"R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"}


@dataclass(frozen=True)
class Check:
    state: Literal["PASS", "PENDING", "FAIL"]
    label: str
    detail: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str


def run_command(arguments: list[str]) -> CommandResult:
    environment = {**os.environ, "CI": "1", "NO_COLOR": "1"}
    result = subprocess.run(  # noqa: S603 - arguments are fixed by internal call sites
        arguments,
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = result.stdout.strip()
    if result.returncode != 0 and result.stderr:
        output = f"{output}\n{result.stderr}".strip()
    return CommandResult(result.returncode, output)


def load_environment(path: Path) -> tuple[dict, dict[str, str]]:
    """Load one concrete Wrangler target; deployed configs must be flat."""
    config = load_jsonc(path)
    if "env" in config:
        raise ValueError("use a rendered target config without Wrangler environments")
    variables = config.get("vars", {})
    return config, variables


def require_remote_environment(variables: dict[str, str]) -> None:
    """Refuse the tracked local template as a remote audit target."""
    app_environment = variables.get("APP_ENV", "").strip().lower()
    if app_environment not in {"development", "staging", "production"}:
        raise ValueError(
            "remote preflight requires APP_ENV to be development, staging, or production"
        )


def configured_queue_names(environment: dict) -> set[str]:
    queues = environment.get("queues", {})
    names = {
        item["queue"]
        for group in ("producers", "consumers")
        for item in queues.get(group, [])
        if item.get("queue")
    }
    names.update(
        item["dead_letter_queue"]
        for item in queues.get("consumers", [])
        if item.get("dead_letter_queue")
    )
    return names


def static_configuration_checks(environment: dict, variables: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    app_environment = variables.get("APP_ENV", "")
    base_url = variables.get("PUBLIC_BASE_URL", "").rstrip("/")
    allowed_origins = {
        value.strip() for value in variables.get("ALLOWED_ORIGINS", "").split(",") if value.strip()
    }
    placeholder = ".example." in base_url or "YOUR-" in base_url
    checks.append(
        Check(
            "PASS" if base_url.startswith("https://") and not placeholder else "FAIL",
            "public origin",
            base_url or "PUBLIC_BASE_URL is missing",
        )
    )
    checks.append(
        Check(
            "PASS" if base_url in allowed_origins else "FAIL",
            "origin allowlist",
            "PUBLIC_BASE_URL is allowlisted" if base_url in allowed_origins else "origin mismatch",
        )
    )
    # Demo sign-in mints a session without a credential. Match the runtime's
    # explicit environment allowlist so copied configuration fails closed.
    demo_login = variables.get("DEMO_LOGIN_ENABLED", "").strip().lower()
    demo_identities = {
        name: variables.get(name, "").strip() for name in DEMO_USER_ID_VALUES
    }
    if demo_login == "true" and app_environment not in {"local", "development"}:
        checks.append(
            Check(
                "FAIL",
                "demo sign-in",
                "password-free demo login is allowed only in local or development; "
                f"got {app_environment or 'an unnamed environment'}",
            )
        )
    elif demo_login == "true" and not all(demo_identities.values()):
        checks.append(
            Check(
                "FAIL",
                "demo sign-in",
                "every DEMO_*_USER_ID must be set when demo login is enabled",
            )
        )
    elif demo_login == "true" and demo_identities != DEMO_USER_ID_VALUES:
        checks.append(
            Check(
                "FAIL",
                "demo sign-in",
                "DEMO_*_USER_ID values must match the seed-owned demo personas",
            )
        )
    elif demo_login == "true":
        checks.append(Check("PASS", "demo sign-in", f"explicit {app_environment} demo personas"))
    else:
        checks.append(Check("PASS", "demo sign-in", "disabled"))
    scan_mode = variables.get("MALWARE_SCAN_MODE", "required")
    if scan_mode == "disabled" and app_environment not in {"local", "development"}:
        checks.append(
            Check("FAIL", "malware scanning", "bypass is forbidden outside development")
        )
    elif scan_mode == "disabled":
        checks.append(
            Check("PASS", "malware scanning", f"explicit {app_environment} bypass")
        )
    elif not variables.get("SCANNER_URL"):
        checks.append(Check("PENDING", "malware scanning", "SCANNER_URL is required"))
    else:
        checks.append(Check("PASS", "malware scanning", "required scanner endpoint configured"))
    if environment.get("containers"):
        checks.append(
            Check("FAIL", "Cloudflare Containers", "unexpected container binding configured")
        )
    else:
        checks.append(Check("PASS", "Cloudflare Containers", "none configured"))
    for name in ("CLOUDFLARE_ACCOUNT_ID", "R2_BUCKET_NAME"):
        checks.append(
            Check(
                "PASS" if variables.get(name) else "FAIL",
                name,
                "configured" if variables.get(name) else "missing",
            )
        )
    sender = variables.get("RESEND_FROM_ADDRESS", "")
    sender_ready = bool(sender) and "example.com" not in sender and "example.test" not in sender
    checks.append(
        Check(
            "PASS" if sender_ready else "PENDING",
            "email sender",
            sender if sender else "RESEND_FROM_ADDRESS is missing",
        )
    )
    return checks


def secret_checks(secret_names: set[str], organization_count: int) -> list[Check]:
    checks: list[Check] = []
    missing_core = sorted(CORE_SECRETS - secret_names)
    checks.append(
        Check(
            "FAIL" if missing_core else "PASS",
            "application secrets",
            f"missing: {', '.join(missing_core)}" if missing_core else "all core secrets installed",
        )
    )
    missing_email = sorted(EMAIL_SECRETS - secret_names)
    checks.append(
        Check(
            "PENDING" if missing_email else "PASS",
            "Resend activation",
            f"missing: {', '.join(missing_email)}" if missing_email else "secret installed",
        )
    )
    missing_r2 = sorted(R2_UPLOAD_SECRETS - secret_names)
    checks.append(
        Check(
            "PENDING" if missing_r2 else "PASS",
            "direct R2 uploads",
            f"missing: {', '.join(missing_r2)}" if missing_r2 else "upload credentials installed",
        )
    )
    bootstrap_missing = organization_count == 0
    checks.append(
        Check(
            "PENDING" if bootstrap_missing else "PASS",
            "initial bootstrap",
            (
                "organization database is empty; use the migration-generated setup key"
                if bootstrap_missing
                else "already bootstrapped"
            ),
        )
    )
    return checks


def request_status(url: str) -> tuple[int, str]:
    request = Request(  # noqa: S310 - caller supplies the validated HTTPS deployment origin
        url, headers={"user-agent": "SessionBuddy-Preflight/1"}
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured HTTPS URL
            return response.status, response.read(4_096).decode("utf-8", errors="replace")
    except HTTPError as error:
        return error.code, error.read(4_096).decode("utf-8", errors="replace")
    except URLError as error:
        return 0, str(error.reason)


def parse_organization_count(output: str) -> int:
    payload = json.loads(output)
    return int(payload[0]["results"][0]["organization_count"])


def r2_cors_ready(output: str, public_origin: str) -> bool:
    values: dict[str, str] = {}
    for line in output.splitlines():
        label, separator, value = line.partition(":")
        if separator:
            values[label.strip()] = value.strip()
    expected = {
        "allowed_origins": public_origin,
        "allowed_methods": "PUT",
        "allowed_headers": "Content-Type",
        "exposed_headers": "ETag",
        "max_age_seconds": "3600",
    }
    return all(values.get(label) == value for label, value in expected.items())


def remote_checks(
    config_path: Path,
    environment: dict,
    variables: dict[str, str],
    *,
    allow_pending_migrations: bool = False,
) -> tuple[list[Check], int, set[str]]:
    checks: list[Check] = []
    whoami = run_command([NPX, "wrangler", "whoami"])
    checks.append(
        Check(
            "PASS" if whoami.returncode == 0 and "logged in" in whoami.output else "FAIL",
            "Wrangler authentication",
            "authenticated" if whoami.returncode == 0 else "not authenticated",
        )
    )

    migrations = run_command(
        [
            NPX,
            "wrangler",
            "d1",
            "migrations",
            "list",
            "DB",
            "--remote",
            "--config",
            str(config_path),
        ]
    )
    migrations_ready = migrations.returncode == 0 and "No migrations to apply" in migrations.output
    migrations_pending = migrations.returncode == 0 and not migrations_ready
    migration_state: Literal["PASS", "PENDING", "FAIL"] = (
        "PASS"
        if migrations_ready
        else "PENDING"
        if migrations_pending and allow_pending_migrations
        else "FAIL"
    )
    checks.append(
        Check(
            migration_state,
            "D1 migrations",
            (
                "no pending migrations"
                if migrations_ready
                else "pending migrations explicitly allowed before reviewed migration application"
                if migration_state == "PENDING"
                else "migration check failed or migrations pending"
            ),
        )
    )

    database = run_command(
        [
            NPX,
            "wrangler",
            "d1",
            "execute",
            "DB",
            "--remote",
            "--config",
            str(config_path),
            "--command",
            "SELECT COUNT(*) AS organization_count FROM organizations",
            "--json",
        ]
    )
    try:
        organization_count = parse_organization_count(database.output)
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
        organization_count = -1
    checks.append(
        Check(
            "PASS" if database.returncode == 0 and organization_count >= 0 else "FAIL",
            "D1 query",
            (
                f"organization count: {organization_count}"
                if organization_count >= 0
                else "remote organization query failed"
            ),
        )
    )

    r2_name = variables.get("R2_BUCKET_NAME", "")
    r2 = run_command(
        [NPX, "wrangler", "r2", "bucket", "info", r2_name, "--config", str(config_path)]
    )
    checks.append(
        Check(
            "PASS" if r2_name and r2.returncode == 0 else "FAIL",
            "R2 bucket",
            r2_name if r2.returncode == 0 else "configured bucket is unavailable",
        )
    )

    base_url = variables.get("PUBLIC_BASE_URL", "").rstrip("/")
    r2_cors = run_command(
        [
            NPX,
            "wrangler",
            "r2",
            "bucket",
            "cors",
            "list",
            r2_name,
            "--config",
            str(config_path),
        ]
    )
    cors_ready = r2_cors.returncode == 0 and r2_cors_ready(r2_cors.output, base_url)
    checks.append(
        Check(
            "PASS" if cors_ready else "FAIL",
            "R2 browser upload CORS",
            (
                "exact Worker origin, PUT, and Content-Type are allowed"
                if cors_ready
                else "apply the checked-in development R2 CORS policy"
            ),
        )
    )

    queue_names = configured_queue_names(environment)
    queues = run_command([NPX, "wrangler", "queues", "list", "--config", str(config_path)])
    missing_queues = sorted(name for name in queue_names if name not in queues.output)
    checks.append(
        Check(
            "PASS" if queues.returncode == 0 and not missing_queues else "FAIL",
            "Queues and DLQs",
            (
                f"missing: {', '.join(missing_queues)}"
                if missing_queues
                else "all configured queues exist"
            ),
        )
    )

    secrets = run_command(
        [
            NPX,
            "wrangler",
            "secret",
            "list",
            "--config",
            str(config_path),
            "--format",
            "json",
        ]
    )
    try:
        secret_names = {str(item["name"]) for item in json.loads(secrets.output)}
    except (KeyError, TypeError, json.JSONDecodeError):
        secret_names = set()
    checks.append(
        Check(
            "PASS" if secrets.returncode == 0 else "FAIL",
            "secret inventory",
            (
                f"{len(secret_names)} secret names read"
                if secrets.returncode == 0
                else "inventory failed"
            ),
        )
    )

    health_status, health_body = request_status(f"{base_url}/health")
    health_ready = health_status == 200 and '"status":"ok"' in health_body.replace(" ", "")
    checks.append(
        Check(
            "PASS" if health_ready else "FAIL",
            "deployed Worker health",
            f"HTTP {health_status}" if health_status else health_body,
        )
    )
    for path in ("/sign-in", "/admin/events", "/admin"):
        status, _ = request_status(f"{base_url}{path}")
        checks.append(
            Check(
                "PASS" if status == 200 else "FAIL",
                f"browser route {path}",
                f"HTTP {status}",
            )
        )
    session_status, _ = request_status(f"{base_url}/api/v1/auth/session")
    checks.append(
        Check(
            "PASS" if session_status == 401 else "FAIL",
            "anonymous identity boundary",
            f"HTTP {session_status}; expected 401",
        )
    )
    return checks, organization_count, secret_names


def print_report(checks: list[Check]) -> None:
    for check in checks:
        print(f"{check.state:7} {check.label}: {check.detail}")
    counts = {
        state: sum(check.state == state for check in checks)
        for state in ("PASS", "PENDING", "FAIL")
    }
    print(
        "\nSummary: "
        f"{counts['PASS']} passed, {counts['PENDING']} pending activation, {counts['FAIL']} failed"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--deployment-only",
        action="store_true",
        help="return success when only provider/bootstrap activation remains",
    )
    parser.add_argument(
        "--allow-pending-migrations",
        action="store_true",
        help="permit a successful pending-migration listing during pre-mutation review",
    )
    arguments = parser.parse_args()
    if arguments.allow_pending_migrations and not arguments.deployment_only:
        parser.error("--allow-pending-migrations requires --deployment-only")
    try:
        environment, variables = load_environment(arguments.config)
        require_remote_environment(variables)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL configuration: {error}", file=sys.stderr)
        return 1
    checks = static_configuration_checks(environment, variables)
    remote, organization_count, secret_names = remote_checks(
        arguments.config,
        environment,
        variables,
        allow_pending_migrations=arguments.allow_pending_migrations,
    )
    checks.extend(remote)
    checks.extend(secret_checks(secret_names, organization_count))
    print_report(checks)
    if any(check.state == "FAIL" for check in checks):
        return 1
    if not arguments.deployment_only and any(check.state == "PENDING" for check in checks):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

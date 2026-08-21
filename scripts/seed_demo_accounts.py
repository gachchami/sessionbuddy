# ruff: noqa: S608
"""Create and repair the three demo personas used by password-free sign-in.

This is a data seed, never a migration. It creates one synthetic organizer and
validates that the configured reviewer and speaker identities are usable, so a
demo control is only ever offered for an account that can actually sign in and
land on a populated workspace.

The synthetic organizer receives a ``manage`` grant on the demo organization
rather than ownership. Ownership cannot be revoked through the product, so a
demo identity must never hold it.

Usage::

    python scripts/seed_demo_accounts.py --local --create-credentials
    python scripts/seed_demo_accounts.py --local --reset
    python scripts/seed_demo_accounts.py --env dev --organization-id <id>
    python scripts/seed_demo_accounts.py --env dev2 --config <private-config> \
        --pepper-file <private-pepper>
    python scripts/seed_demo_accounts.py --local --verify-only
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import stat
import subprocess
import sys
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import NAMESPACE_URL, uuid5

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_password_hasher():
    """Load the verifier primitives directly, without importing the web app.

    Seeding must not require FastAPI or the Worker runtime, so this bypasses
    the ``sessionbuddy.platform.auth`` package initialiser.
    """
    path = PROJECT_ROOT / "src" / "sessionbuddy" / "platform" / "auth" / "passwords.py"
    spec = importlib.util.spec_from_file_location("sessionbuddy_demo_passwords", path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging failure
        raise RuntimeError(f"could not load password primitives from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.hash_password


hash_password = _load_password_hasher()

NPX = "npx"
SEED_NAMESPACE = "sessionbuddy:demo-accounts-v1"
ALLOWED_ENVIRONMENTS = {"local", "development"}
DEMO_ORGANIZER_EMAIL = "demo-organizer@sessionbuddy.demo"
DEMO_ORGANIZER_NAME = "Dana Demo"
DEFAULT_CREDENTIALS_PATH = PROJECT_ROOT / ".local" / "demo-account-credentials.json"
DEMO_EMAILS = {
    "organizer": DEMO_ORGANIZER_EMAIL,
    "reviewer": "demo-reviewer@sessionbuddy.demo",
    "speaker": "demo-speaker@sessionbuddy.demo",
}
DEMO_NAMES = {
    "organizer": ("Dana Demo", "Dana", "Demo"),
    "reviewer": ("Riley Reviewer", "Riley", "Reviewer"),
    "speaker": ("Sasha Speaker", "Sasha", "Speaker"),
}
DEMO_USER_ID_VARIABLES = {
    "organizer": "DEMO_ORGANIZER_USER_ID",
    "reviewer": "DEMO_REVIEWER_USER_ID",
    "speaker": "DEMO_SPEAKER_USER_ID",
}
WRANGLER_CONFIG: Path | None = None


class SeedError(RuntimeError):
    """A demo seed refused to run or could not complete safely."""


def stable_id(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{SEED_NAMESPACE}:{label}"))


ORGANIZER_USER_ID = stable_id("user:demo-organizer")
ORGANIZER_MEMBERSHIP_ID = stable_id("org-member:demo-organizer")
ORGANIZER_GRANT_ID = stable_id("grant:demo-organizer")
REVIEWER_USER_ID = stable_id("user:demo-reviewer")
SPEAKER_USER_ID = stable_id("user:demo-speaker")
DEMO_USER_IDS = {
    "organizer": ORGANIZER_USER_ID,
    "reviewer": REVIEWER_USER_ID,
    "speaker": SPEAKER_USER_ID,
}


def run_wrangler(arguments: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "CI": "1", "NO_COLOR": "1"}
    config = ["--config", str(WRANGLER_CONFIG)] if WRANGLER_CONFIG is not None else []
    return subprocess.run(  # noqa: S603 - fixed executable and validated arguments
        [NPX, "wrangler", *config, *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def d1_target(environment_name: str, *, local: bool) -> list[str]:
    return ["--local"] if local else ["--remote", "--env", environment_name]


def execute_sql(environment_name: str, sql: str, *, local: bool) -> list[dict]:
    result = run_wrangler(
        [
            "d1",
            "execute",
            "DB",
            *d1_target(environment_name, local=local),
            "--command",
            sql,
            "--json",
        ]
    )
    if result.returncode != 0:
        detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise SeedError(detail or "Wrangler D1 execution failed without diagnostic output")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SeedError("Wrangler returned an invalid D1 response") from error
    if not isinstance(payload, list) or any(not item.get("success") for item in payload):
        raise SeedError("D1 did not report success for every demo seed statement")
    return payload


def rows(payload: list[dict]) -> list[dict]:
    try:
        results = payload[0]["results"]
    except (IndexError, KeyError, TypeError) as error:
        raise SeedError("D1 returned an unexpected response shape") from error
    return results if isinstance(results, list) else []


def literal(value: str | int | None) -> str:
    """Render a SQL literal. Every interpolated value here is script-owned."""
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + value.replace("'", "''") + "'"


def create_credentials_file(path: Path) -> None:
    """Create one private, reusable credential set without printing passwords."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "version": 1,
        "accounts": {
            role: {"email": email, "password": secrets.token_urlsafe(24)}
            for role, email in DEMO_EMAILS.items()
        },
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise SeedError(f"credential file already exists: {path}") from error
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def load_credentials(path: Path) -> dict[str, dict[str, str]]:
    """Load a mode-0600 credential file and validate its public identity fields."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError as error:
        raise SeedError(
            f"credential file is missing: {path}; run once with --create-credentials"
        ) from error
    if mode & 0o077:
        raise SeedError(f"credential file must not be group/world accessible: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise SeedError(f"could not read credential file: {path}") from error
    accounts = payload.get("accounts") if isinstance(payload, dict) else None
    if payload.get("version") != 1 or not isinstance(accounts, dict):
        raise SeedError("unsupported demo credential file format")
    validated: dict[str, dict[str, str]] = {}
    for role, expected_email in DEMO_EMAILS.items():
        account = accounts.get(role)
        if not isinstance(account, dict):
            raise SeedError(f"credential file is missing the {role} account")
        email = str(account.get("email", "")).strip().lower()
        password = str(account.get("password", ""))
        if email != expected_email:
            raise SeedError(
                f"credential file {role} email must be {expected_email}; got {email or '<empty>'}"
            )
        if len(password) < 15:
            raise SeedError(f"credential file {role} password is too short")
        validated[role] = {"email": email, "password": password}
    return validated


def read_pepper(path: Path | None) -> str:
    if path is None:
        return os.environ.get("PASSWORD_PEPPER", "")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise SeedError(f"could not read password pepper file: {path}") from error


def _runtime_request(
    opener, url: str, *, method: str = "GET", body: dict | None = None, csrf: str | None = None
) -> dict:
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    headers = {
        "accept": "application/json",
        "origin": origin,
        "referer": f"{origin}/sign-in",
        "sec-fetch-site": "same-origin",
        "user-agent": "SessionBuddy demo credential provisioner",
    }
    data = None
    if body is not None:
        headers["content-type"] = "application/json"
        data = json.dumps(body).encode()
    if csrf:
        headers["x-csrf-token"] = csrf
    # The only caller first constrains the base URL to an HTTPS origin and
    # appends fixed application paths; redirects remain handled by urllib.
    request = Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with opener.open(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as error:
        raise SeedError(
            f"demo runtime returned HTTP {error.code} for {urlparse(url).path}"
        ) from error
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise SeedError(f"demo runtime request failed for {urlparse(url).path}") from error
    if not isinstance(payload, dict):
        raise SeedError(f"demo runtime returned an invalid response for {urlparse(url).path}")
    return payload


def provision_runtime_credentials(base_url: str, credentials: dict[str, dict[str, str]]) -> None:
    """Rotate existing demo personas through the deployed app's own pepper."""
    parsed = urlparse(base_url)
    loopback_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (
        (parsed.scheme != "https" and not loopback_http)
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise SeedError("--app-url must be an HTTPS or loopback HTTP origin without credentials")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for role, account in credentials.items():
        opener = build_opener(HTTPCookieProcessor(CookieJar()))
        session = _runtime_request(
            opener,
            f"{origin}/api/v1/auth/demo-sign-in",
            method="POST",
            body={"role": role, "redirect_path": "/account"},
        )
        csrf = str(session.get("csrf_token", ""))
        if not session.get("authenticated") or not csrf:
            raise SeedError(f"{role}: demo sign-in did not establish a session")
        profile = _runtime_request(opener, f"{origin}/api/v1/account/profile")
        if str(profile.get("email", "")).strip().lower() != account["email"]:
            raise SeedError(f"{role}: configured demo endpoint resolved an unexpected account")
        first_name = str(profile.get("first_name") or "").strip()
        last_name = str(profile.get("last_name") or "").strip()
        if not first_name or not last_name:
            raise SeedError(f"{role}: demo profile is incomplete; refusing a partial profile write")
        update = {
            "first_name": first_name,
            "last_name": last_name,
            "job_title": profile.get("job_title"),
            "company": profile.get("company"),
            "time_zone": profile.get("time_zone"),
            "description": profile.get("description"),
            "website_url": profile.get("website_url"),
            "linkedin_url": profile.get("linkedin_url"),
            "x_url": profile.get("x_url"),
            "public_profile_enabled": bool(profile.get("public_profile_enabled")),
            "password": account["password"],
            "password_confirmation": account["password"],
            "version": int(profile["version"]),
        }
        updated = _runtime_request(
            opener,
            f"{origin}/api/v1/account/profile",
            method="PATCH",
            body=update,
            csrf=csrf,
        )
        if not updated.get("has_password"):
            raise SeedError(f"{role}: password rotation was not confirmed")
        verification = _runtime_request(
            build_opener(HTTPCookieProcessor(CookieJar())),
            f"{origin}/api/v1/auth/password/sign-in",
            method="POST",
            body={
                "email": account["email"],
                "password": account["password"],
                "redirect_path": "/",
            },
        )
        if verification.get("user_id") != DEMO_USER_IDS[role]:
            raise SeedError(f"{role}: password sign-in did not resolve the expected demo account")
        print(f"  ok  {role}: password rotated through {parsed.netloc}")


def assert_demo_login_enabled(environment_name: str, *, local: bool) -> None:
    """Only seed where the runtime environment and capability both permit it."""
    payload = execute_sql(environment_name, "SELECT 1 AS reachable", local=local)
    if not rows(payload):
        raise SeedError("could not reach the target database")
    app_env = os.environ.get("APP_ENV", "local" if local else "development").strip().lower()
    if app_env not in ALLOWED_ENVIRONMENTS:
        raise SeedError(
            f"refusing to seed demo identities with APP_ENV={app_env!r}; "
            "only local and development are supported"
        )
    if os.environ.get("DEMO_LOGIN_ENABLED", "").strip().lower() != "true":
        raise SeedError(
            "refusing to seed demo identities: set DEMO_LOGIN_ENABLED=true for the "
            "target environment first, so seeding and sign-in agree on one switch"
        )


def resolve_organization(environment_name: str, *, local: bool, requested: str | None) -> str:
    if requested:
        found = rows(
            execute_sql(
                environment_name,
                f"SELECT id FROM organizations WHERE id={literal(requested)} LIMIT 1",
                local=local,
            )
        )
        if not found:
            raise SeedError(f"organization {requested} does not exist")
        return requested
    found = rows(
        execute_sql(
            environment_name,
            "SELECT id,name FROM organizations ORDER BY created_at_ms LIMIT 2",
            local=local,
        )
    )
    if not found:
        raise SeedError("no organization exists; bootstrap the instance first")
    if len(found) > 1:
        raise SeedError(
            "multiple organizations exist; pass --organization-id to choose the demo tenant"
        )
    return str(found[0]["id"])


def owner_of(environment_name: str, organization_id: str, *, local: bool) -> str:
    found = rows(
        execute_sql(
            environment_name,
            "SELECT owner_user_id FROM owned_resources "
            f"WHERE id={literal(organization_id)} AND resource_type='organization' LIMIT 1",
            local=local,
        )
    )
    if not found:
        raise SeedError(f"organization {organization_id} has no ownership record")
    return str(found[0]["owner_user_id"])


def verify_persona(
    environment_name: str, *, local: bool, role: str, user_id: str
) -> tuple[bool, str]:
    """Report whether a configured persona can actually sign in as that role."""
    if not user_id:
        return False, f"{role}: no user id configured"
    found = rows(
        execute_sql(
            environment_name,
            "SELECT u.id,u.display_name,u.status,r.role,r.is_default,"
            "(SELECT COUNT(*) FROM password_credentials c "
            " WHERE c.user_id=u.id AND c.status='active') AS credentials "
            "FROM users u LEFT JOIN user_roles r "
            f"  ON r.user_id=u.id AND r.role={literal(role)} AND r.status='active' "
            f"WHERE u.id={literal(user_id)} LIMIT 1",
            local=local,
        )
    )
    if not found:
        return False, f"{role}: user {user_id} does not exist"
    record = found[0]
    if str(record.get("status")) != "active":
        return False, f"{role}: user {user_id} is not active"
    if record.get("role") is None:
        return False, f"{role}: user {user_id} does not hold an active {role} role"
    notes = []
    if not record.get("is_default"):
        notes.append("role is not the account default")
    if not record.get("credentials"):
        notes.append("no active password credential, so only demo sign-in will work")
    name = record.get("display_name") or user_id
    suffix = f" ({'; '.join(notes)})" if notes else ""
    return True, f"{role}: {name}{suffix}"


def organizer_statements(
    organization_id: str, owner_user_id: str, verifier: str, now_ms: int
) -> list[str]:
    """Idempotent upserts for the synthetic organizer identity."""
    return [
        "INSERT INTO users"
        " (id,email,normalized_email,display_name,first_name,last_name,status,"
        "  email_verified_at_ms,profile_completed_at_ms,created_at_ms,updated_at_ms)"
        f" VALUES({literal(ORGANIZER_USER_ID)},{literal(DEMO_ORGANIZER_EMAIL)},"
        f" {literal(DEMO_ORGANIZER_EMAIL)},{literal(DEMO_ORGANIZER_NAME)},'Dana','Demo','active',"
        f" {now_ms},{now_ms},{now_ms},{now_ms})"
        " ON CONFLICT(id) DO UPDATE SET status='active',deleted_at_ms=NULL,"
        f" display_name={literal(DEMO_ORGANIZER_NAME)},"
        f" profile_completed_at_ms=COALESCE(profile_completed_at_ms,{now_ms}),"
        f" updated_at_ms={now_ms}",
        "INSERT INTO user_roles (user_id,role,status,created_at_ms,updated_at_ms,is_default)"
        f" VALUES({literal(ORGANIZER_USER_ID)},'organizer','active',{now_ms},{now_ms},1)"
        " ON CONFLICT(user_id,role) DO UPDATE SET status='active',revoked_at_ms=NULL,"
        f" is_default=1,updated_at_ms={now_ms}",
        # Revoke first, then bump before verifier rotation. A database trigger
        # rejects password changes while any active session carries an equal or
        # newer authorization version, including a session created during a
        # concurrent demo visit.
        f"UPDATE sessions SET revoked_at_ms={now_ms},revoke_reason='demo_password_rotated'"
        f" WHERE user_id={literal(ORGANIZER_USER_ID)} AND revoked_at_ms IS NULL",
        "UPDATE users SET authorization_version=authorization_version+1,"
        f" updated_at_ms={now_ms} WHERE id={literal(ORGANIZER_USER_ID)}",
        "INSERT INTO password_credentials"
        " (user_id,verifier_phc,pepper_version,status,created_at_ms,updated_at_ms)"
        f" VALUES({literal(ORGANIZER_USER_ID)},{literal(verifier)},1,'active',{now_ms},{now_ms})"
        f" ON CONFLICT(user_id) DO UPDATE SET verifier_phc={literal(verifier)},"
        f" status='active',updated_at_ms={now_ms}",
        "INSERT INTO organization_memberships"
        " (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)"
        f" VALUES({literal(ORGANIZER_MEMBERSHIP_ID)},{literal(organization_id)},"
        f" {literal(ORGANIZER_USER_ID)},'organization_admin','active',{now_ms},{now_ms})"
        " ON CONFLICT(organization_id,user_id) DO UPDATE SET status='active',"
        f" role='organization_admin',revoked_at_ms=NULL,updated_at_ms={now_ms}",
        # A manage grant, not ownership: ownership cannot be revoked through the
        # product, so a demo identity must never hold it.
        "INSERT INTO resource_access_grants"
        " (id,resource_id,user_id,permission,status,granted_by_user_id,"
        "  created_at_ms,updated_at_ms)"
        f" VALUES({literal(ORGANIZER_GRANT_ID)},{literal(organization_id)},"
        f" {literal(ORGANIZER_USER_ID)},'manage','active',{literal(owner_user_id)},"
        f" {now_ms},{now_ms})"
        " ON CONFLICT(resource_id,user_id,permission) DO UPDATE SET status='active',"
        f" revoked_at_ms=NULL,revoked_by_user_id=NULL,updated_at_ms={now_ms}",
    ]


def complete_persona_profile_statement(user_id: str, now_ms: int) -> str:
    """Keep a configured demo persona out of mandatory profile onboarding.

    Demo personas are preconfigured accounts, so routing them through the
    first-run account form defeats the one-click workspace entry point. The
    update preserves an existing completion timestamp and only repairs older
    demo seeds where it is absent.
    """
    return (
        "UPDATE users SET "
        f"profile_completed_at_ms=COALESCE(profile_completed_at_ms,{now_ms}),"
        f"updated_at_ms={now_ms} WHERE id={literal(user_id)}"
    )


def password_credential_statement(user_id: str, verifier: str, now_ms: int) -> str:
    """Rotate one configured demo persona without affecting any other account."""
    return (
        "INSERT INTO password_credentials"
        " (user_id,verifier_phc,pepper_version,status,created_at_ms,updated_at_ms)"
        f" VALUES({literal(user_id)},{literal(verifier)},1,'active',{now_ms},{now_ms})"
        f" ON CONFLICT(user_id) DO UPDATE SET verifier_phc={literal(verifier)},"
        f" pepper_version=1,status='active',updated_at_ms={now_ms}"
    )


def persona_statements(organization_id: str, *, role: str, verifier: str, now_ms: int) -> list[str]:
    """Create or repair a non-organizer demo persona with stable identity."""
    user_id = DEMO_USER_IDS[role]
    email = DEMO_EMAILS[role]
    display_name, first_name, last_name = DEMO_NAMES[role]
    membership_id = stable_id(f"org-member:demo-{role}")
    return [
        "INSERT INTO users"
        " (id,email,normalized_email,display_name,first_name,last_name,status,"
        "  email_verified_at_ms,profile_completed_at_ms,created_at_ms,updated_at_ms)"
        f" VALUES({literal(user_id)},{literal(email)},{literal(email)},"
        f" {literal(display_name)},{literal(first_name)},{literal(last_name)},'active',"
        f" {now_ms},{now_ms},{now_ms},{now_ms})"
        " ON CONFLICT(id) DO UPDATE SET status='active',deleted_at_ms=NULL,"
        f" display_name={literal(display_name)},first_name={literal(first_name)},"
        f" last_name={literal(last_name)},"
        f" profile_completed_at_ms=COALESCE(profile_completed_at_ms,{now_ms}),"
        f" updated_at_ms={now_ms}",
        "INSERT INTO user_roles (user_id,role,status,created_at_ms,updated_at_ms,is_default)"
        f" VALUES({literal(user_id)},{literal(role)},'active',{now_ms},{now_ms},1)"
        " ON CONFLICT(user_id,role) DO UPDATE SET status='active',revoked_at_ms=NULL,"
        f" is_default=1,updated_at_ms={now_ms}",
        f"UPDATE sessions SET revoked_at_ms={now_ms},revoke_reason='demo_password_rotated'"
        f" WHERE user_id={literal(user_id)} AND revoked_at_ms IS NULL",
        "UPDATE users SET authorization_version=authorization_version+1,"
        f" updated_at_ms={now_ms} WHERE id={literal(user_id)}",
        password_credential_statement(user_id, verifier, now_ms),
        "INSERT INTO organization_memberships"
        " (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)"
        f" VALUES({literal(membership_id)},{literal(organization_id)},"
        f" {literal(user_id)},'member','active',{now_ms},{now_ms})"
        " ON CONFLICT(organization_id,user_id) DO UPDATE SET status='active',"
        f" role='member',revoked_at_ms=NULL,updated_at_ms={now_ms}",
    ]


def require_expected_persona(
    environment_name: str,
    *,
    local: bool,
    role: str,
    user_id: str,
    expected_email: str,
) -> None:
    """Fail before writes if configuration points at an unexpected identity."""
    found = rows(
        execute_sql(
            environment_name,
            "SELECT u.normalized_email,r.role FROM users u JOIN user_roles r ON r.user_id=u.id "
            f"WHERE u.id={literal(user_id)} AND u.status='active' AND u.deleted_at_ms IS NULL "
            f"AND r.role={literal(role)} AND r.status='active' LIMIT 1",
            local=local,
        )
    )
    if not found:
        raise SeedError(f"configured {role} demo account is missing or inactive: {user_id}")
    actual_email = str(found[0].get("normalized_email", "")).strip().lower()
    if actual_email != expected_email:
        raise SeedError(
            f"configured {role} demo user has email {actual_email or '<empty>'}, "
            f"expected {expected_email}; refusing to rotate the wrong account"
        )


def require_safe_persona_target(
    environment_name: str,
    *,
    local: bool,
    role: str,
    user_id: str,
    expected_email: str,
) -> None:
    """Allow a missing deterministic account, but never overwrite another identity."""
    found = rows(
        execute_sql(
            environment_name,
            f"SELECT normalized_email FROM users WHERE id={literal(user_id)} LIMIT 1",
            local=local,
        )
    )
    if not found:
        return
    actual_email = str(found[0].get("normalized_email", "")).strip().lower()
    if actual_email != expected_email:
        raise SeedError(
            f"configured {role} demo id belongs to {actual_email or '<empty>'}, "
            f"expected {expected_email}; refusing to overwrite it"
        )


def reset_statements(now_ms: int) -> list[str]:
    """Retire the synthetic organizer's demo-only relationships.

    Only rows this script owns are touched. Nothing belonging to a real
    account, and no submission, evaluation, or agenda content, is removed.
    """
    return [
        "DELETE FROM session_active_roles WHERE user_id=" + literal(ORGANIZER_USER_ID),
        f"UPDATE sessions SET revoked_at_ms={now_ms},revoke_reason='demo_reset'"
        f" WHERE user_id={literal(ORGANIZER_USER_ID)} AND revoked_at_ms IS NULL",
        f"DELETE FROM resource_access_grants WHERE id={literal(ORGANIZER_GRANT_ID)}",
        f"DELETE FROM organization_memberships WHERE id={literal(ORGANIZER_MEMBERSHIP_ID)}",
        f"DELETE FROM password_credentials WHERE user_id={literal(ORGANIZER_USER_ID)}",
        f"DELETE FROM user_roles WHERE user_id={literal(ORGANIZER_USER_ID)}",
        f"DELETE FROM users WHERE id={literal(ORGANIZER_USER_ID)}",
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--local", action="store_true", help="seed the local D1 database")
    target.add_argument("--env", dest="environment", help="wrangler environment name, e.g. dev")
    target.add_argument(
        "--app-url",
        help="rotate existing remote demo passwords through the gated application endpoint",
    )
    parser.add_argument("--organization-id", help="demo tenant; required when several exist")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="remove the synthetic organizer and its grants, then seed again",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="validate the configured personas without writing anything",
    )
    parser.add_argument(
        "--credentials-file",
        type=Path,
        default=DEFAULT_CREDENTIALS_PATH,
        help="ignored mode-0600 JSON file holding all three demo credentials",
    )
    parser.add_argument(
        "--create-credentials",
        action="store_true",
        help="create the credential file if it is absent, then provision from it",
    )
    parser.add_argument(
        "--pepper-file",
        type=Path,
        help="read PASSWORD_PEPPER from this private file instead of the environment",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Wrangler config to use (for example the ignored Dev2 config)",
    )
    return parser.parse_args()


def main() -> int:
    global WRANGLER_CONFIG  # noqa: PLW0603 - one CLI invocation owns one config
    args = parse_args()
    WRANGLER_CONFIG = args.config.resolve() if args.config is not None else None
    environment_name = "" if args.local else str(args.environment or "")
    try:
        credentials_path = args.credentials_file.resolve()
        if args.create_credentials and not credentials_path.exists():
            create_credentials_file(credentials_path)
            print(f"created private demo credentials: {credentials_path}")
        if args.app_url:
            provision_runtime_credentials(args.app_url, load_credentials(credentials_path))
            return 0
        assert_demo_login_enabled(environment_name, local=args.local)
        organization_id = resolve_organization(
            environment_name, local=args.local, requested=args.organization_id
        )
        if not args.verify_only:
            credentials = load_credentials(credentials_path)
            pepper = read_pepper(args.pepper_file)
            if len(pepper.encode()) < 32:
                raise SeedError(
                    "provide a 32+ byte PASSWORD_PEPPER through the environment or --pepper-file"
                )
            persona_ids = {
                role: os.environ.get(variable, "").strip() or DEMO_USER_IDS[role]
                for role, variable in DEMO_USER_ID_VARIABLES.items()
            }
            missing = [role for role, user_id in persona_ids.items() if not user_id]
            if missing:
                raise SeedError(f"missing configured demo user ids: {', '.join(missing)}")
            for role, expected_user_id in DEMO_USER_IDS.items():
                if persona_ids[role] != expected_user_id:
                    raise SeedError(
                        f"{DEMO_USER_ID_VARIABLES[role]} does not match the deterministic "
                        f"{role} account owned by this seed; refusing to rotate another user"
                    )
                require_safe_persona_target(
                    environment_name,
                    local=args.local,
                    role=role,
                    user_id=persona_ids[role],
                    expected_email=credentials[role]["email"],
                )
            verifier = hash_password(credentials["organizer"]["password"], pepper.encode())
            owner_user_id = owner_of(environment_name, organization_id, local=args.local)
            now_ms = int(
                rows(
                    execute_sql(
                        environment_name, "SELECT unixepoch()*1000 AS now", local=args.local
                    )
                )[0]["now"]
            )
            if args.reset:
                for statement in reset_statements(now_ms):
                    execute_sql(environment_name, statement, local=args.local)
                print("reset: removed the synthetic demo organizer")
            for statement in organizer_statements(organization_id, owner_user_id, verifier, now_ms):
                execute_sql(environment_name, statement, local=args.local)
            for role in ("reviewer", "speaker"):
                persona_verifier = hash_password(credentials[role]["password"], pepper.encode())
                for statement in persona_statements(
                    organization_id, role=role, verifier=persona_verifier, now_ms=now_ms
                ):
                    execute_sql(environment_name, statement, local=args.local)
            for role, user_id in persona_ids.items():
                require_expected_persona(
                    environment_name,
                    local=args.local,
                    role=role,
                    user_id=user_id,
                    expected_email=credentials[role]["email"],
                )
                execute_sql(
                    environment_name,
                    complete_persona_profile_statement(user_id, now_ms),
                    local=args.local,
                )
    except SeedError as error:
        print(f"demo seed failed: {error}", file=sys.stderr)
        return 1
    except (subprocess.TimeoutExpired, OSError) as error:
        print(f"demo seed failed: {error}", file=sys.stderr)
        return 1

    print(f"organization: {organization_id}")
    # Every persona is verified, the seeded organizer included. A demo control
    # must never be offered for an account that cannot actually sign in.
    incomplete = []
    for role, variable, fallback in (
        ("organizer", "DEMO_ORGANIZER_USER_ID", ORGANIZER_USER_ID),
        ("reviewer", "DEMO_REVIEWER_USER_ID", REVIEWER_USER_ID),
        ("speaker", "DEMO_SPEAKER_USER_ID", SPEAKER_USER_ID),
    ):
        usable, detail = verify_persona(
            environment_name,
            local=args.local,
            role=role,
            user_id=os.environ.get(variable, "").strip() or fallback,
        )
        print(("  ok  " if usable else "  !!  ") + detail)
        if not usable:
            incomplete.append(variable)
    print()
    print("Set these before enabling demo sign-in:")
    for role, variable in DEMO_USER_ID_VARIABLES.items():
        print(f"  {variable}={DEMO_USER_IDS[role]}")
    if incomplete:
        print()
        print(
            "demo seed failed: every persona must resolve to an active account "
            f"holding its role; unresolved: {', '.join(incomplete)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

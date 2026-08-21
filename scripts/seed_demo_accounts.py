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

    python scripts/seed_demo_accounts.py --local
    python scripts/seed_demo_accounts.py --local --reset
    python scripts/seed_demo_accounts.py --env dev --organization-id <id>
    python scripts/seed_demo_accounts.py --local --verify-only
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
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


class SeedError(RuntimeError):
    """A demo seed refused to run or could not complete safely."""


def stable_id(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{SEED_NAMESPACE}:{label}"))


ORGANIZER_USER_ID = stable_id("user:demo-organizer")
ORGANIZER_MEMBERSHIP_ID = stable_id("org-member:demo-organizer")
ORGANIZER_GRANT_ID = stable_id("grant:demo-organizer")


def run_wrangler(arguments: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "CI": "1", "NO_COLOR": "1"}
    return subprocess.run(  # noqa: S603 - fixed executable and validated arguments
        [NPX, "wrangler", *arguments],
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
        "  email_verified_at_ms,created_at_ms,updated_at_ms)"
        f" VALUES({literal(ORGANIZER_USER_ID)},{literal(DEMO_ORGANIZER_EMAIL)},"
        f" {literal(DEMO_ORGANIZER_EMAIL)},{literal(DEMO_ORGANIZER_NAME)},'Dana','Demo','active',"
        f" {now_ms},{now_ms},{now_ms})"
        " ON CONFLICT(id) DO UPDATE SET status='active',deleted_at_ms=NULL,"
        f" display_name={literal(DEMO_ORGANIZER_NAME)},updated_at_ms={now_ms}",
        "INSERT INTO user_roles (user_id,role,status,created_at_ms,updated_at_ms,is_default)"
        f" VALUES({literal(ORGANIZER_USER_ID)},'organizer','active',{now_ms},{now_ms},1)"
        " ON CONFLICT(user_id,role) DO UPDATE SET status='active',revoked_at_ms=NULL,"
        f" is_default=1,updated_at_ms={now_ms}",
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
        # Force re-authentication so a stale session cannot outlive a repair.
        "UPDATE users SET authorization_version=authorization_version+1,"
        f" updated_at_ms={now_ms} WHERE id={literal(ORGANIZER_USER_ID)}",
    ]


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
        "--password-variable",
        default="DEMO_ACCOUNT_PASSWORD",
        help="environment variable holding the demo organizer password",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    environment_name = "" if args.local else str(args.environment)
    try:
        assert_demo_login_enabled(environment_name, local=args.local)
        organization_id = resolve_organization(
            environment_name, local=args.local, requested=args.organization_id
        )
        if not args.verify_only:
            password = os.environ.get(args.password_variable, "")
            pepper = os.environ.get("PASSWORD_PEPPER", "")
            if not password or len(pepper.encode()) < 32:
                raise SeedError(
                    f"set {args.password_variable} and a 32+ byte PASSWORD_PEPPER before "
                    "seeding; the demo organizer needs a real credential so ordinary "
                    "sign-in also works"
                )
            verifier = hash_password(password, pepper.encode())
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
            for statement in organizer_statements(
                organization_id, owner_user_id, verifier, now_ms
            ):
                execute_sql(environment_name, statement, local=args.local)
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
        ("reviewer", "DEMO_REVIEWER_USER_ID", ""),
        ("speaker", "DEMO_SPEAKER_USER_ID", ""),
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
    print(f"  DEMO_ORGANIZER_USER_ID={ORGANIZER_USER_ID}")
    for variable in ("DEMO_REVIEWER_USER_ID", "DEMO_SPEAKER_USER_ID"):
        print(f"  {variable}={os.environ.get(variable, '') or '<unset>'}")
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

"""The demo seed and the release preflight must both refuse production."""

import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclass field resolution looks the defining
    # module up in sys.modules while the class body is being processed.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


seed = _load("seed_demo_accounts", "scripts/seed_demo_accounts.py")
preflight = _load("cloudflare_preflight", "scripts/cloudflare_preflight.py")


def _check_state(checks, label: str) -> str:
    for check in checks:
        if check.label == label:
            return check.state
    raise AssertionError(f"no {label!r} check was produced")


def _variables(**overrides) -> dict[str, str]:
    values = {
        "APP_ENV": "production",
        "PUBLIC_BASE_URL": "https://sessionbuddy.example.com",
        "ALLOWED_ORIGINS": "https://sessionbuddy.example.com",
        "MALWARE_SCAN_MODE": "required",
        "SCANNER_URL": "https://scanner.example.com",
    }
    values.update(overrides)
    return values


DEMO_IDS = {
    "DEMO_ORGANIZER_USER_ID": "a",
    "DEMO_REVIEWER_USER_ID": "b",
    "DEMO_SPEAKER_USER_ID": "c",
}


@pytest.mark.parametrize("app_env", ["production", "staging", "preview", "demo", "", "unknown"])
def test_preflight_fails_when_demo_login_is_enabled_outside_supported_environments(
    app_env: str,
) -> None:
    checks = preflight.static_configuration_checks(
        {}, _variables(APP_ENV=app_env, DEMO_LOGIN_ENABLED="true", **DEMO_IDS)
    )
    assert _check_state(checks, "demo sign-in") == "FAIL"


@pytest.mark.parametrize("app_env", ["local", "development"])
def test_preflight_allows_demo_login_in_supported_environments(app_env: str) -> None:
    checks = preflight.static_configuration_checks(
        {},
        _variables(
            APP_ENV=app_env,
            DEMO_LOGIN_ENABLED="true",
            MALWARE_SCAN_MODE="disabled",
            **DEMO_IDS,
        ),
    )
    assert _check_state(checks, "demo sign-in") == "PASS"


@pytest.mark.parametrize("missing", sorted(DEMO_IDS))
def test_preflight_fails_when_any_demo_identity_is_unset(missing: str) -> None:
    identities = {**DEMO_IDS, missing: ""}
    checks = preflight.static_configuration_checks(
        {},
        _variables(
            APP_ENV="development",
            DEMO_LOGIN_ENABLED="true",
            MALWARE_SCAN_MODE="disabled",
            **identities,
        ),
    )
    assert _check_state(checks, "demo sign-in") == "FAIL"


def test_preflight_passes_when_demo_login_is_absent() -> None:
    checks = preflight.static_configuration_checks({}, _variables())
    assert _check_state(checks, "demo sign-in") == "PASS"


def test_seed_identifiers_are_deterministic() -> None:
    assert seed.ORGANIZER_USER_ID == seed.stable_id("user:demo-organizer")
    assert seed.ORGANIZER_USER_ID != seed.ORGANIZER_GRANT_ID


def test_seed_statements_are_idempotent_upserts() -> None:
    statements = seed.organizer_statements("org-1", "owner-1", "$pbkdf2-sha256$i=1$a$b", 1_700)
    inserts = [statement for statement in statements if statement.startswith("INSERT")]
    assert inserts, "the seed must insert the demo organizer"
    for statement in inserts:
        assert "ON CONFLICT" in statement, f"not idempotent: {statement[:60]}"


def test_demo_seed_marks_new_and_existing_personas_profile_complete() -> None:
    statements = seed.organizer_statements("org-1", "owner-1", "$pbkdf2-sha256$i=1$a$b", 1_700)
    organizer_upsert = statements[0]
    repair = seed.complete_persona_profile_statement("demo-user", 1_700)

    assert "profile_completed_at_ms" in organizer_upsert
    assert "profile_completed_at_ms=COALESCE(profile_completed_at_ms,1700)" in organizer_upsert
    assert "profile_completed_at_ms=COALESCE(profile_completed_at_ms,1700)" in repair
    assert "WHERE id='demo-user'" in repair


def test_the_demo_organizer_is_granted_manage_and_never_ownership() -> None:
    statements = seed.organizer_statements("org-1", "owner-1", "$pbkdf2-sha256$i=1$a$b", 1_700)
    combined = " ".join(statements)
    assert "resource_access_grants" in combined
    assert "'manage'" in combined
    # Ownership cannot be revoked through the product, so a demo identity that
    # held it could never be removed from the tenant.
    assert "owned_resources" not in combined


def test_reset_only_touches_rows_the_seed_owns() -> None:
    owned = {seed.ORGANIZER_USER_ID, seed.ORGANIZER_GRANT_ID, seed.ORGANIZER_MEMBERSHIP_ID}
    for statement in seed.reset_statements(1_700):
        assert any(identifier in statement for identifier in owned), statement


def test_reset_never_deletes_tenant_content() -> None:
    forbidden = ("submissions", "evaluations", "events", "organizations", "agenda")
    for statement in seed.reset_statements(1_700):
        for table in forbidden:
            assert f" {table} " not in statement, statement


def test_sql_literals_escape_quotes() -> None:
    assert seed.literal("o'brien") == "'o''brien'"
    assert seed.literal(None) == "NULL"
    assert seed.literal(7) == "7"

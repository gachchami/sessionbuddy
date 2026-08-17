"""The demo seed and the release preflight must both refuse production."""

import importlib.util
import json
import stat
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


DEMO_IDS = dict(preflight.DEMO_USER_ID_VALUES)


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


def test_preflight_rejects_demo_login_for_an_arbitrary_user() -> None:
    checks = preflight.static_configuration_checks(
        {},
        _variables(
            APP_ENV="development",
            DEMO_LOGIN_ENABLED="true",
            MALWARE_SCAN_MODE="disabled",
            **{**DEMO_IDS, "DEMO_SPEAKER_USER_ID": "arbitrary-user"},
        ),
    )

    assert _check_state(checks, "demo sign-in") == "FAIL"


def test_seed_identifiers_are_deterministic() -> None:
    assert seed.ORGANIZER_USER_ID == seed.stable_id("user:demo-organizer")
    assert seed.REVIEWER_USER_ID == seed.stable_id("user:demo-reviewer")
    assert seed.SPEAKER_USER_ID == seed.stable_id("user:demo-speaker")
    assert seed.ORGANIZER_USER_ID != seed.ORGANIZER_GRANT_ID


def test_demo_config_variables_match_the_seed_owned_personas() -> None:
    expected = {
        variable: seed.DEMO_USER_IDS[role]
        for role, variable in seed.DEMO_USER_ID_VARIABLES.items()
    }

    stable = {
        "DEMO_ORGANIZER_USER_ID": seed.stable_id("user:demo-organizer"),
        "DEMO_REVIEWER_USER_ID": seed.stable_id("user:demo-reviewer"),
        "DEMO_SPEAKER_USER_ID": seed.stable_id("user:demo-speaker"),
    }
    _, variables = preflight.load_environment(PROJECT_ROOT / "wrangler.jsonc")

    assert expected == stable
    assert {name: variables[name] for name in stable} == stable


def test_seed_guard_uses_selected_config_instead_of_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEMO_LOGIN_ENABLED", "false")
    monkeypatch.setattr(
        seed,
        "execute_sql",
        lambda _environment, _sql, local: [{"results": [{"reachable": 1}]}],
    )

    seed.assert_demo_login_enabled(
        "",
        {"APP_ENV": "development", "DEMO_LOGIN_ENABLED": "true"},
        local=False,
    )


def test_seed_guard_rejects_a_config_that_disables_demo_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEMO_LOGIN_ENABLED", "true")
    monkeypatch.setattr(
        seed,
        "execute_sql",
        lambda _environment, _sql, local: [{"results": [{"reachable": 1}]}],
    )

    with pytest.raises(seed.SeedError, match="selected config"):
        seed.assert_demo_login_enabled(
            "",
            {"APP_ENV": "development", "DEMO_LOGIN_ENABLED": "false"},
            local=False,
        )


def test_seed_statements_are_idempotent_upserts() -> None:
    statements = seed.organizer_statements("org-1", "owner-1", "$pbkdf2-sha256$i=1$a$b", 1_700)
    inserts = [statement for statement in statements if statement.startswith("INSERT")]
    assert inserts, "the seed must insert the demo organizer"
    for statement in inserts:
        assert "ON CONFLICT" in statement, f"not idempotent: {statement[:60]}"
    password_index = next(
        i for i, statement in enumerate(statements) if "password_credentials" in statement
    )
    bump_index = next(
        i for i, statement in enumerate(statements) if "authorization_version+1" in statement
    )
    revoke_index = next(
        i for i, statement in enumerate(statements) if "demo_password_rotated" in statement
    )
    assert revoke_index < bump_index < password_index


def test_private_credential_file_contains_all_three_demo_accounts(tmp_path: Path) -> None:
    path = tmp_path / "demo-account-credentials.json"
    seed.create_credentials_file(path)

    payload = json.loads(path.read_text())
    assert payload["version"] == 1
    assert set(payload["accounts"]) == {"organizer", "reviewer", "speaker"}
    assert {
        role: account["email"] for role, account in payload["accounts"].items()
    } == seed.DEMO_EMAILS
    assert len({account["password"] for account in payload["accounts"].values()}) == 3
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert seed.load_credentials(path) == payload["accounts"]


def test_credential_loader_rejects_a_world_readable_secret(tmp_path: Path) -> None:
    path = tmp_path / "demo-account-credentials.json"
    seed.create_credentials_file(path)
    path.chmod(0o644)

    with pytest.raises(seed.SeedError, match="must not be group/world accessible"):
        seed.load_credentials(path)


def test_runtime_provisioning_rotates_each_expected_demo_account(monkeypatch) -> None:
    credentials = {
        role: {"email": email, "password": f"a-secure-{role}-password"}
        for role, email in seed.DEMO_EMAILS.items()
    }
    sessions: dict[int, str] = {}
    requests: list[tuple[str, str, dict | None, str | None]] = []

    monkeypatch.setattr(seed, "build_opener", lambda *_args: object())

    def fake_request(opener, url, *, method="GET", body=None, csrf=None):
        requests.append((method, url, body, csrf))
        if url.endswith("/demo-sign-in"):
            sessions[id(opener)] = body["role"]
            return {"authenticated": True, "csrf_token": f"csrf-{body['role']}"}
        if url.endswith("/password/sign-in"):
            role = next(
                role for role, account in credentials.items() if account["email"] == body["email"]
            )
            assert body["password"] == credentials[role]["password"]
            return {"authenticated": True, "user_id": seed.DEMO_USER_IDS[role]}
        role = sessions[id(opener)]
        if method == "GET":
            first_name, last_name = seed.DEMO_NAMES[role][1:]
            return {
                "email": credentials[role]["email"],
                "first_name": first_name,
                "last_name": last_name,
                "public_profile_enabled": False,
                "version": 7,
            }
        assert body["password"] == credentials[role]["password"]
        assert body["password_confirmation"] == credentials[role]["password"]
        assert csrf == f"csrf-{role}"
        return {"has_password": True}

    monkeypatch.setattr(seed, "_runtime_request", fake_request)

    seed.provision_runtime_credentials("https://demo.example.test", credentials)

    assert [request[0] for request in requests] == ["POST", "GET", "PATCH", "POST"] * 3
    assert [request[2]["role"] for request in requests if request[1].endswith("/demo-sign-in")] == [
        "organizer",
        "reviewer",
        "speaker",
    ]


def test_runtime_provisioning_requires_a_plain_https_origin() -> None:
    with pytest.raises(seed.SeedError, match="HTTPS or loopback HTTP origin"):
        seed.provision_runtime_credentials("http://demo.example.test", {})
    with pytest.raises(seed.SeedError, match="HTTPS or loopback HTTP origin"):
        seed.provision_runtime_credentials("https://user:secret@example.test", {})


def test_runtime_provisioning_allows_loopback_http(monkeypatch) -> None:
    monkeypatch.setattr(seed, "build_opener", lambda *_args: object())
    seed.provision_runtime_credentials("http://127.0.0.1:8787", {})


def test_each_demo_persona_password_rotation_is_an_idempotent_upsert() -> None:
    statement = seed.password_credential_statement("demo-user", "$pbkdf2-sha256$i=1$a$b", 1_700)
    assert statement.startswith("INSERT INTO password_credentials")
    assert "ON CONFLICT(user_id) DO UPDATE" in statement
    assert "pepper_version=1" in statement


@pytest.mark.parametrize("role", ["reviewer", "speaker"])
def test_non_organizer_demo_personas_are_created_idempotently(role: str) -> None:
    statements = seed.persona_statements(
        "org-1", role=role, verifier="$pbkdf2-sha256$i=1$a$b", now_ms=1_700
    )
    inserts = [statement for statement in statements if statement.startswith("INSERT")]
    assert len(inserts) == 4
    assert all("ON CONFLICT" in statement for statement in inserts)
    assert seed.DEMO_USER_IDS[role] in " ".join(statements)
    assert seed.DEMO_EMAILS[role] in statements[0]
    password_index = next(
        i for i, statement in enumerate(statements) if "password_credentials" in statement
    )
    bump_index = next(
        i for i, statement in enumerate(statements) if "authorization_version+1" in statement
    )
    revoke_index = next(
        i for i, statement in enumerate(statements) if "demo_password_rotated" in statement
    )
    assert revoke_index < bump_index < password_index


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

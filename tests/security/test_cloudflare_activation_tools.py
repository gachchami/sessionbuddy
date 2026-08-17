import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap_cloudflare, setup_key
from scripts.bootstrap_cloudflare import bootstrap_payload, timestamp_ms
from scripts.cloudflare_preflight import (
    CORE_SECRETS,
    CommandResult,
    load_environment,
    parse_organization_count,
    r2_cors_ready,
    remote_checks,
    require_remote_environment,
    run_command,
    secret_checks,
    static_configuration_checks,
)
from scripts.render_private_cloudflare_config import (
    DEMO_USER_ID_VALUES,
    load_jsonc,
    main_config,
)

ROOT = Path(__file__).parents[2]


def _rendered_development_config() -> tuple[dict, dict[str, str]]:
    base = load_jsonc(ROOT / "wrangler.jsonc")
    values = {
        "WORKER_NAME": "sessionbuddy-development-test",
        "D1_DATABASE_NAME": "sessionbuddy-development-test",
        "D1_DATABASE_ID": "11111111-1111-4111-8111-111111111111",
        "R2_BUCKET_NAME": "sessionbuddy-assets-development",
        "ASSET_SCAN_QUEUE": "asset-scans",
        "ASSET_SCAN_DLQ": "asset-scans-dlq",
        "COMMUNICATION_QUEUE": "communications",
        "COMMUNICATION_DLQ": "communications-dlq",
        "REMINDER_WORKFLOW": "reminders",
        "PUBLIC_BASE_URL": "https://development.sessionbuddy.test",
        "CLOUDFLARE_ACCOUNT_ID": "account-test",
        "APP_VERSION": "test-version",
        "RATE_LIMIT_NAMESPACE_BASE": "2000",
        **DEMO_USER_ID_VALUES,
        "RESEND_FROM_ADDRESS": "SessionBuddy <events@example.test>",
    }
    environment = main_config(base, values)
    return environment, environment["vars"]


def test_development_cloudflare_config_has_no_deployment_failures() -> None:
    environment, variables = _rendered_development_config()
    checks = static_configuration_checks(environment, variables)
    rate_limits = {
        binding["name"]: binding["simple"] for binding in environment["ratelimits"]
    }

    assert not [check for check in checks if check.state == "FAIL"]
    assert variables["PUBLIC_BASE_URL"] in variables["ALLOWED_ORIGINS"].split(",")
    assert variables["CLOUDFLARE_ACCOUNT_ID"]
    assert variables["R2_BUCKET_NAME"] == "sessionbuddy-assets-development"
    assert environment["limits"] == {"cpu_ms": 5000}
    assert any(
        check.label == "malware scanning"
        and check.state == "PASS"
        and check.detail == "explicit development bypass"
        for check in checks
    )
    assert any(
        check.label == "Cloudflare Containers" and check.state == "PASS" for check in checks
    )
    assert rate_limits["SPEAKER_UPLOAD_AUTH_RATE_LIMITER"] == {"limit": 3, "period": 60}
    assert rate_limits["HEADSHOT_UPLOAD_RATE_LIMITER"] == {"limit": 3, "period": 60}
    assert rate_limits["MAGIC_LINK_RECIPIENT_RATE_LIMITER"] == {
        "limit": 3,
        "period": 60,
    }
    assert rate_limits["MAGIC_LINK_SOURCE_RATE_LIMITER"] == {"limit": 10, "period": 60}


def test_deployed_development_can_explicitly_disable_scanning() -> None:
    environment, variables = _rendered_development_config()
    variables["MALWARE_SCAN_MODE"] = "disabled"
    checks = static_configuration_checks(environment, variables)

    malware = next(check for check in checks if check.label == "malware scanning")
    assert malware.state == "PASS"
    assert malware.detail == "explicit development bypass"


def test_staging_cannot_inherit_the_development_scanner_bypass() -> None:
    environment, variables = _rendered_development_config()
    variables["APP_ENV"] = "staging"
    variables["MALWARE_SCAN_MODE"] = "disabled"
    checks = static_configuration_checks(environment, variables)

    malware = next(check for check in checks if check.label == "malware scanning")
    assert malware.state == "FAIL"
    assert "outside development" in malware.detail


def test_activation_preflight_distinguishes_core_and_provider_secrets() -> None:
    checks = secret_checks(set(CORE_SECRETS), organization_count=0)
    by_label = {check.label: check for check in checks}

    assert by_label["application secrets"].state == "PASS"
    assert by_label["Resend activation"].state == "PENDING"
    assert by_label["direct R2 uploads"].state == "PENDING"
    assert by_label["initial bootstrap"].state == "PENDING"


def test_bootstrap_payload_requires_offset_dates_and_orders_them() -> None:
    arguments = argparse.Namespace(
        organization_name="Example Events",
        admin_name="Example Admin",
        event_name="Example Conference",
        admin_email="admin@example.test",
        starts_at="2026-11-01T09:00:00+05:30",
        ends_at="2026-11-01T18:00:00+05:30",
        time_zone="Asia/Kolkata",
        event_location="Mumbai",
        event_description="Example conference",
        event_delivery_mode="hybrid",
    )

    payload = bootstrap_payload(arguments)

    assert payload["ends_at_ms"] > payload["starts_at_ms"]
    assert payload["admin_email"] == "admin@example.test"
    assert payload["admin_name"] == "Example Admin"
    with pytest.raises(ValueError, match="UTC offset"):
        timestamp_ms("2026-11-01T09:00:00")


def test_bootstrap_payload_allows_an_organization_without_an_event() -> None:
    arguments = argparse.Namespace(
        organization_name="SessionBuddy Development",
        admin_name="Development Admin",
        admin_email="admin@example.test",
        event_name=None,
        starts_at=None,
        ends_at=None,
        time_zone=None,
        event_location=None,
        event_description=None,
        event_delivery_mode=None,
    )

    assert bootstrap_payload(arguments) == {
        "organization_name": "SessionBuddy Development",
        "admin_name": "Development Admin",
        "admin_email": "admin@example.test",
    }


def test_bootstrap_payload_preserves_exact_administrator_name_parts() -> None:
    arguments = argparse.Namespace(
        organization_name="SessionBuddy Development",
        admin_name=None,
        admin_first_name="Devang",
        admin_last_name="Hanushali",
        admin_email="admin@example.test",
        event_name=None,
        starts_at=None,
        ends_at=None,
        time_zone=None,
        event_location=None,
        event_description=None,
        event_delivery_mode=None,
    )

    assert bootstrap_payload(arguments) == {
        "organization_name": "SessionBuddy Development",
        "admin_name": "Devang Hanushali",
        "admin_first_name": "Devang",
        "admin_last_name": "Hanushali",
        "admin_email": "admin@example.test",
    }


def test_preflight_parses_wrangler_d1_json() -> None:
    output = json.dumps([{"results": [{"organization_count": 3}]}])
    assert parse_organization_count(output) == 3


def test_preflight_keeps_successful_json_stdout_separate_from_warnings(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.cloudflare_preflight.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='[{"name":"SESSION_HMAC_KEY"}]\n',
            stderr="Wrangler configuration warning\n",
        ),
    )

    result = run_command(["npx", "wrangler", "secret", "list"])

    assert result.output == '[{"name":"SESSION_HMAC_KEY"}]'


def test_preflight_requires_exact_r2_browser_upload_cors() -> None:
    origin = "https://development.sessionbuddy.test"
    output = """allowed_origins:  https://development.sessionbuddy.test
allowed_methods:  PUT
allowed_headers:  Content-Type
exposed_headers:  ETag
    max_age_seconds:  3600"""

    assert r2_cors_ready(output, origin)
    wrong_method = output.replace("allowed_methods:  PUT", "allowed_methods:  GET")
    assert not r2_cors_ready(wrong_method, origin)


def test_preflight_loads_only_a_flat_concrete_config(tmp_path: Path) -> None:
    config = tmp_path / "target.jsonc"
    config.write_text('{"vars":{"APP_ENV":"development"}}', encoding="utf-8")

    environment, variables = load_environment(config)

    assert environment["vars"] == variables
    config.write_text('{"env":{"dev":{"vars":{}}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="without Wrangler environments"):
        load_environment(config)
    config.write_text('{"env":{},"vars":{"APP_ENV":"development"}}', encoding="utf-8")
    with pytest.raises(ValueError, match="without Wrangler environments"):
        load_environment(config)


@pytest.mark.parametrize("app_env", ["local", "", "developmnt", "preview"])
def test_preflight_rejects_unknown_or_non_remote_environment(app_env: str) -> None:
    with pytest.raises(ValueError, match="development, staging, or production"):
        require_remote_environment({"APP_ENV": app_env})


@pytest.mark.parametrize("app_env", ["development", "staging", "production"])
def test_preflight_accepts_an_explicit_remote_environment(app_env: str) -> None:
    require_remote_environment({"APP_ENV": app_env})


def test_preflight_binds_every_remote_wrangler_command_to_selected_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Path("target.private.jsonc")
    environment, variables = _rendered_development_config()
    commands: list[list[str]] = []

    def fake_command(arguments: list[str]) -> CommandResult:
        commands.append(arguments)
        joined = " ".join(arguments)
        if "whoami" in joined:
            return CommandResult(0, "logged in")
        if "migrations list" in joined:
            return CommandResult(0, "No migrations to apply")
        if "d1 execute" in joined:
            return CommandResult(0, '[{"results":[{"organization_count":1}]}]')
        if "cors list" in joined:
            return CommandResult(
                0,
                "allowed_origins: https://development.sessionbuddy.test\n"
                "allowed_methods: PUT\nallowed_headers: Content-Type\n"
                "exposed_headers: ETag\nmax_age_seconds: 3600",
            )
        if "secret list" in joined:
            return CommandResult(0, "[]")
        return CommandResult(0, "")

    monkeypatch.setattr("scripts.cloudflare_preflight.run_command", fake_command)
    monkeypatch.setattr(
        "scripts.cloudflare_preflight.request_status",
        lambda url: (401, "") if url.endswith("/api/v1/auth/session") else (200, '{"status":"ok"}'),
    )

    remote_checks(config, environment, variables)

    wrangler_commands = [command for command in commands if "whoami" not in command]
    assert wrangler_commands
    assert all("--config" in command for command in wrangler_commands)
    assert all("--env" not in command for command in wrangler_commands)


def test_pre_mutation_preflight_can_report_reviewed_migrations_as_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, variables = _rendered_development_config()

    def fake_command(arguments: list[str]) -> CommandResult:
        joined = " ".join(arguments)
        if "whoami" in joined:
            return CommandResult(0, "logged in")
        if "migrations list" in joined:
            return CommandResult(0, "0009_reviewed_change.sql")
        if "d1 execute" in joined:
            return CommandResult(0, '[{"results":[{"organization_count":1}]}]')
        if "cors list" in joined:
            return CommandResult(
                0,
                "allowed_origins: https://development.sessionbuddy.test\n"
                "allowed_methods: PUT\nallowed_headers: Content-Type\n"
                "exposed_headers: ETag\nmax_age_seconds: 3600",
            )
        if "secret list" in joined:
            return CommandResult(0, "[]")
        return CommandResult(0, "")

    monkeypatch.setattr("scripts.cloudflare_preflight.run_command", fake_command)
    monkeypatch.setattr(
        "scripts.cloudflare_preflight.request_status",
        lambda url: (401, "") if url.endswith("/api/v1/auth/session") else (200, '{"status":"ok"}'),
    )

    checks, _, _ = remote_checks(
        Path("target.private.jsonc"),
        environment,
        variables,
        allow_pending_migrations=True,
    )

    migrations = next(check for check in checks if check.label == "D1 migrations")
    assert migrations.state == "PENDING"
    assert "explicitly allowed" in migrations.detail


def test_bootstrap_command_uses_migration_key_without_printing_it(monkeypatch, capsys) -> None:
    deployment_key = "a" * 64
    supplied_keys: list[str] = []
    monkeypatch.setattr(
        bootstrap_cloudflare,
        "load_environment",
        lambda _path: (
            {},
            {"PUBLIC_BASE_URL": "https://sessionbuddy.test.workers.dev"},
        ),
    )
    monkeypatch.setattr(
        bootstrap_cloudflare,
        "read_setup_key",
        lambda _config: deployment_key,
    )
    monkeypatch.setattr(
        bootstrap_cloudflare,
        "post_bootstrap",
        lambda _base_url, token, _payload: (
            supplied_keys.append(token)
            or {
                "organization_id": "organization",
                "event_id": None,
                "admin_user_id": "admin",
            }
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "bootstrap_cloudflare.py",
            "--config",
            "target.private.jsonc",
            "--organization-name",
            "Example Events",
            "--admin-name",
            "Example Admin",
            "--admin-email",
            "admin@example.test",
        ],
    )

    assert bootstrap_cloudflare.main() == 0
    assert supplied_keys == [deployment_key]
    assert deployment_key not in capsys.readouterr().out


def test_setup_key_commands_parse_only_valid_d1_keys(monkeypatch) -> None:
    generated = "b" * 64
    operations: list[str] = []
    monkeypatch.setattr(
        setup_key,
        "_execute",
        lambda _config, sql, local=False: (
            operations.append(sql) or [{"results": [{"deployment_key": generated}]}]
        ),
    )

    config = Path("target.private.jsonc")
    assert setup_key.read_setup_key(config) == generated
    assert setup_key.regenerate_setup_key(config) == generated
    assert operations == [setup_key.READ_KEY_SQL, setup_key.REGENERATE_KEY_SQL]

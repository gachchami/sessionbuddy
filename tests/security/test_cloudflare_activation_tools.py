import argparse
import json
from pathlib import Path

import pytest

from scripts import bootstrap_cloudflare
from scripts.bootstrap_cloudflare import bootstrap_payload, timestamp_ms
from scripts.cloudflare_preflight import (
    CORE_SECRETS,
    load_environment,
    parse_organization_count,
    secret_checks,
    static_configuration_checks,
)

ROOT = Path(__file__).parents[2]


def test_development_cloudflare_config_has_no_deployment_failures() -> None:
    environment, variables = load_environment(ROOT / "wrangler.jsonc", "dev")
    checks = static_configuration_checks(environment, variables)

    assert not [check for check in checks if check.state == "FAIL"]
    assert variables["PUBLIC_BASE_URL"] in variables["ALLOWED_ORIGINS"].split(",")
    assert variables["CLOUDFLARE_ACCOUNT_ID"]
    assert variables["R2_BUCKET_NAME"] == "sessionbuddy-assets-development"
    assert any(
        check.label == "malware scanning" and "development-only bypass" in check.detail
        for check in checks
    )
    assert any(
        check.label == "Cloudflare Containers" and check.state == "PASS" for check in checks
    )


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
        event_name="Example Conference",
        admin_email="admin@example.test",
        starts_at="2026-11-01T09:00:00+05:30",
        ends_at="2026-11-01T18:00:00+05:30",
        time_zone="Asia/Kolkata",
    )

    payload = bootstrap_payload(arguments)

    assert payload["ends_at_ms"] > payload["starts_at_ms"]
    assert payload["admin_email"] == "admin@example.test"
    with pytest.raises(ValueError, match="UTC offset"):
        timestamp_ms("2026-11-01T09:00:00")


def test_preflight_parses_wrangler_d1_json() -> None:
    output = json.dumps([{"results": [{"organization_count": 3}]}])
    assert parse_organization_count(output) == 3


def test_bootstrap_command_streams_and_removes_temporary_secret(monkeypatch, capsys) -> None:
    operations: list[tuple[list[str], str | None]] = []
    monkeypatch.setattr(
        bootstrap_cloudflare,
        "load_environment",
        lambda _path, _environment: (
            {},
            {"PUBLIC_BASE_URL": "https://sessionbuddy.test.workers.dev"},
        ),
    )
    monkeypatch.setattr(
        bootstrap_cloudflare,
        "wrangler_secret",
        lambda arguments, value=None: operations.append((arguments, value)),
    )
    monkeypatch.setattr(
        bootstrap_cloudflare,
        "post_bootstrap",
        lambda _base_url, _token, _payload: {
            "organization_id": "organization",
            "event_id": "event",
            "admin_user_id": "admin",
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "bootstrap_cloudflare.py",
            "--organization-name",
            "Example Events",
            "--event-name",
            "Example Conference",
            "--admin-email",
            "admin@example.test",
            "--starts-at",
            "2026-11-01T09:00:00+05:30",
            "--ends-at",
            "2026-11-01T18:00:00+05:30",
            "--time-zone",
            "Asia/Kolkata",
        ],
    )

    assert bootstrap_cloudflare.main() == 0
    assert operations[0][0][:2] == ["put", "BOOTSTRAP_TOKEN"]
    assert operations[0][1] is not None and len(operations[0][1]) >= 32
    assert operations[1] == (["delete", "BOOTSTRAP_TOKEN", "--env", "dev"], None)
    assert operations[0][1] not in capsys.readouterr().out

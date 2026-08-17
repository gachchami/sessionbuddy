import json
from pathlib import Path

import pytest

from scripts.render_private_cloudflare_config import (
    DEMO_USER_ID_VALUES,
    REQUIRED_MANIFEST_KEYS,
    activity_config,
    load_jsonc,
    load_manifest,
    main_config,
    r2_cors_config,
    validate_manifest_keys,
)

ROOT = Path(__file__).parents[1]


def deployment_values() -> dict[str, str]:
    return {
        "WORKER_NAME": "sessionbuddy-target",
        "ACTIVITY_WORKER_NAME": "sessionbuddy-activity-target",
        "D1_DATABASE_NAME": "sessionbuddy-target",
        "D1_DATABASE_ID": "11111111-1111-4111-8111-111111111111",
        "R2_BUCKET_NAME": "sessionbuddy-assets-target",
        "ASSET_SCAN_QUEUE": "asset-scans-target",
        "ASSET_SCAN_DLQ": "asset-scans-target-dlq",
        "COMMUNICATION_QUEUE": "communications-target",
        "COMMUNICATION_DLQ": "communications-target-dlq",
        "REMINDER_WORKFLOW": "reminders-target",
        "ACTIVITY_QUEUE": "activity-target",
        "ACTIVITY_DLQ": "activity-target-dlq",
        "PUBLIC_BASE_URL": "https://target.example.test",
        "CLOUDFLARE_ACCOUNT_ID": "account-target",
        "APP_VERSION": "target-version",
        "RATE_LIMIT_NAMESPACE_BASE": "4000",
        **DEMO_USER_ID_VALUES,
        "RESEND_FROM_ADDRESS": "SessionBuddy <events@example.test>",
    }


def test_example_manifest_matches_every_renderer_input() -> None:
    example = load_manifest(ROOT / "deployment.private.example")

    assert set(example) == REQUIRED_MANIFEST_KEYS
    assert set(deployment_values()) == REQUIRED_MANIFEST_KEYS


def test_renderer_rejects_a_missing_required_value() -> None:
    values = deployment_values()
    del values["D1_DATABASE_ID"]

    with pytest.raises(ValueError, match="D1_DATABASE_ID"):
        main_config(load_jsonc(ROOT / "wrangler.jsonc"), values)


def test_manifest_rejects_demo_ids_not_owned_by_the_seed() -> None:
    values = deployment_values()
    values["DEMO_SPEAKER_USER_ID"] = "55555555-5555-4555-8555-555555555555"

    with pytest.raises(ValueError, match="seed-owned.*DEMO_SPEAKER_USER_ID"):
        validate_manifest_keys(values)


def test_manifest_rejects_duplicate_target_identity(tmp_path: Path) -> None:
    manifest = tmp_path / "target.local"
    manifest.write_text("D1_DATABASE_ID=first\nD1_DATABASE_ID=second\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate.*D1_DATABASE_ID"):
        load_manifest(manifest)


def test_renderer_keeps_target_bindings_and_cors_consistent() -> None:
    values = deployment_values()
    main = main_config(load_jsonc(ROOT / "wrangler.jsonc"), values)
    activity = activity_config(load_jsonc(ROOT / "wrangler.activity.jsonc"), values)
    cors = r2_cors_config(values)

    assert main["d1_databases"][0]["database_id"] == activity["d1_databases"][0][
        "database_id"
    ]
    assert main["r2_buckets"][0]["bucket_name"] == values["R2_BUCKET_NAME"]
    assert cors["rules"][0]["allowed"]["origins"] == [values["PUBLIC_BASE_URL"]]
    namespace_ids = [int(binding["namespace_id"]) for binding in main["ratelimits"]]
    assert namespace_ids == list(range(4001, 4010))
    assert len(set(namespace_ids)) == 9


def test_manifest_normalizes_the_public_origin_once(tmp_path: Path) -> None:
    manifest = tmp_path / "target.local"
    manifest.write_text("PUBLIC_BASE_URL=https://target.example.test/\n", encoding="utf-8")

    assert load_manifest(manifest)["PUBLIC_BASE_URL"] == "https://target.example.test"


def test_jsonc_loader_preserves_url_slashes_and_ignores_comments(tmp_path: Path) -> None:
    config = tmp_path / "config.jsonc"
    config.write_text(
        '{\n// target comment\n"url":"https://example.test/path",/* block */"ok":true,\n}',
        encoding="utf-8",
    )

    assert load_jsonc(config) == {"url": "https://example.test/path", "ok": True}


def test_rendered_documents_are_repeatable() -> None:
    values = deployment_values()
    base = load_jsonc(ROOT / "wrangler.jsonc")

    first = json.dumps(main_config(base, values), sort_keys=True)
    second = json.dumps(main_config(base, values), sort_keys=True)

    assert first == second

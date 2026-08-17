"""Render ignored concrete Wrangler configs from a private resource manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pyjson5

REQUIRED_MANIFEST_KEYS = {
    "WORKER_NAME",
    "ACTIVITY_WORKER_NAME",
    "D1_DATABASE_NAME",
    "D1_DATABASE_ID",
    "R2_BUCKET_NAME",
    "ASSET_SCAN_QUEUE",
    "ASSET_SCAN_DLQ",
    "COMMUNICATION_QUEUE",
    "COMMUNICATION_DLQ",
    "REMINDER_WORKFLOW",
    "ACTIVITY_QUEUE",
    "ACTIVITY_DLQ",
    "PUBLIC_BASE_URL",
    "CLOUDFLARE_ACCOUNT_ID",
    "APP_VERSION",
    "RATE_LIMIT_NAMESPACE_BASE",
    "DEMO_ORGANIZER_USER_ID",
    "DEMO_REVIEWER_USER_ID",
    "DEMO_SPEAKER_USER_ID",
    "RESEND_FROM_ADDRESS",
}
DEMO_USER_ID_VALUES = {
    "DEMO_ORGANIZER_USER_ID": "c2c12e5e-2cea-5d9a-bf7f-dc5dc012e2b4",
    "DEMO_REVIEWER_USER_ID": "f88610c7-3a09-5804-987d-6588993d2e78",
    "DEMO_SPEAKER_USER_ID": "c05e0af4-f1ed-5cec-8540-426a38979046",
}


def load_jsonc(path: Path) -> dict:
    """Load the full JSONC syntax used by Wrangler configs."""
    try:
        value = pyjson5.decode(path.read_text(encoding="utf-8"))
    except pyjson5.Json5Exception as error:
        raise ValueError(f"invalid JSONC in {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Wrangler config must be an object: {path}")
    return value


def load_manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise ValueError(f"invalid manifest line: {raw_line!r}")
        if key in values:
            raise ValueError(f"duplicate private deployment value: {key}")
        values[key] = value.strip()
    public_base_url = values.get("PUBLIC_BASE_URL")
    if public_base_url:
        values["PUBLIC_BASE_URL"] = public_base_url.rstrip("/")
    return values


def required(values: dict[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"missing private deployment value: {name}")
    return value


def validate_manifest_keys(values: dict[str, str]) -> None:
    missing = sorted(REQUIRED_MANIFEST_KEYS - set(values))
    unexpected = sorted(set(values) - REQUIRED_MANIFEST_KEYS)
    if missing or unexpected:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing))
        if unexpected:
            detail.append("unexpected: " + ", ".join(unexpected))
        raise ValueError(
            "private deployment manifest keys do not match renderer ("
            + "; ".join(detail)
            + ")"
        )
    mismatched_demo_ids = [
        name for name, expected in DEMO_USER_ID_VALUES.items() if values.get(name) != expected
    ]
    if mismatched_demo_ids:
        raise ValueError(
            "private deployment demo identities must match the seed-owned personas: "
            + ", ".join(sorted(mismatched_demo_ids))
        )


def main_config(base: dict, values: dict[str, str]) -> dict:
    config = {key: value for key, value in base.items() if key not in {"env", "vars"}}
    config.update(
        {
            "name": required(values, "WORKER_NAME"),
            "d1_databases": [
                {
                    "binding": "DB",
                    "database_name": required(values, "D1_DATABASE_NAME"),
                    "database_id": required(values, "D1_DATABASE_ID"),
                    "migrations_dir": "migrations_baseline",
                }
            ],
            "r2_buckets": [
                {"binding": "ASSETS", "bucket_name": required(values, "R2_BUCKET_NAME")}
            ],
            "queues": {
                "producers": [
                    {"binding": "ASSET_SCAN_QUEUE", "queue": required(values, "ASSET_SCAN_QUEUE")},
                    {
                        "binding": "COMMUNICATION_QUEUE",
                        "queue": required(values, "COMMUNICATION_QUEUE"),
                    },
                ],
                "consumers": [
                    {
                        "queue": required(values, "ASSET_SCAN_QUEUE"),
                        "max_batch_size": 5,
                        "max_batch_timeout": 5,
                        "max_retries": 5,
                        "dead_letter_queue": required(values, "ASSET_SCAN_DLQ"),
                    },
                    {
                        "queue": required(values, "COMMUNICATION_QUEUE"),
                        "max_batch_size": 10,
                        "max_batch_timeout": 5,
                        "max_retries": 5,
                        "dead_letter_queue": required(values, "COMMUNICATION_DLQ"),
                    },
                ],
            },
            "workflows": [
                {
                    "name": required(values, "REMINDER_WORKFLOW"),
                    "binding": "REMINDER_WORKFLOW",
                    "class_name": "ReminderWorkflow",
                }
            ],
            "ratelimits": [
                rate_limit("AUTH_RATE_LIMITER", rate_limit_id(values, 1), 10),
                rate_limit("PUBLIC_RATE_LIMITER", rate_limit_id(values, 2), 50),
                rate_limit("CFP_UPLOAD_AUTH_RATE_LIMITER", rate_limit_id(values, 3), 10),
                rate_limit("CFP_UPLOAD_POLL_RATE_LIMITER", rate_limit_id(values, 4), 240),
                rate_limit("SPEAKER_UPLOAD_AUTH_RATE_LIMITER", rate_limit_id(values, 5), 3),
                rate_limit("HEADSHOT_UPLOAD_RATE_LIMITER", rate_limit_id(values, 6), 3),
                rate_limit("MAGIC_LINK_RECIPIENT_RATE_LIMITER", rate_limit_id(values, 7), 3),
                rate_limit("MAGIC_LINK_SOURCE_RATE_LIMITER", rate_limit_id(values, 8), 10),
                rate_limit("DEMO_AUTH_RATE_LIMITER", rate_limit_id(values, 9), 30),
            ],
            "limits": {"cpu_ms": 5000},
            "vars": {
                "APP_ENV": "development",
                "APP_VERSION": required(values, "APP_VERSION"),
                "ALLOWED_ORIGINS": required(values, "PUBLIC_BASE_URL"),
                "CLOUDFLARE_ACCOUNT_ID": required(values, "CLOUDFLARE_ACCOUNT_ID"),
                "DEMO_LOGIN_ENABLED": "true",
                "DEMO_ORGANIZER_USER_ID": required(values, "DEMO_ORGANIZER_USER_ID"),
                "DEMO_REVIEWER_USER_ID": required(values, "DEMO_REVIEWER_USER_ID"),
                "DEMO_SPEAKER_USER_ID": required(values, "DEMO_SPEAKER_USER_ID"),
                "MALWARE_SCAN_MODE": "disabled",
                "PUBLIC_BASE_URL": required(values, "PUBLIC_BASE_URL"),
                "R2_BUCKET_NAME": required(values, "R2_BUCKET_NAME"),
                "RESEND_FROM_ADDRESS": required(values, "RESEND_FROM_ADDRESS"),
                "SCANNER_URL": "",
                "SKIP_PROFILE_ONBOARDING": "true",
            },
        }
    )
    return config


def activity_config(base: dict, values: dict[str, str]) -> dict:
    config = {key: value for key, value in base.items() if key not in {"env", "vars"}}
    config.update(
        {
            "name": required(values, "ACTIVITY_WORKER_NAME"),
            "d1_databases": [
                {
                    "binding": "DB",
                    "database_name": required(values, "D1_DATABASE_NAME"),
                    "database_id": required(values, "D1_DATABASE_ID"),
                    "migrations_dir": "migrations_baseline",
                }
            ],
            "queues": {
                "producers": [
                    {"binding": "ACTIVITY_QUEUE", "queue": required(values, "ACTIVITY_QUEUE")}
                ],
                "consumers": [
                    {
                        "queue": required(values, "ACTIVITY_QUEUE"),
                        "max_batch_size": 25,
                        "max_batch_timeout": 5,
                        "max_retries": 5,
                        "dead_letter_queue": required(values, "ACTIVITY_DLQ"),
                    }
                ],
            },
            "limits": {"cpu_ms": 5000},
            "vars": {"APP_ENV": "development"},
        }
    )
    return config


def r2_cors_config(values: dict[str, str]) -> dict:
    return {
        "rules": [
            {
                "allowed": {
                    "origins": [required(values, "PUBLIC_BASE_URL")],
                    "methods": ["PUT"],
                    "headers": ["Content-Type"],
                },
                "exposeHeaders": ["ETag"],
                "maxAgeSeconds": 3600,
            }
        ]
    }


def write_config(path: Path, config: dict) -> None:
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def relativize_repository_paths(config: dict, *, root: Path, output: Path) -> None:
    """Keep Wrangler paths valid when an ignored config lives below the repo root."""
    base = output.resolve().parent
    main = config.get("main")
    if isinstance(main, str):
        config["main"] = os.path.relpath(root / main, base)
    for binding in config.get("d1_databases", []):
        migrations_dir = binding.get("migrations_dir")
        if isinstance(migrations_dir, str):
            binding["migrations_dir"] = os.path.relpath(root / migrations_dir, base)


def rate_limit(name: str, namespace_id: str, limit: int) -> dict[str, object]:
    return {
        "name": name,
        "namespace_id": namespace_id,
        "simple": {"limit": limit, "period": 60},
    }


def rate_limit_id(values: dict[str, str], offset: int) -> str:
    base = int(required(values, "RATE_LIMIT_NAMESPACE_BASE"))
    return str(base + offset)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--main-output", type=Path)
    parser.add_argument("--activity-output", type=Path)
    parser.add_argument("--cors-output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.check:
        if any((args.manifest, args.main_output, args.activity_output, args.cors_output)):
            parser.error("--check does not accept render inputs or outputs")
        values = load_manifest(root / "deployment.private.example")
        validate_manifest_keys(values)
        check_values = {
            key: (
                DEMO_USER_ID_VALUES[key]
                if key in DEMO_USER_ID_VALUES
                else "1000"
                if key == "RATE_LIMIT_NAMESPACE_BASE"
                else "https://target.example.test"
                if key == "PUBLIC_BASE_URL"
                else "11111111-1111-4111-8111-111111111111"
                if key == "D1_DATABASE_ID"
                else "SessionBuddy <events@example.test>"
                if key == "RESEND_FROM_ADDRESS"
                else f"test-{key.lower().replace('_', '-')}"
            )
            for key in values
        }
        import tempfile

        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            rendered_main = main_config(load_jsonc(root / "wrangler.jsonc"), check_values)
            rendered_activity = activity_config(
                load_jsonc(root / "wrangler.activity.jsonc"), check_values
            )
            main_output = temporary / "wrangler.target.private.jsonc"
            activity_output = temporary / "wrangler.activity.target.private.jsonc"
            cors_output = temporary / "r2-cors.target.private.json"
            relativize_repository_paths(rendered_main, root=root, output=main_output)
            relativize_repository_paths(
                rendered_activity, root=root, output=activity_output
            )
            write_config(main_output, rendered_main)
            write_config(activity_output, rendered_activity)
            write_config(cors_output, r2_cors_config(check_values))
            load_jsonc(main_output)
            load_jsonc(activity_output)
            json.loads(cors_output.read_text(encoding="utf-8"))
        print("Private deployment renderer check passed.")
        return 0
    required_arguments = {
        "--manifest": args.manifest,
        "--main-output": args.main_output,
        "--activity-output": args.activity_output,
        "--cors-output": args.cors_output,
    }
    missing_arguments = [name for name, value in required_arguments.items() if value is None]
    if missing_arguments:
        parser.error("the following arguments are required: " + ", ".join(missing_arguments))
    values = load_manifest(args.manifest)
    validate_manifest_keys(values)
    public_main = load_jsonc(root / "wrangler.jsonc")
    rendered_main = main_config(public_main, values)
    rendered_activity = activity_config(
        load_jsonc(root / "wrangler.activity.jsonc"), values
    )
    relativize_repository_paths(rendered_main, root=root, output=args.main_output)
    relativize_repository_paths(rendered_activity, root=root, output=args.activity_output)
    write_config(args.main_output, rendered_main)
    write_config(args.activity_output, rendered_activity)
    write_config(args.cors_output, r2_cors_config(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

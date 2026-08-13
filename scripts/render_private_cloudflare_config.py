"""Render ignored concrete Wrangler configs from a private resource manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def load_manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise ValueError(f"invalid manifest line: {raw_line!r}")
        values[key] = value
    return values


def required(values: dict[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(f"missing private deployment value: {name}")
    return value


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
                rate_limit("AUTH_RATE_LIMITER", "2001", 10),
                rate_limit("PUBLIC_RATE_LIMITER", "2002", 50),
                rate_limit("CFP_UPLOAD_AUTH_RATE_LIMITER", "2003", 10),
                rate_limit("CFP_UPLOAD_POLL_RATE_LIMITER", "2004", 240),
                rate_limit("SPEAKER_UPLOAD_AUTH_RATE_LIMITER", "2005", 3),
                rate_limit("HEADSHOT_UPLOAD_RATE_LIMITER", "2006", 3),
                rate_limit("MAGIC_LINK_RECIPIENT_RATE_LIMITER", "2007", 3),
                rate_limit("MAGIC_LINK_SOURCE_RATE_LIMITER", "2008", 10),
                rate_limit("DEMO_AUTH_RATE_LIMITER", "2009", 30),
            ],
            "limits": {"cpu_ms": 5000},
            "vars": {
                "APP_ENV": "development",
                "APP_VERSION": "development-2",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--main-output", type=Path, required=True)
    parser.add_argument("--activity-output", type=Path, required=True)
    args = parser.parse_args()
    values = load_manifest(args.manifest)
    root = Path(__file__).resolve().parents[1]
    public_main = json.loads((root / "wrangler.jsonc").read_text())
    rendered_main = main_config(public_main, values)
    rendered_activity = activity_config(
        json.loads((root / "wrangler.activity.jsonc").read_text()), values
    )
    relativize_repository_paths(rendered_main, root=root, output=args.main_output)
    relativize_repository_paths(rendered_activity, root=root, output=args.activity_output)
    write_config(args.main_output, rendered_main)
    write_config(args.activity_output, rendered_activity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

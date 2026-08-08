"""Retrieve or regenerate the private D1 key used for first-time setup."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NPX = shutil.which("npx") or "/usr/local/bin/npx"
KEY_PATTERN = re.compile(r"[0-9a-f]{64}")

READ_KEY_SQL = """SELECT credentials.deployment_key
FROM instance_setup_credentials AS credentials
WHERE credentials.singleton_key='primary'
  AND NOT EXISTS (
    SELECT 1 FROM instance_setup AS setup WHERE setup.singleton_key='primary'
  )
  AND NOT EXISTS (SELECT 1 FROM organizations)
LIMIT 1"""

REGENERATE_KEY_SQL = """UPDATE instance_setup_credentials
SET deployment_key=lower(hex(randomblob(32))), generated_at_ms=unixepoch() * 1000
WHERE singleton_key='primary'
  AND NOT EXISTS (
    SELECT 1 FROM instance_setup AS setup WHERE setup.singleton_key='primary'
  )
  AND NOT EXISTS (SELECT 1 FROM organizations)
RETURNING deployment_key"""


class SetupKeyError(RuntimeError):
    """An operator-safe setup-key management failure."""


def _execute(environment_name: str, sql: str, *, local: bool = False) -> list[dict]:
    target = ["--local"] if local else ["--remote", "--env", environment_name]
    environment = {**os.environ, "CI": "1", "NO_COLOR": "1"}
    result = subprocess.run(  # noqa: S603 - arguments are assembled by this trusted CLI
        [
            NPX,
            "wrangler",
            "d1",
            "execute",
            "DB",
            *target,
            "--command",
            sql,
            "--json",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SetupKeyError(f"Wrangler could not access the setup key: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SetupKeyError("Wrangler returned an invalid setup-key response") from error
    if not isinstance(payload, list):
        raise SetupKeyError("Wrangler returned an invalid setup-key response")
    return payload


def _key_from_payload(payload: list[dict]) -> str:
    try:
        key = str(payload[0]["results"][0]["deployment_key"])
    except (IndexError, KeyError, TypeError) as error:
        raise SetupKeyError(
            "No setup key is available. Apply all migrations, or setup has already completed."
        ) from error
    if KEY_PATTERN.fullmatch(key) is None:
        raise SetupKeyError("D1 returned an invalid setup key")
    return key


def read_setup_key(environment_name: str, *, local: bool = False) -> str:
    """Read the setup key without printing it."""
    return _key_from_payload(_execute(environment_name, READ_KEY_SQL, local=local))


def regenerate_setup_key(environment_name: str, *, local: bool = False) -> str:
    """Atomically replace and return the setup key while setup remains incomplete."""
    return _key_from_payload(_execute(environment_name, REGENERATE_KEY_SQL, local=local))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default="dev", help="Wrangler environment name")
    parser.add_argument("--local", action="store_true", help="manage the local D1 database")
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="invalidate the current key and generate a replacement",
    )
    arguments = parser.parse_args()
    try:
        operation = regenerate_setup_key if arguments.regenerate else read_setup_key
        key = operation(arguments.env, local=arguments.local)
    except SetupKeyError as error:
        print(str(error), file=sys.stderr)
        return 1
    label = "New deployment setup key" if arguments.regenerate else "Deployment setup key"
    print(f"{label}: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

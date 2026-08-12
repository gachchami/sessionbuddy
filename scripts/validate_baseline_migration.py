"""Validate the immutable baseline and its ordered D1 migration chain."""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
from pathlib import Path

BASELINE_NAME = "0001_baseline.sql"
CHECKSUM_NAME = "checksums.sha256"
MIGRATION_NAME = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql$")
SETUP_KEY = re.compile(r"[0-9a-f]{64}")


def validate_fresh_database(connection: sqlite3.Connection) -> None:
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise ValueError(f"baseline violates foreign keys: {violations[:5]}")

    table_names = [
        str(row[0])
        for row in connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        ).fetchall()
    ]
    if "d1_migrations" in table_names:
        raise ValueError("baseline must not contain Wrangler's d1_migrations table")
    if "instance_setup_credentials" not in table_names:
        raise ValueError("baseline does not create instance_setup_credentials")

    credential_rows = connection.execute(
        "SELECT singleton_key,deployment_key FROM instance_setup_credentials"
    ).fetchall()
    if (
        len(credential_rows) != 1
        or credential_rows[0][0] != "primary"
        or SETUP_KEY.fullmatch(str(credential_rows[0][1])) is None
    ):
        raise ValueError("baseline must create exactly one valid setup credential")

    populated: list[tuple[str, int]] = []
    for table_name in table_names:
        if table_name == "instance_setup_credentials":
            continue
        quoted_name = table_name.replace('"', '""')
        count = int(
            connection.execute(
                f'SELECT COUNT(*) FROM "{quoted_name}"'  # noqa: S608
            ).fetchone()[0]
        )
        if count:
            populated.append((table_name, count))
    if populated:
        raise ValueError(f"baseline contains business data: {populated}")


def validate(baseline: Path) -> None:
    if baseline.name != BASELINE_NAME:
        raise ValueError(f"the canonical baseline must be named {BASELINE_NAME}")
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(baseline.read_text(encoding="utf-8"))
        validate_fresh_database(connection)
    finally:
        connection.close()


def migration_files(directory: Path) -> list[Path]:
    files = sorted(directory.glob("*.sql"))
    if not files or files[0].name != BASELINE_NAME:
        raise ValueError(f"migration chain must start with {BASELINE_NAME}")
    invalid = [path.name for path in files if MIGRATION_NAME.fullmatch(path.name) is None]
    if invalid:
        raise ValueError(f"invalid migration filenames: {invalid}")
    prefixes = [path.name.split("_", 1)[0] for path in files]
    if len(prefixes) != len(set(prefixes)):
        raise ValueError("migration sequence numbers must be unique")
    return files


def validate_checksums(directory: Path, files: list[Path]) -> None:
    manifest = directory / CHECKSUM_NAME
    if not manifest.is_file():
        raise ValueError(f"migration chain requires {CHECKSUM_NAME}")
    recorded: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or re.fullmatch(r"[0-9a-f]{64}", parts[0]) is None:
            raise ValueError(f"invalid checksum manifest line {line_number}")
        name = parts[1].removeprefix("*")
        if name in recorded:
            raise ValueError(f"duplicate checksum entry for {name}")
        recorded[name] = parts[0]
    expected = {path.name for path in files}
    if set(recorded) != expected:
        raise ValueError(
            f"checksum manifest mismatch: expected {sorted(expected)}, "
            f"recorded {sorted(recorded)}"
        )
    for path in files:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != recorded[path.name]:
            raise ValueError(f"released migration changed: {path.name}")


def validate_chain(directory: Path) -> None:
    files = migration_files(directory)
    validate_checksums(directory, files)
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for migration in files:
            connection.executescript(migration.read_text(encoding="utf-8"))
        validate_fresh_database(connection)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--migrations",
        type=Path,
        default=Path("migrations_baseline"),
    )
    arguments = parser.parse_args()
    validate_chain(arguments.migrations)


if __name__ == "__main__":
    main()

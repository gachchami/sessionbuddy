"""Validate the canonical fresh-install D1 baseline."""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

BASELINE_NAME = "0001_baseline.sql"
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
    files = sorted(baseline.parent.glob("*.sql"))
    if baseline.name != BASELINE_NAME or files != [baseline]:
        names = [path.name for path in files]
        raise ValueError(
            f"{baseline.parent} must contain only {BASELINE_NAME}; found {names}"
        )
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(baseline.read_text(encoding="utf-8"))
        validate_fresh_database(connection)
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("migrations_baseline/0001_baseline.sql"),
    )
    arguments = parser.parse_args()
    validate(arguments.baseline)


if __name__ == "__main__":
    main()

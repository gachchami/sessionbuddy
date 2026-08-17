"""Compare an ordered migration chain with a proposed rebased baseline."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

if __package__:
    from scripts.validate_baseline_migration import (
        migration_files,
        validate_checksums,
        validate_fresh_database,
    )
else:
    from validate_baseline_migration import (  # type: ignore[import-not-found]
        migration_files,
        validate_checksums,
        validate_fresh_database,
    )


def _normalized_sql(value: str | None) -> str | None:
    return " ".join(value.split()) if value is not None else None


def _encoded_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"blob_hex": value.hex()}
    return value


def _rows(connection: sqlite3.Connection, query: str) -> list[list[Any]]:
    return [
        [_encoded_value(value) for value in row]
        for row in connection.execute(query).fetchall()
    ]


def apply_sql_files(paths: Iterable[Path]) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for path in paths:
            connection.executescript(path.read_text(encoding="utf-8"))
        validate_fresh_database(connection)
    except Exception:
        connection.close()
        raise
    return connection


def schema_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    objects = connection.execute(
        """SELECT type,name,tbl_name,sql FROM sqlite_master
           WHERE name NOT LIKE 'sqlite_%'
           ORDER BY type,name"""
    ).fetchall()
    tables = sorted(
        str(row[1]) for row in objects if str(row[0]) == "table"
    )
    table_contracts: dict[str, Any] = {}
    seed_rows: dict[str, list[list[Any]]] = {}

    for table in tables:
        quoted = table.replace('"', '""')
        columns = _rows(connection, f'PRAGMA table_xinfo("{quoted}")')
        table_contracts[table] = {
            "columns": columns,
            "foreign_keys": _rows(
                connection, f'PRAGMA foreign_key_list("{quoted}")'
            ),
            "indexes": _rows(connection, f'PRAGMA index_list("{quoted}")'),
        }
        rows = _rows(
            connection,
            f'SELECT * FROM "{quoted}"',  # noqa: S608 - sqlite_master identifier
        )
        if rows:
            if table == "instance_setup_credentials":
                for row in rows:
                    for index, column in enumerate(columns):
                        if column[1] == "deployment_key":
                            row[index] = "<valid-setup-key>"
                        elif column[1] == "generated_at_ms":
                            row[index] = "<generated-at-ms>"
            seed_rows[table] = sorted(
                rows,
                key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")),
            )

    return {
        "objects": [
            {
                "type": str(object_type),
                "name": str(name),
                "table": str(table),
                "sql": _normalized_sql(sql),
            }
            for object_type, name, table, sql in objects
        ],
        "tables": table_contracts,
        "seed_rows": seed_rows,
        "foreign_key_check": _rows(connection, "PRAGMA foreign_key_check"),
    }


def _first_difference(left: Any, right: Any, path: str = "snapshot") -> str:
    if type(left) is not type(right):
        return f"{path}: type {type(left).__name__} != {type(right).__name__}"
    if isinstance(left, dict):
        if set(left) != set(right):
            return (
                f"{path}: keys {sorted(left)} != {sorted(right)}"
            )
        for key in sorted(left):
            if left[key] != right[key]:
                return _first_difference(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, list):
        if len(left) != len(right):
            return f"{path}: length {len(left)} != {len(right)}"
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            if left_item != right_item:
                return _first_difference(
                    left_item, right_item, f"{path}[{index}]"
                )
    elif left != right:
        return f"{path}: {left!r} != {right!r}"
    return path


def compare_chain_to_baseline(
    previous_migrations: Path, candidate_baseline: Path
) -> None:
    previous_files = migration_files(previous_migrations)
    validate_checksums(previous_migrations, previous_files)
    previous = apply_sql_files(previous_files)
    candidate = apply_sql_files([candidate_baseline])
    try:
        previous_snapshot = schema_snapshot(previous)
        candidate_snapshot = schema_snapshot(candidate)
    finally:
        previous.close()
        candidate.close()

    if previous_snapshot != candidate_snapshot:
        raise ValueError(
            "candidate baseline is not equivalent to the previous migration chain: "
            + _first_difference(previous_snapshot, candidate_snapshot)
        )


def write_rebased_baseline(previous_migrations: Path, output: Path) -> None:
    previous_files = migration_files(previous_migrations)
    validate_checksums(previous_migrations, previous_files)
    connection = apply_sql_files(previous_files)
    try:
        statements = [
            statement
            for statement in connection.iterdump()
            if statement not in {"BEGIN TRANSACTION;", "COMMIT;"}
        ]
    finally:
        connection.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "-- Candidate baseline generated from the validated migration chain.\n"
        + "\n".join(statements)
        + "\n",
        encoding="utf-8",
    )
    compare_chain_to_baseline(previous_migrations, output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare a released migration chain with a rebased baseline."
    )
    parser.add_argument("--previous-migrations", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--candidate-baseline", type=Path)
    action.add_argument("--write-candidate", type=Path)
    arguments = parser.parse_args()
    if arguments.write_candidate is not None:
        write_rebased_baseline(
            arguments.previous_migrations, arguments.write_candidate
        )
    else:
        compare_chain_to_baseline(
            arguments.previous_migrations, arguments.candidate_baseline
        )


if __name__ == "__main__":
    main()

"""Migration, integrity, backup, and isolated restore smoke test."""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import tempfile
import time
from pathlib import Path

try:
    from scripts.seed_large import SeedScale, apply_migrations, seed_large
except ModuleNotFoundError:  # Direct `python scripts/release_db_smoke.py` execution.
    from seed_large import SeedScale, apply_migrations, seed_large

CORE_TABLES = ("submissions", "people", "event_speakers", "speaker_tasks", "agenda_items")
COUNT_QUERIES = {
    "submissions": "SELECT count(*) FROM submissions",
    "people": "SELECT count(*) FROM people",
    "event_speakers": "SELECT count(*) FROM event_speakers",
    "speaker_tasks": "SELECT count(*) FROM speaker_tasks",
    "agenda_items": "SELECT count(*) FROM agenda_items",
}


def schema_digest(db: sqlite3.Connection) -> str:
    rows = db.execute(
        """SELECT type,name,tbl_name,sql FROM sqlite_schema
           WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"""
    ).fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def validate(db: sqlite3.Connection) -> dict[str, int]:
    integrity = db.execute("PRAGMA integrity_check").fetchone()
    if integrity != ("ok",):
        raise RuntimeError(f"integrity check failed: {integrity}")
    violations = db.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"foreign-key check failed: {violations[:5]}")
    return {table: db.execute(COUNT_QUERIES[table]).fetchone()[0] for table in CORE_TABLES}


def backup_restore_smoke(migrations: Path, scale: SeedScale) -> dict[str, object]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sessionbuddy-db-smoke-") as directory:
        source_path = Path(directory) / "source.sqlite3"
        backup_path = Path(directory) / "backup.sqlite3"
        restored_path = Path(directory) / "restored.sqlite3"

        source = sqlite3.connect(source_path)
        try:
            apply_migrations(source, migrations)
            seed_large(source, scale)
            source_counts = validate(source)
            source_schema = schema_digest(source)
            backup = sqlite3.connect(backup_path)
            try:
                source.backup(backup)
            finally:
                backup.close()
        finally:
            source.close()

        backup = sqlite3.connect(backup_path)
        restored = sqlite3.connect(restored_path)
        try:
            backup.backup(restored)
        finally:
            backup.close()
        try:
            restored.execute("PRAGMA foreign_keys = ON")
            restored_counts = validate(restored)
            restored_schema = schema_digest(restored)
        finally:
            restored.close()

    if restored_counts != source_counts or restored_schema != source_schema:
        raise RuntimeError("restored database does not match source database")
    return {
        "elapsed_ms": round((time.monotonic() - started) * 1_000),
        "schema_sha256": source_schema,
        "row_counts": source_counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migrations", type=Path, default=Path("migrations"))
    parser.add_argument("--large", action="store_true", help="use the complete release envelope")
    args = parser.parse_args()
    scale = SeedScale() if args.large else SeedScale(100, 20, 200, 20)
    result = backup_restore_smoke(args.migrations, scale)
    print(f"database release smoke passed in {result['elapsed_ms']} ms")
    print(f"schema sha256: {result['schema_sha256']}")
    print(f"row counts: {result['row_counts']}")


if __name__ == "__main__":
    main()

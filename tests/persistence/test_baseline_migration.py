import re
import sqlite3
from pathlib import Path

from scripts.build_baseline_migration import build

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEDGER = PROJECT_ROOT / "migrations"
BASELINE = PROJECT_ROOT / "migrations_baseline" / "0001_baseline.sql"


def apply_sql_files(directory: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in sorted(directory.glob("*.sql")):
        connection.executescript(migration.read_text(encoding="utf-8"))
    return connection


def schema_catalog(connection: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    return connection.execute(
        """SELECT type,name,tbl_name,sql
           FROM sqlite_master
           WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
           ORDER BY type,name"""
    ).fetchall()


def test_checked_in_baseline_is_deterministic_and_current(tmp_path: Path) -> None:
    first = tmp_path / "first.sql"
    second = tmp_path / "second.sql"
    build(LEDGER, first)
    build(LEDGER, second)

    assert first.read_bytes() == second.read_bytes()
    # Applied baseline files remain immutable; additive baseline migrations bring
    # fresh installs forward without rewriting the checked-in 0001 snapshot.
    build(LEDGER, BASELINE, check=True)


def test_baseline_matches_the_immutable_ledger_schema() -> None:
    ledger = apply_sql_files(LEDGER)
    baseline = apply_sql_files(BASELINE.parent)
    try:
        assert schema_catalog(baseline) == schema_catalog(ledger)
    finally:
        ledger.close()
        baseline.close()


def test_baseline_creates_only_one_setup_credential_and_no_business_data() -> None:
    connection = apply_sql_files(BASELINE.parent)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'
                   ORDER BY name"""
            ).fetchall()
        ]
        assert "d1_migrations" not in tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        credential = connection.execute(
            "SELECT singleton_key,deployment_key FROM instance_setup_credentials"
        ).fetchone()
        assert credential is not None
        assert credential[0] == "primary"
        assert re.fullmatch(r"[0-9a-f]{64}", credential[1])

        for table_name in tables:
            expected = 1 if table_name == "instance_setup_credentials" else 0
            quoted_name = table_name.replace('"', '""')
            count = connection.execute(
                f'SELECT COUNT(*) FROM "{quoted_name}"'  # noqa: S608
            ).fetchone()[0]
            assert count == expected, table_name
    finally:
        connection.close()

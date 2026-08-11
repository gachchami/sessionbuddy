import sqlite3
from pathlib import Path

import pytest

from scripts.validate_baseline_migration import validate
from tests.schema import BASELINE


def apply_baseline(path: Path = BASELINE) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(path.read_text(encoding="utf-8"))
    return connection


def test_canonical_baseline_is_the_only_active_migration() -> None:
    assert sorted(BASELINE.parent.glob("*.sql")) == [BASELINE]
    validate(BASELINE)


def test_validator_rejects_an_additional_active_migration(tmp_path: Path) -> None:
    baseline = tmp_path / BASELINE.name
    baseline.write_bytes(BASELINE.read_bytes())
    (tmp_path / "0002_unwanted.sql").write_text("SELECT 1;\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain only"):
        validate(baseline)


def test_baseline_creates_only_one_setup_credential_and_no_business_data() -> None:
    connection = apply_baseline()
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
        assert len(credential[1]) == 64

        for table_name in tables:
            expected = 1 if table_name == "instance_setup_credentials" else 0
            quoted_name = table_name.replace('"', '""')
            count = connection.execute(
                f'SELECT COUNT(*) FROM "{quoted_name}"'  # noqa: S608
            ).fetchone()[0]
            assert count == expected, table_name
    finally:
        connection.close()


def test_rebased_baseline_contains_the_complete_fresh_install_schema() -> None:
    connection = apply_baseline()
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
        }
        assert len(tables) == 66
        assert {
            "owned_resources",
            "resource_access_grants",
            "resource_ownership_transfers",
            "event_labels",
            "accepted_session_labels",
        } <= tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()

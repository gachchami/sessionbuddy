import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts.compare_migration_schemas import (
    apply_sql_files,
    compare_chain_to_baseline,
    schema_snapshot,
    write_rebased_baseline,
)


def database(tmp_path: Path, sql: str) -> sqlite3.Connection:
    tmp_path.mkdir(parents=True, exist_ok=True)
    migration = tmp_path / "schema.sql"
    migration.write_text(sql, encoding="utf-8")
    return apply_sql_files([migration])


REQUIRED_SETUP = """
CREATE TABLE instance_setup_credentials (
  singleton_key TEXT PRIMARY KEY,
  deployment_key TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL
);
INSERT INTO instance_setup_credentials VALUES (
  'primary',
  '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  1000
);
"""


def test_schema_snapshot_is_stable_across_formatting(tmp_path: Path) -> None:
    left = database(
        tmp_path / "left",
        REQUIRED_SETUP
        + """
          CREATE TABLE example (id TEXT PRIMARY KEY, state TEXT CHECK(state IN ('a','b')));
          CREATE INDEX example_state ON example(state);
          CREATE TRIGGER example_state_guard BEFORE UPDATE OF state ON example
          WHEN NEW.state='b' BEGIN SELECT RAISE(ABORT,'blocked'); END;
          """,
    )
    right = database(
        tmp_path / "right",
        REQUIRED_SETUP
        + """CREATE TABLE example (id TEXT PRIMARY KEY,
          state TEXT CHECK(state IN ('a','b')));
        CREATE INDEX example_state ON example(state);
        CREATE TRIGGER example_state_guard
        BEFORE UPDATE OF state ON example WHEN NEW.state='b'
        BEGIN SELECT RAISE(ABORT,'blocked'); END;
        """,
    )
    try:
        assert schema_snapshot(left) == schema_snapshot(right)
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize(
    "changed_sql",
    [
        "CREATE TABLE example (id TEXT PRIMARY KEY, state TEXT CHECK(state IN ('a','b','c')));",
        "CREATE TABLE example (id TEXT PRIMARY KEY, state TEXT CHECK(state IN ('a','b'))); "
        "CREATE INDEX example_state ON example(state);",
    ],
)
def test_schema_snapshot_detects_contract_drift(
    tmp_path: Path, changed_sql: str
) -> None:
    expected = database(
        tmp_path / "expected",
        REQUIRED_SETUP
        + """
          CREATE TABLE example (id TEXT PRIMARY KEY, state TEXT CHECK(state IN ('a','b')));
          CREATE INDEX example_state ON example(state);
          CREATE TRIGGER example_state_guard BEFORE UPDATE OF state ON example
          WHEN NEW.state='b' BEGIN SELECT RAISE(ABORT,'blocked'); END;
          """,
    )
    changed = database(tmp_path / "changed", REQUIRED_SETUP + changed_sql)
    try:
        assert schema_snapshot(expected) != schema_snapshot(changed)
    finally:
        expected.close()
        changed.close()


def test_schema_snapshot_compares_setup_key_semantics_not_random_value(
    tmp_path: Path,
) -> None:
    expected = database(tmp_path / "expected", REQUIRED_SETUP)
    changed = database(
        tmp_path / "changed",
        REQUIRED_SETUP.replace("0123456789abcdef", "fedcba9876543210"),
    )
    try:
        assert schema_snapshot(expected) == schema_snapshot(changed)
    finally:
        expected.close()
        changed.close()


def test_generated_candidate_is_equivalent_and_has_no_transaction_wrapper(
    tmp_path: Path,
) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    baseline = migrations / "0001_baseline.sql"
    baseline.write_text(
        REQUIRED_SETUP + "CREATE TABLE example (id TEXT PRIMARY KEY);\n",
        encoding="utf-8",
    )
    incremental = migrations / "0002_example_state.sql"
    incremental.write_text(
        "ALTER TABLE example ADD COLUMN state TEXT DEFAULT 'active';\n",
        encoding="utf-8",
    )
    (migrations / "checksums.sha256").write_text(
        f"{hashlib.sha256(baseline.read_bytes()).hexdigest()}  {baseline.name}\n"
        f"{hashlib.sha256(incremental.read_bytes()).hexdigest()}  {incremental.name}\n",
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate.sql"

    write_rebased_baseline(migrations, candidate)

    generated = candidate.read_text(encoding="utf-8")
    assert "BEGIN TRANSACTION" not in generated
    assert "COMMIT;" not in generated
    compare_chain_to_baseline(migrations, candidate)

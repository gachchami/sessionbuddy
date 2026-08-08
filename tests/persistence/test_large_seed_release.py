from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.release_db_smoke import backup_restore_smoke, schema_digest, validate
from scripts.seed_large import SeedScale, apply_migrations, seed_large

ROOT = Path(__file__).parents[2]
MIGRATIONS = ROOT / "migrations"


@pytest.fixture(scope="module")
def large_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    apply_migrations(db, MIGRATIONS)
    seed_large(db)
    yield db
    db.close()


def query_plan(db: sqlite3.Connection, sql: str, values: tuple[object, ...]) -> str:
    return " ".join(
        str(value)
        for row in db.execute(f"EXPLAIN QUERY PLAN {sql}", values)
        for value in row
    )


def test_large_seed_matches_release_envelope_and_has_clean_foreign_keys(
    large_db: sqlite3.Connection,
) -> None:
    assert validate(large_db) == {
        "submissions": 10_000,
        "people": 2_000,
        "event_speakers": 2_000,
        "speaker_tasks": 50_000,
        "agenda_items": 2_000,
    }
    assert large_db.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    ("sql", "values", "index"),
    (
        (
            """SELECT id FROM submissions
               WHERE organization_id=? AND event_id=? AND program_id=?
               ORDER BY submitted_at_ms DESC,id DESC LIMIT ?""",
            ("load-org", "load-event", "load-program", 100),
            "idx_submissions_program_recent",
        ),
        (
            """SELECT id FROM event_speakers
               WHERE organization_id=? AND event_id=? AND status=?
               ORDER BY last_activity_at_ms DESC,id DESC LIMIT ?""",
            ("load-org", "load-event", "onboarding", 100),
            "idx_event_speakers_event_status_activity",
        ),
        (
            """SELECT id FROM speaker_tasks
               WHERE organization_id=? AND event_id=? AND state=?
               ORDER BY (due_at_ms IS NULL),due_at_ms,id LIMIT ?""",
            ("load-org", "load-event", "open", 100),
            "idx_speaker_tasks_dashboard_state_deadline",
        ),
        (
            """SELECT id FROM agenda_items
               WHERE organization_id=? AND event_id=? AND revision_id=? AND starts_at_ms>=?
               ORDER BY starts_at_ms,id LIMIT ?""",
            ("load-org", "load-event", "load-revision", 1_786_154_400_000, 100),
            "idx_agenda_items_revision_time",
        ),
    ),
)
def test_large_list_query_plans_are_indexed_and_avoid_temp_sort(
    large_db: sqlite3.Connection,
    sql: str,
    values: tuple[object, ...],
    index: str,
) -> None:
    plan = query_plan(large_db, sql, values)
    assert index in plan
    assert "USE TEMP B-TREE FOR ORDER BY" not in plan


def test_seed_is_deterministic_at_database_level() -> None:
    scale = SeedScale(40, 10, 60, 10)
    databases = []
    for _ in range(2):
        db = sqlite3.connect(":memory:")
        apply_migrations(db, MIGRATIONS)
        seed_large(db, scale)
        databases.append(db)
    try:
        assert schema_digest(databases[0]) == schema_digest(databases[1])
        for table in ("submissions", "people", "speaker_tasks", "agenda_items"):
            assert databases[0].execute(
                f"SELECT * FROM {table} ORDER BY id"  # noqa: S608 - fixed test allow-list
            ).fetchall() == (
                databases[1]
                .execute(f"SELECT * FROM {table} ORDER BY id")  # noqa: S608
                .fetchall()
            )
    finally:
        for db in databases:
            db.close()


def test_migration_backup_and_restore_smoke() -> None:
    result = backup_restore_smoke(MIGRATIONS, SeedScale(50, 10, 80, 10))
    assert result["row_counts"] == {
        "submissions": 50,
        "people": 10,
        "event_speakers": 10,
        "speaker_tasks": 80,
        "agenda_items": 10,
    }
    assert len(str(result["schema_sha256"])) == 64

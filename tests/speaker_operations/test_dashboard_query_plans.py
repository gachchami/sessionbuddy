import sqlite3

from tests.speaker_operations.test_speaker_onboarding_schema import (
    add_speaker,
    link_submission_speaker,
    seed_platform,
)

TASK_COUNT = 50_000


def migrated_database() -> sqlite3.Connection:
    from tests.speaker_operations.test_speaker_onboarding_schema import MIGRATIONS

    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        db.executescript(migration.read_text(encoding="utf-8"))
    seed_platform(db)
    add_speaker(db)
    link_submission_speaker(db)
    return db


def seed_task_envelope(db: sqlite3.Connection) -> None:
    rows = (
        (
            f"task-{number:05d}",
            "org-a",
            "event-a",
            "speaker-a",
            None,
            "profile" if number % 2 else "slides",
            f"Task {number}",
            "profile" if number % 2 else "slides",
            "open" if number % 3 else "completed",
            None if number % 20 == 0 else 2_000 + number,
            1_000 if number % 3 == 0 else None,
            1_000,
            1_000,
            f"fingerprint-{number}".encode(),
        )
        for number in range(TASK_COUNT)
    )
    db.executemany(
        """INSERT INTO speaker_tasks
           (id, organization_id, event_id, event_speaker_id, submission_id,
            task_type, title, destination_type, state, due_at_ms, completed_at_ms,
            created_at_ms, updated_at_ms, content_fingerprint)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


def explain(db: sqlite3.Connection, sql: str, values: tuple[object, ...]) -> str:
    return " ".join(
        str(value) for row in db.execute(f"EXPLAIN QUERY PLAN {sql}", values) for value in row
    )


def assert_indexed_without_sort(plan: str, index: str) -> None:
    assert index in plan
    assert "SCAN speaker_tasks" not in plan
    assert "USE TEMP B-TREE FOR ORDER BY" not in plan


def test_dashboard_filter_plans_at_50k_task_envelope() -> None:
    db = migrated_database()
    seed_task_envelope(db)
    assert db.execute("SELECT count(*) FROM speaker_tasks").fetchone() == (TASK_COUNT,)

    state_plan = explain(
        db,
        """SELECT id, event_speaker_id, submission_id, task_type, state, due_at_ms
           FROM speaker_tasks
           WHERE organization_id = ? AND event_id = ? AND state = ?
             AND ((due_at_ms IS NULL) > ?
                  OR ((due_at_ms IS NULL) = ? AND due_at_ms > ?)
                  OR ((due_at_ms IS NULL) = ? AND due_at_ms = ? AND id > ?))
           ORDER BY (due_at_ms IS NULL), due_at_ms, id LIMIT ?""",
        ("org-a", "event-a", "open", 0, 0, 20_000, 0, 20_000, "task-00001", 100),
    )
    assert_indexed_without_sort(state_plan, "idx_speaker_tasks_dashboard_state_deadline")

    type_state_plan = explain(
        db,
        """SELECT id FROM speaker_tasks
           WHERE organization_id = ? AND event_id = ? AND task_type = ? AND state = ?
           ORDER BY (due_at_ms IS NULL), due_at_ms, id LIMIT ?""",
        ("org-a", "event-a", "slides", "open", 100),
    )
    assert_indexed_without_sort(type_state_plan, "idx_speaker_tasks_dashboard_type_state_deadline")

    type_plan = explain(
        db,
        """SELECT id FROM speaker_tasks
           WHERE organization_id = ? AND event_id = ? AND task_type = ?
           ORDER BY (due_at_ms IS NULL), due_at_ms, id LIMIT ?""",
        ("org-a", "event-a", "slides", 100),
    )
    assert_indexed_without_sort(type_plan, "idx_speaker_tasks_dashboard_type_deadline")

    all_plan = explain(
        db,
        """SELECT id FROM speaker_tasks
           WHERE organization_id = ? AND event_id = ?
           ORDER BY (due_at_ms IS NULL), due_at_ms, id LIMIT ?""",
        ("org-a", "event-a", 100),
    )
    assert_indexed_without_sort(all_plan, "idx_speaker_tasks_dashboard_all_deadline")


def test_portal_plan_at_50k_task_envelope() -> None:
    db = migrated_database()
    seed_task_envelope(db)
    plan = explain(
        db,
        """SELECT id, task_type, title, state, due_at_ms FROM speaker_tasks
           WHERE organization_id = ? AND event_id = ? AND event_speaker_id = ?
             AND state = ?
           ORDER BY (due_at_ms IS NULL), due_at_ms, id LIMIT ?""",
        ("org-a", "event-a", "speaker-a", "open", 100),
    )
    assert_indexed_without_sort(plan, "idx_speaker_tasks_portal_state_deadline")

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))
INSERT_SUBMISSION_SPEAKER = """INSERT INTO submission_speakers
    (id, organization_id, event_id, submission_id, event_speaker_id, role,
     snapshot_name, created_at_ms)
    VALUES (?, ?, ?, ?, ?, ?, ?, 1000)"""


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    seed_platform(connection)
    return connection


def seed_platform(db: sqlite3.Connection) -> None:
    now = 1_000
    for suffix in ("a", "b"):
        db.execute(
            "INSERT INTO organizations (id,name,status,created_at_ms,updated_at_ms) "
            "VALUES (?,?, 'active',?,?)",
            (f"org-{suffix}", f"Org {suffix}", now, now),
        )
        db.execute(
            "INSERT INTO users (id,email,normalized_email,status,created_at_ms,updated_at_ms) "
            "VALUES (?,?,?,'active',?,?)",
            (
                f"user-{suffix}",
                f"speaker-{suffix}@example.test",
                f"speaker-{suffix}@example.test",
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO organization_memberships "
            "(id,organization_id,user_id,role,status,created_at_ms,updated_at_ms) "
            "VALUES (?,?,?,'member','active',?,?)",
            (f"org-member-{suffix}", f"org-{suffix}", f"user-{suffix}", now, now),
        )
        db.execute(
            "INSERT INTO events "
            "(id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,delivery_mode,status,"
            "created_at_ms,updated_at_ms) VALUES (?,?,?,1000,2000,'UTC','hybrid','active',?,?)",
            (f"event-{suffix}", f"org-{suffix}", f"Event {suffix}", now, now),
        )
        db.execute(
            "INSERT INTO event_memberships "
            "(id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms) "
            "VALUES (?,?,?,?, 'speaker','active',?,?)",
            (
                f"event-member-{suffix}",
                f"org-{suffix}",
                f"event-{suffix}",
                f"user-{suffix}",
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO call_for_speaker_forms "
            "(id,organization_id,event_id,version,slug,welcome_text,schema_json,"
            "status,published_at_ms,created_at_ms,updated_at_ms) "
            "VALUES (?,?,?,1,?,'Welcome','{}','published',?,?,?)",
            (
                f"form-{suffix}",
                f"org-{suffix}",
                f"event-{suffix}",
                f"form-{suffix}",
                now,
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO submissions "
            "(id,organization_id,event_id,form_id,public_session_id,proposal_title,"
            "proposal_abstract,speaker_name,status,submitted_at_ms,created_at_ms,updated_at_ms) "
            "VALUES (?,?,?,?,?,?,'Abstract','Same Speaker','submitted',?,?,?)",
            (
                f"submission-{suffix}",
                f"org-{suffix}",
                f"event-{suffix}",
                f"form-{suffix}",
                f"public-{suffix}",
                f"Talk {suffix}",
                now,
                now,
                now,
            ),
        )
        db.execute(
            """INSERT INTO submission_drafts
               (id,organization_id,event_id,form_id,user_id,answers_json,
                created_at_ms,updated_at_ms)
               VALUES(?,?,?,?,?,'{}',?,?)""",
            (
                f"draft-{suffix}",
                f"org-{suffix}",
                f"event-{suffix}",
                f"form-{suffix}",
                f"user-{suffix}",
                now,
                now,
            ),
        )
        db.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,
                created_at_ms,updated_at_ms)
               VALUES(?,?,?,?,'{}','draft',?,?)""",
            (
                f"seed-round-{suffix}",
                f"org-{suffix}",
                f"event-{suffix}",
                f"Seed round {suffix}",
                now,
                now,
            ),
        )


def add_speaker(db: sqlite3.Connection, suffix: str = "a") -> None:
    db.execute(
        "INSERT INTO people "
        "(id,organization_id,user_id,display_name,created_at_ms,updated_at_ms) "
        "VALUES (?,?,?,?,1000,1000)",
        (f"person-{suffix}", f"org-{suffix}", f"user-{suffix}", "Same Speaker"),
    )
    db.execute(
        "INSERT INTO event_speakers "
        "(id,organization_id,event_id,person_id,status,accepted_at_ms,last_activity_at_ms,"
        "created_at_ms,updated_at_ms) VALUES (?,?,?,?,'onboarding',1000,1000,1000,1000)",
        (f"speaker-{suffix}", f"org-{suffix}", f"event-{suffix}", f"person-{suffix}"),
    )


def link_submission_speaker(db: sqlite3.Connection, suffix: str = "a") -> None:
    db.execute(
        INSERT_SUBMISSION_SPEAKER,
        (
            f"link-{suffix}",
            f"org-{suffix}",
            f"event-{suffix}",
            f"submission-{suffix}",
            f"speaker-{suffix}",
            "primary",
            "Same Speaker",
        ),
    )


def test_all_migrations_apply_and_foreign_keys_are_clean(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"people", "event_speakers", "submission_speakers", "speaker_tasks"} <= tables


def test_people_link_requires_membership_in_same_organization(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO people "
            "(id,organization_id,user_id,display_name,created_at_ms,updated_at_ms) "
            "VALUES ('cross-person','org-a','user-b','Speaker',1000,1000)"
        )


def test_submission_speaker_rejects_cross_tenant_links_and_second_primary(
    db: sqlite3.Connection,
) -> None:
    add_speaker(db, "a")
    add_speaker(db, "b")
    db.execute(
        INSERT_SUBMISSION_SPEAKER,
        ("link-a", "org-a", "event-a", "submission-a", "speaker-a", "primary", "Same Speaker"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            INSERT_SUBMISSION_SPEAKER,
            (
                "cross",
                "org-a",
                "event-a",
                "submission-a",
                "speaker-b",
                "co_speaker",
                "Same Speaker",
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            INSERT_SUBMISSION_SPEAKER,
            ("second", "org-a", "event-a", "submission-a", "speaker-a", "primary", "Same Speaker"),
        )


def test_task_state_constraints_and_tenant_safe_parentage(db: sqlite3.Connection) -> None:
    add_speaker(db, "a")
    link_submission_speaker(db, "a")
    db.execute(
        "INSERT INTO speaker_tasks "
        "(id,organization_id,event_id,event_speaker_id,submission_id,task_type,title,"
        "destination_type,state,due_at_ms,created_at_ms,updated_at_ms) "
        "VALUES ('task-a','org-a','event-a','speaker-a','submission-a','profile',"
        "'Complete profile','profile','open',2000,1000,1000)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO speaker_tasks "
            "(id,organization_id,event_id,event_speaker_id,task_type,title,destination_type,"
            "state,completed_at_ms,created_at_ms,updated_at_ms) "
            "VALUES ('invalid-state','org-a','event-a','speaker-a','profile','Profile',"
            "'profile','open',1000,1000,1000)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO speaker_tasks "
            "(id,organization_id,event_id,event_speaker_id,task_type,title,destination_type,"
            "state,created_at_ms,updated_at_ms) VALUES "
            "('cross-task','org-b','event-b','speaker-a','profile','Profile','profile','open',1000,1000)"
        )


def test_portal_and_dashboard_access_paths_use_tenant_indexes(db: sqlite3.Connection) -> None:
    portal_plan = " ".join(
        str(value)
        for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM speaker_tasks WHERE organization_id=? "
            "AND event_id=? AND event_speaker_id=? AND state=? "
            "ORDER BY due_at_ms,id LIMIT 25",
            ("org-a", "event-a", "speaker-a", "open"),
        )
        for value in row
    )
    dashboard_plan = " ".join(
        str(value)
        for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM speaker_tasks WHERE organization_id=? "
            "AND event_id=? AND state=? ORDER BY due_at_ms,id LIMIT 25",
            ("org-a", "event-a", "open"),
        )
        for value in row
    )
    assert "idx_speaker_tasks_portal" in portal_plan
    assert "idx_speaker_tasks_dashboard" in dashboard_plan

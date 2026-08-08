import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in sorted((ROOT / "migrations").glob("*.sql")):
        connection.executescript(migration.read_text())
    connection.execute("INSERT INTO organizations VALUES ('org','Org','active',1,1,1,NULL)")
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,
            authorization_version,version,created_at_ms,updated_at_ms)
           VALUES ('user','a@b.test','a@b.test','active',1,1,1,1,1)"""
    )
    connection.execute(
        """INSERT INTO events VALUES
           ('event','org','Event',1,2,'UTC',NULL,'virtual',NULL,NULL,NULL,
            'active',1,1,1,NULL)"""
    )
    return connection


def test_deterministic_message_key_prevents_duplicate_delivery(db) -> None:
    values = (
        "message",
        "org",
        "event",
        None,
        "user",
        "a@b.test",
        "Subject",
        "Body",
        "same",
        "queued",
        1,
        1,
    )
    sql = """INSERT INTO communication_messages
      (id,organization_id,event_id,template_id,recipient_user_id,recipient_email,subject,
       html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    db.execute(sql, values)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, ("other", *values[1:]))


def test_organization_level_auth_message_does_not_require_an_event(db) -> None:
    values = (
        "auth-message",
        "org",
        None,
        None,
        "user",
        "a@b.test",
        "Sign in",
        "Body",
        "auth:challenge",
        "queued",
        1,
        1,
    )
    sql = """INSERT INTO communication_messages
      (id,organization_id,event_id,template_id,recipient_user_id,recipient_email,subject,
       html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
    db.execute(sql, values)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(sql, ("other-auth-message", *values[1:]))


def test_reminder_recompute_slot_is_unique_and_due_query_indexed(db) -> None:
    db.execute(
        """INSERT INTO communication_templates VALUES
           ('tpl','org','event','Reminder','task_reminder','Hi','Body',1,1,1)"""
    )
    db.execute(
        """INSERT INTO reminder_schedules
           (id,organization_id,event_id,task_id,template_id,recipient_user_id,
            send_at_ms,state,updated_at_ms)
           VALUES ('r','org','event','task','tpl','user',100,'scheduled',1)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO reminder_schedules
               (id,organization_id,event_id,task_id,template_id,recipient_user_id,
                send_at_ms,state,updated_at_ms)
               VALUES ('r2','org','event','task','tpl','user',200,'scheduled',1)"""
        )
    plan = " ".join(
        row[3]
        for row in db.execute(
            """EXPLAIN QUERY PLAN SELECT id FROM reminder_schedules
               WHERE state='scheduled' AND send_at_ms<=100
               ORDER BY send_at_ms,id LIMIT 100"""
        )
    )
    assert "idx_reminders_due" in plan

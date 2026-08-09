import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
LEDGER = sorted((ROOT / "migrations").glob("*.sql"))
MIGRATION = ROOT / "migrations" / "0034_event_owned_program_flow.sql"


def database_before_migration() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    for migration in LEDGER:
        if migration == MIGRATION:
            break
        db.executescript(migration.read_text(encoding="utf-8"))
    db.execute(
        "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
        "VALUES('org','Org','active',1,1)"
    )
    db.execute(
        """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES('user','user@example.test','user@example.test','active',1,1)"""
    )
    db.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('member','org','user','member','active',1,1)"""
    )
    db.execute(
        """INSERT INTO events
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,delivery_mode,
            status,created_at_ms,updated_at_ms)
           VALUES('event','org','Event',1,2,'UTC','hybrid','active',1,1)"""
    )
    return db


def add_program(db: sqlite3.Connection, suffix: str, status: str = "open") -> str:
    program_id = f"program-{suffix}"
    db.execute(
        """INSERT INTO programs
           (id,organization_id,event_id,name,status,created_at_ms,updated_at_ms)
           VALUES(?,'org','event',?,?,1,1)""",
        (program_id, f"Program {suffix}", status),
    )
    return program_id


def apply_refactor(db: sqlite3.Connection) -> None:
    script = MIGRATION.read_text(encoding="utf-8")
    db.executescript(f"BEGIN IMMEDIATE;\n{script}\nCOMMIT;")


def test_event_owned_refactor_preserves_rows_and_foreign_keys() -> None:
    db = database_before_migration()
    try:
        program_id = add_program(db, "main")
        db.execute(
            """INSERT INTO call_for_speaker_forms
               (id,organization_id,event_id,program_id,version,slug,welcome_text,schema_json,
                status,published_at_ms,created_at_ms,updated_at_ms)
               VALUES('form','org','event',?,1,'event-cfp','Welcome','{}','published',1,1,1)""",
            (program_id,),
        )
        db.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,program_id,form_id,public_session_id,
                proposal_title,proposal_abstract,speaker_name,speaker_email,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES('submission','org','event',?,'form','public','Title','Abstract',
                      'Speaker','speaker@example.test','submitted',1,1,1)""",
            (program_id,),
        )
        db.execute(
            """INSERT INTO submission_drafts
               (id,organization_id,event_id,program_id,form_id,user_id,answers_json,
                created_at_ms,updated_at_ms)
               VALUES('draft','org','event',?,'form','user','{"title":"Draft"}',1,1)""",
            (program_id,),
        )
        db.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,program_id,name,rubric_json,status,
                created_at_ms,updated_at_ms)
               VALUES('round','org','event',?,'Round','{}','open',1,1)""",
            (program_id,),
        )
        db.execute(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES('evaluator-member','org','event','user','evaluator','active',1,1)"""
        )
        db.execute(
            """INSERT INTO cfp_form_write_guards(id,form_id,applied_changes,created_at_ms)
               VALUES('guard','form',1,1)"""
        )
        db.execute(
            """INSERT INTO submission_contributors
               (id,organization_id,event_id,submission_id,display_name,email,
                normalized_email,created_at_ms,updated_at_ms)
               VALUES('contributor','org','event','submission','Contributor',
                      'contributor@example.test','contributor@example.test',1,1)"""
        )
        db.execute(
            """INSERT INTO ai_triage_results
               (id,organization_id,event_id,submission_id,model,score,recommendation,
                rationale,generated_by_user_id,generated_at_ms)
               VALUES('triage','org','event','submission','synthetic',8,'accept',
                      'Synthetic rationale','user',1)"""
        )
        db.execute(
            """INSERT INTO people
               (id,organization_id,display_name,links_json,created_at_ms,updated_at_ms)
               VALUES('person','org','Speaker','[]',1,1)"""
        )
        db.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,accepted_at_ms,
                last_activity_at_ms,created_at_ms,updated_at_ms)
               VALUES('event-speaker','org','event','person','onboarding',1,1,1,1)"""
        )
        db.execute(
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,
                snapshot_name,created_at_ms)
               VALUES('submission-speaker','org','event','submission','event-speaker',
                      'primary','Speaker',1)"""
        )
        db.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,submission_id,task_type,
                title,destination_type,state,created_at_ms,updated_at_ms)
               VALUES('task','org','event','event-speaker','submission','slides',
                      'Slides','slides','open',1,1)"""
        )
        db.execute(
            """INSERT INTO speaker_assets
               (id,organization_id,event_id,event_speaker_id,submission_id,task_id,kind,
                created_at_ms,updated_at_ms)
               VALUES('asset','org','event','event-speaker','submission','task','slides',1,1)"""
        )
        db.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,status,
                created_at_ms,updated_at_ms)
               VALUES('assignment','org','event','round','submission','user','assigned',1,1)"""
        )
        db.execute(
            """INSERT INTO evaluations
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,rating,
                recommendation,internal_comment,state,created_at_ms,updated_at_ms)
               VALUES('evaluation','org','event','round','assignment','user',4,
                      'accept','','draft',1,1)"""
        )
        db.execute(
            """INSERT INTO evaluation_conflicts
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,
                conflict_type,explanation,declared_at_ms)
               VALUES('conflict','org','event','round','assignment','user','other','Known',1)"""
        )
        db.execute(
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
                decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES('decision','org','event','round','submission','accepted','',
                      'user',1,1)"""
        )
        db.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
               VALUES('accepted','org','event','submission','decision',1)"""
        )

        apply_refactor(db)

        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='programs'"
        ).fetchone() is None
        for table in (
            "call_for_speaker_forms",
            "submissions",
            "submission_drafts",
            "evaluation_rounds",
        ):
            assert "program_id" not in {
                row[1] for row in db.execute(f"PRAGMA table_info({table})")
            }
            assert db.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed table allow-list
            ).fetchone()[0] == 1
        assert db.execute(
            "SELECT answers_json FROM submission_drafts WHERE id='draft'"
        ).fetchone()[0] == '{"title":"Draft"}'
        for dependent in (
            "cfp_form_write_guards",
            "evaluation_assignments",
            "evaluations",
            "evaluation_conflicts",
            "submission_decisions",
            "submission_speakers",
            "submission_contributors",
            "ai_triage_results",
            "speaker_assets",
            "speaker_tasks",
            "accepted_sessions",
        ):
            assert db.execute(
                f"SELECT COUNT(*) FROM {dependent}"  # noqa: S608 - fixed table allow-list
            ).fetchone()[0] == 1, dependent
    finally:
        db.close()


def test_refactor_aborts_for_duplicate_event_form_versions() -> None:
    db = database_before_migration()
    try:
        first = add_program(db, "first")
        second = add_program(db, "second", "archived")
        for suffix, program_id in (("first", first), ("second", second)):
            db.execute(
                """INSERT INTO call_for_speaker_forms
                   (id,organization_id,event_id,program_id,version,slug,welcome_text,
                    schema_json,status,created_at_ms,updated_at_ms)
                   VALUES(?,?, 'event',?,1,?,'Welcome','{}','draft',1,1)""",
                (f"form-{suffix}", "org", program_id, f"form-{suffix}"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            apply_refactor(db)
    finally:
        db.close()


def test_refactor_aborts_for_multiple_open_event_rounds() -> None:
    db = database_before_migration()
    try:
        first = add_program(db, "first")
        second = add_program(db, "second", "archived")
        for suffix, program_id in (("first", first), ("second", second)):
            db.execute(
                """INSERT INTO evaluation_rounds
                   (id,organization_id,event_id,program_id,name,rubric_json,status,
                    created_at_ms,updated_at_ms)
                   VALUES(?,?, 'event',?,?,'{}','open',1,1)""",
                (f"round-{suffix}", "org", program_id, f"Round {suffix}"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            apply_refactor(db)
    finally:
        db.close()

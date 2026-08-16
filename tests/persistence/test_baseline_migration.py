import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts.validate_baseline_migration import validate, validate_chain
from tests.schema import BASELINE
from tests.speaker_operations.test_speaker_onboarding_schema import (
    add_speaker,
    link_submission_speaker,
    seed_platform,
)


def apply_baseline(path: Path = BASELINE) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(path.read_text(encoding="utf-8"))
    return connection


def test_canonical_baseline_remains_immutable_and_first() -> None:
    assert sorted(BASELINE.parent.glob("*.sql"))[0] == BASELINE
    validate(BASELINE)


def test_validator_accepts_an_additive_migration_ledger(tmp_path: Path) -> None:
    baseline = tmp_path / BASELINE.name
    baseline.write_bytes(BASELINE.read_bytes())
    incremental = tmp_path / "0002_unwanted.sql"
    incremental.write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "checksums.sha256").write_text(
        f"{hashlib.sha256(baseline.read_bytes()).hexdigest()}  {baseline.name}\n"
        f"{hashlib.sha256(incremental.read_bytes()).hexdigest()}  {incremental.name}\n",
        encoding="utf-8",
    )

    validate_chain(tmp_path)


def test_validator_rejects_a_changed_released_migration(tmp_path: Path) -> None:
    for source in BASELINE.parent.iterdir():
        if source.is_file() and (source.suffix == ".sql" or source.name == "checksums.sha256"):
            (tmp_path / source.name).write_bytes(source.read_bytes())
    with (tmp_path / BASELINE.name).open("ab") as changed:
        changed.write(b"\n-- unauthorized rewrite\n")

    with pytest.raises(ValueError, match="released migration changed: 0001_baseline.sql"):
        validate_chain(tmp_path)


def test_complete_migration_chain_builds_the_current_schema() -> None:
    validate_chain(BASELINE.parent)
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for migration in sorted(BASELINE.parent.glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8"))
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        object_counts = dict(
            connection.execute(
                """SELECT type,COUNT(*) FROM sqlite_master
                   WHERE name NOT LIKE 'sqlite_%' GROUP BY type"""
            ).fetchall()
        )
        assert object_counts == {"index": 116, "table": 81, "trigger": 106}
        assert connection.execute(
            "SELECT lifecycle_status,withdrawn_at_ms FROM accepted_sessions LIMIT 0"
        ).description is not None
        assert connection.execute(
            "SELECT visibility FROM speaker_asset_comments LIMIT 0"
        ).description is not None
        assert connection.execute(
            "SELECT content_fingerprint FROM speaker_tasks LIMIT 0"
        ).description is not None
        assert connection.execute(
            "SELECT name_key FROM evaluation_rounds LIMIT 0"
        ).description is not None
        assert connection.execute(
            "SELECT subject_source FROM communication_messages LIMIT 0"
        ).description is not None
        assert connection.execute(
            """SELECT sql FROM sqlite_master
               WHERE type='index' AND name='uq_evaluation_rounds_live_name'"""
        ).fetchone() is not None
    finally:
        connection.close()


def test_incremental_chain_preserves_and_explicitly_backfills_existing_rows() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        add_speaker(connection, "a")
        link_submission_speaker(connection, "a")
        connection.execute(
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,submission_id,decision,internal_reason,
                decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES('decision-a','org-a','event-a','submission-a','accepted','Selected',
                      'user-a',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
               VALUES('session-a','org-a','event-a','submission-a','decision-a',1000)"""
        )
        connection.execute(
            """INSERT INTO accepted_session_participants
               (id,organization_id,event_id,accepted_session_id,event_speaker_id,
                display_name_snapshot,created_at_ms,updated_at_ms)
               VALUES('participant-a','org-a','event-a','session-a','speaker-a',
                      'Existing speaker',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,submission_id,task_type,
                title,help_text,destination_type,state,created_at_ms,updated_at_ms)
               VALUES('task-a','org-a','event-a','speaker-a','submission-a','headshot',
                      'Upload your headshot','Add a program-ready profile photo.',
                      'headshot','open',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES('legacy-decision-message','org-a','event-a','speaker@example.test',
                      'Existing decision subject','<p>Existing body</p>',
                      'submission-decision:legacy:v1','delivered',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES('legacy-manual-message','org-a','event-a','speaker@example.test',
                      'Existing manual subject','<p>Existing body</p>',
                      'manual:legacy:v1','delivered',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,submission_id,task_type,
                title,help_text,destination_type,state,created_at_ms,updated_at_ms)
               VALUES('task-duplicate','org-a','event-a','speaker-a','submission-a','headshot',
                      'Upload your headshot','Add a program-ready profile photo.',
                      'headshot','open',1001,1001)"""
        )
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,submission_id,task_type,
                title,destination_type,state,created_at_ms,updated_at_ms)
               VALUES('task-manual','org-a','event-a','speaker-a','submission-a','headshot',
                      'Conference guide portrait','headshot','open',1002,1002)"""
        )

        for migration in sorted(BASELINE.parent.glob("*.sql"))[1:]:
            connection.executescript(migration.read_text(encoding="utf-8"))

        assert connection.execute(
            """SELECT decision_id,decision_correction_id,lifecycle_status,withdrawn_at_ms
               FROM accepted_sessions WHERE id='session-a'"""
        ).fetchone() == ("decision-a", None, "active", None)
        assert connection.execute(
            "SELECT content_fingerprint FROM speaker_tasks WHERE id='task-a'"
        ).fetchone() == (None,)
        assert connection.execute(
            "SELECT state,waived_at_ms FROM speaker_tasks WHERE id='task-duplicate'"
        ).fetchone() == ("waived", 1001)
        manual_fingerprint = connection.execute(
            "SELECT content_fingerprint FROM speaker_tasks WHERE id='task-manual'"
        ).fetchone()[0]
        assert manual_fingerprint == b"legacy:task-manual"
        assert connection.execute(
            "SELECT accepted_session_id FROM accepted_session_participants WHERE id='participant-a'"
        ).fetchone() == ("session-a",)
        assert connection.execute("SELECT COUNT(*) FROM speaker_asset_comments").fetchone() == (0,)
        assert connection.execute(
            """SELECT id,subject,subject_source FROM communication_messages
               ORDER BY id"""
        ).fetchall() == [
            ("legacy-decision-message", "Existing decision subject", None),
            ("legacy-manual-message", "Existing manual subject", None),
        ]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def _seed_round(connection, round_id, event_id, name, status, created_at_ms) -> None:
    connection.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,name,rubric_json,status,
            created_at_ms,updated_at_ms,closed_at_ms)
           VALUES(?,?,?,?,'{}',?,?,?,?)""",
        (
            round_id,
            f"org-{event_id[-1]}",
            event_id,
            name,
            status,
            created_at_ms,
            created_at_ms,
            created_at_ms if status == "closed" else None,
        ),
    )


def test_round_name_key_backfills_and_deduplicates_only_live_rounds() -> None:
    """Upgrade from the preceding schema, with the duplicates the constraint has to survive.

    A database that ran the buggy code already contains the rows the new unique index would
    refuse, so the migration has to resolve them before it can create the index -- and it
    has to resolve them the same way every time it runs, on every replica.
    """
    connection = apply_baseline()
    try:
        seed_platform(connection)
        _seed_round(connection, "r-keep", "event-a", "Initial review", "draft", 1000)
        _seed_round(connection, "r-dupe", "event-a", "Initial Review", "draft", 2000)
        _seed_round(connection, "r-dupe-spaced", "event-a", "INITIAL  review", "draft", 3000)
        _seed_round(connection, "r-closed", "event-a", "Initial review", "closed", 500)
        _seed_round(connection, "r-tie-b", "event-a", "Tie", "draft", 4000)
        _seed_round(connection, "r-tie-a", "event-a", "tie", "draft", 4000)
        _seed_round(connection, "r-other-event", "event-b", "Initial review", "draft", 1000)
        # A rank suffix would rename r-rank-b onto the name r-rank-c already holds. " (2)"
        # is a name organizers type, so the collision the repair creates is not exotic.
        _seed_round(connection, "r-rank-a", "event-a", "Round", "draft", 6000)
        _seed_round(connection, "r-rank-b", "event-a", "round", "draft", 6001)
        _seed_round(connection, "r-rank-c", "event-a", "Round (2)", "draft", 6002)
        # And the same collision reached the other way: truncating to fit the name CHECK
        # can push a renamed row exactly onto an existing name.
        _seed_round(connection, "r-long-a", "event-a", "A" * 200, "draft", 7000)
        _seed_round(connection, "r-long-b", "event-a", "a" * 200, "draft", 7001)
        _seed_round(connection, "r-long-c", "event-a", "A" * 190 + " (2)", "draft", 7002)

        for migration in sorted(BASELINE.parent.glob("*.sql"))[1:]:
            connection.executescript(migration.read_text(encoding="utf-8"))

        stored = dict(
            (row[0], (row[1], row[2]))
            for row in connection.execute("SELECT id,name,name_key FROM evaluation_rounds")
        )
        # The oldest live round keeps the name the organizer gave it.
        assert stored["r-keep"] == ("Initial review", "initial review")
        # Later collisions are renamed, not deleted: a round owns assignments and decisions.
        # The suffix is the row's own id, which cannot collide with another row's.
        assert stored["r-dupe"] == ("Initial Review (r-dupe)", "initial review (r-dupe)")
        assert stored["r-dupe-spaced"] == (
            "INITIAL  review (r-dupe-spaced)",
            "initial review (r-dupe-spaced)",
        )
        # An exact created_at_ms tie still has to resolve, so id breaks it.
        assert stored["r-tie-a"] == ("tie", "tie")
        assert stored["r-tie-b"] == ("Tie (r-tie-b)", "tie (r-tie-b)")
        # The repair does not walk into the name it was avoiding.
        assert stored["r-rank-a"] == ("Round", "round")
        assert stored["r-rank-b"] == ("round (r-rank-b)", "round (r-rank-b)")
        assert stored["r-rank-c"] == ("Round (2)", "round (2)")
        assert stored["r-long-a"] == ("A" * 200, "a" * 200)
        assert stored["r-long-b"] == ("a" * 160 + " (r-long-b)", "a" * 160 + " (r-long-b)")
        assert stored["r-long-c"] == ("A" * 190 + " (2)", "a" * 190 + " (2)")
        for name, _key in stored.values():
            assert 1 <= len(name) <= 200, name
        # The point of the whole step: nothing is left for the index to refuse.
        assert connection.execute(
            """SELECT COUNT(*) FROM (
                 SELECT 1 FROM evaluation_rounds WHERE status IN ('draft','open')
                  GROUP BY organization_id,event_id,name_key HAVING COUNT(*) > 1)"""
        ).fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        # And the constraint is now doing the work the application guard used to do alone.
        # This is the concurrent-insert case: two writers that both read "name is free".
        def insert_live(round_id, name_key, status="draft"):
            connection.execute(
                """INSERT INTO evaluation_rounds
                   (id,organization_id,event_id,name,name_key,rubric_json,status,
                    created_at_ms,updated_at_ms,closed_at_ms)
                   VALUES(?,'org-a','event-a','Initial review',?,'{}',?,5000,5000,?)""",
                (round_id, name_key, status, 5000 if status == "closed" else None),
            )

        with pytest.raises(sqlite3.IntegrityError):
            insert_live("r-racing", "initial review")
        insert_live("r-free", "initial review 2026")
        insert_live("r-archived", "initial review", status="closed")
    finally:
        connection.close()


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


def test_immutable_baseline_retains_its_released_schema() -> None:
    connection = apply_baseline()
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
        }
        assert len(tables) == 79
        assert {
            "owned_resources",
            "resource_access_grants",
            "resource_ownership_transfers",
            "event_labels",
            "accepted_session_labels",
            "accepted_session_participants",
            "evaluation_round_submissions",
            "evaluation_round_evaluators",
        } <= tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()

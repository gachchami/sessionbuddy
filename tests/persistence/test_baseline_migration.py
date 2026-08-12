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
        assert object_counts == {"index": 115, "table": 81, "trigger": 106}
        assert connection.execute(
            "SELECT lifecycle_status,withdrawn_at_ms FROM accepted_sessions LIMIT 0"
        ).description is not None
        assert connection.execute(
            "SELECT visibility FROM speaker_asset_comments LIMIT 0"
        ).description is not None
        assert connection.execute(
            "SELECT content_fingerprint FROM speaker_tasks LIMIT 0"
        ).description is not None
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
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
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

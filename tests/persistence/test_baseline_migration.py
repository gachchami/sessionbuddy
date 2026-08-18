import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from scripts.validate_baseline_migration import validate, validate_chain
from sessionbuddy.platform.upload_contracts import task_form_schema_json
from tests.schema import BASELINE
from tests.speaker_operations.test_speaker_onboarding_schema import add_speaker, seed_platform


def apply_baseline(path: Path = BASELINE) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(path.read_text(encoding="utf-8"))
    return connection


def execute_migration_in_transaction(
    connection: sqlite3.Connection, migration: Path
) -> None:
    """Exercise D1's migration shape without executescript's implicit commit."""
    statements: list[str] = []
    buffered = ""
    for line in migration.read_text(encoding="utf-8").splitlines(keepends=True):
        buffered += line
        if sqlite3.complete_statement(buffered):
            statements.append(buffered)
            buffered = ""
    assert not buffered.strip()
    connection.execute("BEGIN")
    try:
        for statement in statements:
            connection.execute(statement)
    except BaseException:
        connection.rollback()
        raise
    connection.commit()


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
        assert object_counts == {"index": 118, "table": 83, "trigger": 117}
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
        assert connection.execute(
            "SELECT version FROM evaluation_rounds LIMIT 0"
        ).description is not None
        assert connection.execute(
            "SELECT id,round_id,applied_changes,created_at_ms "
            "FROM evaluation_round_write_guards LIMIT 0"
        ).description is not None
        assert connection.execute(
            "SELECT declined_at_ms FROM identity_invitations LIMIT 0"
        ).description is not None
        assert connection.execute(
            """SELECT sql FROM sqlite_master WHERE type='index'
               AND name='idx_accepted_session_participants_speaker'"""
        ).fetchone() is not None
    finally:
        connection.close()


def test_invitation_decline_migration_preserves_referenced_pending_rows() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        for migration_name in (
            "0002_speaker_task_upload_contract.sql",
            "0003_remove_speaker_task_destination_type.sql",
        ):
            connection.executescript(
                (BASELINE.parent / migration_name).read_text(encoding="utf-8")
            )
        connection.execute(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms)
               VALUES('invite-a','org-a','event-a','guest@example.test',
                      'guest@example.test','speaker','pending','user-a',5000,1000,1000)"""
        )
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,pending_invitation_id,
                task_type,title,state,form_schema_json,created_at_ms,updated_at_ms)
               VALUES('pending-task','org-a','event-a',NULL,'invite-a','profile',
                      'Complete profile','open','{}',1000,1000)"""
        )
        connection.commit()

        execute_migration_in_transaction(
            connection,
            BASELINE.parent / "0004_identity_invitation_decline.sql",
        )

        assert connection.execute(
            "SELECT id,status,declined_at_ms FROM identity_invitations"
        ).fetchall() == [("invite-a", "pending", None)]
        assert connection.execute(
            "SELECT pending_invitation_id FROM speaker_tasks WHERE id='pending-task'"
        ).fetchone() == ("invite-a",)
        connection.execute(
            """UPDATE identity_invitations
               SET status='revoked',declined_at_ms=2000,revoked_at_ms=2000,
                   updated_at_ms=2000
               WHERE id='invite-a'"""
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_speaker_session_lookup_migration_upgrades_0004_without_data_loss() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        connection.commit()
        for migration_name in (
            "0002_speaker_task_upload_contract.sql",
            "0003_remove_speaker_task_destination_type.sql",
            "0004_identity_invitation_decline.sql",
        ):
            execute_migration_in_transaction(connection, BASELINE.parent / migration_name)
        connection.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,source_type,organizer_title,
                organizer_abstract,created_at_ms)
               VALUES('session-a','org-a','event-a','organizer_created',
                      'Direct session','Stable abstract',1000)"""
        )
        add_speaker(connection, "a")
        connection.execute(
            """INSERT INTO accepted_session_participants
               (id,organization_id,event_id,accepted_session_id,event_speaker_id,
                display_name_snapshot,created_at_ms,updated_at_ms)
               VALUES('participant-a','org-a','event-a','session-a','speaker-a',
                      'Speaker A',1000,1000)"""
        )
        connection.commit()

        execute_migration_in_transaction(
            connection,
            BASELINE.parent / "0005_speaker_session_participant_lookup.sql",
        )

        assert connection.execute(
            "SELECT accepted_session_id FROM accepted_session_participants"
        ).fetchall() == [("session-a",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        plan = " ".join(
            str(row[3])
            for row in connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT accepted_session_id FROM accepted_session_participants
                   WHERE organization_id='org-a' AND event_id='event-a'
                     AND event_speaker_id='speaker-a'"""
            )
        )
        assert "idx_accepted_session_participants_speaker" in plan
    finally:
        connection.close()


def test_upload_contract_migration_repairs_existing_file_tasks() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        add_speaker(connection, "a")
        for task_type, schema in (
            ("headshot", "{}"),
            ("slides", None),
            (
                "supporting_document",
                '{"upload":{"enabled":false,"allowed_content_types":[],"max_file_bytes":null}}',
            ),
        ):
            connection.execute(
                """INSERT INTO speaker_tasks
                   (id,organization_id,event_id,event_speaker_id,task_type,title,
                    destination_type,state,version,created_at_ms,updated_at_ms,form_schema_json)
                   VALUES(?, 'org-a','event-a','speaker-a',?,'Upload',?,'open',1,1000,1000,?)""",
                (f"legacy-{task_type}", task_type, task_type, schema),
            )

        before_repair_ms = int(time.time()) * 1000
        migration = BASELINE.parent / "0002_speaker_task_upload_contract.sql"
        connection.executescript(migration.read_text(encoding="utf-8"))

        repaired = connection.execute(
            """SELECT task_type,form_schema_json,version,updated_at_ms
               FROM speaker_tasks ORDER BY task_type"""
        ).fetchall()
        assert [row[:3] for row in repaired] == [
            (task_type, task_form_schema_json(task_type), 2)
            for task_type in sorted(("headshot", "slides", "supporting_document"))
        ]
        assert all(row[3] >= before_repair_ms for row in repaired)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_upload_contract_migration_guard_is_restartable_after_precondition_failure() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        add_speaker(connection, "a")
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,task_type,title,
                destination_type,state,version,created_at_ms,updated_at_ms,form_schema_json)
               VALUES('legacy-mismatch','org-a','event-a','speaker-a','headshot','Upload',
                      'slides','open',1,1000,1000,'{}')"""
        )
        migration = BASELINE.parent / "0002_speaker_task_upload_contract.sql"

        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(migration.read_text(encoding="utf-8"))

        connection.execute(
            "UPDATE speaker_tasks SET destination_type='headshot' WHERE id='legacy-mismatch'"
        )
        connection.executescript(migration.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT version FROM speaker_tasks WHERE id='legacy-mismatch'"
        ).fetchone() == (2,)
    finally:
        connection.close()


def test_upload_contract_migration_rejects_an_incomplete_backfill() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        add_speaker(connection, "a")
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,task_type,title,
                destination_type,state,version,created_at_ms,updated_at_ms,form_schema_json)
               VALUES('legacy-headshot','org-a','event-a','speaker-a','headshot','Upload',
                      'headshot','open',1,1000,1000,'{}')"""
        )
        migration = (
            BASELINE.parent / "0002_speaker_task_upload_contract.sql"
        ).read_text(encoding="utf-8")
        broken_backfill = migration.replace(
            "WHEN 'headshot' THEN json_object(",
            "WHEN 'not_headshot' THEN json_object(",
            1,
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(broken_backfill)
        assert connection.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type='trigger' AND name LIKE 'speaker_task_upload_contract_%'"""
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_destination_type_removal_preserves_tasks_and_upload_enforcement() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        add_speaker(connection, "a")
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,task_type,title,
                destination_type,state,version,created_at_ms,updated_at_ms,form_schema_json)
               VALUES('existing-headshot','org-a','event-a','speaker-a','headshot','Upload',
                      'headshot','open',3,1000,2000,?)""",
            (task_form_schema_json("headshot"),),
        )
        for migration_name in (
            "0002_speaker_task_upload_contract.sql",
            "0003_remove_speaker_task_destination_type.sql",
        ):
            connection.executescript(
                (BASELINE.parent / migration_name).read_text(encoding="utf-8")
            )

        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(speaker_tasks)")
        }
        assert "destination_type" not in columns
        assert connection.execute(
            """SELECT task_type,state,version,created_at_ms,updated_at_ms,form_schema_json
               FROM speaker_tasks WHERE id='existing-headshot'"""
        ).fetchone() == (
            "headshot",
            "open",
            3,
            1000,
            2000,
            task_form_schema_json("headshot"),
        )
        with pytest.raises(
            sqlite3.IntegrityError, match="speaker task upload contract invalid"
        ):
            connection.execute(
                """INSERT INTO speaker_tasks
                   (id,organization_id,event_id,event_speaker_id,task_type,title,state,
                    created_at_ms,updated_at_ms,form_schema_json)
                   VALUES('invalid-headshot','org-a','event-a','speaker-a','headshot','Upload',
                          'open',3000,3000,'{}')"""
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_database_rejects_speaker_tasks_that_bypass_the_upload_contract() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for migration in sorted(BASELINE.parent.glob("*.sql")):
            connection.executescript(migration.read_text(encoding="utf-8"))
        seed_platform(connection)

        with pytest.raises(
            sqlite3.IntegrityError, match="speaker task upload contract invalid"
        ):
            connection.execute(
                """INSERT INTO speaker_tasks
                   (id,organization_id,event_id,task_type,title,state,
                    created_at_ms,updated_at_ms,form_schema_json)
                   VALUES('bad-upload','org-a','event-a','headshot','Upload','open',
                          1000,1000,'{}')"""
            )
        with pytest.raises(
            sqlite3.IntegrityError, match="speaker task upload contract invalid"
        ):
            connection.execute(
                """INSERT INTO speaker_tasks
                   (id,organization_id,event_id,task_type,title,state,
                    created_at_ms,updated_at_ms,form_schema_json)
                   VALUES('profile-upload','org-a','event-a','profile','Profile','open',
                          1000,1000,?)""",
                (task_form_schema_json("headshot"),),
            )
    finally:
        connection.close()


def test_canonical_baseline_expresses_organization_admin_authority_natively() -> None:
    connection = apply_baseline()
    try:
        events_columns = {
            str(row[1]): (str(row[2]), int(row[3]))
            for row in connection.execute("PRAGMA table_info(events)")
        }
        assert events_columns["created_by_user_id"] == ("TEXT", 1)

        schema = {
            str(row[0]): str(row[1])
            for row in connection.execute(
                """SELECT name,sql FROM sqlite_master
                   WHERE type='table' AND name IN (
                     'event_memberships','identity_invitations',
                     'owned_resources','resource_access_grants'
                   )"""
            )
        }
        assert "role = 'speaker'" in schema["event_memberships"]
        assert "event_admin" not in schema["event_memberships"]
        assert "event_admin" not in schema["identity_invitations"]
        assert "'event'" not in schema["owned_resources"]
        assert "permission = 'manage'" in schema["resource_access_grants"]
        assert "'view'" not in schema["resource_access_grants"]
        assert "'edit'" not in schema["resource_access_grants"]

        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
        assert "event_creator_is_immutable" in triggers
        assert "enforce_organization_manage_grant_insert" in triggers
        assert "enforce_organization_manage_grant_update" in triggers
        assert not any(name.startswith("prevent_retired_") for name in triggers)
        assert not any(name.startswith("prevent_event_owned_resource_") for name in triggers)
        assert "require_event_creator_insert" not in triggers
    finally:
        connection.close()


def test_canonical_authority_constraints_reject_retired_values() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO event_memberships
                   (id,organization_id,event_id,user_id,role,status,version,
                    created_at_ms,updated_at_ms)
                   VALUES('retired-membership','org-a','event-a','user-a',
                          'event_admin','active',1,1000,1000)"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO owned_resources
                   (id,resource_type,created_by_user_id,owner_user_id,status,version,
                    created_at_ms,updated_at_ms)
                   VALUES('retired-event-owner','event','user-a','user-a',
                          'active',1,1000,1000)"""
            )
        for permission in ("view", "edit"):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    """INSERT INTO resource_access_grants
                       (id,resource_id,user_id,permission,status,granted_by_user_id,
                        version,created_at_ms,updated_at_ms)
                       VALUES(?, 'org-a','user-a',?,'active','user-a',1,1000,1000)""",
                    (f"retired-{permission}", permission),
                )
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


def test_canonical_baseline_retains_the_current_schema() -> None:
    connection = apply_baseline()
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name NOT LIKE 'sqlite_%'"""
            ).fetchall()
        }
        assert len(tables) == 82
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

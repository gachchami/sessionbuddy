import hashlib
import sqlite3
from pathlib import Path

import pytest

from scripts.validate_baseline_migration import validate, validate_chain
from tests.schema import BASELINE
from tests.speaker_operations.test_speaker_onboarding_schema import seed_platform


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
        assert object_counts == {"index": 117, "table": 82, "trigger": 113}
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

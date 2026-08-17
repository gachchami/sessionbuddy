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
        assert object_counts == {"index": 117, "table": 82, "trigger": 120}
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


def test_round_version_migration_upgrades_existing_rounds_without_losing_assignments() -> None:
    """Upgrade the immediately preceding schema with live round data in place."""
    migrations = sorted(BASELINE.parent.glob("*.sql"))
    round_migration = next(
        item for item in migrations if item.name == "0008_evaluation_round_version_contract.sql"
    )
    round_index = migrations.index(round_migration)
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for migration in migrations[:round_index]:
            connection.executescript(migration.read_text(encoding="utf-8"))
        seed_platform(connection)
        connection.execute(
            "INSERT INTO user_roles VALUES ('user-a','reviewer','active',1000,1000,NULL,1)"
        )
        connection.execute(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,accepted_at_ms,created_at_ms,updated_at_ms)
               VALUES('legacy-reviewer-invitation','org-a','event-a',
                      'speaker-a@example.test','speaker-a@example.test','evaluator','accepted',
                      'user-a',9999999999999,1000,1000,1000)"""
        )
        for index, status in enumerate(("draft", "open", "closed"), start=1):
            round_id = f"legacy-round-{status}"
            connection.execute(
                """INSERT INTO evaluation_rounds
                   (id,organization_id,event_id,name,name_key,rubric_json,status,
                    created_at_ms,updated_at_ms,closed_at_ms)
                   VALUES(?,?,?,?,?,'{}',?,?,?,?)""",
                (
                    round_id,
                    "org-a",
                    "event-a",
                    f"Legacy {status}",
                    f"legacy {status}",
                    status,
                    1_000 + index,
                    1_000 + index,
                    1_000 + index if status == "closed" else None,
                ),
            )
        connection.execute(
            """INSERT INTO evaluation_round_submissions
               (round_id,submission_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
               VALUES('legacy-round-draft','submission-a','org-a','event-a','active',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO evaluation_round_evaluators
               (round_id,evaluator_user_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
               VALUES('legacy-round-draft','user-a','org-a','event-a','active',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                status,created_at_ms,updated_at_ms)
               VALUES('legacy-assignment','org-a','event-a','legacy-round-draft',
                      'submission-a','user-a','assigned',1000,1000)"""
        )
        connection.commit()

        connection.executescript(round_migration.read_text(encoding="utf-8"))

        assert connection.execute(
            "SELECT id,version FROM evaluation_rounds WHERE id LIKE 'legacy-round-%' ORDER BY id"
        ).fetchall() == [
            ("legacy-round-closed", 1),
            ("legacy-round-draft", 1),
            ("legacy-round-open", 1),
        ]
        assert tuple(
            connection.execute(
                "SELECT round_id,submission_id,evaluator_user_id,status "
                "FROM evaluation_assignments WHERE id='legacy-assignment'"
            ).fetchone()
        ) == ("legacy-round-draft", "submission-a", "user-a", "assigned")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_event_administration_retirement_revokes_legacy_authority() -> None:
    migrations = sorted(BASELINE.parent.glob("*.sql"))
    retirement = migrations[-1]
    assert retirement.name == "0009_retire_event_administration.sql"
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for migration in migrations[:-1]:
            connection.executescript(migration.read_text(encoding="utf-8"))
        seed_platform(connection)
        connection.execute(
            "INSERT INTO user_roles VALUES ('user-a','organizer','active',1000,1000,NULL,1)"
        )
        connection.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,version,
                created_at_ms,updated_at_ms)
               VALUES('org-a','organization','user-b','user-b','active',1,1000,1000),
                     ('org-b','organization','user-a','user-a','active',1,1000,1000)"""
        )
        connection.execute(
            """INSERT INTO resource_ownership_transfers
               (id,resource_id,from_user_id,to_user_id,transferred_by_user_id,
                reason,transferred_at_ms)
               VALUES('legacy-event-transfer','event-a','user-a','user-b','user-a',
                      'Historical handoff',1000)"""
        )
        connection.execute(
            """INSERT INTO audit_events
               (id,organization_id,event_id,actor_user_id,actor_type,action,target_type,
                target_id,result,correlation_id,metadata_json,occurred_at_ms)
               VALUES('legacy-event-transfer-audit','org-a','event-a','user-a','user',
                      'resource_ownership.transfer','event','event-a','succeeded','request-a',
                      ?,1000)""",
            (
                '{"previous_owner_user_id":"user-a","new_owner_user_id":"user-b",'
                '"grant_previous_owner_manage":false}',
            ),
        )
        connection.execute(
            "UPDATE owned_resources SET owner_user_id='user-b',version=version+1 "
            "WHERE id='event-a'"
        )
        connection.execute(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms)
               VALUES('legacy-admin-invite','org-a','event-a','speaker-a@example.test',
                      'speaker-a@example.test','event_admin','pending','user-a',9999999999999,
                      1000,1000)"""
        )
        connection.execute(
            """INSERT INTO authentication_challenges
               (id,normalized_email,token_hash,purpose,provisioning_context,
                redirect_path,expires_at_ms,created_at_ms,organization_id,event_id,
                invitation_id)
               VALUES('legacy-admin-challenge','speaker-a@example.test',X'01','sign_in',
                      'invitation','/admin',9999999999999,1000,'org-a','event-a',
                      'legacy-admin-invite')"""
        )
        connection.execute(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES('legacy-admin-membership','org-a','event-a','user-a','event_admin',
                      'active',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO resource_access_grants
               (id,resource_id,user_id,permission,status,granted_by_user_id,
                created_at_ms,updated_at_ms)
               VALUES('legacy-event-manage','event-a','user-a','manage','active','user-a',1000,1000),
                     ('legacy-org-edit','org-a','user-a','edit','active','user-a',1000,1000)"""
        )
        before_version = connection.execute(
            "SELECT authorization_version FROM users WHERE id='user-a'"
        ).fetchone()[0]
        unaffected_owner_version = connection.execute(
            "SELECT authorization_version FROM users WHERE id='user-b'"
        ).fetchone()[0]

        connection.executescript(retirement.read_text(encoding="utf-8"))

        assert connection.execute(
            "SELECT status FROM identity_invitations WHERE id='legacy-admin-invite'"
        ).fetchone()[0] == "revoked"
        assert connection.execute(
            "SELECT consumed_at_ms FROM authentication_challenges "
            "WHERE id='legacy-admin-challenge'"
        ).fetchone()[0] > 1000
        assert connection.execute(
            "SELECT status FROM event_memberships WHERE id='legacy-admin-membership'"
        ).fetchone()[0] == "revoked"
        assert connection.execute(
            "SELECT id,status FROM resource_access_grants ORDER BY id"
        ).fetchall() == [
            ("legacy-org-edit", "revoked"),
        ]
        assert connection.execute(
            "SELECT authorization_version FROM users WHERE id='user-a'"
        ).fetchone()[0] == before_version + 1
        assert connection.execute(
            "SELECT authorization_version FROM users WHERE id='user-b'"
        ).fetchone()[0] == unaffected_owner_version
        assert connection.execute(
            "SELECT COUNT(*) FROM owned_resources WHERE resource_type='event'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT id,created_by_user_id FROM events ORDER BY id"
        ).fetchall() == [("event-a", "user-a"), ("event-b", "user-b")]
        assert connection.execute(
            "SELECT COUNT(*) FROM resource_ownership_transfers WHERE resource_id='event-a'"
        ).fetchone() == (0,)
        audit_metadata = connection.execute(
            "SELECT metadata_json FROM audit_events WHERE id='legacy-event-transfer-audit'"
        ).fetchone()[0]
        assert '"reason":"Historical handoff"' in audit_metadata
        with pytest.raises(sqlite3.IntegrityError, match="events are owned by their organization"):
            connection.execute(
                """INSERT INTO owned_resources
                   (id,resource_type,created_by_user_id,owner_user_id,status,version,
                    created_at_ms,updated_at_ms)
                   VALUES('blocked-event-owner','event','user-a','user-a','active',1,1000,1000)"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="event_admin invitations are retired"):
            connection.execute(
                """INSERT INTO identity_invitations
                   (id,organization_id,event_id,normalized_email,email,role,status,
                    invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms)
                   VALUES('blocked','org-a','event-a','blocked@example.test','blocked@example.test',
                          'event_admin','pending','user-a',9999999999999,1000,1000)"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="event_admin invitations are retired"):
            connection.execute(
                "UPDATE identity_invitations SET status='pending' "
                "WHERE id='legacy-admin-invite'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="event_admin memberships are retired"):
            connection.execute(
                "UPDATE event_memberships SET status='active' "
                "WHERE id='legacy-admin-membership'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="only organization manage grants"):
            connection.execute(
                """INSERT INTO resource_access_grants
                   (id,resource_id,user_id,permission,status,granted_by_user_id,
                    created_at_ms,updated_at_ms)
                   VALUES('blocked-grant','event-a','user-a','manage','active','user-a',1000,1000)"""
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_event_authority_retirement_refuses_to_invent_missing_creator_provenance() -> None:
    migrations = sorted(BASELINE.parent.glob("*.sql"))
    retirement = migrations[-1]
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for migration in migrations[:-1]:
            connection.executescript(migration.read_text(encoding="utf-8"))
        seed_platform(connection)
        connection.execute("DELETE FROM owned_resources WHERE resource_type='event'")

        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(retirement.read_text(encoding="utf-8"))

        assert connection.execute(
            "SELECT COUNT(*) FROM owned_resources WHERE resource_type='event'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM pragma_table_info('events') "
            "WHERE name='created_by_user_id'"
        ).fetchone() == (0,)

        connection.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,version,
                created_at_ms,updated_at_ms)
               SELECT event_row.id,'event','user-a','user-a','active',1,1000,1000
               FROM events event_row"""
        )
        connection.executescript(retirement.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT COUNT(*) FROM pragma_table_info('events') "
            "WHERE name='created_by_user_id'"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_event_authority_retirement_refuses_to_delete_unaudited_transfer() -> None:
    migrations = sorted(BASELINE.parent.glob("*.sql"))
    retirement = migrations[-1]
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        for migration in migrations[:-1]:
            connection.executescript(migration.read_text(encoding="utf-8"))
        seed_platform(connection)
        connection.execute(
            """INSERT INTO resource_ownership_transfers
               (id,resource_id,from_user_id,to_user_id,transferred_by_user_id,
                reason,transferred_at_ms)
               VALUES('unaudited-transfer','event-a','user-a','user-b','user-a',
                      'Missing audit event',1000)"""
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.executescript(retirement.read_text(encoding="utf-8"))

        assert connection.execute(
            "SELECT COUNT(*) FROM pragma_table_info('events') "
            "WHERE name='created_by_user_id'"
        ).fetchone() == (0,)
        connection.execute(
            """INSERT INTO audit_events
               (id,organization_id,event_id,actor_user_id,actor_type,action,target_type,
                target_id,result,correlation_id,metadata_json,occurred_at_ms)
               VALUES('transfer-audit','org-a','event-a','user-a','user',
                      'resource_ownership.transfer','event','event-a','succeeded','request-a',
                      ?,1000)""",
            ('{"previous_owner_user_id":"user-a","new_owner_user_id":"user-b"}',),
        )
        connection.executescript(retirement.read_text(encoding="utf-8"))
        assert connection.execute(
            "SELECT COUNT(*) FROM resource_ownership_transfers "
            "WHERE id='unaudited-transfer'"
        ).fetchone() == (0,)
    finally:
        connection.close()


def test_incremental_chain_preserves_and_explicitly_backfills_existing_rows() -> None:
    connection = apply_baseline()
    try:
        seed_platform(connection)
        connection.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms)
               VALUES('legacy-draft-event','org-a','Existing draft',2000,3000,'UTC',
                      'Existing venue','hybrid','Existing description','draft',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms)
               VALUES('legacy-epoch-active','org-a','Epoch active',0,1,'UTC',
                      'Existing venue','hybrid','Existing description','active',1000,1000)"""
        )
        connection.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms,archived_at_ms)
               VALUES('legacy-epoch-archived','org-a','Epoch archived',0,1,'UTC',
                      'Existing venue','hybrid','Existing description','archived',1000,1000,1000)"""
        )
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
        connection.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,version,
                created_at_ms,updated_at_ms)
               SELECT id,'event',
                      CASE organization_id WHEN 'org-a' THEN 'user-a' ELSE 'user-b' END,
                      CASE organization_id WHEN 'org-a' THEN 'user-a' ELSE 'user-b' END,
                      'active',1,created_at_ms,updated_at_ms
               FROM events event_row
               WHERE NOT EXISTS (
                 SELECT 1 FROM owned_resources owned WHERE owned.id=event_row.id
               )"""
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
        assert connection.execute(
            """SELECT draft_starts_at_ms,draft_ends_at_ms,draft_delivery_mode
               FROM events WHERE id='legacy-draft-event'"""
        ).fetchone() == (2000, 3000, "hybrid")
        # A complete draft must mirror the released projection; this update is
        # rejected before its invalid time range could become observable.
        with pytest.raises(sqlite3.IntegrityError, match="draft storage projection mismatch"):
            connection.execute(
                """UPDATE events SET starts_at_ms=0,ends_at_ms=1,delivery_mode='in_person',
                          draft_starts_at_ms=2000,draft_ends_at_ms=1000
                   WHERE id='legacy-draft-event'"""
            )
        connection.execute(
            """UPDATE events SET description='Updated active description'
               WHERE id='legacy-epoch-active'"""
        )
        connection.execute(
            """UPDATE events SET description='Updated archived description'
               WHERE id='legacy-epoch-archived'"""
        )
        assert connection.execute(
            """SELECT id,starts_at_ms,description FROM events
               WHERE id LIKE 'legacy-epoch-%' ORDER BY id"""
        ).fetchall() == [
            ("legacy-epoch-active", 0, "Updated active description"),
            ("legacy-epoch-archived", 0, "Updated archived description"),
        ]
        with pytest.raises(sqlite3.IntegrityError, match="event details are incomplete"):
            connection.execute(
                """UPDATE events SET status='active',archived_at_ms=NULL
                   WHERE id='legacy-epoch-archived'"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="event draft end must be after start"):
            connection.execute(
                """UPDATE events SET starts_at_ms=0,ends_at_ms=1,delivery_mode='in_person',
                          draft_starts_at_ms=-1,draft_ends_at_ms=NULL,
                          draft_delivery_mode=NULL
                   WHERE id='legacy-draft-event'"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="draft storage projection mismatch"):
            connection.execute(
                """UPDATE events SET draft_ends_at_ms=NULL
                   WHERE id='legacy-draft-event'"""
            )
        with pytest.raises(sqlite3.IntegrityError, match="draft storage projection mismatch"):
            connection.execute(
                """UPDATE events SET location=''
                   WHERE id='legacy-draft-event'"""
            )
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

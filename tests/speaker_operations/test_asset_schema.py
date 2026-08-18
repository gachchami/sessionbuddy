import sqlite3

import pytest

from sessionbuddy.platform.upload_contracts import task_form_schema_json
from tests.speaker_operations.test_speaker_onboarding_schema import (
    MIGRATIONS,
    add_speaker,
    link_submission_speaker,
    seed_platform,
)


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    seed_platform(connection)
    add_speaker(connection, "a")
    add_speaker(connection, "b")
    link_submission_speaker(connection, "a")
    connection.execute(
        """INSERT INTO speaker_tasks
           (id, organization_id, event_id, event_speaker_id, submission_id,
            task_type, title, state, created_at_ms, updated_at_ms,
            form_schema_json)
           VALUES ('task-a','org-a','event-a','speaker-a','submission-a',
                   'headshot','Upload headshot','open',1000,1000,?)""",
        (task_form_schema_json("headshot"),),
    )
    return connection


def add_asset(db: sqlite3.Connection, asset_id: str = "asset-a") -> None:
    db.execute(
        """INSERT INTO speaker_assets
           (id, organization_id, event_id, event_speaker_id, submission_id, task_id,
            kind, created_at_ms, updated_at_ms)
           VALUES (?, 'org-a', 'event-a', 'speaker-a', 'submission-a', 'task-a',
                   'headshot', 1000, 1000)""",
        (asset_id,),
    )


def add_version(
    db: sqlite3.Connection,
    version_id: str,
    generation: int,
    *,
    state: str = "pending_upload",
    current: int = 0,
) -> None:
    complete = state != "pending_upload"
    scanning = state in {"scanning", "clean", "rejected", "superseded"}
    scanned = state in {"clean", "rejected", "superseded"}
    db.execute(
        """INSERT INTO speaker_asset_versions
           (id, organization_id, event_id, event_speaker_id, asset_id, generation, object_key,
            original_filename, content_type, byte_size, checksum_sha256, scan_state,
            is_current, created_at_ms, uploaded_at_ms, scan_started_at_ms, scanned_at_ms)
           VALUES (?, 'org-a', 'event-a', 'speaker-a', 'asset-a', ?, ?, 'headshot.png', ?, ?, ?,
                   ?, ?, 1000, ?, ?, ?)""",
        (
            version_id,
            generation,
            f"private/org-a/event-a/{version_id}",
            "image/png" if complete else None,
            1234 if complete else None,
            bytes(32) if complete else None,
            state,
            current,
            1100 if complete else None,
            1200 if scanning else None,
            1300 if scanned else None,
        ),
    )


def query_plan(db: sqlite3.Connection, sql: str, values: tuple[object, ...]) -> str:
    return " ".join(
        str(value)
        for row in db.execute(f"EXPLAIN QUERY PLAN {sql}", values)
        for value in row
    )


def test_logical_slot_and_tenant_parentage_are_enforced(db: sqlite3.Connection) -> None:
    add_asset(db)
    with pytest.raises(sqlite3.IntegrityError):
        add_asset(db, "duplicate-slot")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO speaker_assets
               (id, organization_id, event_id, event_speaker_id, kind,
                created_at_ms, updated_at_ms)
               VALUES ('cross','org-a','event-a','speaker-b','slides',1000,1000)"""
        )


def test_generation_and_current_clean_constraints(db: sqlite3.Connection) -> None:
    add_asset(db)
    add_version(db, "version-1", 1, state="clean", current=1)
    with pytest.raises(sqlite3.IntegrityError):
        add_version(db, "duplicate-generation", 1, state="clean")
    with pytest.raises(sqlite3.IntegrityError):
        add_version(db, "second-current", 2, state="clean", current=1)
    with pytest.raises(sqlite3.IntegrityError):
        add_version(db, "unscanned-current", 3, state="pending_upload", current=1)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE speaker_asset_versions SET generation=4 WHERE id='version-1'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE speaker_asset_versions SET version_comment='' WHERE id='version-1'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "UPDATE speaker_asset_versions SET version_comment='Changed' "
            "WHERE id='version-1'"
        )


def test_asset_versions_record_the_uploader_for_inventory_metadata(
    db: sqlite3.Connection,
) -> None:
    columns = {
        row[1]: row
        for row in db.execute("PRAGMA table_info(speaker_asset_versions)").fetchall()
    }
    assert "uploaded_by_user_id" in columns
    assert columns["version_comment"][3] == 0
    assert columns["version_comment"][4] is None
    headshot_columns = {
        row[1] for row in db.execute("PRAGMA table_info(user_headshots)").fetchall()
    }
    assert "speaker_asset_version_id" in headshot_columns


def test_asset_comments_are_version_scoped_and_immutable(db: sqlite3.Connection) -> None:
    add_asset(db)
    add_version(db, "version-1", 1, state="clean", current=1)
    db.execute(
        """INSERT INTO speaker_asset_comments
           (id,organization_id,event_id,asset_id,version_id,author_user_id,
            body_text,created_at_ms)
           VALUES ('comment-a','org-a','event-a','asset-a','version-1','user-a',
                   'Please replace the crop.',1400)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO speaker_asset_comments
               (id,organization_id,event_id,asset_id,version_id,author_user_id,
                body_text,created_at_ms)
               VALUES ('comment-empty','org-a','event-a','asset-a','version-1','user-a',
                       '   ',1500)"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "UPDATE speaker_asset_comments SET body_text='Changed' WHERE id='comment-a'"
        )


def test_scan_states_require_expected_metadata_and_timestamps(db: sqlite3.Connection) -> None:
    add_asset(db)
    add_version(db, "pending", 1)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO speaker_asset_versions
               (id, organization_id, event_id, event_speaker_id, asset_id, generation, object_key,
                original_filename, scan_state, created_at_ms, uploaded_at_ms)
               VALUES ('bad-upload','org-a','event-a','speaker-a','asset-a',2,
                       'private/org-a/event-a/bad-upload','file.png','uploaded',1000,1100)"""
        )
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_upload_intent_hash_expiry_and_tenant_owner(db: sqlite3.Connection) -> None:
    add_asset(db)
    add_version(db, "version-1", 1)
    db.execute(
        """INSERT INTO upload_intents
           (id, organization_id, event_id, event_speaker_id, asset_version_id,
            purpose, token_hash, expected_content_type, expected_byte_size,
            expected_checksum_sha256, expires_at_ms, created_at_ms)
           VALUES ('intent-a','org-a','event-a','speaker-a','version-1','create',?,
                   'image/png',1234,?,2000,1000)""",
        (bytes(range(32)), bytes(32)),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO upload_intents
               (id, organization_id, event_id, event_speaker_id, asset_version_id,
                purpose, token_hash, expected_content_type, expected_byte_size,
                expected_checksum_sha256, expires_at_ms, created_at_ms)
               VALUES ('short-hash','org-a','event-a','speaker-a','version-1','replace',?,
                       'image/png',1234,?,2000,1000)""",
            (b"short", bytes(32)),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO upload_intents
               (id, organization_id, event_id, event_speaker_id, asset_version_id,
                purpose, token_hash, expected_content_type, expected_byte_size,
                expected_checksum_sha256, expires_at_ms, created_at_ms)
               VALUES ('cross-owner','org-b','event-b','speaker-b','version-1','replace',?,
                       'image/png',1234,?,2000,1000)""",
            (bytes(reversed(range(32))), bytes(32)),
        )


def test_owner_scanner_and_expiry_queries_are_indexed(db: sqlite3.Connection) -> None:
    owner = query_plan(
        db,
        """SELECT id FROM speaker_assets
           WHERE organization_id=? AND event_id=? AND event_speaker_id=? AND kind=?
           ORDER BY updated_at_ms DESC, id DESC LIMIT 25""",
        ("org-a", "event-a", "speaker-a", "headshot"),
    )
    scanner = query_plan(
        db,
        """SELECT id FROM speaker_asset_versions
           WHERE scan_state='uploaded' ORDER BY uploaded_at_ms,id LIMIT 25""",
        (),
    )
    expiry = query_plan(
        db,
        """SELECT id FROM upload_intents
           WHERE consumed_at_ms IS NULL AND expires_at_ms < ?
           ORDER BY expires_at_ms,id LIMIT 100""",
        (2_000,),
    )
    assert "idx_speaker_assets_owner" in owner
    assert "idx_speaker_asset_versions_scanner" in scanner
    assert "idx_upload_intents_expiry" in expiry
    assert "USE TEMP B-TREE" not in owner + scanner + expiry

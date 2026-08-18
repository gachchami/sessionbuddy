import hashlib
import sqlite3

import pytest
from fastapi import HTTPException

from sessionbuddy.platform.upload_contracts import task_form_schema_json
from sessionbuddy.speaker_operations.purge import purge_expired_speaker_uploads
from sessionbuddy.speaker_operations.router import _enforce_speaker_upload_quota
from tests.speaker_operations.test_asset_boundary import AsyncSqlite
from tests.speaker_operations.test_asset_schema import add_asset, add_version
from tests.speaker_operations.test_speaker_onboarding_schema import (
    MIGRATIONS,
    add_speaker,
    link_submission_speaker,
    seed_platform,
)


@pytest.fixture
def database() -> AsyncSqlite:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    seed_platform(connection)
    add_speaker(connection, "a")
    link_submission_speaker(connection, "a")
    connection.execute(
        """INSERT INTO speaker_tasks
           (id,organization_id,event_id,event_speaker_id,submission_id,task_type,title,
            destination_type,state,created_at_ms,updated_at_ms,form_schema_json)
           VALUES ('task-a','org-a','event-a','speaker-a','submission-a','headshot',
                   'Upload','headshot','open',1000,1000,?)""",
        (task_form_schema_json("headshot"),),
    )
    return AsyncSqlite(connection)


def _add_intent(
    connection: sqlite3.Connection,
    *,
    intent_id: str,
    version_id: str,
    expires_at_ms: int,
    expected_bytes: int = 1_234,
) -> None:
    connection.execute(
        """INSERT INTO upload_intents
           (id,organization_id,event_id,event_speaker_id,asset_version_id,purpose,
            token_hash,expected_content_type,expected_byte_size,expected_checksum_sha256,
            expires_at_ms,created_at_ms)
           VALUES (?,'org-a','event-a','speaker-a',?,'create',?,'image/png',?,?,?,1000)""",
        (
            intent_id,
            version_id,
            hashlib.sha256(intent_id.encode()).digest(),
            expected_bytes,
            bytes(32),
            expires_at_ms,
        ),
    )


class Bucket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        if self.fail:
            raise RuntimeError("R2 unavailable")
        self.deleted.append(key)


async def test_quota_rejects_fourth_live_pending_intent(database: AsyncSqlite) -> None:
    add_asset(database.connection)
    for generation in range(1, 4):
        version_id = f"version-{generation}"
        add_version(database.connection, version_id, generation)
        _add_intent(
            database.connection,
            intent_id=f"intent-{generation}",
            version_id=version_id,
            expires_at_ms=5_000,
        )

    with pytest.raises(HTTPException) as raised:
        await _enforce_speaker_upload_quota(
            database,
            organization_id="org-a",
            event_id="event-a",
            event_speaker_id="speaker-a",
            requested_bytes=1,
            now_ms=2_000,
        )

    assert raised.value.status_code == 429
    assert raised.value.headers == {"Retry-After": "600"}


async def test_quota_counts_retained_version_storage(database: AsyncSqlite) -> None:
    add_asset(database.connection)
    add_version(database.connection, "version-1", 1, state="clean", current=1)
    database.connection.execute(
        "UPDATE speaker_asset_versions SET byte_size=? WHERE id='version-1'",
        (250 * 1024 * 1024,),
    )

    with pytest.raises(HTTPException) as raised:
        await _enforce_speaker_upload_quota(
            database,
            organization_id="org-a",
            event_id="event-a",
            event_speaker_id="speaker-a",
            requested_bytes=1,
            now_ms=2_000,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail == "Speaker asset storage quota exceeded for this event."


async def test_purge_deletes_expired_pending_object_and_graph(database: AsyncSqlite) -> None:
    add_asset(database.connection)
    add_version(database.connection, "version-1", 1)
    _add_intent(
        database.connection,
        intent_id="intent-1",
        version_id="version-1",
        expires_at_ms=2_000,
    )
    database.connection.execute(
        """INSERT INTO idempotency_records
           (id,principal_key,organization_id,event_id,route_key,idempotency_key_hash,
            request_fingerprint,state,response_status,response_resource_type,
            response_resource_id,created_at_ms,completed_at_ms,expires_at_ms)
           VALUES ('idem','user-a','org-a','event-a','route',?,?, 'completed',201,
                   'upload_intent','intent-1',1000,1000,9000)""",
        (bytes(32), bytes([1]) * 32),
    )
    bucket = Bucket()

    result = await purge_expired_speaker_uploads(database, bucket, 2_000)

    assert result.deleted_rows == 1
    assert result.deleted_objects == 1
    assert result.delete_failures == 0
    assert bucket.deleted == ["private/org-a/event-a/version-1"]
    assert database.connection.execute(
        "SELECT COUNT(*) FROM idempotency_records"
    ).fetchone()[0] == 0
    assert database.connection.execute("SELECT COUNT(*) FROM upload_intents").fetchone()[0] == 0
    assert database.connection.execute(
        "SELECT COUNT(*) FROM speaker_asset_versions"
    ).fetchone()[0] == 0
    assert database.connection.execute("SELECT COUNT(*) FROM speaker_assets").fetchone()[0] == 0


async def test_purge_keeps_rows_for_retry_when_r2_delete_fails(database: AsyncSqlite) -> None:
    add_asset(database.connection)
    add_version(database.connection, "version-1", 1)
    _add_intent(
        database.connection,
        intent_id="intent-1",
        version_id="version-1",
        expires_at_ms=2_000,
    )

    result = await purge_expired_speaker_uploads(database, Bucket(fail=True), 2_000)

    assert result.deleted_rows == 0
    assert result.deleted_objects == 0
    assert result.delete_failures == 1
    assert database.connection.execute("SELECT COUNT(*) FROM upload_intents").fetchone()[0] == 1
    assert database.connection.execute(
        "SELECT COUNT(*) FROM speaker_asset_versions"
    ).fetchone()[0] == 1


async def test_purge_does_not_touch_unexpired_pending_upload(database: AsyncSqlite) -> None:
    add_asset(database.connection)
    add_version(database.connection, "version-1", 1)
    _add_intent(
        database.connection,
        intent_id="intent-1",
        version_id="version-1",
        expires_at_ms=3_000,
    )
    bucket = Bucket()

    result = await purge_expired_speaker_uploads(database, bucket, 2_000)

    assert result.deleted_rows == 0
    assert result.deleted_objects == 0
    assert result.delete_failures == 0
    assert bucket.deleted == []
    assert database.connection.execute("SELECT COUNT(*) FROM upload_intents").fetchone()[0] == 1

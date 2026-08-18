import sqlite3
from dataclasses import dataclass

import pytest

from sessionbuddy.platform.upload_contracts import task_form_schema_json
from sessionbuddy.speaker_operations.asset_boundary import (
    AssetAccessScope,
    AssetRepository,
    ScanJob,
    ScanResult,
    consume_scan_job,
)
from tests.speaker_operations.test_asset_schema import add_asset, add_version
from tests.speaker_operations.test_speaker_onboarding_schema import (
    MIGRATIONS,
    add_speaker,
    link_submission_speaker,
    seed_platform,
)


class Statement:
    def __init__(self, db: "AsyncSqlite", sql: str) -> None:
        self.db = db
        self.sql = sql
        self.values: tuple[object, ...] = ()

    def bind(self, *values: object) -> "Statement":
        self.values = values
        return self

    async def first(self, column: str | None = None):
        row = self.db.connection.execute(self.sql, self.values).fetchone()
        if row is None:
            return None
        converted = dict(row)
        return converted[column] if column is not None else converted

    async def run(self):
        cursor = self.db.connection.execute(self.sql, self.values)
        return {"success": True, "changes": cursor.rowcount}

    async def all(self):
        rows = self.db.connection.execute(self.sql, self.values).fetchall()
        return {"results": [dict(row) for row in rows]}


class AsyncSqlite:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def prepare(self, sql: str) -> Statement:
        return Statement(self, sql)

    async def batch(self, statements):
        self.connection.execute("SAVEPOINT d1_batch")
        try:
            results = []
            for statement in statements:
                cursor = self.connection.execute(statement.sql, statement.values)
                results.append({"success": True, "changes": cursor.rowcount})
            self.connection.execute("RELEASE d1_batch")
            return results
        except Exception:
            self.connection.execute("ROLLBACK TO d1_batch")
            self.connection.execute("RELEASE d1_batch")
            raise


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


@dataclass
class Stored:
    body: bytes


class Bucket:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.requested: list[str] = []

    async def get(self, key: str):
        self.requested.append(key)
        value = self.objects.get(key)
        return None if value is None else Stored(value)


class Scanner:
    def __init__(self, result: ScanResult) -> None:
        self.result = result
        self.calls = 0

    async def scan(self, body, *, job: ScanJob) -> ScanResult:
        self.calls += 1
        assert body.body == b"private bytes"
        assert job.checksum_sha256 == bytes(32)
        return self.result


def owner_scope(**changes: object) -> AssetAccessScope:
    values: dict[str, object] = {
        "organization_id": "org-a",
        "event_id": "event-a",
        "actor_user_id": "user-a",
        "event_speaker_id": "speaker-a",
    }
    values.update(changes)
    return AssetAccessScope(**values)


def scan_payload(version: str = "version-1", generation: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "organization_id": "org-a",
        "event_id": "event-a",
        "asset_version_id": version,
        "generation": generation,
        "checksum_sha256": bytes(32).hex(),
        "job_id": f"job-{generation}",
    }


def test_scan_queue_contract_round_trips_without_object_key() -> None:
    job = ScanJob.decode(scan_payload())
    assert job.to_message() == scan_payload()
    assert "object_key" not in job.to_message()


async def test_download_grant_is_owner_scoped_short_lived_and_single_use(
    database: AsyncSqlite,
) -> None:
    add_asset(database.connection)
    add_version(database.connection, "version-1", 1, state="clean", current=1)
    repository = AssetRepository(database)
    assert (
        await repository.create_download_grant(
            owner_scope(actor_user_id="user-b"), "asset-a", now_ms=2_000
        )
        is None
    )
    grant = await repository.create_download_grant(
        owner_scope(), "asset-a", now_ms=2_000, ttl_ms=5_000
    )
    assert grant is not None
    assert grant.expires_at_ms == 7_000
    bucket = Bucket({"private/org-a/event-a/version-1": b"private bytes"})
    download = await repository.consume_download_grant(
        bucket, actor_user_id="user-a", token=grant.token, now_ms=2_001
    )
    assert download is not None
    assert download.body.body == b"private bytes"
    assert "object_key" not in download.__dataclass_fields__
    assert (
        await repository.consume_download_grant(
            bucket, actor_user_id="user-a", token=grant.token, now_ms=2_002
        )
        is None
    )


async def test_download_fails_closed_for_unscanned_or_expired_version(
    database: AsyncSqlite,
) -> None:
    add_asset(database.connection)
    add_version(database.connection, "version-1", 1, state="uploaded")
    repository = AssetRepository(database)
    assert await repository.create_download_grant(owner_scope(), "asset-a", now_ms=2_000) is None

    database.connection.execute("DELETE FROM speaker_asset_versions")
    add_version(database.connection, "version-clean", 1, state="clean", current=1)
    grant = await repository.create_download_grant(
        owner_scope(), "asset-a", now_ms=2_000, ttl_ms=1_000
    )
    assert grant is not None
    bucket = Bucket({"private/org-a/event-a/version-clean": b"private bytes"})
    assert (
        await repository.consume_download_grant(
            bucket, actor_user_id="user-a", token=grant.token, now_ms=3_000
        )
        is None
    )
    assert bucket.requested == []


async def test_historical_clean_version_has_its_own_scoped_single_use_grant(
    database: AsyncSqlite,
) -> None:
    add_asset(database.connection)
    add_version(database.connection, "version-1", 1, state="superseded")
    add_version(database.connection, "version-2", 2, state="clean", current=1)
    repository = AssetRepository(database)

    historical = await repository.create_download_grant(
        owner_scope(), "asset-a", version_id="version-1", now_ms=2_000
    )
    assert historical is not None
    bucket = Bucket(
        {
            "private/org-a/event-a/version-1": b"old bytes",
            "private/org-a/event-a/version-2": b"current bytes",
        }
    )
    download = await repository.consume_download_grant(
        bucket, actor_user_id="user-a", token=historical.token, now_ms=2_001
    )
    assert download is not None
    assert download.body.body == b"old bytes"
    assert bucket.requested == ["private/org-a/event-a/version-1"]
    assert await repository.consume_download_grant(
        bucket, actor_user_id="user-a", token=historical.token, now_ms=2_002
    ) is None

    assert await repository.create_download_grant(
        owner_scope(actor_user_id="user-b"),
        "asset-a",
        version_id="version-1",
        now_ms=2_000,
    ) is None
    assert await repository.create_download_grant(
        owner_scope(), "asset-a", version_id="missing", now_ms=2_000
    ) is None


async def test_scan_consumer_promotes_clean_exact_job_once(database: AsyncSqlite) -> None:
    add_asset(database.connection)
    add_version(database.connection, "version-1", 1, state="uploaded")
    bucket = Bucket({"private/org-a/event-a/version-1": b"private bytes"})
    scanner = Scanner(ScanResult("provider-1", "clean", "example-scanner"))
    disposition = await consume_scan_job(database, bucket, scanner, scan_payload(), now_ms=2_000)
    assert disposition.ack and disposition.reason == "clean"
    assert tuple(
        database.connection.execute(
            "SELECT scan_state,is_current FROM speaker_asset_versions WHERE id='version-1'"
        ).fetchone()
    ) == ("clean", 1)
    replay = await consume_scan_job(database, bucket, scanner, scan_payload(), now_ms=2_001)
    assert replay.ack and replay.reason == "stale_or_complete"
    assert scanner.calls == 1
    assert database.connection.execute("SELECT count(*) FROM asset_scan_events").fetchone()[0] == 1


async def test_scan_consumer_fails_closed_for_malformed_missing_and_malicious(
    database: AsyncSqlite,
) -> None:
    malformed = await consume_scan_job(
        database,
        Bucket({}),
        Scanner(ScanResult("provider", "clean", "scanner")),
        {"schema_version": 2},
        now_ms=2_000,
    )
    assert malformed.ack and malformed.reason == "invalid_contract"

    add_asset(database.connection)
    add_version(database.connection, "version-1", 1, state="uploaded")
    scanner = Scanner(ScanResult("provider-1", "malicious", "example-scanner", "EICAR"))
    missing = await consume_scan_job(database, Bucket({}), scanner, scan_payload(), now_ms=2_000)
    assert not missing.ack and missing.reason == "object_unavailable"
    rejected = await consume_scan_job(
        database,
        Bucket({"private/org-a/event-a/version-1": b"private bytes"}),
        scanner,
        scan_payload(),
        now_ms=2_001,
    )
    assert rejected.ack and rejected.reason == "malicious"
    assert tuple(
        database.connection.execute(
            "SELECT scan_state,is_current FROM speaker_asset_versions WHERE id='version-1'"
        ).fetchone()
    ) == ("rejected", 0)

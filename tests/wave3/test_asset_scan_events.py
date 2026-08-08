import sqlite3

import pytest

from tests.wave3.test_asset_schema import add_asset, add_version
from tests.wave3.test_speaker_onboarding_schema import MIGRATIONS, add_speaker, seed_foundation

CHECKSUM = bytes(32)


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    seed_foundation(connection)
    add_speaker(connection, "a")
    connection.execute(
        """INSERT INTO speaker_tasks
           (id, organization_id, event_id, event_speaker_id, submission_id,
            task_type, title, destination_type, state, created_at_ms, updated_at_ms)
           VALUES ('task-a','org-a','event-a','speaker-a','submission-a',
                   'headshot','Upload headshot','headshot','open',1000,1000)"""
    )
    return connection


def insert_scan_event(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    provider_event_id: str,
    job_id: str,
    version_id: str = "version-1",
    generation: int = 1,
    checksum: bytes = CHECKSUM,
) -> int:
    connection.execute(
        """INSERT INTO asset_scan_events
           (id, organization_id, event_id, asset_version_id, generation,
            checksum_sha256, provider_event_id, job_id, verdict, engine,
            signature_code, received_at_ms)
           VALUES (?, 'org-a', 'event-a', ?, ?, ?, ?, ?, 'clean',
                   'example-scanner', 'NO_THREAT', 1400)
           ON CONFLICT DO NOTHING""",
        (event_id, version_id, generation, checksum, provider_event_id, job_id),
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def promote_if_latest(
    connection: sqlite3.Connection,
    *,
    version_id: str,
    generation: int,
    checksum: bytes,
) -> int:
    connection.execute(
        """UPDATE speaker_asset_versions AS candidate
           SET scan_state = 'clean', is_current = 1, scanned_at_ms = 1400
           WHERE candidate.id = ?
             AND candidate.generation = ?
             AND candidate.checksum_sha256 = ?
             AND candidate.scan_state = 'scanning'
             AND EXISTS (
               SELECT 1 FROM asset_scan_events receipt
               WHERE receipt.organization_id = candidate.organization_id
                 AND receipt.event_id = candidate.event_id
                 AND receipt.asset_version_id = candidate.id
                 AND receipt.generation = candidate.generation
                 AND receipt.checksum_sha256 = candidate.checksum_sha256
                 AND receipt.verdict = 'clean'
             )
             AND NOT EXISTS (
               SELECT 1 FROM speaker_asset_versions newer
               WHERE newer.asset_id = candidate.asset_id
                 AND newer.generation > candidate.generation
             )""",
        (version_id, generation, checksum),
    )
    return int(connection.execute("SELECT changes()").fetchone()[0])


def test_scan_receipt_is_bound_to_exact_generation_and_checksum(
    db: sqlite3.Connection,
) -> None:
    add_asset(db)
    add_version(db, "version-1", 1, state="scanning")
    assert insert_scan_event(
        db, event_id="receipt-1", provider_event_id="provider-1", job_id="job-1"
    ) == 1
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO asset_scan_events
               (id, organization_id, event_id, asset_version_id, generation,
                checksum_sha256, provider_event_id, job_id, verdict, engine, received_at_ms)
               VALUES ('wrong-checksum','org-a','event-a','version-1',1,?,
                       'provider-2','job-2','clean','example-scanner',1400)""",
            (bytes([1]) * 32,),
        )


def test_duplicate_provider_event_and_exact_job_replay_are_noops(
    db: sqlite3.Connection,
) -> None:
    add_asset(db)
    add_version(db, "version-1", 1, state="scanning")
    assert insert_scan_event(
        db, event_id="receipt-1", provider_event_id="provider-1", job_id="job-1"
    ) == 1
    assert insert_scan_event(
        db, event_id="receipt-duplicate", provider_event_id="provider-1", job_id="job-other"
    ) == 0
    assert insert_scan_event(
        db, event_id="receipt-job-replay", provider_event_id="provider-other", job_id="job-1"
    ) == 0
    assert db.execute("SELECT count(*) FROM asset_scan_events").fetchone() == (1,)


def test_late_clean_result_cannot_promote_an_old_replaced_generation(
    db: sqlite3.Connection,
) -> None:
    add_asset(db)
    add_version(db, "version-1", 1, state="scanning")
    add_version(db, "version-2", 2, state="pending_upload")
    assert insert_scan_event(
        db, event_id="receipt-1", provider_event_id="provider-1", job_id="job-1"
    ) == 1

    assert promote_if_latest(
        db, version_id="version-1", generation=1, checksum=CHECKSUM
    ) == 0
    assert db.execute(
        "SELECT scan_state, is_current FROM speaker_asset_versions WHERE id='version-1'"
    ).fetchone() == ("scanning", 0)
    with pytest.raises(sqlite3.IntegrityError, match="newer asset generation"):
        db.execute(
            """UPDATE speaker_asset_versions
               SET scan_state='clean', is_current=1, scanned_at_ms=1400
               WHERE id='version-1'"""
        )


def test_current_generation_promotes_only_after_clean_receipt(db: sqlite3.Connection) -> None:
    add_asset(db)
    add_version(db, "version-1", 1, state="scanning")
    assert promote_if_latest(
        db, version_id="version-1", generation=1, checksum=CHECKSUM
    ) == 0
    assert insert_scan_event(
        db, event_id="receipt-1", provider_event_id="provider-1", job_id="job-1"
    ) == 1
    assert promote_if_latest(
        db, version_id="version-1", generation=1, checksum=CHECKSUM
    ) == 1
    assert db.execute(
        "SELECT scan_state, is_current FROM speaker_asset_versions WHERE id='version-1'"
    ).fetchone() == ("clean", 1)

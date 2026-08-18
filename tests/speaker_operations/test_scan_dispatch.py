"""The scheduled scan re-dispatcher and the graceful post-commit enqueue.

A committed upload whose queue envelope was never published (swallowed
transient failure, crash between commit and publish, poisoned message) must
still be scanned eventually; the cron pass is that guarantee for both the
speaker asset and CFP staged-asset quarantines.
"""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from sessionbuddy.platform.upload_contracts import task_form_schema_json
from sessionbuddy.speaker_operations.asset_boundary import (
    SCANNING_RECOVERY_AFTER_MS,
    ScanJob,
    ScanResult,
    consume_scan_job,
    dispatch_stuck_asset_scans,
)
from sessionbuddy.speaker_operations.router import _enqueue_asset_scan
from tests.speaker_operations.test_asset_boundary import (
    AsyncSqlite,
    Bucket,
    Scanner,
    scan_payload,
)
from tests.speaker_operations.test_asset_schema import add_asset, add_version
from tests.speaker_operations.test_speaker_onboarding_schema import (
    MIGRATIONS,
    add_speaker,
    link_submission_speaker,
    seed_platform,
)

PROJECT_ROOT = Path(__file__).parents[2]
NOW_MS = 10_000_000


class RecordingQueue:
    def __init__(self, fail_on_attempts: frozenset[int] = frozenset()) -> None:
        self.fail_on_attempts = fail_on_attempts
        self.messages: list[dict[str, object]] = []
        self.attempts = 0

    async def send(self, message: dict[str, object]) -> None:
        self.attempts += 1
        if self.attempts in self.fail_on_attempts:
            raise RuntimeError("synthetic queue outage")
        self.messages.append(message)


@pytest.fixture
def scan_database() -> tuple[sqlite3.Connection, AsyncSqlite]:
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
    add_asset(connection)
    # seed_platform() already provides call_for_speaker_forms row 'form-a'.
    yield connection, AsyncSqlite(connection)
    connection.close()


def add_staged(
    connection: sqlite3.Connection, staged_id: str, *, status: str, updated_at_ms: int
) -> None:
    connection.execute(
        """INSERT INTO cfp_staged_assets
           (id,organization_id,event_id,form_id,user_id,kind,object_key,
            original_filename,content_type,byte_size,checksum_sha256,
            upload_token_hash,status,expires_at_ms,created_at_ms,updated_at_ms)
           VALUES(?,'org-a','event-a','form-a','user-a','supporting_document',?,
                  'paper.pdf','application/pdf',10,?,?,?,99999999999,1000,?)""",
        (
            staged_id,
            f"private/cfp/org-a/{staged_id}",
            bytes(32),
            staged_id.encode().ljust(32, b"0")[:32],
            status,
            updated_at_ms,
        ),
    )


async def test_dispatch_republishes_stuck_uploads_across_both_quarantines(
    scan_database,
) -> None:
    connection, database = scan_database
    # Stale committed upload whose envelope never arrived: must republish.
    add_version(connection, "version-stale", 1, state="uploaded")
    # Fresh upload: the request path just published it; leave it alone.
    add_version(connection, "version-fresh", 2, state="uploaded")
    connection.execute(
        "UPDATE speaker_asset_versions SET uploaded_at_ms=? WHERE id='version-fresh'",
        (NOW_MS,),
    )
    # Stale 'scanning' row: the backstop for a poisoned or lost queue message.
    add_version(connection, "version-lost-scan", 3, state="scanning")
    # Terminal state: never republished.
    add_version(connection, "version-clean", 4, state="clean")
    add_staged(connection, "staged-stale", status="uploaded", updated_at_ms=1_000)
    # A fresh enqueue claim bumps updated_at_ms; the cut must skip it.
    add_staged(connection, "staged-claimed", status="uploaded", updated_at_ms=NOW_MS)

    queue = RecordingQueue()
    result = await dispatch_stuck_asset_scans(database, queue, NOW_MS)

    assert (result.published, result.publish_failures) == (3, 0)
    jobs = [ScanJob.decode(message) for message in queue.messages]
    assert {job.asset_version_id for job in jobs} == {
        "version-stale",
        "version-lost-scan",
        "staged-stale",
    }
    by_id = {job.asset_version_id: job for job in jobs}
    assert by_id["version-stale"].generation == 1
    assert by_id["version-lost-scan"].generation == 3
    assert by_id["staged-stale"].generation == 1
    assert all(job.organization_id == "org-a" for job in jobs)


async def test_dispatch_counts_publish_failures_and_continues(scan_database) -> None:
    connection, database = scan_database
    add_version(connection, "version-one", 1, state="uploaded")
    add_version(connection, "version-two", 2, state="uploaded")

    queue = RecordingQueue(fail_on_attempts=frozenset({1}))
    result = await dispatch_stuck_asset_scans(database, queue, NOW_MS)

    assert (result.published, result.publish_failures) == (1, 1)
    assert len(queue.messages) == 1


async def test_dispatch_rejects_an_unbounded_retry_policy(scan_database) -> None:
    _connection, database = scan_database
    with pytest.raises(ValueError, match="limit"):
        await dispatch_stuck_asset_scans(database, RecordingQueue(), NOW_MS, limit=0)
    with pytest.raises(ValueError, match="retry policy"):
        await dispatch_stuck_asset_scans(
            database, RecordingQueue(), NOW_MS, uploaded_after_ms=1_000
        )
    with pytest.raises(ValueError, match="retry policy"):
        await dispatch_stuck_asset_scans(
            database,
            RecordingQueue(),
            NOW_MS,
            scanning_after_ms=600_000,
            give_up_after_ms=600_000,
        )


async def test_dispatch_spaces_retries_and_eventually_gives_up(scan_database) -> None:
    """A failed attempt holds its claim, so the row sits in 'scanning' with
    scan_started_at_ms as the last-attempt marker: it waits out the recovery
    window instead of being republished on every two-minute cron pass, and
    rows past the give-up age stop being retried entirely (they stay
    fail-closed in quarantine)."""
    connection, database = scan_database
    add_version(connection, "version-attempted", 1, state="scanning")
    connection.execute(
        "UPDATE speaker_asset_versions SET scan_started_at_ms=? WHERE id='version-attempted'",
        (NOW_MS,),
    )

    queue = RecordingQueue()
    spaced = await dispatch_stuck_asset_scans(database, queue, NOW_MS)
    assert (spaced.published, len(queue.messages)) == (0, 0)

    retry_due = await dispatch_stuck_asset_scans(
        database, queue, NOW_MS + SCANNING_RECOVERY_AFTER_MS + 1
    )
    assert retry_due.published == 1

    gave_up = await dispatch_stuck_asset_scans(
        database,
        RecordingQueue(),
        NOW_MS + SCANNING_RECOVERY_AFTER_MS + 1,
        give_up_after_ms=700_000,
    )
    assert gave_up.published == 0  # uploaded_at_ms=1100 is older than the cap


async def test_duplicate_scan_job_for_a_fresh_claim_acks_without_scanning(
    scan_database,
) -> None:
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="scanning")  # scan_started_at_ms=1200
    scanner = Scanner(ScanResult("provider-1", "clean", "example-scanner"))

    duplicate = await consume_scan_job(
        database,
        Bucket({"private/org-a/event-a/version-1": b"private bytes"}),
        scanner,
        scan_payload(),
        now_ms=2_000,
    )

    assert duplicate.ack and duplicate.reason == "already_claimed"
    assert scanner.calls == 0


async def test_stale_scanning_claim_is_taken_over_and_completed(scan_database) -> None:
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="scanning")
    scanner = Scanner(ScanResult("provider-1", "clean", "example-scanner"))

    takeover = await consume_scan_job(
        database,
        Bucket({"private/org-a/event-a/version-1": b"private bytes"}),
        scanner,
        scan_payload(),
        now_ms=NOW_MS,
    )

    assert takeover.ack and takeover.reason == "clean"
    assert scanner.calls == 1
    assert tuple(
        connection.execute(
            """SELECT scan_state,is_current,scan_started_at_ms
               FROM speaker_asset_versions WHERE id='version-1'"""
        ).fetchone()
    ) == ("clean", 1, NOW_MS)


async def test_scanner_error_holds_the_claim_instead_of_rejecting(
    scan_database,
) -> None:
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="uploaded")
    bucket = Bucket({"private/org-a/event-a/version-1": b"private bytes"})

    errored = await consume_scan_job(
        database,
        bucket,
        Scanner(ScanResult("provider-1", "error", "example-scanner")),
        scan_payload(),
        now_ms=2_000,
    )

    assert not errored.ack and errored.reason == "scanner_error"
    # Never quarantine-rejected; the claim is held as the last-attempt marker.
    assert tuple(
        connection.execute(
            """SELECT scan_state,scan_started_at_ms FROM speaker_asset_versions
               WHERE id='version-1'"""
        ).fetchone()
    ) == ("scanning", 2_000)
    assert connection.execute(
        "SELECT count(*) FROM asset_scan_events"
    ).fetchone()[0] == 0

    # A prompt redelivery must not hammer the failing scanner...
    redelivered = await consume_scan_job(
        database,
        bucket,
        Scanner(ScanResult("provider-2", "clean", "example-scanner")),
        scan_payload(),
        now_ms=2_001,
    )
    assert redelivered.ack and redelivered.reason == "already_claimed"

    # ...while the recovery-window takeover retries and completes the scan.
    recovered = await consume_scan_job(
        database,
        bucket,
        Scanner(ScanResult("provider-3", "clean", "example-scanner")),
        scan_payload(),
        now_ms=2_000 + SCANNING_RECOVERY_AFTER_MS + 1,
    )
    assert recovered.ack and recovered.reason == "clean"
    assert connection.execute(
        "SELECT scan_state FROM speaker_asset_versions WHERE id='version-1'"
    ).fetchone()[0] == "clean"


async def test_missing_object_releases_the_claim_for_immediate_retry(
    scan_database,
) -> None:
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="uploaded")
    scanner = Scanner(ScanResult("provider-1", "clean", "example-scanner"))

    missing = await consume_scan_job(database, Bucket({}), scanner, scan_payload(), now_ms=2_000)
    assert not missing.ack and missing.reason == "object_unavailable"
    # Released to 'uploaded', whose schema invariant clears the attempt marker.
    assert tuple(
        connection.execute(
            """SELECT scan_state,scan_started_at_ms FROM speaker_asset_versions
               WHERE id='version-1'"""
        ).fetchone()
    ) == ("uploaded", None)

    retried = await consume_scan_job(
        database,
        Bucket({"private/org-a/event-a/version-1": b"private bytes"}),
        scanner,
        scan_payload(),
        now_ms=2_001,
    )
    assert retried.ack and retried.reason == "clean"


class _TakeoverDuringScan:
    """Scanner stub that simulates a hung owner: while this consumer is inside
    the provider call, another worker's takeover re-claims the row (bumping
    the claim token). The original owner's terminal write must then no-op."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        statement: str,
        params: tuple[object, ...],
        result: ScanResult,
    ) -> None:
        self.connection = connection
        self.statement = statement
        self.params = params
        self.result = result

    async def scan(self, _body, *, job: ScanJob) -> ScanResult:
        del job
        self.connection.execute(self.statement, self.params)
        return self.result


async def test_hung_owner_cannot_reject_after_a_takeover(scan_database) -> None:
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="uploaded")
    takeover_ms = 2_000 + SCANNING_RECOVERY_AFTER_MS + 1
    scanner = _TakeoverDuringScan(
        connection,
        "UPDATE speaker_asset_versions SET scan_started_at_ms=? "
        "WHERE id='version-1' AND scan_state='scanning'",
        (takeover_ms,),
        ScanResult("provider-1", "malicious", "example-scanner", "EICAR"),
    )

    late = await consume_scan_job(
        database,
        Bucket({"private/org-a/event-a/version-1": b"private bytes"}),
        scanner,
        scan_payload(),
        now_ms=2_000,
    )

    # The old owner's message acks, but the takeover keeps the row: it is
    # still the takeover's in-flight 'scanning' claim, never 'rejected'.
    assert late.ack
    assert tuple(
        connection.execute(
            """SELECT scan_state,scan_started_at_ms,scan_result_code
               FROM speaker_asset_versions WHERE id='version-1'"""
        ).fetchone()
    ) == ("scanning", takeover_ms, None)
    # ...and its receipt is withheld too. asset_scan_events is unique per
    # (engine, job_id, asset_version_id, generation, checksum), so an engine has
    # one receipt slot per job and every attempt competes for it. Recording the
    # loser's verdict here would squat that slot: the takeover's own receipt
    # would hit ON CONFLICT DO NOTHING while its terminal state landed, leaving
    # the stored verdict describing a scan that never decided anything.
    assert connection.execute(
        "SELECT count(*) FROM asset_scan_events"
    ).fetchone()[0] == 0


async def test_hung_owner_clean_result_cannot_demote_a_takeover(scan_database) -> None:
    """The dangerous half of the race: without token-gating the supersede
    step, a hung owner's clean batch demotes the asset's current version and
    then fails its own promote, leaving the asset with no current version."""
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="clean", current=1)
    add_version(connection, "version-2", 2, state="uploaded")
    takeover_ms = 2_000 + SCANNING_RECOVERY_AFTER_MS + 1
    scanner = _TakeoverDuringScan(
        connection,
        "UPDATE speaker_asset_versions SET scan_started_at_ms=? "
        "WHERE id='version-2' AND scan_state='scanning'",
        (takeover_ms,),
        ScanResult("provider-2", "clean", "example-scanner"),
    )

    late = await consume_scan_job(
        database,
        Bucket({"private/org-a/event-a/version-2": b"private bytes"}),
        scanner,
        scan_payload(version="version-2", generation=2),
        now_ms=2_000,
    )

    assert late.ack
    assert tuple(
        connection.execute(
            "SELECT scan_state,is_current FROM speaker_asset_versions WHERE id='version-1'"
        ).fetchone()
    ) == ("clean", 1)  # the current version survives the stale owner's batch
    assert tuple(
        connection.execute(
            """SELECT scan_state,is_current,scan_started_at_ms
               FROM speaker_asset_versions WHERE id='version-2'"""
        ).fetchone()
    ) == ("scanning", 0, takeover_ms)


async def test_a_losing_attempt_cannot_squat_the_receipt_slot(scan_database) -> None:
    """The stored verdict must describe the scan that actually decided the row.

    asset_scan_events is unique on (engine, job_id, asset_version_id,
    generation, checksum_sha256), so one engine has a single receipt slot per
    job and every attempt competes for it. A hung owner that writes an ungated
    receipt takes that slot even though its terminal state write no-ops; the
    attempt that does own the claim then inserts ON CONFLICT DO NOTHING while
    its state transition lands, and the row ends up 'clean' with 'malicious' as
    its only receipt. Gating the insert on the claim token closes it.
    """
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="uploaded")
    lost_claim_ms = 2_000 + SCANNING_RECOVERY_AFTER_MS + 1
    hung_owner = _TakeoverDuringScan(
        connection,
        "UPDATE speaker_asset_versions SET scan_started_at_ms=? "
        "WHERE id='version-1' AND scan_state='scanning'",
        (lost_claim_ms,),
        ScanResult("provider-1", "malicious", "example-scanner", "EICAR"),
    )
    bucket = Bucket({"private/org-a/event-a/version-1": b"private bytes"})

    # The hung owner finishes first and tries to file 'malicious' against a
    # claim it no longer holds.
    assert (
        await consume_scan_job(database, bucket, hung_owner, scan_payload(), now_ms=2_000)
    ).ack
    assert connection.execute("SELECT count(*) FROM asset_scan_events").fetchone()[0] == 0

    # The attempt that does own the row when it decides gets the slot.
    decided = await consume_scan_job(
        database,
        bucket,
        Scanner(ScanResult("provider-2", "clean", "example-scanner")),
        scan_payload(),
        now_ms=lost_claim_ms + SCANNING_RECOVERY_AFTER_MS + 1,
    )

    assert decided.ack and decided.reason == "clean"
    assert connection.execute(
        "SELECT scan_state FROM speaker_asset_versions WHERE id='version-1'"
    ).fetchone()[0] == "clean"
    assert [
        tuple(row)
        for row in connection.execute(
            "SELECT verdict,provider_event_id FROM asset_scan_events"
        ).fetchall()
    ] == [("clean", "provider-2")]


async def test_clean_promotion_requires_its_own_stored_receipt(scan_database) -> None:
    """'clean' is unwritable without a stored receipt saying so.

    The receipt insert shares the batch with the promote, so normally the two
    are inseparable. This drives the one way they can come apart: another row
    already holds this job's slot in asset_scan_events, so the consumer's
    insert is dropped by ON CONFLICT DO NOTHING. The promote and the supersede
    both check for the receipt, so the batch fails closed as a whole instead of
    marking the version clean on the strength of a 'malicious' receipt -- and
    instead of demoting a current version for a promote that will not land.
    """
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="uploaded")
    connection.execute(
        """INSERT INTO asset_scan_events
           (id,organization_id,event_id,asset_version_id,generation,checksum_sha256,
            provider_event_id,job_id,verdict,engine,received_at_ms)
           VALUES ('squatter','org-a','event-a','version-1',1,?,'other-provider','job-1',
                   'malicious','example-scanner',1000)""",
        (bytes(32),),
    )

    result = await consume_scan_job(
        database,
        Bucket({"private/org-a/event-a/version-1": b"private bytes"}),
        Scanner(ScanResult("provider-1", "clean", "example-scanner")),
        scan_payload(),
        now_ms=2_000,
    )

    assert result.ack
    # Quarantined, for the re-dispatcher to retry until give_up_after_ms.
    assert tuple(
        connection.execute(
            "SELECT scan_state,is_current FROM speaker_asset_versions WHERE id='version-1'"
        ).fetchone()
    ) == ("scanning", 0)
    assert [
        row[0] for row in connection.execute("SELECT verdict FROM asset_scan_events")
    ] == ["malicious"]

async def test_rejection_requires_a_matching_stored_receipt(scan_database) -> None:
    """A rejection is a terminal verdict, and gets the same guard as a promote.

    The interesting direction is the quiet one: writing 'rejected' while the only
    receipt on file says 'clean' diverges exactly as badly as promoting against a
    'malicious' receipt, but nobody files a bug about an upload that was refused.
    Failing closed here still leaves the malicious upload quarantined -- the row
    stays 'scanning' for the re-dispatcher rather than reaching 'rejected'.
    """
    connection, database = scan_database
    add_version(connection, "version-1", 1, state="uploaded")
    connection.execute(
        """INSERT INTO asset_scan_events
           (id,organization_id,event_id,asset_version_id,generation,checksum_sha256,
            provider_event_id,job_id,verdict,engine,received_at_ms)
           VALUES ('squatter','org-a','event-a','version-1',1,?,'other-provider','job-1',
                   'clean','example-scanner',1000)""",
        (bytes(32),),
    )

    result = await consume_scan_job(
        database,
        Bucket({"private/org-a/event-a/version-1": b"private bytes"}),
        Scanner(ScanResult("provider-1", "malicious", "example-scanner", "EICAR")),
        scan_payload(),
        now_ms=2_000,
    )

    assert result.ack and result.reason == "malicious"
    assert tuple(
        connection.execute(
            """SELECT scan_state,is_current,scan_result_code
               FROM speaker_asset_versions WHERE id='version-1'"""
        ).fetchone()
    ) == ("scanning", 0, None)
    assert [
        row[0] for row in connection.execute("SELECT verdict FROM asset_scan_events")
    ] == ["clean"]

async def test_hung_owner_cannot_finalize_a_taken_over_staged_scan(
    scan_database,
) -> None:
    connection, database = scan_database
    add_staged(connection, "staged-race", status="uploaded", updated_at_ms=1_000)
    takeover_ms = 2_000 + SCANNING_RECOVERY_AFTER_MS + 1
    scanner = _TakeoverDuringScan(
        connection,
        "UPDATE cfp_staged_assets SET updated_at_ms=? "
        "WHERE id='staged-race' AND status='scanning'",
        (takeover_ms,),
        ScanResult("provider-3", "malicious", "example-scanner", "EICAR"),
    )
    job = {
        "schema_version": 1,
        "organization_id": "org-a",
        "event_id": "event-a",
        "asset_version_id": "staged-race",
        "generation": 1,
        "checksum_sha256": bytes(32).hex(),
        "job_id": "staged-race",
    }

    late = await consume_scan_job(
        database,
        Bucket({"private/cfp/org-a/staged-race": b"%PDF-1.4"}),
        scanner,
        job,
        now_ms=2_000,
    )

    assert late.ack
    assert tuple(
        connection.execute(
            """SELECT status,updated_at_ms,scan_result_code
               FROM cfp_staged_assets WHERE id='staged-race'"""
        ).fetchone()
    ) == ("scanning", takeover_ms, None)


async def test_duplicate_staged_scan_job_for_a_fresh_claim_acks(scan_database) -> None:
    connection, database = scan_database
    add_staged(connection, "staged-busy", status="scanning", updated_at_ms=NOW_MS)
    scanner = Scanner(ScanResult("provider-1", "clean", "example-scanner"))
    job = {
        "schema_version": 1,
        "organization_id": "org-a",
        "event_id": "event-a",
        "asset_version_id": "staged-busy",
        "generation": 1,
        "checksum_sha256": bytes(32).hex(),
        "job_id": "staged-busy",
    }

    duplicate = await consume_scan_job(
        database, Bucket({}), scanner, job, now_ms=NOW_MS + 1_000
    )

    assert duplicate.ack and duplicate.reason == "already_claimed"
    assert scanner.calls == 0


def _request(environment: SimpleNamespace) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/speaker/events/event-a/upload-intents/intent/complete",
            "headers": [],
            "env": environment,
        }
    )
    request.state.request_id = "scan-dispatch-request"
    request.state.timings = {}
    return request


_VERSION_ROW = {
    "organization_id": "org-a",
    "event_id": "event-a",
    "version_id": "version-stale",
    "generation": 1,
    "expected_checksum_sha256": bytes(32),
}


async def test_upload_completion_absorbs_a_transient_publish_failure() -> None:
    class FailingQueue:
        async def send(self, _message: dict[str, object]) -> None:
            raise RuntimeError("synthetic queue outage")

    request = _request(SimpleNamespace(ASSET_SCAN_QUEUE=FailingQueue()))

    await _enqueue_asset_scan(request, _VERSION_ROW)

    assert request.state.degradations == ["asset_scan_queue_publish_failed"]


async def test_upload_completion_still_fails_loudly_without_a_queue_binding() -> None:
    with pytest.raises(HTTPException) as missing:
        await _enqueue_asset_scan(_request(SimpleNamespace()), _VERSION_ROW)
    assert missing.value.status_code == 503


def test_source_wiring_entry_routes_suffixed_scan_queues_and_schedules_recovery() -> None:
    """Environment-suffixed queues (sessionbuddy-asset-scans-development-2)
    must reach the scan consumer instead of being acked as malformed
    communication envelopes, and the cron must run the scan re-dispatcher."""
    source = (PROJECT_ROOT / "src" / "entry.py").read_text(encoding="utf-8")
    assert 'str(batch.queue).startswith("sessionbuddy-asset-scans")' in source
    assert '== "sessionbuddy-asset-scans"' not in source
    assert "dispatch_stuck_asset_scans(" in source

"""Private asset download and replay-safe scanning integration boundary."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Protocol

from sessionbuddy.platform.auth.tokens import generate_token
from sessionbuddy.platform.db.d1 import (
    D1Database,
    execute_batch,
    result_rows,
    row_mapping,
    to_python,
)
from sessionbuddy.platform.db.types import new_id


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetAccessScope:
    organization_id: str
    event_id: str
    actor_user_id: str
    event_speaker_id: str | None = None
    organizer_access: bool = False


@dataclass(frozen=True, slots=True)
class PrivateAsset:
    """Internal value. Object keys must never enter API response models or logs."""

    version_id: str
    object_key: str
    filename: str
    content_type: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class DownloadGrant:
    token: str
    expires_at_ms: int


@dataclass(frozen=True, slots=True)
class DownloadBody:
    body: Any
    filename: str
    content_type: str
    byte_size: int


class PrivateBucket(Protocol):
    async def get(self, key: str) -> Any | None: ...


class AssetRepository:
    def __init__(self, db: D1Database) -> None:
        self._db = db

    async def clean_version_for_scope(
        self, scope: AssetAccessScope, asset_id: str, version_id: str | None = None
    ) -> PrivateAsset | None:
        values: list[object] = [scope.organization_id, scope.event_id, asset_id, version_id]
        query = """SELECT v.id, v.object_key, v.original_filename, v.content_type,
                          v.byte_size
                   FROM speaker_assets a
                   JOIN speaker_asset_versions v
                     ON v.organization_id = a.organization_id
                    AND v.event_id = a.event_id AND v.asset_id = a.id
                   JOIN event_speakers es
                     ON es.organization_id = a.organization_id
                    AND es.event_id = a.event_id AND es.id = a.event_speaker_id
                   JOIN people p
                     ON p.organization_id = es.organization_id AND p.id = es.person_id
                   WHERE a.organization_id = ?1 AND a.event_id = ?2 AND a.id = ?3
                     AND v.scan_state IN ('clean','superseded')
                     AND ((?4 IS NULL AND v.is_current=1) OR v.id=?4)"""
        if not scope.organizer_access:
            if scope.event_speaker_id is None:
                return None
            query = """SELECT v.id, v.object_key, v.original_filename, v.content_type,
                              v.byte_size
                       FROM speaker_assets a
                       JOIN speaker_asset_versions v
                         ON v.organization_id = a.organization_id
                        AND v.event_id = a.event_id AND v.asset_id = a.id
                       JOIN event_speakers es
                         ON es.organization_id = a.organization_id
                        AND es.event_id = a.event_id AND es.id = a.event_speaker_id
                       JOIN people p
                         ON p.organization_id = es.organization_id AND p.id = es.person_id
                       WHERE a.organization_id = ?1 AND a.event_id = ?2 AND a.id = ?3
                         AND v.scan_state IN ('clean','superseded')
                         AND ((?4 IS NULL AND v.is_current=1) OR v.id=?4)
                         AND a.event_speaker_id = ?5 AND p.user_id = ?6"""
            values.extend((scope.event_speaker_id, scope.actor_user_id))
        row = row_mapping(await self._db.prepare(query).bind(*values).first())
        if row is None:
            return None
        return PrivateAsset(
            version_id=str(row["id"]),
            object_key=str(row["object_key"]),
            filename=str(row["original_filename"]),
            content_type=str(row["content_type"]),
            byte_size=int(row["byte_size"]),
        )

    async def create_download_grant(
        self,
        scope: AssetAccessScope,
        asset_id: str,
        *,
        now_ms: int,
        version_id: str | None = None,
        ttl_ms: int = 60_000,
    ) -> DownloadGrant | None:
        if not 1_000 <= ttl_ms <= 300_000:
            raise ValueError("download grant TTL must be between 1 and 300 seconds")
        asset = await self.clean_version_for_scope(scope, asset_id, version_id)
        if asset is None:
            return None
        token = generate_token()
        await (
            self._db.prepare(
                """INSERT INTO asset_download_grants
               (id, organization_id, event_id, principal_user_id, asset_version_id,
                purpose, token_hash, expires_at_ms, created_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)"""
            )
            .bind(
                new_id(),
                scope.organization_id,
                scope.event_id,
                scope.actor_user_id,
                asset.version_id,
                "admin_download" if scope.organizer_access else "speaker_download",
                sha256(token.encode()).digest(),
                now_ms + ttl_ms,
                now_ms,
            )
            .run()
        )
        return DownloadGrant(token=token, expires_at_ms=now_ms + ttl_ms)

    async def consume_download_grant(
        self,
        bucket: PrivateBucket,
        *,
        actor_user_id: str,
        token: str,
        now_ms: int,
    ) -> DownloadBody | None:
        row = row_mapping(
            await self._db.prepare(
                """UPDATE asset_download_grants AS grant_row
                   SET consumed_at_ms = ?1
                   WHERE token_hash = ?2 AND principal_user_id = ?3
                     AND consumed_at_ms IS NULL AND expires_at_ms > ?1
                     AND EXISTS (
                       SELECT 1 FROM speaker_asset_versions version_row
                       WHERE version_row.organization_id = grant_row.organization_id
                         AND version_row.event_id = grant_row.event_id
                         AND version_row.id = grant_row.asset_version_id
                         AND version_row.scan_state IN ('clean','superseded')
                     )
                   RETURNING organization_id, event_id, asset_version_id"""
            )
            .bind(now_ms, sha256(token.encode()).digest(), actor_user_id)
            .first()
        )
        if row is None:
            return None
        asset = row_mapping(
            await self._db.prepare(
                """SELECT object_key, original_filename, content_type, byte_size
                   FROM speaker_asset_versions
                   WHERE organization_id = ?1 AND event_id = ?2 AND id = ?3
                     AND scan_state IN ('clean','superseded')"""
            )
            .bind(row["organization_id"], row["event_id"], row["asset_version_id"])
            .first()
        )
        if asset is None:
            return None
        stored = await bucket.get(str(asset["object_key"]))
        if stored is None:
            return None
        return DownloadBody(
            body=stored,
            filename=str(asset["original_filename"]),
            content_type=str(asset["content_type"]),
            byte_size=int(asset["byte_size"]),
        )


@dataclass(frozen=True, slots=True)
class ScanJob:
    schema_version: Literal[1]
    organization_id: str
    event_id: str
    asset_version_id: str
    generation: int
    checksum_sha256: bytes
    job_id: str

    def to_message(self) -> dict[str, object]:
        """Return the complete queue contract without private object locations."""
        return {
            "schema_version": self.schema_version,
            "organization_id": self.organization_id,
            "event_id": self.event_id,
            "asset_version_id": self.asset_version_id,
            "generation": self.generation,
            "checksum_sha256": self.checksum_sha256.hex(),
            "job_id": self.job_id,
        }

    @classmethod
    def decode(cls, value: str | bytes | Mapping[str, object]) -> "ScanJob":
        raw: object = json.loads(value) if isinstance(value, (str, bytes)) else value
        if not isinstance(raw, Mapping):
            raise ValueError("invalid scan job")
        expected = {
            "schema_version",
            "organization_id",
            "event_id",
            "asset_version_id",
            "generation",
            "checksum_sha256",
            "job_id",
        }
        if set(raw) != expected or raw.get("schema_version") != 1:
            raise ValueError("invalid scan job contract")
        strings = ("organization_id", "event_id", "asset_version_id", "job_id")
        if any(
            not isinstance(raw.get(key), str) or not 1 <= len(str(raw[key])) <= 200
            for key in strings
        ):
            raise ValueError("invalid scan job identifier")
        generation = raw.get("generation")
        checksum = raw.get("checksum_sha256")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ValueError("invalid scan generation")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise ValueError("invalid scan checksum")
        try:
            checksum_bytes = bytes.fromhex(checksum)
        except ValueError as exc:
            raise ValueError("invalid scan checksum") from exc
        return cls(
            1,
            *(str(raw[key]) for key in strings[:3]),
            generation,
            checksum_bytes,
            str(raw["job_id"]),
        )


@dataclass(frozen=True, slots=True)
class ScanResult:
    provider_event_id: str
    verdict: Literal["clean", "malicious", "error"]
    engine: str
    signature_code: str | None = None


class MalwareScanner(Protocol):
    async def scan(self, body: Any, *, job: ScanJob) -> ScanResult: ...


@dataclass(frozen=True, slots=True)
class ScanDisposition:
    ack: bool
    reason: str


# How long a 'scanning' claim confers exclusive ownership. A consumer that
# crashed or hung past this window loses the row to a stale takeover (by a
# redelivered message or the scheduled re-dispatcher).
SCANNING_RECOVERY_AFTER_MS = 10 * 60 * 1000


async def consume_scan_job(
    db: D1Database,
    bucket: PrivateBucket,
    scanner: MalwareScanner,
    payload: str | bytes | Mapping[str, object],
    *,
    now_ms: int,
) -> ScanDisposition:
    """Fail closed: malformed/stale jobs ack safely; infrastructure failures retry.

    Ownership is strict end to end: the claim is a compare-and-swap on the
    state row, and every terminal write is additionally gated on the claim's
    own timestamp token, so an owner that hung past the recovery window and
    lost its claim to a takeover cannot land any terminal state transition --
    including its scan event receipt, which shares the winner's single
    per-version slot and would otherwise leave the stored verdict contradicting
    the state it is supposed to explain.
    """
    try:
        job = ScanJob.decode(payload)
    except (ValueError, TypeError, json.JSONDecodeError):
        return ScanDisposition(ack=True, reason="invalid_contract")
    row = row_mapping(
        await db.prepare(
            """SELECT object_key, scan_state FROM speaker_asset_versions
               WHERE organization_id=?1 AND event_id=?2 AND id=?3 AND generation=?4
                 AND checksum_sha256=?5 AND scan_state IN ('uploaded','scanning')"""
        )
        .bind(
            job.organization_id,
            job.event_id,
            job.asset_version_id,
            job.generation,
            job.checksum_sha256,
        )
        .first()
    )
    if row is None:
        return await _consume_staged_scan_job(db, bucket, scanner, job, now_ms=now_ms)

    async def release_claim() -> None:
        """Return the row to 'uploaded' so a queue redelivery retries at once.

        The schema couples columns to states — 'uploaded' requires
        scan_started_at_ms IS NULL — so the release clears the marker rather
        than keeping it. Guarding on our own scan_started_at_ms token means a
        takeover's fresher claim is never rolled back by a stale owner."""
        await (
            db.prepare(
                """UPDATE speaker_asset_versions
               SET scan_state='uploaded', scan_started_at_ms=NULL
               WHERE organization_id=?1 AND event_id=?2 AND id=?3 AND generation=?4
                 AND checksum_sha256=?5 AND scan_state='scanning'
                 AND scan_started_at_ms=?6"""
            )
            .bind(
                job.organization_id,
                job.event_id,
                job.asset_version_id,
                job.generation,
                job.checksum_sha256,
                now_ms,
            )
            .run()
        )

    try:
        # Atomic ownership: claim a fresh upload, or take over a claim whose
        # owner went silent past the recovery window. A duplicate observing a
        # fresh 'scanning' claim acks instead of scanning concurrently.
        claimed = row_mapping(
            await db.prepare(
                """UPDATE speaker_asset_versions SET scan_state='scanning', scan_started_at_ms=?1
               WHERE organization_id=?2 AND event_id=?3 AND id=?4 AND generation=?5
                 AND checksum_sha256=?6 AND (
                   scan_state='uploaded'
                   OR (scan_state='scanning' AND scan_started_at_ms<=?7)
                 )
               RETURNING id"""
            )
            .bind(
                now_ms,
                job.organization_id,
                job.event_id,
                job.asset_version_id,
                job.generation,
                job.checksum_sha256,
                now_ms - SCANNING_RECOVERY_AFTER_MS,
            )
            .first()
        )
        if claimed is None:
            return ScanDisposition(ack=True, reason="already_claimed")
        stored = await bucket.get(str(row["object_key"]))
        if stored is None:
            await release_claim()
            return ScanDisposition(ack=False, reason="object_unavailable")
        result = await scanner.scan(stored, job=job)
        if result.verdict == "error":
            # A scanner infrastructure failure is not a malware verdict; the
            # rejection branch below must never quarantine-reject on it. The
            # claim is deliberately HELD: 'scanning' with our
            # scan_started_at_ms is the schema's own last-attempt marker, so
            # the redelivered message acks as already_claimed and the
            # scheduled re-dispatcher retries via stale takeover — spacing
            # attempts at the recovery window instead of hammering a failing
            # scanner on every redelivery.
            return ScanDisposition(ack=False, reason="scanner_error")
        event_id = new_id()
        statements = [
            # Gated on the claim token, exactly like the terminal state writes
            # below. asset_scan_events is UNIQUE on
            # (engine, job_id, asset_version_id, generation, checksum_sha256)
            # and job_id is the asset version id, so an engine gets exactly one
            # receipt slot per version: every attempt competes for the same row.
            # Ungated, a hung owner whose state write no-ops could still take
            # that slot first, and the takeover's receipt would then be
            # swallowed by ON CONFLICT DO NOTHING while its terminal state
            # landed -- persisted state 'clean' explained by a lone 'malicious'
            # receipt, or the reverse. Gating makes the receipt and the state it
            # explains stand or fall together, as one claim's atomic batch.
            #
            # The cost is deliberate: a losing attempt's provider verdict is not
            # retained. Keeping every attempt needs an attempt-history table
            # without this job-level uniqueness, not a second row here.
            db.prepare(
                """INSERT INTO asset_scan_events
                   (id,organization_id,event_id,asset_version_id,generation,checksum_sha256,
                    provider_event_id,job_id,verdict,engine,signature_code,received_at_ms)
                   SELECT ?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12
                   WHERE EXISTS (
                     SELECT 1 FROM speaker_asset_versions
                     WHERE organization_id=?2 AND event_id=?3 AND id=?4 AND generation=?5
                       AND checksum_sha256=?6 AND scan_state='scanning'
                       AND scan_started_at_ms=?12
                   )
                   ON CONFLICT DO NOTHING"""
            ).bind(
                event_id,
                job.organization_id,
                job.event_id,
                job.asset_version_id,
                job.generation,
                job.checksum_sha256,
                result.provider_event_id,
                job.job_id,
                result.verdict,
                result.engine,
                result.signature_code,
                now_ms,
            )
        ]
        if result.verdict == "clean":
            statements.extend(_clean_result_statements(db, job, now_ms))
        else:
            statements.append(
                db.prepare(
                    # Requires the matching receipt for the same reason the clean path
                    # does. A rejection written against a stored 'clean' receipt is the
                    # same divergence as a promote against a stored 'malicious' one --
                    # it just fails in the quiet direction, so nobody reports it. Without
                    # the receipt the row stays 'scanning' and is re-dispatched, which
                    # keeps a malicious upload quarantined either way.
                    """UPDATE speaker_asset_versions SET scan_state='rejected', is_current=0,
                          scanned_at_ms=?1, scan_result_code=?2
                   WHERE organization_id=?3 AND event_id=?4 AND id=?5 AND generation=?6
                     AND checksum_sha256=?7 AND scan_state='scanning'
                     AND scan_started_at_ms=?1 AND EXISTS (
                       SELECT 1 FROM asset_scan_events receipt
                       WHERE receipt.organization_id=speaker_asset_versions.organization_id
                         AND receipt.event_id=speaker_asset_versions.event_id
                         AND receipt.asset_version_id=speaker_asset_versions.id
                         AND receipt.generation=speaker_asset_versions.generation
                         AND receipt.checksum_sha256=speaker_asset_versions.checksum_sha256
                         AND receipt.verdict='malicious'
                     )"""
                ).bind(
                    now_ms,
                    result.signature_code or result.verdict,
                    job.organization_id,
                    job.event_id,
                    job.asset_version_id,
                    job.generation,
                    job.checksum_sha256,
                )
            )
        await execute_batch(db, statements)
        return ScanDisposition(ack=True, reason=result.verdict)
    except Exception:
        # Claim (if committed) is held; the re-dispatcher retries after the
        # recovery window. If the claim itself failed, the row is untouched
        # and the queue redelivery retries in full.
        return ScanDisposition(ack=False, reason="infrastructure_failure")


@dataclass(frozen=True, slots=True)
class ScanDispatchResult:
    published: int
    publish_failures: int


def _checksum_bytes(value: object) -> bytes:
    converted = to_python(value)
    return converted if isinstance(converted, bytes) else bytes(converted)


async def dispatch_stuck_asset_scans(
    db: D1Database,
    queue,
    now_ms: int,
    *,
    uploaded_after_ms: int = 2 * 60 * 1000,
    scanning_after_ms: int = SCANNING_RECOVERY_AFTER_MS,
    give_up_after_ms: int = 7 * 24 * 60 * 60 * 1000,
    limit: int = 50,
) -> ScanDispatchResult:
    """Republish scan jobs for committed uploads whose envelope never arrived.

    Queue publication after a commit is best-effort everywhere (a completion
    request may swallow a transient send failure, a process can crash between
    commit and publish, and a poisoned message can exhaust queue retries), so
    this scheduled pass is the durable guarantee that a quarantined upload is
    eventually scanned. Consumers take exclusive ownership via a compare-and-
    swap claim, so a duplicate envelope for a fresh claim acks without a
    second concurrent scan. Retry spacing lives in the state machine itself:
    a failed scan attempt HOLDS its claim, so the row sits in 'scanning' with
    scan_started_at_ms as the last-attempt marker and is republished only
    after scanning_after_ms via stale takeover. A missing object releases
    back to 'uploaded' (whose schema invariant clears the marker) for cheap
    uploaded_after_ms-cadence retries that never reach the scanner. Rows
    older than give_up_after_ms stop being retried so a persistently failing
    scanner cannot burn budget forever (the row stays fail-closed in
    quarantine for operators).
    """
    if not 1 <= limit <= 500:
        raise ValueError("scan dispatch limit must be between 1 and 500")
    if uploaded_after_ms < 60_000 or scanning_after_ms < 60_000:
        raise ValueError("invalid scan dispatch retry policy")
    if give_up_after_ms <= scanning_after_ms:
        raise ValueError("invalid scan dispatch retry policy")
    uploaded_before_ms = now_ms - uploaded_after_ms
    scanning_before_ms = now_ms - scanning_after_ms
    give_up_before_ms = now_ms - give_up_after_ms
    jobs: list[ScanJob] = []
    speaker_rows = result_rows(
        await db.prepare(
            """SELECT organization_id,event_id,id,generation,checksum_sha256
               FROM speaker_asset_versions
               WHERE checksum_sha256 IS NOT NULL AND uploaded_at_ms IS NOT NULL
                 AND uploaded_at_ms>?4 AND (
                 (scan_state='uploaded' AND uploaded_at_ms<=?1)
                 OR (scan_state='scanning' AND scan_started_at_ms IS NOT NULL
                    AND scan_started_at_ms<=?2)
               )
               ORDER BY COALESCE(scan_started_at_ms,uploaded_at_ms),id LIMIT ?3"""
        )
        .bind(uploaded_before_ms, scanning_before_ms, limit, give_up_before_ms)
        .all()
    )
    for row in speaker_rows:
        jobs.append(
            ScanJob(
                schema_version=1,
                organization_id=str(row["organization_id"]),
                event_id=str(row["event_id"]),
                asset_version_id=str(row["id"]),
                generation=int(row["generation"]),
                checksum_sha256=_checksum_bytes(row["checksum_sha256"]),
                job_id=str(row["id"]),
            )
        )
    # A fresh CFP enqueue claim bumps updated_at_ms, so the staleness cut
    # inherently skips rows the request path just published.
    staged_rows = []
    if len(jobs) < limit:
        staged_rows = result_rows(
            await db.prepare(
                """SELECT id,organization_id,event_id,checksum_sha256
                   FROM cfp_staged_assets
                   WHERE expires_at_ms>?4 AND (
                     (status='uploaded' AND updated_at_ms<=?1)
                     OR (status='scanning' AND updated_at_ms<=?2)
                   )
                   ORDER BY updated_at_ms,id LIMIT ?3"""
            )
            .bind(uploaded_before_ms, scanning_before_ms, limit - len(jobs), now_ms)
            .all()
        )
    for row in staged_rows:
        jobs.append(
            ScanJob(
                schema_version=1,
                organization_id=str(row["organization_id"]),
                event_id=str(row["event_id"]),
                asset_version_id=str(row["id"]),
                generation=1,
                checksum_sha256=_checksum_bytes(row["checksum_sha256"]),
                job_id=str(row["id"]),
            )
        )
    published = publish_failures = 0
    for job in jobs:
        try:
            await queue.send(job.to_message())
            published += 1
        except Exception:
            publish_failures += 1
    return ScanDispatchResult(published=published, publish_failures=publish_failures)


async def _consume_staged_scan_job(
    db: D1Database,
    bucket: PrivateBucket,
    scanner: MalwareScanner,
    job: ScanJob,
    *,
    now_ms: int,
) -> ScanDisposition:
    """Scan a staged CFP upload (no speaker graph exists yet for these rows)."""
    staged = row_mapping(
        await db.prepare(
            """SELECT object_key, status FROM cfp_staged_assets
               WHERE id=?1 AND checksum_sha256=?2 AND organization_id=?3 AND event_id=?4
                 AND status IN ('uploaded','scanning')"""
        )
        .bind(job.asset_version_id, job.checksum_sha256, job.organization_id, job.event_id)
        .first()
    )
    if staged is None:
        return ScanDisposition(ack=True, reason="stale_or_complete")

    async def release_claim() -> None:
        """Return a failed attempt to 'uploaded' so a queue redelivery retries
        immediately; the updated_at_ms token protects a fresher takeover."""
        await (
            db.prepare(
                """UPDATE cfp_staged_assets SET status='uploaded'
               WHERE id=?1 AND checksum_sha256=?2 AND status='scanning'
                 AND updated_at_ms=?3"""
            )
            .bind(job.asset_version_id, job.checksum_sha256, now_ms)
            .run()
        )

    try:
        claimed = row_mapping(
            await db.prepare(
                """UPDATE cfp_staged_assets SET status='scanning', updated_at_ms=?1
               WHERE id=?2 AND checksum_sha256=?3 AND (
                 status='uploaded'
                 OR (status='scanning' AND updated_at_ms<=?4)
               )
               RETURNING id"""
            )
            .bind(
                now_ms,
                job.asset_version_id,
                job.checksum_sha256,
                now_ms - SCANNING_RECOVERY_AFTER_MS,
            )
            .first()
        )
        if claimed is None:
            return ScanDisposition(ack=True, reason="already_claimed")
        stored = await bucket.get(str(staged["object_key"]))
        if stored is None:
            await release_claim()
            return ScanDisposition(ack=False, reason="object_unavailable")
        result = await scanner.scan(stored, job=job)
        if result.verdict == "error":
            # Held claim: redelivery acks, the re-dispatcher retries after the
            # recovery window (see the speaker path for the rationale).
            return ScanDisposition(ack=False, reason="scanner_error")
        if result.verdict == "clean":
            status, code = "staged", "clean"
        else:
            status, code = "rejected", result.signature_code or result.verdict
        await (
            db.prepare(
                """UPDATE cfp_staged_assets SET status=?1, scan_result_code=?2, updated_at_ms=?3
               WHERE id=?4 AND checksum_sha256=?5 AND status='scanning'
                 AND updated_at_ms=?3"""
            )
            .bind(status, code, now_ms, job.asset_version_id, job.checksum_sha256)
            .run()
        )
        return ScanDisposition(ack=True, reason=result.verdict)
    except Exception:
        return ScanDisposition(ack=False, reason="infrastructure_failure")


def _clean_result_statements(db: D1Database, job: ScanJob, now_ms: int) -> list[Any]:
    """Terminal statements for a clean verdict, all gated on the caller's
    claim token (scan_started_at_ms == now_ms). A hung owner that outlives
    its claim must not demote the asset's current version and then fail the
    promote, stranding the asset with no current version until the takeover
    finishes — so the supersede step checks the token too, making the whole
    batch a no-op for anyone but the claim's owner.

    Both steps additionally require this version's own 'clean' receipt, which
    the caller inserts earlier in the same batch under the same token. That
    makes 'clean' unwritable without a stored receipt saying so, matching the
    guard the inline completion path in the router already carries. It has to
    be on BOTH steps: a receipt guard on the promote alone would let a
    supersede land and its promote no-op, which is the stranding this
    docstring exists to describe."""
    return [
        db.prepare(
            """UPDATE speaker_asset_versions AS previous SET scan_state='superseded',
                      is_current=0
                 WHERE previous.asset_id=(SELECT candidate.asset_id
                   FROM speaker_asset_versions candidate WHERE candidate.id=?1
                     AND candidate.scan_state='scanning'
                     AND candidate.scan_started_at_ms=?2 AND EXISTS (
                     SELECT 1 FROM asset_scan_events receipt
                     WHERE receipt.organization_id=candidate.organization_id
                       AND receipt.event_id=candidate.event_id
                       AND receipt.asset_version_id=candidate.id
                       AND receipt.generation=candidate.generation
                       AND receipt.checksum_sha256=candidate.checksum_sha256
                       AND receipt.verdict='clean'
                   ) AND NOT EXISTS (
                     SELECT 1 FROM speaker_asset_versions newer
                     WHERE newer.asset_id=candidate.asset_id
                       AND newer.generation>candidate.generation
                   ))
                   AND previous.id<>?1 AND previous.is_current=1"""
        ).bind(job.asset_version_id, now_ms),
        db.prepare(
            """UPDATE speaker_asset_versions AS candidate
                 SET scan_state='clean', is_current=1, scanned_at_ms=?1,
                     scan_result_code='clean'
                 WHERE organization_id=?2 AND event_id=?3 AND id=?4 AND generation=?5
                   AND checksum_sha256=?6 AND scan_state='scanning'
                   AND scan_started_at_ms=?1 AND EXISTS (
                     SELECT 1 FROM asset_scan_events receipt
                     WHERE receipt.organization_id=candidate.organization_id
                       AND receipt.event_id=candidate.event_id
                       AND receipt.asset_version_id=candidate.id
                       AND receipt.generation=candidate.generation
                       AND receipt.checksum_sha256=candidate.checksum_sha256
                       AND receipt.verdict='clean'
                   ) AND NOT EXISTS (
                     SELECT 1 FROM speaker_asset_versions newer
                     WHERE newer.asset_id=candidate.asset_id
                       AND newer.generation>candidate.generation
                   )"""
        ).bind(
            now_ms,
            job.organization_id,
            job.event_id,
            job.asset_version_id,
            job.generation,
            job.checksum_sha256,
        ),
        # Production parity with the inline local path: a clean, current version
        # completes the matching onboarding task in the same atomic batch.
        db.prepare(
            """UPDATE speaker_tasks SET state='completed', completed_at_ms=?1,
                      version=version+1, updated_at_ms=?1
                 WHERE state='open' AND organization_id=?2 AND event_id=?3
                   AND EXISTS (
                     SELECT 1 FROM speaker_asset_versions v
                     JOIN speaker_assets a ON a.id=v.asset_id
                     WHERE v.id=?4 AND v.is_current=1 AND v.scan_state='clean'
                       AND a.event_speaker_id=speaker_tasks.event_speaker_id
                       AND a.kind=speaker_tasks.task_type
                       AND COALESCE(a.submission_id,'')
                           = COALESCE(speaker_tasks.submission_id,'')
                   )"""
        ).bind(now_ms, job.organization_id, job.event_id, job.asset_version_id),
    ]

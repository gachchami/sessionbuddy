"""Private asset download and replay-safe scanning integration boundary."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal, Protocol

from sessionbuddy.platform.auth.tokens import generate_token
from sessionbuddy.platform.db.d1 import D1Database, execute_batch, row_mapping
from sessionbuddy.platform.db.types import new_id


@dataclass(frozen=True, slots=True, kw_only=True)
class AssetAccessScope:
    organization_id: str
    event_id: str
    actor_user_id: str
    event_speaker_id: str | None = None
    event_admin: bool = False


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
        if not scope.event_admin:
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
                "admin_download" if scope.event_admin else "speaker_download",
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


async def consume_scan_job(
    db: D1Database,
    bucket: PrivateBucket,
    scanner: MalwareScanner,
    payload: str | bytes | Mapping[str, object],
    *,
    now_ms: int,
) -> ScanDisposition:
    """Fail closed: malformed/stale jobs ack safely; infrastructure failures retry."""
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
    try:
        await (
            db.prepare(
                """UPDATE speaker_asset_versions SET scan_state='scanning', scan_started_at_ms=?1
               WHERE organization_id=?2 AND event_id=?3 AND id=?4 AND generation=?5
                 AND checksum_sha256=?6 AND scan_state='uploaded'"""
            )
            .bind(
                now_ms,
                job.organization_id,
                job.event_id,
                job.asset_version_id,
                job.generation,
                job.checksum_sha256,
            )
            .run()
        )
        stored = await bucket.get(str(row["object_key"]))
        if stored is None:
            return ScanDisposition(ack=False, reason="object_unavailable")
        result = await scanner.scan(stored, job=job)
        event_id = new_id()
        statements = [
            db.prepare(
                """INSERT INTO asset_scan_events
                   (id,organization_id,event_id,asset_version_id,generation,checksum_sha256,
                    provider_event_id,job_id,verdict,engine,signature_code,received_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12)
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
                    """UPDATE speaker_asset_versions SET scan_state='rejected', is_current=0,
                          scanned_at_ms=?1, scan_result_code=?2
                   WHERE organization_id=?3 AND event_id=?4 AND id=?5 AND generation=?6
                     AND checksum_sha256=?7 AND scan_state='scanning'"""
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
        return ScanDisposition(ack=False, reason="infrastructure_failure")


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
    try:
        await (
            db.prepare(
                """UPDATE cfp_staged_assets SET status='scanning', updated_at_ms=?1
               WHERE id=?2 AND checksum_sha256=?3 AND status='uploaded'"""
            )
            .bind(now_ms, job.asset_version_id, job.checksum_sha256)
            .run()
        )
        stored = await bucket.get(str(staged["object_key"]))
        if stored is None:
            return ScanDisposition(ack=False, reason="object_unavailable")
        result = await scanner.scan(stored, job=job)
        if result.verdict == "error":
            return ScanDisposition(ack=False, reason="scanner_error")
        if result.verdict == "clean":
            status, code = "staged", "clean"
        else:
            status, code = "rejected", result.signature_code or result.verdict
        await (
            db.prepare(
                """UPDATE cfp_staged_assets SET status=?1, scan_result_code=?2, updated_at_ms=?3
               WHERE id=?4 AND checksum_sha256=?5 AND status='scanning'"""
            )
            .bind(status, code, now_ms, job.asset_version_id, job.checksum_sha256)
            .run()
        )
        return ScanDisposition(ack=True, reason=result.verdict)
    except Exception:
        return ScanDisposition(ack=False, reason="infrastructure_failure")


def _clean_result_statements(db: D1Database, job: ScanJob, now_ms: int) -> list[Any]:
    return [
        db.prepare(
            """UPDATE speaker_asset_versions AS previous SET scan_state='superseded',
                      is_current=0
                 WHERE previous.asset_id=(SELECT candidate.asset_id
                   FROM speaker_asset_versions candidate WHERE candidate.id=?1 AND NOT EXISTS (
                     SELECT 1 FROM speaker_asset_versions newer
                     WHERE newer.asset_id=candidate.asset_id
                       AND newer.generation>candidate.generation
                   ))
                   AND previous.id<>?1 AND previous.is_current=1"""
        ).bind(job.asset_version_id),
        db.prepare(
            """UPDATE speaker_asset_versions AS candidate
                 SET scan_state='clean', is_current=1, scanned_at_ms=?1,
                     scan_result_code='clean'
                 WHERE organization_id=?2 AND event_id=?3 AND id=?4 AND generation=?5
                   AND checksum_sha256=?6 AND scan_state='scanning' AND NOT EXISTS (
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

"""Scheduled cleanup for abandoned direct-to-R2 speaker uploads."""

from dataclasses import dataclass

from sessionbuddy.platform.db.d1 import result_rows


@dataclass(frozen=True, slots=True)
class SpeakerUploadPurgeResult:
    deleted_rows: int
    deleted_objects: int
    delete_failures: int


async def purge_expired_speaker_uploads(
    db, bucket, now_ms: int, *, limit: int = 50
) -> SpeakerUploadPurgeResult:
    """Remove expired, never-completed speaker uploads and empty asset slots.

    The object is deleted before its database rows. If R2 is unavailable the
    rows remain intact, allowing the next scheduled run to retry. Completed or
    consumed intents and every version beyond ``pending_upload`` are excluded.
    """
    if not 1 <= limit <= 500:
        raise ValueError("speaker upload purge limit must be between 1 and 500")
    rows = result_rows(
        await db.prepare(
            """SELECT ui.id AS intent_id,av.id AS version_id,av.asset_id,av.object_key
               FROM upload_intents ui
               JOIN speaker_asset_versions av
                 ON av.organization_id=ui.organization_id
                AND av.event_id=ui.event_id AND av.id=ui.asset_version_id
               WHERE ui.consumed_at_ms IS NULL AND ui.expires_at_ms<=?1
                 AND av.scan_state='pending_upload'
               ORDER BY ui.expires_at_ms,ui.id LIMIT ?2"""
        )
        .bind(now_ms, limit)
        .all()
    )
    deleted_rows = 0
    deleted_objects = 0
    delete_failures = 0
    for row in rows:
        try:
            await bucket.delete(str(row["object_key"]))
        except Exception:
            delete_failures += 1
            continue
        deleted_objects += 1
        intent_id = str(row["intent_id"])
        version_id = str(row["version_id"])
        asset_id = str(row["asset_id"])
        await db.prepare(
            """DELETE FROM idempotency_records
               WHERE response_resource_type='upload_intent'
                 AND response_resource_id=?1"""
        ).bind(intent_id).run()
        await db.prepare(
            """DELETE FROM upload_intents
               WHERE id=?1 AND consumed_at_ms IS NULL AND expires_at_ms<=?2"""
        ).bind(intent_id, now_ms).run()
        await db.prepare(
            """DELETE FROM speaker_asset_versions
               WHERE id=?1 AND scan_state='pending_upload'
                 AND NOT EXISTS (
                   SELECT 1 FROM upload_intents ui WHERE ui.asset_version_id=?1
                 )"""
        ).bind(version_id).run()
        await db.prepare(
            """DELETE FROM speaker_assets WHERE id=?1
               AND NOT EXISTS (
                 SELECT 1 FROM speaker_asset_versions av WHERE av.asset_id=?1
               )"""
        ).bind(asset_id).run()
        deleted_rows += 1
    return SpeakerUploadPurgeResult(
        deleted_rows=deleted_rows,
        deleted_objects=deleted_objects,
        delete_failures=delete_failures,
    )

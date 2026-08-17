"""Scheduled cleanup for abandoned event-branding uploads."""

from dataclasses import dataclass

from sessionbuddy.platform.db.d1 import execute_batch, result_rows, statement_changes

PENDING_BRANDING_RETENTION_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class EventBrandingPurgeResult:
    deleted_rows: int
    deleted_objects: int
    delete_failures: int


async def purge_pending_branding_assets(
    db, bucket, now_ms: int, *, limit: int = 200
) -> EventBrandingPurgeResult:
    """Delete pending branding objects older than 24 hours, tenant by tenant.

    The per-organization query follows ``idx_event_branding_assets_pending``.
    The guarded database row is deleted first so a concurrent attachment can
    win without leaving an event pointed at a deleted object. R2 failures can
    leave an inaccessible object for later storage reconciliation, but never a
    broken attached asset. Attached and retired assets are never selected.
    """
    if not 1 <= limit <= 500:
        raise ValueError("event branding purge limit must be between 1 and 500")
    cutoff_ms = now_ms - PENDING_BRANDING_RETENTION_MS
    organizations = result_rows(
        await db.prepare("SELECT id FROM organizations ORDER BY id").all()
    )
    deleted_rows = 0
    deleted_objects = 0
    delete_failures = 0
    for organization in organizations:
        rows = result_rows(
            await db.prepare(
                """SELECT id,object_key FROM event_branding_assets
                   WHERE organization_id=?1 AND status='pending'
                     AND created_at_ms<=?2 AND event_id IS NULL
                   ORDER BY created_at_ms,id LIMIT ?3"""
            )
            .bind(str(organization["id"]), cutoff_ms, limit)
            .all()
        )
        for row in rows:
            delete_results = await execute_batch(
                db,
                [
                    db.prepare(
                        """DELETE FROM event_branding_assets
                           WHERE id=?1 AND status='pending' AND event_id IS NULL
                             AND created_at_ms<=?2"""
                    ).bind(str(row["id"]), cutoff_ms)
                ],
            )
            if statement_changes(delete_results, 0) != 1:
                continue
            deleted_rows += 1
            try:
                await bucket.delete(str(row["object_key"]))
            except Exception:
                delete_failures += 1
                continue
            deleted_objects += 1
    return EventBrandingPurgeResult(
        deleted_rows=deleted_rows,
        deleted_objects=deleted_objects,
        delete_failures=delete_failures,
    )

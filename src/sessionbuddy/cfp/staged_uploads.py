"""Staged CFP uploads: pre-submission file staging without a speaker graph.

A first-time submitter authenticates (magic link), stages file answers against
the published form, and only a successful create_submission converts the
staged rows into the speaker-asset graph — atomically, in the same command
batch that creates the person/speaker/membership rows. Nothing here ever
creates or reactivates a membership.
"""

import hmac
from base64 import urlsafe_b64encode
from dataclasses import dataclass

from fastapi import HTTPException

from sessionbuddy.platform.auth import generate_token, hash_token
from sessionbuddy.platform.db.d1 import result_rows, row_mapping
from sessionbuddy.platform.db.types import new_id

STAGED_ASSET_RULES = {
    "headshot": ({"image/jpeg", "image/png", "image/webp"}, 5 * 1024 * 1024),
    "supporting_document": ({"application/pdf"}, 20 * 1024 * 1024),
}

# Enforced per (form, user) across live (not yet claimed or expired) rows.
MAX_ACTIVE_STAGED_FILES = 10
MAX_ACTIVE_STAGED_BYTES = 100 * 1024 * 1024

# Claimed rows leave the active quota above, so a submit-and-restage loop could
# grow R2 without bound. This creation quota counts EVERY row created in the
# window — claimed included — per (form, user).
MAX_STAGED_AUTHORIZATIONS_PER_HOUR = 20
STAGED_AUTHORIZATION_WINDOW_MS = 60 * 60 * 1000

STAGED_UPLOAD_TTL_MS = 24 * 60 * 60 * 1000
STAGED_UPLOAD_URL_TTL_MS = 10 * 60 * 1000

STAGED_REFERENCE_PREFIX = "staged:"


def staged_upload_token(secret: bytes, staged_id: str) -> str:
    """Deterministic per-row token so idempotent replays return the same URL."""
    digest = hmac.digest(secret, f"cfp-staged-upload:{staged_id}".encode(), "sha256")
    return urlsafe_b64encode(digest).decode().rstrip("=")


def staged_references(schema: dict[str, object], answers: dict[str, object]) -> list[str]:
    """Staged-row ids referenced by file answers, in field order."""
    references: list[str] = []
    raw_fields = schema.get("fields", [])
    if not isinstance(raw_fields, list):
        return references
    for field in raw_fields:
        if not isinstance(field, dict) or field.get("type") not in {"file", "image"}:
            continue
        value = answers.get(str(field.get("key", "")))
        if isinstance(value, str) and value.startswith(STAGED_REFERENCE_PREFIX):
            references.append(value.removeprefix(STAGED_REFERENCE_PREFIX))
    return references


@dataclass(frozen=True, slots=True)
class StagedClaim:
    statements: list[object]
    answer_rewrites: dict[str, str]  # "staged:<id>" -> "upload:<intent_id>"


async def build_staged_claim(
    db,
    *,
    organization_id: str,
    event_id: str,
    event_speaker_id: str,
    submission_id: str,
    form_id: str,
    user_id: str,
    staged_ids: list[str],
    now: int,
) -> StagedClaim:
    """Statements that attach staged rows to a submission in one batch.

    Converts each staged row into the speaker-asset graph (asset, clean
    version, consumed upload intent) and marks the row claimed. Double-claim
    races abort the whole batch via the UNIQUE(object_key) constraint on
    speaker_asset_versions.
    """
    if not staged_ids:
        return StagedClaim(statements=[], answer_rewrites={})
    rows: list[dict[str, object]] = []
    for staged_id in dict.fromkeys(staged_ids):
        row = row_mapping(
            await db.prepare(
                """SELECT id, kind, object_key, original_filename, content_type,
                          byte_size, checksum_sha256, scan_result_code
                   FROM cfp_staged_assets
                   WHERE id=?1 AND form_id=?2 AND user_id=?3
                     AND status='staged' AND expires_at_ms>?4 LIMIT 1"""
            )
            .bind(staged_id, form_id, user_id, now)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=422)
        rows.append(row)
    statements: list[object] = []
    rewrites: dict[str, str] = {}
    slots: dict[str, tuple[str, int, bool]] = {}  # kind -> (asset_id, next_gen, existed)
    for kind in dict.fromkeys(str(row["kind"]) for row in rows):
        existing = row_mapping(
            await db.prepare(
                """SELECT asset.id AS asset_id,
                          COALESCE(MAX(version.generation), 0) AS max_generation
                   FROM speaker_assets asset
                   LEFT JOIN speaker_asset_versions version ON version.asset_id=asset.id
                   WHERE asset.organization_id=?1 AND asset.event_id=?2
                     AND asset.event_speaker_id=?3
                     AND COALESCE(asset.submission_id,'')=?4
                     AND COALESCE(asset.task_id,'')='' AND asset.kind=?5
                   GROUP BY asset.id LIMIT 1"""
            )
            .bind(organization_id, event_id, event_speaker_id, submission_id, kind)
            .first()
        )
        if existing is None:
            slots[kind] = (new_id(), 1, False)
        else:
            slots[kind] = (str(existing["asset_id"]), int(existing["max_generation"]) + 1, True)
    for kind, (asset_id, _, existed) in slots.items():
        if existed:
            statements.append(
                db.prepare(
                    """UPDATE speaker_asset_versions SET is_current=0, scan_state='superseded'
                       WHERE asset_id=?1 AND is_current=1 AND scan_state='clean'"""
                ).bind(asset_id)
            )
            statements.append(
                db.prepare(
                    "UPDATE speaker_assets SET updated_at_ms=?1 WHERE id=?2"
                ).bind(now, asset_id)
            )
        else:
            statements.append(
                db.prepare(
                    """INSERT INTO speaker_assets
                       (id, organization_id, event_id, event_speaker_id, submission_id,
                        task_id, kind, created_at_ms, updated_at_ms)
                       VALUES (?1, ?2, ?3, ?4, ?5, NULL, ?6, ?7, ?7)"""
                ).bind(
                    asset_id,
                    organization_id,
                    event_id,
                    event_speaker_id,
                    submission_id,
                    kind,
                    now,
                )
            )
    generations = {kind: slot[1] for kind, slot in slots.items()}
    last_row_id_by_kind = {str(row["kind"]): str(row["id"]) for row in rows}
    for row in rows:
        kind = str(row["kind"])
        asset_id = slots[kind][0]
        generation = generations[kind]
        generations[kind] = generation + 1
        version_id, intent_id = new_id(), new_id()
        is_current = 1 if str(row["id"]) == last_row_id_by_kind[kind] else 0
        statements.append(
            db.prepare(
                """INSERT INTO speaker_asset_versions
                   (id, organization_id, event_id, event_speaker_id, asset_id, generation,
                    object_key, original_filename, content_type, byte_size, checksum_sha256,
                    scan_state, is_current, created_at_ms, uploaded_at_ms,
                    scan_started_at_ms, scanned_at_ms, scan_result_code, version_comment)
                   VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11,
                           'clean', ?12, ?13, ?13, ?13, ?13, ?14, ?15)"""
            ).bind(
                version_id,
                organization_id,
                event_id,
                event_speaker_id,
                asset_id,
                generation,
                row["object_key"],
                row["original_filename"],
                row["content_type"],
                row["byte_size"],
                row["checksum_sha256"],
                is_current,
                now,
                str(row["scan_result_code"] or "clean"),
                "Uploaded with the Call for Proposals submission",
            )
        )
        statements.append(
            db.prepare(
                """INSERT INTO upload_intents
                   (id, organization_id, event_id, event_speaker_id, asset_version_id,
                    purpose, token_hash, expected_content_type, expected_byte_size,
                    expected_checksum_sha256, expires_at_ms, consumed_at_ms, created_at_ms)
                   VALUES (?1, ?2, ?3, ?4, ?5, 'create', ?6, ?7, ?8, ?9, ?10, ?11, ?11)"""
            ).bind(
                intent_id,
                organization_id,
                event_id,
                event_speaker_id,
                version_id,
                hash_token(generate_token()),
                row["content_type"],
                row["byte_size"],
                row["checksum_sha256"],
                now + 1,
                now,
            )
        )
        statements.append(
            db.prepare(
                """UPDATE cfp_staged_assets SET status='claimed', claimed_submission_id=?1,
                       claimed_at_ms=?2, updated_at_ms=?2
                   WHERE id=?3 AND form_id=?4 AND user_id=?5 AND status='staged'"""
            ).bind(submission_id, now, row["id"], form_id, user_id)
        )
        rewrites[f"{STAGED_REFERENCE_PREFIX}{row['id']}"] = f"upload:{intent_id}"
    return StagedClaim(statements=statements, answer_rewrites=rewrites)


@dataclass(frozen=True, slots=True)
class StagedPurgeResult:
    deleted_rows: int
    deleted_objects: int
    delete_failures: int


async def purge_expired_staged_assets(
    db, bucket, now_ms: int, *, limit: int = 50
) -> StagedPurgeResult:
    """Delete expired staged rows; abandoned (unclaimed) objects leave R2 too.

    Claimed rows transferred object ownership to speaker_asset_versions, so
    only their bookkeeping row is removed. An R2 delete failure keeps the row
    so the next scheduled run retries.
    """
    rows = result_rows(
        await db.prepare(
            """SELECT id, object_key, status FROM cfp_staged_assets
               WHERE expires_at_ms<=?1 ORDER BY expires_at_ms, id LIMIT ?2"""
        )
        .bind(now_ms, limit)
        .all()
    )
    deleted_objects = 0
    delete_failures = 0
    removable: list[str] = []
    for row in rows:
        if str(row["status"]) != "claimed":
            try:
                await bucket.delete(str(row["object_key"]))
                deleted_objects += 1
            except Exception:
                delete_failures += 1
                continue
        removable.append(str(row["id"]))
    for staged_id in removable:
        await db.prepare("DELETE FROM cfp_staged_assets WHERE id=?1").bind(staged_id).run()
    return StagedPurgeResult(
        deleted_rows=len(removable),
        deleted_objects=deleted_objects,
        delete_failures=delete_failures,
    )

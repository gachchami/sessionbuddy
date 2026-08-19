"""Resolve public track selectors once; unknown selectors never broaden a feed."""

from pydantic import BaseModel, ConfigDict
from starlette.datastructures import QueryParams

from sessionbuddy.platform.db.d1 import row_mapping


class PublicTrackFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str | None = None
    name: str | None = None
    matched: bool


async def resolve_public_track_filter(
    db, organization_id: str, event_id: str, params: QueryParams
) -> PublicTrackFilter | None:
    # Presence matters: an empty/invalid canonical ID must not fall back to a
    # legacy name or silently turn into an unfiltered request.
    key = "track_id" if "track_id" in params else "track" if "track" in params else None
    if key is None:
        return None
    value = params.get(key, "").strip()
    maximum = 128 if key == "track_id" else 200
    if not value or len(value) > maximum or len(params.getlist(key)) != 1:
        return PublicTrackFilter(matched=False)
    row = row_mapping(
        await db.prepare(
            "SELECT id,name FROM event_tracks WHERE organization_id=?1 AND event_id=?2 "
            "AND status='active' "
            "AND ((?4=1 AND id=?3) OR (?4=0 AND name=?3 COLLATE NOCASE)) LIMIT 1"
        )
        .bind(organization_id, event_id, value, int(key == "track_id"))
        .first()
    )
    if row is None:
        return PublicTrackFilter(matched=False)
    return PublicTrackFilter(track_id=str(row["id"]), name=str(row["name"]), matched=True)

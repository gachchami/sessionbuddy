import json

from sessionbuddy.platform.db.d1 import D1Database, PersistenceError, result_rows, row_mapping

from .domain import AgendaConflict, AgendaSlot


class AgendaRepository:
    def __init__(self, db: D1Database) -> None:
        self._db = db

    async def preview_conflicts(self, slot: AgendaSlot) -> list[AgendaConflict]:
        slot.validate()
        rows = result_rows(
            await self._db.prepare(
                """SELECT DISTINCT kind,item_id FROM (
               SELECT 'room' AS kind, other.id AS item_id FROM agenda_items other
               WHERE other.organization_id=?1 AND other.event_id=?2
                 AND other.revision_id=?3 AND other.id<>COALESCE(?10,'')
                 AND other.room_id=?4 AND other.starts_at_ms<?7 AND other.ends_at_ms>?6
               UNION ALL
               SELECT 'track', other.id FROM agenda_items other JOIN event_tracks track
                 ON track.organization_id=other.organization_id
                AND track.event_id=other.event_id AND track.id=other.track_id
               WHERE other.organization_id=?1 AND other.event_id=?2
                 AND other.revision_id=?3 AND other.id<>COALESCE(?10,'')
                 AND ?5 IS NOT NULL AND other.track_id=?5 AND track.is_exclusive=1
                 AND other.starts_at_ms<?7 AND other.ends_at_ms>?6
               UNION ALL
               SELECT 'speaker', other.id FROM agenda_items other
               JOIN agenda_item_speakers speaker ON speaker.revision_id=other.revision_id
                AND speaker.agenda_item_id=other.id
               WHERE other.organization_id=?1 AND other.event_id=?2
                 AND other.revision_id=?3 AND other.id<>COALESCE(?10,'')
                 AND speaker.event_speaker_id IN (SELECT value FROM json_each(?8))
                 AND other.starts_at_ms<?7 AND other.ends_at_ms>?6
               UNION ALL
               SELECT 'speaker', other.id FROM agenda_items other
               JOIN accepted_session_participants participant
                 ON participant.accepted_session_id=other.accepted_session_id
               WHERE other.organization_id=?1 AND other.event_id=?2
                 AND other.revision_id=?3 AND other.id<>COALESCE(?10,'')
                 AND participant.event_speaker_id IN (SELECT value FROM json_each(?8))
                 AND other.starts_at_ms<?7 AND other.ends_at_ms>?6
               UNION ALL
               SELECT 'speaker', other.id FROM agenda_items other
               JOIN accepted_session_participants participant
                 ON participant.accepted_session_id=other.accepted_session_id
               WHERE other.organization_id=?1 AND other.event_id=?2
                 AND other.revision_id=?3 AND other.id<>COALESCE(?10,'')
                 AND ('invite:' || participant.pending_invitation_id)
                     IN (SELECT value FROM json_each(?8))
                 AND participant.pending_invitation_id IS NOT NULL
                 AND other.starts_at_ms<?7 AND other.ends_at_ms>?6
               ) conflict_candidates
               ORDER BY kind,item_id LIMIT ?9"""
            )
            .bind(
                slot.organization_id,
                slot.event_id,
                slot.revision_id,
                slot.room_id,
                slot.track_id,
                slot.starts_at_ms,
                slot.ends_at_ms,
                json.dumps(slot.speaker_ids),
                100,
                slot.item_id,
            )
            .all()
        )
        return [AgendaConflict(str(row["kind"]), str(row["item_id"])) for row in rows]

    async def move_item(self, slot: AgendaSlot, *, expected_version: int, now_ms: int) -> int:
        slot.validate()
        if slot.item_id is None:
            raise ValueError("item id is required")
        try:
            row = row_mapping(
                await self._db.prepare(
                    """UPDATE agenda_items SET room_id=?1,track_id=?2,event_date=?3,
                          event_time_zone=?4,starts_at_ms=?5,ends_at_ms=?6,
                          version=version+1,updated_at_ms=?7
                   WHERE organization_id=?8 AND event_id=?9 AND revision_id=?10
                     AND id=?11 AND version=?12
                   RETURNING version"""
                )
                .bind(
                    slot.room_id,
                    slot.track_id,
                    slot.event_date,
                    slot.event_time_zone,
                    slot.starts_at_ms,
                    slot.ends_at_ms,
                    now_ms,
                    slot.organization_id,
                    slot.event_id,
                    slot.revision_id,
                    slot.item_id,
                    expected_version,
                )
                .first()
            )
        except Exception as exc:
            raise PersistenceError("agenda save rejected") from exc
        if row is None:
            raise PersistenceError("agenda version conflict")
        return int(row["version"])

    async def publish_revision(
        self,
        *,
        organization_id: str,
        event_id: str,
        revision_id: str,
        expected_version: int,
        now_ms: int,
    ) -> bool:
        candidate = row_mapping(
            await self._db.prepare(
                """SELECT id FROM schedule_revisions WHERE organization_id=?1
                   AND event_id=?2 AND id=?3 AND status='draft' AND version=?4"""
            )
            .bind(organization_id, event_id, revision_id, expected_version)
            .first()
        )
        if candidate is None:
            return False
        statements = [
            self._db.prepare(
                """UPDATE schedule_revisions AS old SET status='superseded',
                          updated_at_ms=?1,version=version+1
                   WHERE organization_id=?2 AND event_id=?3 AND status='published'
                     AND EXISTS (SELECT 1 FROM schedule_revisions draft
                       WHERE draft.organization_id=?2 AND draft.event_id=?3
                         AND draft.id=?4 AND draft.status='draft' AND draft.version=?5)"""
            ).bind(now_ms, organization_id, event_id, revision_id, expected_version),
            self._db.prepare(
                """UPDATE schedule_revisions SET status='published',published_at_ms=?1,
                          updated_at_ms=?1,version=version+1
                   WHERE organization_id=?2 AND event_id=?3 AND id=?4
                     AND status='draft' AND version=?5"""
            ).bind(now_ms, organization_id, event_id, revision_id, expected_version),
        ]
        try:
            await self._db.batch(statements)
        except Exception as exc:
            raise PersistenceError("agenda publish rejected") from exc
        row = row_mapping(
            await self._db.prepare(
                """SELECT version,published_at_ms FROM schedule_revisions
                   WHERE organization_id=?1 AND event_id=?2 AND id=?3
                     AND status='published'"""
            )
            .bind(organization_id, event_id, revision_id)
            .first()
        )
        return (
            row is not None
            and int(row["version"]) == expected_version + 1
            and int(row["published_at_ms"]) == now_ms
        )

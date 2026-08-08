"""Transactional, per-recipient calendar delivery planning after an agenda save."""

import hashlib
import html
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sessionbuddy.communications.calendar import CalendarInvitation, render_ics
from sessionbuddy.platform.db.d1 import (
    D1Database,
    PersistenceError,
    execute_batch,
    row_mapping,
    to_python,
)
from sessionbuddy.platform.db.types import new_id


@dataclass(frozen=True, slots=True)
class AgendaCalendarChange:
    organization_id: str
    event_id: str
    agenda_item_id: str
    starts_at_ms: int
    ends_at_ms: int
    title: str
    description: str
    room: str
    organizer_email: str
    published: bool


@dataclass(frozen=True, slots=True)
class ScheduleSpeaker:
    user_id: str
    email: str
    display_name: str


def _content_hash(change: AgendaCalendarChange, attendee_email: str) -> bytes:
    canonical = json.dumps(
        {
            "agenda_item_id": change.agenda_item_id,
            "attendee_email": attendee_email,
            "description": change.description,
            "ends_at_ms": change.ends_at_ms,
            "event_id": change.event_id,
            "organizer_email": change.organizer_email,
            "room": change.room,
            "starts_at_ms": change.starts_at_ms,
            "title": change.title,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).digest()


def _changes(result: object, index: int) -> int:
    converted = to_python(result)
    if not isinstance(converted, list) or index >= len(converted):
        return 1
    item = to_python(converted[index])
    meta = item.get("meta", item) if isinstance(item, dict) else {}
    return int(meta.get("changes", 1)) if isinstance(meta, dict) else 1


async def queue_calendar_changes(
    db: D1Database,
    change: AgendaCalendarChange,
    speakers: list[ScheduleSpeaker],
    *,
    now_ms: int,
) -> list[str]:
    """Queue one private message per speaker; unchanged/draft saves are no-ops."""
    if not change.published:
        return []
    if change.ends_at_ms <= change.starts_at_ms:
        raise ValueError("agenda interval is invalid")
    unique = {speaker.user_id: speaker for speaker in speakers}
    if len(unique) != len(speakers):
        raise ValueError("duplicate schedule recipient")
    queued: list[str] = []
    generated_at = datetime.fromtimestamp(now_ms / 1000, UTC)
    for speaker in unique.values():
        digest = _content_hash(change, speaker.email)
        existing = row_mapping(
            await db.prepare(
                """SELECT id,calendar_uid,sequence,last_content_hash
                   FROM calendar_invitations WHERE organization_id=?1 AND event_id=?2
                     AND agenda_item_id=?3 AND recipient_user_id=?4 LIMIT 1"""
            )
            .bind(
                change.organization_id,
                change.event_id,
                change.agenda_item_id,
                speaker.user_id,
            )
            .first()
        )
        if existing is not None and bytes(existing["last_content_hash"] or b"") == digest:
            continue
        invitation_id = str(existing["id"]) if existing else new_id()
        uid = str(existing["calendar_uid"]) if existing else f"{new_id()}@sessionbuddy"
        sequence = int(existing["sequence"]) + 1 if existing else 0
        message_id, version_id, outbox_id = new_id(), new_id(), new_id()
        ics = render_ics(
            CalendarInvitation(
                uid=uid,
                sequence=sequence,
                starts_at=datetime.fromtimestamp(change.starts_at_ms / 1000, UTC),
                ends_at=datetime.fromtimestamp(change.ends_at_ms / 1000, UTC),
                summary=change.title,
                description=change.description,
                location=change.room,
                organizer_email=change.organizer_email,
                attendee_email=speaker.email,
            ),
            generated_at=generated_at,
        )
        deterministic = f"calendar:{invitation_id}:sequence:{sequence}"
        kind = "Schedule confirmation" if sequence == 0 else "Schedule change"
        subject = f"{kind}: {html.escape(change.title)}"
        body = (
            f"<p>Hello {html.escape(speaker.display_name)},</p>"
            f"<p>{html.escape(change.title)} is scheduled in {html.escape(change.room)}.</p>"
        )
        statements = []
        if existing is None:
            statements.append(
                db.prepare(
                    """INSERT INTO calendar_invitations
                       (id,organization_id,event_id,agenda_item_id,recipient_user_id,
                        calendar_uid,sequence,last_content_hash,updated_at_ms)
                       VALUES (?1,?2,?3,?4,?5,?6,0,?7,?8)"""
                ).bind(
                    invitation_id,
                    change.organization_id,
                    change.event_id,
                    change.agenda_item_id,
                    speaker.user_id,
                    uid,
                    digest,
                    now_ms,
                )
            )
        else:
            statements.append(
                db.prepare(
                    """UPDATE calendar_invitations SET sequence=?1,last_content_hash=?2,
                              updated_at_ms=?3 WHERE organization_id=?4 AND event_id=?5
                              AND id=?6 AND sequence=?7 AND last_content_hash!=?2"""
                ).bind(
                    sequence,
                    digest,
                    now_ms,
                    change.organization_id,
                    change.event_id,
                    invitation_id,
                    sequence - 1,
                )
            )
        statements.extend(
            [
                db.prepare(
                    """INSERT INTO communication_messages
                       (id,organization_id,event_id,recipient_user_id,recipient_email,
                        subject,html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
                       VALUES (?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9)"""
                ).bind(
                    message_id,
                    change.organization_id,
                    change.event_id,
                    speaker.user_id,
                    speaker.email,
                    subject,
                    body,
                    deterministic,
                    now_ms,
                ),
                db.prepare(
                    """INSERT INTO calendar_invitation_versions
                       (id,organization_id,event_id,invitation_id,sequence,content_hash,
                        ics_content,communication_message_id,created_at_ms)
                       VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)"""
                ).bind(
                    version_id,
                    change.organization_id,
                    change.event_id,
                    invitation_id,
                    sequence,
                    digest,
                    ics,
                    message_id,
                    now_ms,
                ),
                db.prepare(
                    """INSERT INTO outbox_messages
                       (id,organization_id,event_id,topic,payload_version,aggregate_type,
                        aggregate_id,deduplication_key,payload_json,available_at_ms,created_at_ms)
                       VALUES (?1,?2,?3,'communication.delivery.requested',1,
                               'communication_message',?4,?5,?6,?7,?7)"""
                ).bind(
                    outbox_id,
                    change.organization_id,
                    change.event_id,
                    message_id,
                    deterministic,
                    json.dumps(
                        {
                            "schema_version": 1,
                            "message_id": message_id,
                        },
                        separators=(",", ":"),
                    ),
                    now_ms,
                ),
            ]
        )
        try:
            result = await execute_batch(db, statements)
        except PersistenceError:
            replay = (
                await db.prepare(
                    """SELECT id FROM communication_messages WHERE organization_id=?1
                   AND event_id=?2 AND deterministic_key=?3 LIMIT 1"""
                )
                .bind(change.organization_id, change.event_id, deterministic)
                .first("id")
            )
            if replay is not None:
                continue
            raise
        if _changes(result, 0) > 0:
            queued.append(message_id)
    return queued

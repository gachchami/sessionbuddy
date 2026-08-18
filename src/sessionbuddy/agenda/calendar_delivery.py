"""Transactional, per-recipient calendar delivery planning after an agenda save."""

import hashlib
import html
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sessionbuddy.communications.calendar import CalendarInvitation, render_ics
from sessionbuddy.platform.db.d1 import (
    D1Database,
    PersistenceError,
    execute_batch,
    result_rows,
    row_mapping,
    to_python,
)
from sessionbuddy.platform.db.types import new_id

_HIDDEN_CALENDAR_SESSIONS_SQL = """SELECT item.accepted_session_id,
          MAX(invitation.updated_at_ms) AS last_invitation_at_ms
   FROM calendar_invitations invitation
   JOIN agenda_items item
     ON item.organization_id=invitation.organization_id
    AND item.event_id=invitation.event_id
    AND item.id=invitation.agenda_item_id
   WHERE invitation.organization_id=?1 AND invitation.event_id=?2
     AND item.accepted_session_id NOT IN (SELECT value FROM json_each(?3))
   GROUP BY item.accepted_session_id
   ORDER BY last_invitation_at_ms,item.accepted_session_id"""


@dataclass(frozen=True, slots=True)
class AgendaCalendarChange:
    organization_id: str
    event_id: str
    agenda_item_id: str
    accepted_session_id: str
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
            "accepted_session_id": change.accepted_session_id,
            "attendee_email": attendee_email,
            "description": change.description,
            "ends_at_ms": change.ends_at_ms,
            "event_id": change.event_id,
            "organizer_email": change.organizer_email,
            "published": change.published,
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
    """Queue requests for public content and cancellations for prior delivery."""
    if change.ends_at_ms <= change.starts_at_ms:
        raise ValueError("agenda interval is invalid")
    unique = {speaker.user_id: speaker for speaker in speakers}
    if len(unique) != len(speakers):
        raise ValueError("duplicate schedule recipient")
    queued: list[str] = []
    generated_at = datetime.fromtimestamp(now_ms / 1000, UTC)
    for speaker in unique.values():
        existing_rows = result_rows(
            await db.prepare(
                """SELECT invitation.id,invitation.calendar_uid,invitation.sequence,
                          invitation.last_content_hash
                   FROM calendar_invitations invitation
                   JOIN agenda_items item
                     ON item.organization_id=invitation.organization_id
                    AND item.event_id=invitation.event_id
                    AND item.id=invitation.agenda_item_id
                   WHERE invitation.organization_id=?1 AND invitation.event_id=?2
                     AND item.accepted_session_id=?3
                     AND invitation.recipient_user_id=?4
                   ORDER BY invitation.updated_at_ms DESC,invitation.id DESC"""
            )
            .bind(
                change.organization_id,
                change.event_id,
                change.accepted_session_id,
                speaker.user_id,
            )
            .all()
        )
        targets: list[tuple[dict[str, object] | None, bool]]
        if change.published and existing_rows:
            targets = [(existing_rows[0], True)] + [(legacy, False) for legacy in existing_rows[1:]]
        elif change.published:
            targets = [(None, True)]
        else:
            targets = [(existing, False) for existing in existing_rows]
        for existing, target_is_public in targets:
            target_change = replace(change, published=target_is_public)
            digest = _content_hash(target_change, speaker.email)
            if existing is not None and bytes(existing["last_content_hash"] or b"") == digest:
                continue
            invitation_id = str(existing["id"]) if existing else new_id()
            uid = str(existing["calendar_uid"]) if existing else f"{new_id()}@sessionbuddy"
            sequence = int(existing["sequence"]) + 1 if existing else 0
            message_id, version_id = new_id(), new_id()
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
                    cancelled=not target_is_public,
                ),
                generated_at=generated_at,
            )
            deterministic = f"calendar:{invitation_id}:sequence:{sequence}"
            kind = (
                "Schedule cancellation"
                if not target_is_public
                else "Schedule confirmation"
                if sequence == 0
                else "Schedule change"
            )
            subject = f"{kind}: {html.escape(change.title)}"
            status_copy = (
                "is no longer on the published schedule"
                if not target_is_public
                else f"is scheduled in {html.escape(change.room)}"
            )
            body = (
                f"<p>Hello {html.escape(speaker.display_name)},</p>"
                f"<p>{html.escape(change.title)} {status_copy}.</p>"
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


async def reconcile_calendar_projection(
    db: D1Database,
    *,
    organization_id: str,
    event_id: str,
    public_session_ids: set[str],
    organizer_email: str,
    now_ms: int,
) -> list[str]:
    """Cancel every prior invitation whose session left the public projection."""
    hidden_sessions = result_rows(
        await db.prepare(_HIDDEN_CALENDAR_SESSIONS_SQL)
        .bind(organization_id, event_id, json.dumps(sorted(public_session_ids)))
        .all()
    )
    queued: list[str] = []
    for hidden in hidden_sessions:
        accepted_session_id = str(hidden["accepted_session_id"])
        projection = row_mapping(
            await db.prepare(
                """SELECT item.id AS agenda_item_id,item.starts_at_ms,item.ends_at_ms,
                          COALESCE(submission.proposal_title,session.organizer_title,'Session')
                            AS title,
                          COALESCE(submission.proposal_abstract,session.organizer_abstract,'')
                            AS description,
                          COALESCE(room.name,'') AS room_name
                   FROM calendar_invitations invitation
                   JOIN agenda_items item ON item.id=invitation.agenda_item_id
                   JOIN accepted_sessions session ON session.id=item.accepted_session_id
                   LEFT JOIN submissions submission ON submission.id=session.submission_id
                   LEFT JOIN event_rooms room ON room.id=item.room_id
                   WHERE invitation.organization_id=?1 AND invitation.event_id=?2
                     AND item.accepted_session_id=?3
                   ORDER BY invitation.updated_at_ms DESC,invitation.id DESC LIMIT 1"""
            )
            .bind(organization_id, event_id, accepted_session_id)
            .first()
        )
        if projection is None:
            continue
        recipient_rows = result_rows(
            await db.prepare(
                """SELECT DISTINCT user.id AS user_id,user.email,
                          COALESCE(NULLIF(user.display_name,''),user.email) AS display_name
                   FROM calendar_invitations invitation
                   JOIN agenda_items item ON item.id=invitation.agenda_item_id
                   JOIN users user ON user.id=invitation.recipient_user_id
                   WHERE invitation.organization_id=?1 AND invitation.event_id=?2
                     AND item.accepted_session_id=?3 AND user.status='active'
                   ORDER BY user.id"""
            )
            .bind(organization_id, event_id, accepted_session_id)
            .all()
        )
        queued.extend(
            await queue_calendar_changes(
                db,
                AgendaCalendarChange(
                    organization_id=organization_id,
                    event_id=event_id,
                    agenda_item_id=str(projection["agenda_item_id"]),
                    accepted_session_id=accepted_session_id,
                    starts_at_ms=int(projection["starts_at_ms"]),
                    ends_at_ms=int(projection["ends_at_ms"]),
                    title=str(projection["title"]),
                    description=str(projection["description"]),
                    room=str(projection["room_name"]),
                    organizer_email=organizer_email,
                    published=False,
                ),
                [
                    ScheduleSpeaker(
                        str(row["user_id"]),
                        str(row["email"]),
                        str(row["display_name"]),
                    )
                    for row in recipient_rows
                ],
                now_ms=now_ms,
            )
        )
    return queued

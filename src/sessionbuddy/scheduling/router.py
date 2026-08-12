import hashlib
import json
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from sessionbuddy.agenda import (
    AgendaCalendarChange,
    AgendaRepository,
    AgendaSlot,
    ScheduleSpeaker,
    queue_calendar_changes,
)
from sessionbuddy.console import embedded_assets
from sessionbuddy.platform.auth.http import (
    authenticate_request,
    require_document_persona,
    require_permission,
)
from sessionbuddy.platform.authorization import (
    Permission,
    Persona,
    ResourceContext,
    ResourceGrant,
)
from sessionbuddy.platform.db.commands import (
    AuditEvent,
    CommandBatch,
    IdempotencyRecord,
)
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms

from .models import (
    AdminAgendaView,
    AgendaAutoSchedule,
    AgendaAutoScheduleResult,
    AgendaCandidate,
    AgendaConflictView,
    AgendaEventView,
    AgendaItemView,
    AgendaPreviewView,
    AgendaPublish,
    AgendaPublishView,
    AgendaResourceCreate,
    AgendaResourceUpdate,
    AgendaResourceView,
    AgendaRevisionView,
    AgendaScheduledItemView,
    AgendaSetup,
    AgendaUnscheduledSessionView,
    AutoScheduledAgendaView,
    EventLabelCreate,
    EventLabelList,
    EventLabelUpdate,
    EventLabelView,
    EventTrackList,
    EventTrackView,
    ManualSessionCreate,
    ManualSessionParticipantView,
    PublicScheduleEventView,
    PublicScheduleItemView,
    PublicScheduleView,
    PublishedAgendaRevisionView,
    ScheduleEventView,
    ScheduleItemView,
    ScheduleLabelView,
    ScheduleRevisionView,
    ScheduleView,
    SessionLabelAssignmentUpdate,
    SessionLabelAssignmentView,
)

scheduling_router = APIRouter()

_RESOURCE_SELECT_BY_NAME = {
    "room": """SELECT id,status,version FROM event_rooms
               WHERE organization_id=?1 AND event_id=?2 AND lower(name)=lower(?3) LIMIT 1""",
    "track": """SELECT id,status,version FROM event_tracks
                WHERE organization_id=?1 AND event_id=?2 AND lower(name)=lower(?3) LIMIT 1""",
}
_RESOURCE_RESTORE = {
    "room": """UPDATE event_rooms
               SET status='active',name=?1,version=version+1,updated_at_ms=?2
               WHERE organization_id=?3 AND event_id=?4 AND id=?5 AND status='archived'""",
    "track": """UPDATE event_tracks
                SET status='active',name=?1,version=version+1,updated_at_ms=?2
                WHERE organization_id=?3 AND event_id=?4 AND id=?5 AND status='archived'""",
}
_RESOURCE_SELECT_BY_ID = {
    "room": """SELECT id,status,version FROM event_rooms
               WHERE organization_id=?1 AND event_id=?2 AND id=?3 LIMIT 1""",
    "track": """SELECT id,status,version FROM event_tracks
                WHERE organization_id=?1 AND event_id=?2 AND id=?3 LIMIT 1""",
}
_RESOURCE_IN_USE = {
    "room": """SELECT id FROM agenda_items WHERE organization_id=?1 AND event_id=?2
               AND revision_id=?3 AND room_id=?4 LIMIT 1""",
    "track": """SELECT id FROM agenda_items WHERE organization_id=?1 AND event_id=?2
                AND revision_id=?3 AND track_id=?4 LIMIT 1""",
}
_RESOURCE_UPDATE = {
    "room": """UPDATE event_rooms SET status=?1,version=version+1,updated_at_ms=?2
               WHERE organization_id=?3 AND event_id=?4 AND id=?5 AND version=?6""",
    "track": """UPDATE event_tracks SET status=?1,version=version+1,updated_at_ms=?2
                WHERE organization_id=?3 AND event_id=?4 AND id=?5 AND version=?6""",
}


def _db(request: Request):
    db = getattr(request.scope.get("env"), "DB", None)
    if db is None:
        raise HTTPException(status_code=503)
    return db


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


def _product_asset(request: Request, name: str, media_type: str) -> Response:
    return Response(_asset(name), media_type=media_type, headers={"Cache-Control": "no-store"})


@scheduling_router.get("/admin/events/{event_id}/agenda", include_in_schema=False)
async def agenda_page(event_id: str, request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    return _product_asset(request, "agenda_admin.html", "text/html")


@scheduling_router.get("/admin/agenda/assets/agenda.css", include_in_schema=False)
async def agenda_css(request: Request) -> Response:
    return _product_asset(request, "agenda.css", "text/css")


@scheduling_router.get("/admin/agenda/assets/agenda.js", include_in_schema=False)
async def agenda_js(request: Request) -> Response:
    return _product_asset(request, "agenda.js", "text/javascript")


@scheduling_router.get("/events/{event_id}/schedule", include_in_schema=False)
@scheduling_router.get("/events/{event_id}/sessions", include_in_schema=False)
@scheduling_router.get("/embeds/events/{event_id}/schedule", include_in_schema=False)
@scheduling_router.get("/embeds/events/{event_id}/sessions", include_in_schema=False)
@scheduling_router.get("/embeds/events/{event_id}/itinerary", include_in_schema=False)
async def schedule_page(event_id: str, request: Request) -> HTMLResponse:
    return HTMLResponse(
        _asset("schedule.html"),
        headers={"Cache-Control": "public, max-age=300"},
    )


@scheduling_router.get("/schedule/assets/schedule.css", include_in_schema=False)
async def schedule_css() -> Response:
    return Response(
        _asset("schedule.css"),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300"},
    )


@scheduling_router.get("/schedule/assets/schedule.js", include_in_schema=False)
async def schedule_js() -> Response:
    return Response(
        _asset("schedule.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


async def _event_scope(request: Request, event_id: str, permission: Permission, *, mutation: bool):
    event = row_mapping(
        await _db(request)
        .prepare(
            """SELECT id,organization_id,name,time_zone,starts_at_ms,ends_at_ms
               FROM events WHERE id=?1 AND status!='archived' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        permission,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=mutation,
    )
    return event, auth


def _key(value: str | None) -> str:
    if value is None or not 16 <= len(value) <= 255:
        raise HTTPException(status_code=400)
    return value


def _blob(value: object) -> bytes:
    converted = to_python(value)
    return converted if isinstance(converted, bytes) else bytes(converted)


def _fingerprint(
    body: AgendaAutoSchedule | AgendaCandidate | AgendaSetup | ManualSessionCreate,
) -> bytes:
    return hashlib.sha256(
        json.dumps(body.model_dump(), separators=(",", ":"), sort_keys=True).encode()
    ).digest()


async def _revision(db, organization_id: str, event_id: str, state: str = "draft"):
    return row_mapping(
        await db.prepare(
            """SELECT id,revision_number,status,version FROM schedule_revisions
               WHERE organization_id=?1 AND event_id=?2 AND status=?3 LIMIT 1"""
        )
        .bind(organization_id, event_id, state)
        .first()
    )


async def _speaker_ids(db, organization_id: str, event_id: str, accepted_session_id: str):
    rows = result_rows(
        await db.prepare(
            """SELECT ss.event_speaker_id FROM accepted_sessions ac
               JOIN submission_speakers ss ON ss.organization_id=ac.organization_id
                 AND ss.event_id=ac.event_id AND ss.submission_id=ac.submission_id
               WHERE ac.organization_id=?1 AND ac.event_id=?2 AND ac.id=?3
               UNION
               SELECT participant.event_speaker_id
               FROM accepted_session_participants participant
               WHERE participant.organization_id=?1 AND participant.event_id=?2
                 AND participant.accepted_session_id=?3
                 AND participant.event_speaker_id IS NOT NULL
               UNION
               SELECT 'invite:' || participant.pending_invitation_id AS event_speaker_id
               FROM accepted_session_participants participant
               WHERE participant.organization_id=?1 AND participant.event_id=?2
                 AND participant.accepted_session_id=?3
                 AND participant.pending_invitation_id IS NOT NULL
               ORDER BY event_speaker_id"""
        )
        .bind(organization_id, event_id, accepted_session_id)
        .all()
    )
    return tuple(str(row["event_speaker_id"]) for row in rows)


def _can_manage_resource(actor, resource_id: str) -> bool:
    grants = actor.resource_grants.get(resource_id, frozenset())
    return resource_id in actor.owned_resource_ids or bool(
        grants & {ResourceGrant.EDIT, ResourceGrant.MANAGE}
    )


def _can_manage_event(actor, event_id: str) -> bool:
    return event_id in actor.owned_resource_ids or ResourceGrant.MANAGE in (
        actor.resource_grants.get(event_id, frozenset())
    )


def _label_view(row: dict, actor, event_id: str) -> EventLabelView:
    return EventLabelView(
        id=str(row["id"]),
        name=str(row["name"]),
        color=str(row["color"]),
        status=str(row["status"]),
        version=int(row["version"]),
        can_manage=_can_manage_resource(actor, str(row["id"]))
        or _can_manage_event(actor, event_id),
    )


async def _event_label_rows(
    db, organization_id: str, event_id: str, actor, *, status: str = "active"
) -> list[EventLabelView]:
    rows = result_rows(
        await db.prepare(
            """SELECT id,name,color,status,version FROM event_labels
               WHERE organization_id=?1 AND event_id=?2 AND status=?3
               ORDER BY lower(name),id"""
        )
        .bind(organization_id, event_id, status)
        .all()
    )
    return [_label_view(row, actor, event_id) for row in rows]


async def _attach_session_labels(
    db, organization_id: str, event_id: str, sessions: list[dict], actor=None
) -> None:
    rows = result_rows(
        await db.prepare(
            """SELECT assignment.accepted_session_id,label.id,label.name,label.color,
                      label.status,label.version
               FROM accepted_session_labels assignment
               JOIN event_labels label ON label.organization_id=assignment.organization_id
                 AND label.event_id=assignment.event_id AND label.id=assignment.label_id
               WHERE assignment.organization_id=?1 AND assignment.event_id=?2
               ORDER BY lower(label.name),label.id"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    by_session: dict[str, list[dict]] = {}
    for row in rows:
        label = {
            "id": row["id"],
            "name": row["name"],
            "color": row["color"],
        }
        if actor is not None:
            label.update(
                status=row["status"],
                version=row["version"],
                can_manage=_can_manage_resource(actor, str(row["id"]))
                or _can_manage_event(actor, event_id),
            )
        by_session.setdefault(str(row["accepted_session_id"]), []).append(label)
    for session in sessions:
        assigned = by_session.get(str(session["session_id"]), [])
        session["labels"] = assigned
        session["label_ids"] = [str(label["id"]) for label in assigned]


def _agenda_label(row: dict) -> EventLabelView:
    return EventLabelView(
        id=str(row["id"]),
        name=str(row["name"]),
        color=str(row["color"]),
        status=str(row["status"]),
        version=int(row["version"]),
        can_manage=bool(row["can_manage"]),
    )


def _agenda_resource(row: dict) -> AgendaResourceView:
    return AgendaResourceView(
        id=str(row["id"]),
        name=str(row["name"]),
        status=str(row["status"]),
        version=int(row["version"]),
    )


def _scheduled_item(row: dict) -> AgendaScheduledItemView:
    return AgendaScheduledItemView(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        source_type=str(row["source_type"]),
        title=str(row["title"]),
        abstract=str(row["abstract"]),
        content_status=str(row["content_status"]),
        content_version=int(row["content_version"]),
        label_version=int(row["label_version"]),
        start_at_ms=int(row["start_at_ms"]),
        end_at_ms=int(row["end_at_ms"]),
        room_id=str(row["room_id"]),
        room_name=str(row["room_name"]),
        track_id=str(row["track_id"]) if row["track_id"] is not None else None,
        track_name=str(row["track_name"]) if row["track_name"] is not None else None,
        speaker_names=str(row["speaker_names"]),
        participants=[
            ManualSessionParticipantView.model_validate(value)
            for value in json.loads(str(row.get("participants_json") or "[]"))
        ],
        version=int(row["version"]),
        labels=[_agenda_label(label) for label in row["labels"]],
        label_ids=[str(label_id) for label_id in row["label_ids"]],
    )


def _unscheduled_item(row: dict) -> AgendaUnscheduledSessionView:
    return AgendaUnscheduledSessionView(
        session_id=str(row["session_id"]),
        source_type=str(row["source_type"]),
        title=str(row["title"]),
        abstract=str(row["abstract"]),
        content_status=str(row["content_status"]),
        content_version=int(row["content_version"]),
        label_version=int(row["label_version"]),
        track_id=str(row["track_id"]) if row["track_id"] is not None else None,
        track_name=str(row["track_name"]) if row["track_name"] is not None else None,
        speaker_names=str(row["speaker_names"]),
        participants=[
            ManualSessionParticipantView.model_validate(value)
            for value in json.loads(str(row.get("participants_json") or "[]"))
        ],
        labels=[_agenda_label(label) for label in row["labels"]],
        label_ids=[str(label_id) for label_id in row["label_ids"]],
    )


async def _manual_session_candidates(db, organization_id: str, event_id: str):
    return result_rows(
        await db.prepare(
            """SELECT es.id,p.display_name,'active' AS recipient_state
                 FROM event_speakers es JOIN people p
                   ON p.organization_id=es.organization_id AND p.id=es.person_id
                WHERE es.organization_id=?1 AND es.event_id=?2 AND es.status!='withdrawn'
                  AND es.selection_status IN ('submitted','accepted')
               UNION ALL
               SELECT invitation.id,
                      COALESCE(NULLIF(trim(invitation.display_name),''),'Invited speaker'),
                      'invited' AS recipient_state
                 FROM identity_invitations invitation
                WHERE invitation.organization_id=?1 AND invitation.event_id=?2
                  AND invitation.role='speaker' AND invitation.status='pending'
                  AND NOT EXISTS (
                    SELECT 1 FROM event_speakers existing
                    JOIN people person ON person.organization_id=existing.organization_id
                                      AND person.id=existing.person_id
                    JOIN users account ON account.id=person.user_id
                    WHERE existing.organization_id=invitation.organization_id
                      AND existing.event_id=invitation.event_id
                      AND lower(trim(account.email))=invitation.normalized_email
                      AND existing.status!='withdrawn')
               ORDER BY 2 COLLATE NOCASE,1"""
        )
        .bind(organization_id, event_id)
        .all()
    )


async def _agenda_model(db, event, revision, actor) -> AdminAgendaView:
    organization_id, event_id = str(event["organization_id"]), str(event["id"])
    published_revision = row_mapping(
        await db.prepare(
            """SELECT id,revision_number,version FROM schedule_revisions
               WHERE organization_id=?1 AND event_id=?2 AND status='published' LIMIT 1"""
        )
        .bind(organization_id, event_id)
        .first()
    )
    items = result_rows(
        await db.prepare(
            """SELECT ai.id,ai.accepted_session_id AS session_id,ac.source_type,
                      COALESCE(s.proposal_title,ac.organizer_title) AS title,
                      COALESCE(s.proposal_abstract,ac.organizer_abstract) AS abstract,
                      ac.content_status,
                      ac.version AS content_version,ac.label_version,
                      ai.starts_at_ms AS start_at_ms,ai.ends_at_ms AS end_at_ms,
                      ai.room_id,r.name AS room_name,ai.track_id,t.name AS track_name,ai.version,
                      CASE WHEN ac.source_type='organizer_created' THEN
                        COALESCE((SELECT group_concat(participant.display_name_snapshot, ', ')
                          FROM accepted_session_participants participant
                          WHERE participant.accepted_session_id=ac.id),'')
                      ELSE COALESCE((SELECT group_concat(ss.snapshot_name, ', ')
                        FROM submission_speakers ss WHERE ss.submission_id=s.id),s.speaker_name)
                      END AS speaker_names,
                      COALESCE((SELECT json_group_array(json_object(
                        'id',COALESCE(participant.event_speaker_id,participant.pending_invitation_id),
                        'display_name',participant.display_name_snapshot,
                        'recipient_state',CASE WHEN participant.event_speaker_id IS NULL
                          THEN 'invited' ELSE 'active' END))
                        FROM accepted_session_participants participant
                        WHERE participant.accepted_session_id=ac.id),'[]') AS participants_json
               FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
               LEFT JOIN submissions s ON s.id=ac.submission_id
               JOIN event_rooms r ON r.id=ai.room_id
               LEFT JOIN event_tracks t ON t.id=ai.track_id
               WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
                 AND ac.lifecycle_status='active'
               ORDER BY ai.starts_at_ms,ai.id"""
        )
        .bind(organization_id, event_id, revision["id"])
        .all()
    )
    unscheduled = result_rows(
        await db.prepare(
            """SELECT ac.id AS session_id,ac.source_type,
                      COALESCE(s.proposal_title,ac.organizer_title) AS title,
                      COALESCE(s.proposal_abstract,ac.organizer_abstract) AS abstract,
                      ac.content_status,
                      ac.version AS content_version,ac.label_version,
                      t.id AS track_id,t.name AS track_name,
                      CASE WHEN ac.source_type='organizer_created' THEN
                        COALESCE((SELECT group_concat(participant.display_name_snapshot, ', ')
                          FROM accepted_session_participants participant
                          WHERE participant.accepted_session_id=ac.id),'')
                      ELSE COALESCE((SELECT group_concat(ss.snapshot_name, ', ')
                        FROM submission_speakers ss WHERE ss.submission_id=s.id),s.speaker_name)
                      END AS speaker_names,
                      COALESCE((SELECT json_group_array(json_object(
                        'id',COALESCE(participant.event_speaker_id,participant.pending_invitation_id),
                        'display_name',participant.display_name_snapshot,
                        'recipient_state',CASE WHEN participant.event_speaker_id IS NULL
                          THEN 'invited' ELSE 'active' END))
                        FROM accepted_session_participants participant
                        WHERE participant.accepted_session_id=ac.id),'[]') AS participants_json
               FROM accepted_sessions ac LEFT JOIN submissions s ON s.id=ac.submission_id
               LEFT JOIN event_tracks t ON t.organization_id=s.organization_id
                 AND t.event_id=s.event_id AND t.status='active'
                 AND lower(trim(t.name))=lower(trim(s.routed_track))
               WHERE ac.organization_id=?1 AND ac.event_id=?2
                 AND ac.lifecycle_status='active' AND NOT EXISTS (
                 SELECT 1 FROM agenda_items ai WHERE ai.revision_id=?3
                   AND ai.accepted_session_id=ac.id)
               ORDER BY title,ac.id"""
        )
        .bind(organization_id, event_id, revision["id"])
        .all()
    )
    rooms = result_rows(
        await db.prepare(
            """SELECT id,name,status,version FROM event_rooms
               WHERE organization_id=?1 AND event_id=?2 AND status='active'
               ORDER BY name"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    tracks = result_rows(
        await db.prepare(
            """SELECT id,name,status,version FROM event_tracks
               WHERE organization_id=?1 AND event_id=?2 AND status='active'
               ORDER BY name"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    archived_rooms = result_rows(
        await db.prepare(
            """SELECT id,name,status,version FROM event_rooms
               WHERE organization_id=?1 AND event_id=?2 AND status='archived'
               ORDER BY name"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    archived_tracks = result_rows(
        await db.prepare(
            """SELECT id,name,status,version FROM event_tracks
               WHERE organization_id=?1 AND event_id=?2 AND status='archived'
               ORDER BY name"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    labels = await _event_label_rows(db, organization_id, event_id, actor)
    archived_labels = await _event_label_rows(
        db, organization_id, event_id, actor, status="archived"
    )
    await _attach_session_labels(db, organization_id, event_id, items, actor)
    await _attach_session_labels(db, organization_id, event_id, unscheduled, actor)
    participant_rows = await _manual_session_candidates(db, organization_id, event_id)
    return AdminAgendaView(
        event=AgendaEventView(
            id=str(event["id"]),
            name=str(event["name"]),
            time_zone=str(event["time_zone"]),
            starts_at_ms=int(event["starts_at_ms"]),
            ends_at_ms=int(event["ends_at_ms"]),
        ),
        revision=AgendaRevisionView(
            id=str(revision["id"]),
            version=int(revision["version"]),
            state=str(revision["status"]),
        ),
        published_revision=(
            PublishedAgendaRevisionView(
                id=str(published_revision["id"]),
                revision_number=int(published_revision["revision_number"]),
                version=int(published_revision["version"]),
            )
            if published_revision is not None
            else None
        ),
        items=[_scheduled_item(item) for item in items],
        unscheduled_sessions=[_unscheduled_item(item) for item in unscheduled],
        rooms=[_agenda_resource(room) for room in rooms],
        tracks=[_agenda_resource(track) for track in tracks],
        labels=labels,
        archived_rooms=[_agenda_resource(room) for room in archived_rooms],
        archived_tracks=[_agenda_resource(track) for track in archived_tracks],
        archived_labels=archived_labels,
        can_manage_resource_lifecycle=_can_manage_event(actor, event_id),
        session_participants=[
            ManualSessionParticipantView(
                id=str(row["id"]),
                display_name=str(row["display_name"]),
                recipient_state=str(row["recipient_state"]),
            )
            for row in participant_rows
        ],
    )


@scheduling_router.get(
    "/api/v1/admin/events/{event_id}/agenda",
    response_model=AdminAgendaView,
    tags=["agenda"],
)
async def get_admin_agenda(event_id: str, request: Request) -> AdminAgendaView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=False)
    revision = await _revision(_db(request), str(event["organization_id"]), event_id)
    if revision is None:
        raise HTTPException(status_code=404)
    return await _agenda_model(_db(request), event, revision, auth.actor)


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/sessions",
    response_model=AdminAgendaView,
    status_code=201,
    tags=["agenda"],
)
async def create_organizer_session(
    event_id: str,
    request: Request,
    body: ManualSessionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AdminAgendaView:
    """Create a program session without manufacturing a CFP acceptance.

    Pending invitees remain invitations. Their participant row is linked to an
    event speaker only when the invitation is accepted.
    """
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    revision = await _revision(db, organization_id, event_id)
    if revision is None:
        raise HTTPException(status_code=409, detail="Set up the agenda first")
    key, fingerprint = _key(idempotency_key), _fingerprint(body)
    route = "POST /api/v1/admin/events/{event_id}/sessions"
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND event_id=?4 AND state='completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(key.encode()).digest(), event_id)
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _agenda_model(db, event, revision, auth.actor)
    candidates = await _manual_session_candidates(db, organization_id, event_id)
    by_id = {str(row["id"]): row for row in candidates}
    if any(participant_id not in by_id for participant_id in body.participant_ids):
        raise HTTPException(
            status_code=422,
            detail="Choose active speakers or pending speaker invitees from this event",
        )
    now, session_id = utc_now_ms(), new_id()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,source_type,submission_id,decision_id,
                organizer_title,organizer_abstract,created_at_ms,content_status,
                version,label_version)
               VALUES (?1,?2,?3,'organizer_created',NULL,NULL,?4,?5,?6,'draft',1,1)"""
        ).bind(session_id, organization_id, event_id, body.title, body.abstract, now)
    )
    for participant_id in body.participant_ids:
        participant = by_id[participant_id]
        active = str(participant["recipient_state"]) == "active"
        batch.add_statement(
            db.prepare(
                """INSERT INTO accepted_session_participants
                   (id,organization_id,event_id,accepted_session_id,event_speaker_id,
                    pending_invitation_id,display_name_snapshot,created_at_ms,updated_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?8)"""
            ).bind(
                new_id(),
                organization_id,
                event_id,
                session_id,
                participant_id if active else None,
                None if active else participant_id,
                participant["display_name"],
                now,
            )
        )
        if active:
            # An organizer-created session is just as real as a CFP-backed accepted
            # session.  Active participants therefore need the same onboarding
            # work, even though there is no proposal decision to create it for us.
            # Preserve completed evidence when the profile or headshot predates the
            # session, and do not duplicate tasks when the speaker is added to a
            # second organizer-created session.
            for task_type, title, due_days in (
                ("profile", "Complete your speaker profile", 7),
                ("headshot", "Upload your headshot", 7),
                ("slides", "Upload your presentation slides", 10),
            ):
                batch.add_statement(
                    db.prepare(
                        """INSERT INTO speaker_tasks
                           (id,organization_id,event_id,event_speaker_id,pending_invitation_id,
                            submission_id,task_type,title,help_text,destination_type,state,
                            due_at_ms,completed_at_ms,created_at_ms,updated_at_ms,form_schema_json)
                           SELECT ?1,?2,?3,?4,NULL,NULL,?5,?6,'',?5,
                             CASE
                               WHEN ?5='profile' AND NULLIF(TRIM(p.biography),'') IS NOT NULL
                                 THEN 'completed'
                               WHEN ?5='headshot' AND (
                                 EXISTS (SELECT 1 FROM user_headshots h WHERE h.user_id=p.user_id)
                                 OR EXISTS (
                                   SELECT 1 FROM speaker_assets asset
                                   JOIN speaker_asset_versions version
                                     ON version.asset_id=asset.id AND version.is_current=1
                                    AND version.scan_state='clean'
                                   WHERE asset.organization_id=?2 AND asset.event_id=?3
                                     AND asset.event_speaker_id=?4 AND asset.kind='headshot'
                                 )
                               ) THEN 'completed'
                               ELSE 'open'
                             END,
                             ?7,
                             CASE
                               WHEN ?5='profile' AND NULLIF(TRIM(p.biography),'') IS NOT NULL
                                 THEN ?8
                               WHEN ?5='headshot' AND (
                                 EXISTS (SELECT 1 FROM user_headshots h WHERE h.user_id=p.user_id)
                                 OR EXISTS (
                                   SELECT 1 FROM speaker_assets asset
                                   JOIN speaker_asset_versions version
                                     ON version.asset_id=asset.id AND version.is_current=1
                                    AND version.scan_state='clean'
                                   WHERE asset.organization_id=?2 AND asset.event_id=?3
                                     AND asset.event_speaker_id=?4 AND asset.kind='headshot'
                                 )
                               ) THEN ?8
                               ELSE NULL
                             END,
                             ?8,?8,'{}'
                           FROM event_speakers es
                           JOIN people p ON p.organization_id=es.organization_id
                            AND p.id=es.person_id
                           WHERE es.organization_id=?2 AND es.event_id=?3 AND es.id=?4
                             AND NOT EXISTS (
                               SELECT 1 FROM speaker_tasks existing
                               WHERE existing.organization_id=?2 AND existing.event_id=?3
                                 AND existing.event_speaker_id=?4
                                 AND existing.task_type=?5
                                 AND existing.state IN ('open','completed')
                             )"""
                    ).bind(
                        new_id(),
                        organization_id,
                        event_id,
                        participant_id,
                        task_type,
                        title,
                        now + due_days * 86_400_000,
                        now,
                    )
                )
    batch.add_statement(
        db.prepare(
            """INSERT INTO session_content_versions
               (id,organization_id,event_id,accepted_session_id,version,title,abstract,
                content_status,changed_by_user_id,created_at_ms)
               VALUES (?1,?2,?3,?4,1,?5,?6,'draft',?7,?8)"""
        ).bind(
            new_id(),
            organization_id,
            event_id,
            session_id,
            body.title,
            body.abstract,
            auth.actor.user_id,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.session.create",
            target_type="accepted_session",
            target_id=session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
            metadata={
                "source_type": "organizer_created",
                "participant_count": len(body.participant_ids),
            },
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="accepted_session",
        resource_id=session_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409, detail="The session could not be created") from exc
    return await _agenda_model(db, event, revision, auth.actor)


@scheduling_router.delete(
    "/api/v1/admin/events/{event_id}/sessions/{session_id}",
    status_code=204,
    tags=["agenda"],
)
async def delete_manual_session(event_id: str, session_id: str, request: Request) -> Response:
    """Remove an organizer-created session that has never been scheduled."""
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    session = row_mapping(
        await db.prepare(
            """SELECT id,source_type,
                      EXISTS(SELECT 1 FROM agenda_items item
                        WHERE item.accepted_session_id=accepted_sessions.id) AS scheduled
               FROM accepted_sessions
               WHERE id=?1 AND organization_id=?2 AND event_id=?3 LIMIT 1"""
        )
        .bind(session_id, organization_id, event_id)
        .first()
    )
    if session is None:
        return Response(status_code=204)
    if str(session["source_type"]) != "organizer_created" or bool(session["scheduled"]):
        raise HTTPException(
            status_code=409,
            detail="Only an unscheduled organizer-created session can be deleted",
        )
    now = utc_now_ms()
    batch = CommandBatch(db)
    for statement in (
        "DELETE FROM accepted_session_labels WHERE accepted_session_id=?1",
        "DELETE FROM session_content_versions WHERE accepted_session_id=?1",
        "DELETE FROM accepted_session_participants WHERE accepted_session_id=?1",
    ):
        batch.add_statement(db.prepare(statement).bind(session_id))
    batch.add_statement(
        db.prepare(
            "DELETE FROM accepted_sessions WHERE id=?1 AND organization_id=?2 AND event_id=?3"
        ).bind(session_id, organization_id, event_id)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.session.delete",
            target_type="accepted_session",
            target_id=session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
            metadata={"source_type": "organizer_created"},
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409, detail="The session could not be deleted") from exc
    return Response(status_code=204)


@scheduling_router.delete(
    "/api/v1/admin/events/{event_id}/sessions/{session_id}/participants/{participant_id}",
    status_code=204,
    tags=["agenda"],
)
async def remove_manual_session_participant(
    event_id: str, session_id: str, participant_id: str, request: Request
) -> Response:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    participant = row_mapping(
        await db.prepare(
            """SELECT participant.id,participant.event_speaker_id
               FROM accepted_session_participants participant
               JOIN accepted_sessions session ON session.id=participant.accepted_session_id
               WHERE participant.organization_id=?1 AND participant.event_id=?2
                 AND participant.accepted_session_id=?3
                 AND COALESCE(participant.event_speaker_id,participant.pending_invitation_id)=?4
                 AND session.source_type='organizer_created'
                 AND (SELECT COUNT(*) FROM accepted_session_participants remaining
                       WHERE remaining.accepted_session_id=session.id)>1
                 AND NOT EXISTS (
                   SELECT 1 FROM agenda_items item JOIN schedule_revisions revision
                     ON revision.id=item.revision_id
                   WHERE item.accepted_session_id=session.id AND revision.status='published')
               LIMIT 1"""
        )
        .bind(organization_id, event_id, session_id, participant_id)
        .first()
    )
    if participant is None:
        raise HTTPException(
            status_code=409,
            detail="Keep at least one participant and edit only an unpublished organizer session",
        )
    now = utc_now_ms()
    batch = CommandBatch(db)
    if participant["event_speaker_id"] is not None:
        batch.add_statement(
            db.prepare(
                """DELETE FROM agenda_item_speakers
                   WHERE event_speaker_id=?1 AND agenda_item_id IN (
                     SELECT id FROM agenda_items WHERE accepted_session_id=?2)"""
            ).bind(participant["event_speaker_id"], session_id)
        )
    batch.add_statement(
        db.prepare("DELETE FROM accepted_session_participants WHERE id=?1").bind(participant["id"])
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.session.participant.remove",
            target_type="accepted_session",
            target_id=session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
            metadata={"participant_id": participant_id},
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409, detail="The participant could not be removed") from exc
    return Response(status_code=204)


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/setup",
    response_model=AdminAgendaView,
    status_code=201,
    tags=["agenda"],
)
async def setup_admin_agenda(
    event_id: str,
    request: Request,
    body: AgendaSetup,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AdminAgendaView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    key, fingerprint = _key(idempotency_key), _fingerprint(body)
    route = "POST /api/v1/admin/events/{event_id}/agenda/setup"
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND event_id=?4 AND state='completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(key.encode()).digest(), event_id)
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        revision = await _revision(db, organization_id, event_id)
        if revision is None:
            raise HTTPException(status_code=409)
        return await _agenda_model(db, event, revision, auth.actor)
    if await _revision(db, organization_id, event_id) is not None:
        raise HTTPException(status_code=409)

    now, revision_id = utc_now_ms(), new_id()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO schedule_revisions
               (id,organization_id,event_id,revision_number,name,status,version,
                created_by_user_id,created_at_ms,updated_at_ms)
               VALUES (?1,?2,?3,1,'Draft 1','draft',1,?4,?5,?5)"""
        ).bind(revision_id, organization_id, event_id, auth.actor.user_id, now)
    )
    for name in body.room_names:
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_rooms
                   (id,organization_id,event_id,name,status,version,created_at_ms,updated_at_ms)
                   VALUES (?1,?2,?3,?4,'active',1,?5,?5)"""
            ).bind(new_id(), organization_id, event_id, name, now)
        )
    for name in body.track_names:
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_tracks
                   (id,organization_id,event_id,name,is_exclusive,status,version,
                    created_at_ms,updated_at_ms)
                   VALUES (?1,?2,?3,?4,0,'active',1,?5,?5)"""
            ).bind(new_id(), organization_id, event_id, name, now)
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.setup",
            target_type="schedule_revision",
            target_id=revision_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
            metadata={"room_count": len(body.room_names), "track_count": len(body.track_names)},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="schedule_revision",
        resource_id=revision_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    revision = await _revision(db, organization_id, event_id)
    if revision is None:
        raise HTTPException(status_code=409)
    return await _agenda_model(db, event, revision, auth.actor)


async def _create_agenda_resource(
    event_id: str,
    request: Request,
    body: AgendaResourceCreate,
    *,
    resource: str,
) -> AdminAgendaView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    revision = await _revision(db, organization_id, event_id)
    if revision is None:
        raise HTTPException(status_code=409, detail="Create the agenda first")
    existing = row_mapping(
        await db.prepare(_RESOURCE_SELECT_BY_NAME[resource])
        .bind(organization_id, event_id, body.name)
        .first()
    )
    if existing is not None and str(existing["status"]) == "active":
        raise HTTPException(status_code=409, detail=f"That {resource} already exists")
    now, resource_id = utc_now_ms(), str(existing["id"]) if existing else new_id()
    batch = CommandBatch(db)
    if existing is None:
        if resource == "room":
            statement = db.prepare(
                """INSERT INTO event_rooms
                   (id,organization_id,event_id,name,status,version,created_at_ms,updated_at_ms)
                   VALUES (?1,?2,?3,?4,'active',1,?5,?5)"""
            ).bind(resource_id, organization_id, event_id, body.name, now)
        else:
            statement = db.prepare(
                """INSERT INTO event_tracks
                   (id,organization_id,event_id,name,is_exclusive,status,version,
                    created_at_ms,updated_at_ms)
                   VALUES (?1,?2,?3,?4,0,'active',1,?5,?5)"""
            ).bind(resource_id, organization_id, event_id, body.name, now)
    else:
        statement = db.prepare(_RESOURCE_RESTORE[resource]).bind(
            body.name, now, organization_id, event_id, resource_id
        )
    batch.add_statement(statement)
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action=f"agenda.{resource}.create",
            target_type=f"event_{resource}",
            target_id=resource_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return await _agenda_model(db, event, revision, auth.actor)


async def _update_agenda_resource(
    event_id: str,
    resource_id: str,
    request: Request,
    body: AgendaResourceUpdate,
    *,
    resource: str,
) -> AdminAgendaView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    revision = await _revision(db, organization_id, event_id)
    if revision is None:
        raise HTTPException(status_code=409)
    row = row_mapping(
        await db.prepare(_RESOURCE_SELECT_BY_ID[resource])
        .bind(organization_id, event_id, resource_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    if body.status == "archived" or str(row["status"]) == "archived":
        # Rooms and tracks belong to the event. Archiving either one is an
        # exact event-owner/manager operation. Restoring one is the other side
        # of the same lifecycle boundary, even though editors may keep doing
        # ordinary agenda work.
        auth = await require_permission(
            request,
            Permission.RESOURCE_ACCESS_MANAGE,
            ResourceContext(organization_id, event_id),
            mutation=True,
        )
    if int(row["version"]) != body.version:
        raise HTTPException(status_code=409, detail="This item changed. Refresh and try again.")
    if body.status == "archived":
        if resource == "room":
            active = row_mapping(
                await db.prepare(
                    """SELECT count(*) AS total FROM event_rooms
                       WHERE organization_id=?1 AND event_id=?2 AND status='active'"""
                )
                .bind(organization_id, event_id)
                .first()
            )
            if active is not None and int(active["total"]) <= 1:
                raise HTTPException(status_code=409, detail="An agenda needs at least one room")
        used = row_mapping(
            await db.prepare(_RESOURCE_IN_USE[resource])
            .bind(organization_id, event_id, revision["id"], resource_id)
            .first()
        )
        if used is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Move scheduled sessions before archiving this {resource}",
            )
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(_RESOURCE_UPDATE[resource]).bind(
            body.status, now, organization_id, event_id, resource_id, body.version
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action=f"agenda.{resource}.{body.status}",
            target_type=f"event_{resource}",
            target_id=resource_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return await _agenda_model(db, event, revision, auth.actor)


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/rooms",
    response_model=AdminAgendaView,
    tags=["agenda"],
)
async def create_agenda_room(
    event_id: str, request: Request, body: AgendaResourceCreate
) -> AdminAgendaView:
    return await _create_agenda_resource(event_id, request, body, resource="room")


@scheduling_router.patch(
    "/api/v1/admin/events/{event_id}/agenda/rooms/{room_id}",
    response_model=AdminAgendaView,
    tags=["agenda"],
)
async def update_agenda_room(
    event_id: str, room_id: str, request: Request, body: AgendaResourceUpdate
) -> AdminAgendaView:
    return await _update_agenda_resource(event_id, room_id, request, body, resource="room")


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/tracks",
    response_model=AdminAgendaView,
    tags=["agenda"],
)
async def create_agenda_track(
    event_id: str, request: Request, body: AgendaResourceCreate
) -> AdminAgendaView:
    return await _create_agenda_resource(event_id, request, body, resource="track")


@scheduling_router.get(
    "/api/v1/admin/events/{event_id}/agenda/tracks",
    response_model=EventTrackList,
    tags=["agenda"],
)
async def list_event_tracks(event_id: str, request: Request) -> EventTrackList:
    event, _ = await _event_scope(request, event_id, Permission.FORM_MANAGE, mutation=False)
    tracks = result_rows(
        await _db(request)
        .prepare(
            """SELECT id,name,status,version FROM event_tracks
               WHERE organization_id=?1 AND event_id=?2 AND status='active'
               ORDER BY lower(name),id"""
        )
        .bind(str(event["organization_id"]), event_id)
        .all()
    )
    return EventTrackList(
        event_id=event_id,
        data=[
            EventTrackView(
                id=str(track["id"]),
                name=str(track["name"]),
                status=str(track["status"]),
                version=int(track["version"]),
            )
            for track in tracks
        ],
    )


@scheduling_router.patch(
    "/api/v1/admin/events/{event_id}/agenda/tracks/{track_id}",
    response_model=AdminAgendaView,
    tags=["agenda"],
)
async def update_agenda_track(
    event_id: str, track_id: str, request: Request, body: AgendaResourceUpdate
) -> AdminAgendaView:
    return await _update_agenda_resource(event_id, track_id, request, body, resource="track")


@scheduling_router.get(
    "/api/v1/admin/events/{event_id}/labels",
    response_model=EventLabelList,
    tags=["agenda"],
)
async def list_event_labels(event_id: str, request: Request) -> EventLabelList:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=False)
    rows = await _event_label_rows(
        _db(request), str(event["organization_id"]), event_id, auth.actor
    )
    return EventLabelList(event_id=event_id, data=rows)


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/labels",
    response_model=EventLabelView,
    status_code=201,
    tags=["agenda"],
)
async def create_event_label(
    event_id: str, request: Request, body: EventLabelCreate
) -> EventLabelView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    active = row_mapping(
        await db.prepare(
            """SELECT COUNT(*) AS total FROM event_labels
               WHERE organization_id=?1 AND event_id=?2 AND status='active'"""
        )
        .bind(organization_id, event_id)
        .first()
    )
    if active is not None and int(active["total"]) >= 100:
        raise HTTPException(status_code=409, detail="An event can have up to 100 labels")
    duplicate = row_mapping(
        await db.prepare(
            """SELECT id FROM event_labels WHERE organization_id=?1 AND event_id=?2
                 AND status='active' AND lower(name)=lower(?3) LIMIT 1"""
        )
        .bind(organization_id, event_id, body.name)
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="That label already exists")
    now, label_id = utc_now_ms(), new_id()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,version,
                created_at_ms,updated_at_ms)
               VALUES (?1,'label',?2,?2,'active',1,?3,?3)"""
        ).bind(label_id, auth.actor.user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_labels
               (id,organization_id,event_id,name,color,status,version,
                created_at_ms,updated_at_ms,archived_at_ms)
               VALUES (?1,?2,?3,?4,?5,'active',1,?6,?6,NULL)"""
        ).bind(label_id, organization_id, event_id, body.name, body.color, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="label.create",
            target_type="event_label",
            target_id=label_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409, detail="That label already exists") from exc
    return EventLabelView(
        id=label_id,
        name=body.name,
        color=body.color,
        status="active",
        version=1,
        can_manage=True,
    )


@scheduling_router.patch(
    "/api/v1/admin/events/{event_id}/labels/{label_id}",
    response_model=EventLabelView,
    tags=["agenda"],
)
async def update_event_label(
    event_id: str,
    label_id: str,
    request: Request,
    body: EventLabelUpdate,
) -> EventLabelView:
    event, _ = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=False)
    db, organization_id = _db(request), str(event["organization_id"])
    current = row_mapping(
        await db.prepare(
            """SELECT id,name,color,status,version FROM event_labels
               WHERE organization_id=?1 AND event_id=?2 AND id=?3 LIMIT 1"""
        )
        .bind(organization_id, event_id, label_id)
        .first()
    )
    if current is None:
        raise HTTPException(status_code=404)
    if body.status == "archived" or str(current["status"]) == "archived":
        # Label lifecycle belongs to the event owner/managers. This prevents
        # a departed label creator from orphaning an active label forever.
        auth = await require_permission(
            request,
            Permission.RESOURCE_ACCESS_MANAGE,
            ResourceContext(organization_id, event_id),
            mutation=True,
        )
    else:
        auth = await require_permission(
            request,
            Permission.LABEL_MANAGE,
            ResourceContext(organization_id, event_id, resource_id=label_id),
            mutation=True,
        )
    if int(current["version"]) != body.version:
        raise HTTPException(status_code=409, detail="This label changed. Refresh and try again.")
    if body.status == "archived":
        assignment = row_mapping(
            await db.prepare(
                """SELECT accepted_session_id FROM accepted_session_labels
                   WHERE organization_id=?1 AND event_id=?2 AND label_id=?3 LIMIT 1"""
            )
            .bind(organization_id, event_id, label_id)
            .first()
        )
        if assignment is not None:
            raise HTTPException(
                status_code=409,
                detail="Remove this label from every session before archiving it",
            )
    now = utc_now_ms()
    archived_at = now if body.status == "archived" else None
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE event_labels SET name=?1,color=?2,status=?3,
                  version=version+1,updated_at_ms=?4,archived_at_ms=?5
               WHERE organization_id=?6 AND event_id=?7 AND id=?8 AND version=?9"""
        ).bind(
            body.name,
            body.color,
            body.status,
            now,
            archived_at,
            organization_id,
            event_id,
            label_id,
            body.version,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_label_write_guards
               (id,label_id,applied_changes,created_at_ms)
               VALUES (?1,?2,changes(),?3)"""
        ).bind(new_id(), label_id, now)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE owned_resources SET status=?1,version=version+1,
                  updated_at_ms=?2,archived_at_ms=?3
               WHERE id=?4 AND version=?5"""
        ).bind(body.status, now, archived_at, label_id, body.version)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="label.archive" if body.status == "archived" else "label.update",
            target_type="event_label",
            target_id=label_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409, detail="The label could not be updated") from exc
    saved = row_mapping(
        await db.prepare(
            """SELECT id,name,color,status,version FROM event_labels
               WHERE organization_id=?1 AND event_id=?2 AND id=?3 LIMIT 1"""
        )
        .bind(organization_id, event_id, label_id)
        .first()
    )
    if saved is None:
        raise HTTPException(status_code=409)
    return _label_view(saved, auth.actor, event_id)


@scheduling_router.put(
    "/api/v1/admin/events/{event_id}/sessions/{session_id}/labels",
    response_model=SessionLabelAssignmentView,
    tags=["agenda"],
)
async def assign_session_labels(
    event_id: str,
    session_id: str,
    request: Request,
    body: SessionLabelAssignmentUpdate,
) -> SessionLabelAssignmentView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    session = row_mapping(
        await db.prepare(
            """SELECT id,label_version FROM accepted_sessions
               WHERE organization_id=?1 AND event_id=?2 AND id=?3 LIMIT 1"""
        )
        .bind(organization_id, event_id, session_id)
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404)
    if int(session["label_version"]) != body.version:
        raise HTTPException(status_code=409, detail="These labels changed. Refresh and try again.")
    active_labels = result_rows(
        await db.prepare(
            """SELECT id FROM event_labels WHERE organization_id=?1 AND event_id=?2
                 AND status='active' ORDER BY id"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    active_ids = {str(row["id"]) for row in active_labels}
    if not set(body.label_ids).issubset(active_ids):
        raise HTTPException(status_code=422, detail="Choose active labels from this event")
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE accepted_sessions SET label_version=label_version+1
               WHERE organization_id=?1 AND event_id=?2 AND id=?3 AND label_version=?4"""
        ).bind(organization_id, event_id, session_id, body.version)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO session_label_write_guards
               (id,accepted_session_id,applied_changes,created_at_ms)
               VALUES (?1,?2,changes(),?3)"""
        ).bind(new_id(), session_id, now)
    )
    batch.add_statement(
        db.prepare(
            """DELETE FROM accepted_session_labels
               WHERE organization_id=?1 AND event_id=?2 AND accepted_session_id=?3"""
        ).bind(organization_id, event_id, session_id)
    )
    for label_id_value in body.label_ids:
        batch.add_statement(
            db.prepare(
                """INSERT INTO accepted_session_labels
                   (organization_id,event_id,accepted_session_id,label_id,
                    assigned_by_user_id,created_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6)"""
            ).bind(
                organization_id,
                event_id,
                session_id,
                label_id_value,
                auth.actor.user_id,
                now,
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.session_labels.update",
            target_type="accepted_session",
            target_id=session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
            metadata={"label_count": len(body.label_ids)},
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409, detail="The session labels changed") from exc
    payload = [{"session_id": session_id}]
    await _attach_session_labels(db, organization_id, event_id, payload, auth.actor)
    return SessionLabelAssignmentView(
        session_id=session_id,
        version=body.version + 1,
        labels=[_agenda_label(label) for label in payload[0]["labels"]],
    )


def _agenda_date(timestamp_ms: int, time_zone: str) -> str:
    try:
        zone = ZoneInfo(time_zone)
    except Exception:
        zone = UTC
    return datetime.fromtimestamp(timestamp_ms / 1000, zone).strftime("%Y-%m-%d")


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/auto-schedule",
    response_model=AutoScheduledAgendaView,
    response_model_exclude_unset=True,
    tags=["agenda"],
)
async def auto_schedule_agenda(
    event_id: str,
    request: Request,
    body: AgendaAutoSchedule,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AutoScheduledAgendaView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    revision = await _revision(db, organization_id, event_id)
    if revision is None:
        raise HTTPException(status_code=409)
    key, fingerprint = _key(idempotency_key), _fingerprint(body)
    route = "POST /api/v1/admin/events/{event_id}/agenda/auto-schedule"
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND event_id=?4 AND state='completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(key.encode()).digest(), event_id)
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        agenda = await _agenda_model(db, event, revision, auth.actor)
        return AutoScheduledAgendaView.model_validate(agenda.model_dump())
    rooms = result_rows(
        await db.prepare(
            """SELECT id FROM event_rooms WHERE organization_id=?1 AND event_id=?2
               AND status='active' ORDER BY name,id"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    available_rooms = [str(row["id"]) for row in rooms]
    if body.room_ids:
        if not set(body.room_ids).issubset(set(available_rooms)):
            raise HTTPException(status_code=422, detail="Choose active rooms from this event")
        available_rooms = body.room_ids
    if not available_rooms:
        raise HTTPException(status_code=409, detail="Add an active room first")
    sessions = result_rows(
        await db.prepare(
            """SELECT ac.id,COALESCE(s.proposal_title,ac.organizer_title) AS title
               FROM accepted_sessions ac LEFT JOIN submissions s ON s.id=ac.submission_id
               WHERE ac.organization_id=?1 AND ac.event_id=?2
                 AND ac.lifecycle_status='active' AND NOT EXISTS (
                 SELECT 1 FROM agenda_items ai WHERE ai.revision_id=?3
                   AND ai.accepted_session_id=ac.id)
               ORDER BY title,ac.id"""
        )
        .bind(organization_id, event_id, revision["id"])
        .all()
    )
    existing = result_rows(
        await db.prepare(
            """SELECT ai.id,ai.room_id,ai.starts_at_ms,ai.ends_at_ms,
                      COALESCE((SELECT group_concat(identity, ',') FROM (
                        SELECT ais.event_speaker_id AS identity
                          FROM agenda_item_speakers ais
                         WHERE ais.agenda_item_id=ai.id AND ais.revision_id=ai.revision_id
                        UNION
                        SELECT participant.event_speaker_id
                          FROM accepted_session_participants participant
                         WHERE participant.accepted_session_id=ai.accepted_session_id
                           AND participant.event_speaker_id IS NOT NULL
                        UNION
                        SELECT 'invite:' || participant.pending_invitation_id
                          FROM accepted_session_participants participant
                         WHERE participant.accepted_session_id=ai.accepted_session_id
                           AND participant.pending_invitation_id IS NOT NULL
                      )),'') AS speaker_ids
               FROM agenda_items ai
               WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3"""
        )
        .bind(organization_id, event_id, revision["id"])
        .all()
    )
    allocations = [
        {
            "room_id": str(row["room_id"]),
            "start": int(row["starts_at_ms"]),
            "end": int(row["ends_at_ms"]),
            "speakers": frozenset(filter(None, str(row["speaker_ids"]).split(","))),
        }
        for row in existing
    ]
    start = max(int(event["starts_at_ms"]), body.start_at_ms or int(event["starts_at_ms"]))
    duration = body.session_minutes * 60_000
    gap = body.gap_minutes * 60_000
    planned: list[dict[str, object]] = []
    for session in sessions:
        speaker_ids = frozenset(
            await _speaker_ids(db, organization_id, event_id, str(session["id"]))
        )
        choices: list[tuple[int, str]] = []
        for room_id in available_rooms:
            candidate = start
            while candidate + duration <= int(event["ends_at_ms"]):
                conflicts = [
                    value
                    for value in allocations
                    if value["start"] < candidate + duration
                    and value["end"] > candidate
                    and (value["room_id"] == room_id or bool(value["speakers"] & speaker_ids))
                ]
                if not conflicts:
                    choices.append((candidate, room_id))
                    break
                candidate = max(int(value["end"]) + gap for value in conflicts)
        if not choices:
            continue
        starts_at_ms, room_id = min(choices, key=lambda value: (value[0], value[1]))
        allocation = {
            "id": new_id(),
            "session_id": str(session["id"]),
            "room_id": room_id,
            "start": starts_at_ms,
            "end": starts_at_ms + duration,
            "speakers": speaker_ids,
        }
        allocations.append(allocation)
        planned.append(allocation)
    now = utc_now_ms()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    for item in planned:
        batch.add_statement(
            db.prepare(
                """INSERT INTO agenda_items
                   (id,organization_id,event_id,revision_id,accepted_session_id,room_id,
                    track_id,event_date,event_time_zone,starts_at_ms,ends_at_ms,version,
                    created_at_ms,updated_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6,NULL,?7,?8,?9,?10,1,?11,?11)"""
            ).bind(
                item["id"],
                organization_id,
                event_id,
                revision["id"],
                item["session_id"],
                item["room_id"],
                _agenda_date(int(item["start"]), str(event["time_zone"])),
                event["time_zone"],
                item["start"],
                item["end"],
                now,
            )
        )
        for speaker_id in item["speakers"]:
            if str(speaker_id).startswith("invite:"):
                continue
            batch.add_statement(
                db.prepare(
                    """INSERT INTO agenda_item_speakers
                       (id,organization_id,event_id,revision_id,agenda_item_id,
                        event_speaker_id,created_at_ms)
                       VALUES (?1,?2,?3,?4,?5,?6,?7)"""
                ).bind(
                    new_id(),
                    organization_id,
                    event_id,
                    revision["id"],
                    item["id"],
                    speaker_id,
                    now,
                )
            )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.auto_schedule",
            target_type="schedule_revision",
            target_id=str(revision["id"]),
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
            metadata={"scheduled_count": len(planned), "requested_count": len(sessions)},
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="schedule_revision",
        resource_id=str(revision["id"]),
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    agenda = await _agenda_model(db, event, revision, auth.actor)
    return AutoScheduledAgendaView(
        **agenda.model_dump(),
        auto_schedule=AgendaAutoScheduleResult(
            scheduled_count=len(planned),
            remaining_count=len(sessions) - len(planned),
        ),
    )


async def _slot(request: Request, event, revision, body: AgendaCandidate) -> AgendaSlot:
    speaker_ids = await _speaker_ids(
        _db(request), str(event["organization_id"]), str(event["id"]), body.session_id
    )
    if not speaker_ids:
        session_exists = await (
            _db(request)
            .prepare(
                """SELECT id FROM accepted_sessions
                   WHERE organization_id=?1 AND event_id=?2 AND id=?3
                     AND lifecycle_status='active' LIMIT 1"""
            )
            .bind(event["organization_id"], event["id"], body.session_id)
            .first("id")
        )
        if session_exists is None:
            raise HTTPException(status_code=404)
    event_date = body.event_date or _agenda_date(body.start_at_ms, str(event["time_zone"]))
    return AgendaSlot(
        organization_id=str(event["organization_id"]),
        event_id=str(event["id"]),
        revision_id=str(revision["id"]),
        item_id=body.item_id,
        room_id=body.room_id,
        track_id=body.track_id,
        event_date=event_date,
        event_time_zone=str(event["time_zone"]),
        starts_at_ms=body.start_at_ms,
        ends_at_ms=body.end_at_ms,
        speaker_ids=speaker_ids,
    )


def _conflict_view(conflicts) -> list[AgendaConflictView]:
    messages = {
        "room": "The room is already in use during this time.",
        "speaker": "A speaker is already scheduled during this time.",
        "track": "This exclusive track already has a session during this time.",
    }
    return [
        AgendaConflictView(
            code=item.kind,
            message=messages[item.kind],
            conflicting_item_id=str(item.item_id),
        )
        for item in conflicts
    ]


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/preview",
    response_model=AgendaPreviewView,
    tags=["agenda"],
)
async def preview_agenda(
    event_id: str, request: Request, body: AgendaCandidate
) -> AgendaPreviewView:
    event, _ = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    revision = await _revision(_db(request), str(event["organization_id"]), event_id)
    if revision is None:
        raise HTTPException(status_code=409)
    try:
        conflicts = await AgendaRepository(_db(request)).preview_conflicts(
            await _slot(request, event, revision, body)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422) from exc
    return AgendaPreviewView(valid=not conflicts, conflicts=_conflict_view(conflicts))


async def _saved_item(db, organization_id: str, event_id: str, item_id: str) -> AgendaItemView:
    row = row_mapping(
        await db.prepare(
            """SELECT ai.id,ai.accepted_session_id AS session_id,
                  COALESCE(s.proposal_title,ac.organizer_title) AS title,
                  ai.starts_at_ms AS start_at_ms,ai.ends_at_ms AS end_at_ms,
                  ai.room_id,r.name AS room_name,ai.track_id,t.name AS track_name,ai.version
           FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
           LEFT JOIN submissions s ON s.id=ac.submission_id JOIN event_rooms r ON r.id=ai.room_id
           LEFT JOIN event_tracks t ON t.id=ai.track_id
           WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.id=?3"""
        )
        .bind(organization_id, event_id, item_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return AgendaItemView(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        title=str(row["title"]),
        start_at_ms=int(row["start_at_ms"]),
        end_at_ms=int(row["end_at_ms"]),
        room_id=str(row["room_id"]),
        room_name=str(row["room_name"]),
        track_id=str(row["track_id"]) if row["track_id"] is not None else None,
        track_name=str(row["track_name"]) if row["track_name"] is not None else None,
        version=int(row["version"]),
    )


async def _save_item(
    event_id: str,
    request: Request,
    body: AgendaCandidate,
    key_value: str | None,
    item_id: str | None,
) -> AgendaItemView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    revision = await _revision(db, organization_id, event_id)
    if revision is None or (item_id is not None and body.item_id != item_id):
        raise HTTPException(status_code=409)
    key, fingerprint = _key(key_value), _fingerprint(body)
    route = (
        "POST /api/v1/admin/events/{event_id}/agenda/items"
        if item_id is None
        else "PATCH /api/v1/admin/events/{event_id}/agenda/items/{item_id}"
    )
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id
               FROM idempotency_records WHERE principal_key=?1 AND route_key=?2
                 AND idempotency_key_hash=?3 AND state='completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(key.encode()).digest())
        .first()
    )
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _saved_item(db, organization_id, event_id, str(replay["response_resource_id"]))
    slot = await _slot(request, event, revision, body)
    now, saved_id = utc_now_ms(), item_id or new_id()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    if item_id is None:
        batch.add_statement(
            db.prepare(
                """INSERT INTO agenda_items
               (id,organization_id,event_id,revision_id,accepted_session_id,room_id,track_id,
                event_date,event_time_zone,starts_at_ms,ends_at_ms,version,created_at_ms,updated_at_ms)
               VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,1,?12,?12)"""
            ).bind(
                saved_id,
                organization_id,
                event_id,
                revision["id"],
                body.session_id,
                body.room_id,
                body.track_id,
                slot.event_date,
                event["time_zone"],
                body.start_at_ms,
                body.end_at_ms,
                now,
            )
        )
        for speaker_id in slot.speaker_ids:
            if speaker_id.startswith("invite:"):
                continue
            batch.add_statement(
                db.prepare(
                    """INSERT INTO agenda_item_speakers
                   (id,organization_id,event_id,revision_id,agenda_item_id,event_speaker_id,created_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6,?7)"""
                ).bind(
                    new_id(), organization_id, event_id, revision["id"], saved_id, speaker_id, now
                )
            )
    else:
        batch.add_statement(
            db.prepare(
                """UPDATE agenda_items SET room_id=?1,track_id=?2,event_date=?3,
                      event_time_zone=?4,starts_at_ms=?5,ends_at_ms=?6,
                      version=version+1,updated_at_ms=?7
                   WHERE organization_id=?8 AND event_id=?9 AND revision_id=?10
                     AND id=?11 AND version=?12"""
            ).bind(
                body.room_id,
                body.track_id,
                slot.event_date,
                event["time_zone"],
                body.start_at_ms,
                body.end_at_ms,
                now,
                organization_id,
                event_id,
                revision["id"],
                saved_id,
                body.version,
            )
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO agenda_write_guards
               (id,agenda_item_id,applied_changes,created_at_ms)
               VALUES (?1,?2,changes(),?3)"""
            ).bind(new_id(), saved_id, now)
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.item.create" if item_id is None else "agenda.item.move",
            target_type="agenda_item",
            target_id=saved_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
        )
    )
    batch.complete_idempotency(
        record,
        status=201 if item_id is None else 200,
        resource_type="agenda_item",
        resource_id=saved_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    row = await _saved_item(db, organization_id, event_id, saved_id)
    if item_id is not None and row.version != body.version + 1:
        raise HTTPException(status_code=409)
    return row


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/items",
    response_model=AgendaItemView,
    status_code=201,
    tags=["agenda"],
)
async def create_agenda_item(
    event_id: str,
    request: Request,
    body: AgendaCandidate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgendaItemView:
    if body.item_id is not None:
        raise HTTPException(status_code=400)
    return await _save_item(event_id, request, body, idempotency_key, None)


@scheduling_router.patch(
    "/api/v1/admin/events/{event_id}/agenda/items/{item_id}",
    response_model=AgendaItemView,
    tags=["agenda"],
)
async def move_agenda_item(
    event_id: str,
    item_id: str,
    request: Request,
    body: AgendaCandidate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgendaItemView:
    return await _save_item(event_id, request, body, idempotency_key, item_id)


@scheduling_router.delete(
    "/api/v1/admin/events/{event_id}/agenda/items/{item_id}",
    status_code=204,
    tags=["agenda"],
)
async def unschedule_agenda_item(
    event_id: str,
    item_id: str,
    request: Request,
    version: int = Query(ge=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    revision = await _revision(db, organization_id, event_id)
    if revision is None:
        raise HTTPException(status_code=409)
    key = _key(idempotency_key)
    route = "DELETE /api/v1/admin/events/{event_id}/agenda/items/{item_id}"
    fingerprint = hashlib.sha256(f"{item_id}:{version}".encode()).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND event_id=?4 AND state='completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(key.encode()).digest(), event_id)
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return Response(status_code=204)
    item = row_mapping(
        await db.prepare(
            """SELECT id FROM agenda_items WHERE organization_id=?1 AND event_id=?2
                 AND revision_id=?3 AND id=?4 AND version=?5 LIMIT 1"""
        )
        .bind(organization_id, event_id, revision["id"], item_id, version)
        .first()
    )
    if item is None:
        raise HTTPException(status_code=409)
    now = utc_now_ms()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """DELETE FROM agenda_item_speakers WHERE organization_id=?1 AND event_id=?2
                 AND revision_id=?3 AND agenda_item_id=?4"""
        ).bind(organization_id, event_id, revision["id"], item_id)
    )
    batch.add_statement(
        db.prepare(
            """DELETE FROM agenda_items WHERE organization_id=?1 AND event_id=?2
                 AND revision_id=?3 AND id=?4 AND version=?5"""
        ).bind(organization_id, event_id, revision["id"], item_id, version)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO agenda_write_guards
               (id,agenda_item_id,applied_changes,created_at_ms)
               VALUES (?1,?2,changes(),?3)"""
        ).bind(new_id(), item_id, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.item.unschedule",
            target_type="agenda_item",
            target_id=item_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
        )
    )
    batch.complete_idempotency(
        record,
        status=204,
        resource_type="agenda_item",
        resource_id=item_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return Response(status_code=204)


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/publish",
    response_model=AgendaPublishView,
    tags=["agenda"],
)
async def publish_agenda(
    event_id: str,
    request: Request,
    body: AgendaPublish,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgendaPublishView:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    key = _key(idempotency_key)
    route = "POST /api/v1/admin/events/{event_id}/agenda/publish"
    fingerprint = hashlib.sha256(
        json.dumps(
            {"event_id": event_id, "body": body.model_dump()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id
               FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND organization_id=?4 AND event_id=?5 AND state='completed'"""
        )
        .bind(
            auth.actor.user_id,
            route,
            hashlib.sha256(key.encode()).digest(),
            organization_id,
            event_id,
        )
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        replayed_draft_id = str(replay["response_resource_id"])
        replayed_draft = row_mapping(
            await db.prepare(
                """SELECT id FROM schedule_revisions
                   WHERE id=?1 AND organization_id=?2 AND event_id=?3 LIMIT 1"""
            )
            .bind(replayed_draft_id, organization_id, event_id)
            .first()
        )
        if replayed_draft is None:
            raise HTTPException(status_code=409)
        return AgendaPublishView(
            published_revision_id=body.revision_id,
            published_version=body.version + 1,
            draft_revision_id=replayed_draft_id,
        )
    draft = await _revision(db, organization_id, event_id)
    if (
        draft is None
        or str(draft["id"]) != body.revision_id
        or int(draft["version"]) != body.version
    ):
        raise HTTPException(status_code=409)
    item_rows = result_rows(
        await db.prepare(
            """SELECT ai.*,COALESCE(s.proposal_title,ac.organizer_title) AS proposal_title,
                      COALESCE(s.proposal_abstract,ac.organizer_abstract) AS proposal_abstract,
                      r.name AS room_name
               FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
               LEFT JOIN submissions s ON s.id=ac.submission_id
               JOIN event_rooms r ON r.id=ai.room_id
               WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
                 AND ac.lifecycle_status='active'
               ORDER BY ai.id"""
        )
        .bind(organization_id, event_id, body.revision_id)
        .all()
    )
    if not item_rows:
        raise HTTPException(status_code=409)
    speaker_rows = result_rows(
        await db.prepare(
            """SELECT ais.agenda_item_id,ais.event_speaker_id
               FROM agenda_item_speakers ais
               WHERE ais.organization_id=?1 AND ais.event_id=?2 AND ais.revision_id=?3
               UNION
               SELECT item.id,participant.event_speaker_id
                 FROM agenda_items item
                 JOIN accepted_session_participants participant
                   ON participant.accepted_session_id=item.accepted_session_id
                WHERE item.organization_id=?1 AND item.event_id=?2 AND item.revision_id=?3
                  AND participant.event_speaker_id IS NOT NULL
               ORDER BY agenda_item_id,event_speaker_id"""
        )
        .bind(organization_id, event_id, body.revision_id)
        .all()
    )
    by_item: dict[str, list[str]] = {}
    for row in speaker_rows:
        by_item.setdefault(str(row["agenda_item_id"]), []).append(str(row["event_speaker_id"]))
    now, next_revision_id = utc_now_ms(), new_id()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    for speaker_row in speaker_rows:
        batch.add_statement(
            db.prepare(
                """INSERT OR IGNORE INTO agenda_item_speakers
                   (id,organization_id,event_id,revision_id,agenda_item_id,
                    event_speaker_id,created_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6,?7)"""
            ).bind(
                new_id(),
                organization_id,
                event_id,
                body.revision_id,
                speaker_row["agenda_item_id"],
                speaker_row["event_speaker_id"],
                now,
            )
        )
    batch.add_statement(
        db.prepare(
            """UPDATE schedule_revisions SET status='superseded',version=version+1,
                      updated_at_ms=?1
               WHERE organization_id=?2 AND event_id=?3
                      AND status='published'"""
        ).bind(now, organization_id, event_id)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE schedule_revisions SET status='published',published_at_ms=?1,
                      version=version+1,updated_at_ms=?1
               WHERE organization_id=?2 AND event_id=?3 AND id=?4
                 AND status='draft' AND version=?5"""
        ).bind(now, organization_id, event_id, body.revision_id, body.version)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO schedule_revisions
               (id,organization_id,event_id,revision_number,name,status,version,
                created_by_user_id,created_at_ms,updated_at_ms)
               VALUES (?1,?2,?3,?4,?5,'draft',1,?6,?7,?7)"""
        ).bind(
            next_revision_id,
            organization_id,
            event_id,
            int(draft["revision_number"]) + 1,
            f"Draft {int(draft['revision_number']) + 1}",
            auth.actor.user_id,
            now,
        )
    )
    clone_ids: dict[str, str] = {}
    for row in item_rows:
        clone_id = new_id()
        clone_ids[str(row["id"])] = clone_id
        batch.add_statement(
            db.prepare(
                """INSERT INTO agenda_items
                   (id,organization_id,event_id,revision_id,accepted_session_id,room_id,
                    track_id,event_date,event_time_zone,starts_at_ms,ends_at_ms,version,
                    created_at_ms,updated_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,1,?12,?12)"""
            ).bind(
                clone_id,
                organization_id,
                event_id,
                next_revision_id,
                row["accepted_session_id"],
                row["room_id"],
                row["track_id"],
                row["event_date"],
                row["event_time_zone"],
                row["starts_at_ms"],
                row["ends_at_ms"],
                now,
            )
        )
    for row in speaker_rows:
        batch.add_statement(
            db.prepare(
                """INSERT INTO agenda_item_speakers
                   (id,organization_id,event_id,revision_id,agenda_item_id,
                    event_speaker_id,created_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6,?7)"""
            ).bind(
                new_id(),
                organization_id,
                event_id,
                next_revision_id,
                clone_ids[str(row["agenda_item_id"])],
                row["event_speaker_id"],
                now,
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="agenda.publish",
            target_type="schedule_revision",
            target_id=body.revision_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            organization_id=organization_id,
            event_id=event_id,
            occurred_at_ms=now,
            metadata={"item_count": len(item_rows)},
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="schedule_revision",
        resource_id=next_revision_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc

    organizer = str(getattr(request.scope.get("env"), "CALENDAR_FROM", "events@local.invalid"))
    for row in item_rows:
        recipients = result_rows(
            await db.prepare(
                """SELECT u.id AS user_id,u.email,p.display_name
                   FROM agenda_item_speakers ais
                   JOIN event_speakers es ON es.id=ais.event_speaker_id
                   JOIN people p ON p.id=es.person_id JOIN users u ON u.id=p.user_id
                   WHERE ais.organization_id=?1 AND ais.event_id=?2
                     AND ais.revision_id=?3 AND ais.agenda_item_id=?4
                     AND u.status='active' ORDER BY u.id"""
            )
            .bind(organization_id, event_id, body.revision_id, row["id"])
            .all()
        )
        try:
            await queue_calendar_changes(
                db,
                AgendaCalendarChange(
                    organization_id=organization_id,
                    event_id=event_id,
                    agenda_item_id=str(row["id"]),
                    starts_at_ms=int(row["starts_at_ms"]),
                    ends_at_ms=int(row["ends_at_ms"]),
                    title=str(row["proposal_title"]),
                    description=str(row["proposal_abstract"]),
                    room=str(row["room_name"]),
                    organizer_email=organizer,
                    published=True,
                ),
                [
                    ScheduleSpeaker(
                        str(value["user_id"]),
                        str(value["email"]),
                        str(value["display_name"]),
                    )
                    for value in recipients
                ],
                now_ms=now,
            )
        except PersistenceError:
            # Publication already committed durable sync intent. A consumer can
            # safely replay it using deterministic calendar keys.
            continue
    return AgendaPublishView(
        published_revision_id=body.revision_id,
        published_version=body.version + 1,
        draft_revision_id=next_revision_id,
    )


def _schedule_label(row: dict) -> ScheduleLabelView:
    return ScheduleLabelView(
        id=str(row["id"]),
        name=str(row["name"]),
        color=str(row["color"]),
    )


def _schedule_item(row: dict) -> ScheduleItemView:
    return ScheduleItemView(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        title=str(row["title"]),
        start_at_ms=int(row["start_at_ms"]),
        end_at_ms=int(row["end_at_ms"]),
        room_name=str(row["room_name"]),
        track_name=str(row["track_name"]) if row["track_name"] is not None else None,
        speaker_names=str(row["speaker_names"]),
        labels=[_schedule_label(label) for label in row["labels"]],
        label_ids=[str(label_id) for label_id in row["label_ids"]],
    )


def _public_schedule_item(row: dict) -> PublicScheduleItemView:
    item = _schedule_item(row)
    return PublicScheduleItemView(
        **item.model_dump(),
        description=str(row["description"]),
        format_name=str(row["format_name"]),
        speaker_details=str(row["speaker_details"]),
    )


@scheduling_router.get(
    "/api/v1/events/{event_id}/schedule",
    response_model=ScheduleView,
    tags=["agenda"],
)
async def get_schedule(event_id: str, request: Request) -> ScheduleView:
    db = _db(request)
    event = row_mapping(
        await db.prepare(
            """SELECT id,organization_id,name,time_zone,starts_at_ms,ends_at_ms
           FROM events WHERE id=?1 AND status!='archived' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    auth = await authenticate_request(request)
    permission = (
        Permission.SPEAKER_TASK_READ_OWN
        if auth.actor.active_persona is Persona.SPEAKER
        else Permission.DASHBOARD_READ
    )
    await require_permission(
        request,
        permission,
        ResourceContext(
            str(event["organization_id"]), event_id, resource_owner_user_id=auth.actor.user_id
        ),
        mutation=False,
    )
    revision = await _revision(_db(request), str(event["organization_id"]), event_id, "published")
    if revision is None:
        raise HTTPException(status_code=404)
    items = result_rows(
        await _db(request)
        .prepare(
            """SELECT ai.id,ac.id AS session_id,
                  COALESCE(s.proposal_title,ac.organizer_title) AS title,
                  ai.starts_at_ms AS start_at_ms,
                  ai.ends_at_ms AS end_at_ms,r.name AS room_name,t.name AS track_name,
                  CASE WHEN ac.source_type='organizer_created' THEN
                    COALESCE((SELECT group_concat(participant.display_name_snapshot, ', ')
                      FROM accepted_session_participants participant
                      WHERE participant.accepted_session_id=ac.id),'')
                  ELSE COALESCE(group_concat(ss.snapshot_name, ', '),'') END AS speaker_names
           FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
           LEFT JOIN submissions s ON s.id=ac.submission_id JOIN event_rooms r ON r.id=ai.room_id
           LEFT JOIN event_tracks t ON t.id=ai.track_id
           LEFT JOIN submission_speakers ss ON ss.submission_id=ac.submission_id
           WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
             AND ac.lifecycle_status='active'
           GROUP BY ai.id ORDER BY ai.starts_at_ms,ai.id"""
        )
        .bind(event["organization_id"], event_id, revision["id"])
        .all()
    )
    await _attach_session_labels(_db(request), str(event["organization_id"]), event_id, items)
    return ScheduleView(
        event=ScheduleEventView(
            id=str(event["id"]),
            name=str(event["name"]),
            time_zone=str(event["time_zone"]),
        ),
        revision=ScheduleRevisionView(
            id=str(revision["id"]),
            version=int(revision["version"]),
            revision_number=int(revision["revision_number"]),
        ),
        items=[_schedule_item(item) for item in items],
    )


@scheduling_router.get(
    "/api/v1/public/events/{event_id}/schedule",
    response_model=PublicScheduleView,
    tags=["public-program"],
)
async def get_public_schedule(
    event_id: str, request: Request, response: Response
) -> PublicScheduleView:
    # A publication replaces the event's canonical revision at this same URL. Allowing
    # stale responses here makes a successful publish appear to have failed for as long
    # as five minutes, and there is no revision-specific URL the browser can switch to.
    # Prefer immediate correctness; static schedule assets remain independently cacheable.
    response.headers["Cache-Control"] = "no-store"
    db = _db(request)
    event = row_mapping(
        await db.prepare(
            """SELECT id,organization_id,name,time_zone,accent_color,logo_url,
                      cover_image_url,website_url
               FROM events WHERE id=?1 AND status='active' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    revision = await _revision(db, str(event["organization_id"]), event_id, "published")
    if revision is None:
        return PublicScheduleView(
            event=PublicScheduleEventView(
                id=str(event["id"]),
                name=str(event["name"]),
                time_zone=str(event["time_zone"]),
                accent_color=(
                    str(event["accent_color"]) if event["accent_color"] is not None else None
                ),
                logo_url=str(event["logo_url"]) if event["logo_url"] is not None else None,
                cover_image_url=(
                    str(event["cover_image_url"]) if event["cover_image_url"] is not None else None
                ),
                website_url=(
                    str(event["website_url"]) if event["website_url"] is not None else None
                ),
            ),
            revision=None,
            items=[],
        )
    items = result_rows(
        await db.prepare(
            """SELECT ai.id,ac.id AS session_id,
                      COALESCE(s.proposal_title,ac.organizer_title) AS title,
                      COALESCE(s.proposal_abstract,ac.organizer_abstract) AS description,
                      COALESCE(NULLIF(json_extract(s.answers_json,'$.format'),''),
                        NULLIF(json_extract(s.answers_json,'$.session_type'),''),
                        'Session') AS format_name,
                      CASE WHEN ac.source_type='organizer_created' THEN
                        COALESCE((SELECT group_concat(NULLIF(trim(
                          COALESCE(person.job_title,'') ||
                          CASE WHEN person.job_title IS NOT NULL AND person.company IS NOT NULL
                            AND trim(person.job_title)!='' AND trim(person.company)!=''
                            THEN ' at ' ELSE '' END || COALESCE(person.company,'')),''), ' · ')
                          FROM accepted_session_participants participant
                          JOIN event_speakers speaker ON speaker.id=participant.event_speaker_id
                          JOIN people person ON person.id=speaker.person_id
                          WHERE participant.accepted_session_id=ac.id),'')
                      ELSE COALESCE((SELECT group_concat(NULLIF(trim(
                        COALESCE(person.job_title,'') ||
                        CASE WHEN person.job_title IS NOT NULL AND person.company IS NOT NULL
                          AND trim(person.job_title)!='' AND trim(person.company)!=''
                          THEN ' at ' ELSE '' END || COALESCE(person.company,'')),''), ' · ')
                        FROM submission_speakers linked
                        JOIN event_speakers speaker ON speaker.id=linked.event_speaker_id
                        JOIN people person ON person.id=speaker.person_id
                        WHERE linked.submission_id=ac.submission_id),'') END AS speaker_details,
                      ai.starts_at_ms AS start_at_ms,ai.ends_at_ms AS end_at_ms,
                      r.name AS room_name,t.name AS track_name,
                      CASE WHEN ac.source_type='organizer_created' THEN
                        COALESCE((SELECT group_concat(participant.display_name_snapshot, ', ')
                          FROM accepted_session_participants participant
                          WHERE participant.accepted_session_id=ac.id
                            AND participant.event_speaker_id IS NOT NULL),'')
                      ELSE COALESCE(group_concat(ss.snapshot_name, ', '),'') END AS speaker_names
               FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
               LEFT JOIN submissions s ON s.id=ac.submission_id
               JOIN event_rooms r ON r.id=ai.room_id
               LEFT JOIN event_tracks t ON t.id=ai.track_id
               LEFT JOIN submission_speakers ss ON ss.submission_id=ac.submission_id
               WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
                 AND ac.content_status='approved' AND ac.lifecycle_status='active'
               GROUP BY ai.id ORDER BY ai.starts_at_ms,ai.id"""
        )
        .bind(event["organization_id"], event_id, revision["id"])
        .all()
    )
    await _attach_session_labels(db, str(event["organization_id"]), event_id, items)
    return PublicScheduleView(
        event=PublicScheduleEventView(
            id=str(event["id"]),
            name=str(event["name"]),
            time_zone=str(event["time_zone"]),
            accent_color=(
                str(event["accent_color"]) if event["accent_color"] is not None else None
            ),
            logo_url=str(event["logo_url"]) if event["logo_url"] is not None else None,
            cover_image_url=(
                str(event["cover_image_url"]) if event["cover_image_url"] is not None else None
            ),
            website_url=(str(event["website_url"]) if event["website_url"] is not None else None),
        ),
        revision=ScheduleRevisionView(
            id=str(revision["id"]),
            version=int(revision["version"]),
            revision_number=int(revision["revision_number"]),
        ),
        items=[_public_schedule_item(item) for item in items],
    )


def _calendar_stamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).strftime("%Y%m%dT%H%M%SZ")


def _calendar_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


@scheduling_router.get(
    "/api/v1/public/events/{event_id}/schedule.ics",
    include_in_schema=False,
)
async def public_schedule_ical(event_id: str, request: Request) -> Response:
    schedule = await get_public_schedule(event_id, request, Response())
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SessionBuddy//Schedule//EN"]
    for item in schedule.items:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{_calendar_text(item.id)}@sessionbuddy",
                f"DTSTART:{_calendar_stamp(item.start_at_ms)}",
                f"DTEND:{_calendar_stamp(item.end_at_ms)}",
                f"SUMMARY:{_calendar_text(item.title)}",
                f"LOCATION:{_calendar_text(item.room_name)}",
                f"DESCRIPTION:{_calendar_text(item.description)}",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return Response(
        "\r\n".join(lines) + "\r\n",
        media_type="text/calendar; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@scheduling_router.get(
    "/api/v1/public/events/{event_id}/schedule.xml",
    include_in_schema=False,
)
async def public_schedule_xml(event_id: str, request: Request) -> Response:
    schedule = await get_public_schedule(event_id, request, Response())
    sessions = "".join(
        "<session>"
        f"<id>{escape(item.id)}</id><title>{escape(item.title)}</title>"
        f"<description>{escape(item.description)}</description>"
        f"<starts_at_ms>{item.start_at_ms}</starts_at_ms>"
        f"<ends_at_ms>{item.end_at_ms}</ends_at_ms>"
        f"<room>{escape(item.room_name)}</room>"
        f"<track>{escape(item.track_name or '')}</track>"
        f"<format>{escape(item.format_name)}</format>"
        f"<speakers>{escape(item.speaker_names)}</speakers>"
        "</session>"
        for item in schedule.items
    )
    revision = schedule.revision.revision_number if schedule.revision else 0
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<schedule event_id="{escape(schedule.event.id)}" revision="{revision}">'
        f"{sessions}</schedule>"
    )
    return Response(
        body,
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )

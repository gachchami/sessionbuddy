import hashlib
import json
from datetime import UTC, datetime
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
from sessionbuddy.platform.auth.http import authenticate_request, require_permission
from sessionbuddy.platform.authorization import Permission, ResourceContext
from sessionbuddy.platform.db.commands import (
    AuditEvent,
    CommandBatch,
    IdempotencyRecord,
    OutboxMessage,
)
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms

from .models import (
    AgendaAutoSchedule,
    AgendaCandidate,
    AgendaPublish,
    AgendaResourceCreate,
    AgendaResourceUpdate,
    AgendaSetup,
    EventTrackList,
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


def _fingerprint(body: AgendaAutoSchedule | AgendaCandidate | AgendaSetup) -> bytes:
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
               ORDER BY ss.event_speaker_id"""
        )
        .bind(organization_id, event_id, accepted_session_id)
        .all()
    )
    return tuple(str(row["event_speaker_id"]) for row in rows)


async def _agenda_model(db, event, revision) -> dict[str, object]:
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
            """SELECT ai.id,ai.accepted_session_id AS session_id,s.proposal_title AS title,
                      s.proposal_abstract AS abstract,ac.content_status,
                      ac.version AS content_version,
                      ai.starts_at_ms AS start_at_ms,ai.ends_at_ms AS end_at_ms,
                      ai.room_id,r.name AS room_name,ai.track_id,t.name AS track_name,ai.version
               FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
               JOIN submissions s ON s.id=ac.submission_id JOIN event_rooms r ON r.id=ai.room_id
               LEFT JOIN event_tracks t ON t.id=ai.track_id
               WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
               ORDER BY ai.starts_at_ms,ai.id"""
        )
        .bind(organization_id, event_id, revision["id"])
        .all()
    )
    unscheduled = result_rows(
        await db.prepare(
            """SELECT ac.id AS session_id,s.proposal_title AS title,
                      s.proposal_abstract AS abstract,ac.content_status,
                      ac.version AS content_version
               FROM accepted_sessions ac JOIN submissions s ON s.id=ac.submission_id
               WHERE ac.organization_id=?1 AND ac.event_id=?2 AND NOT EXISTS (
                 SELECT 1 FROM agenda_items ai WHERE ai.revision_id=?3
                   AND ai.accepted_session_id=ac.id)
               ORDER BY s.proposal_title,ac.id"""
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
    return {
        "event": {
            key: event[key] for key in ("id", "name", "time_zone", "starts_at_ms", "ends_at_ms")
        },
        "revision": {
            "id": revision["id"],
            "version": revision["version"],
            "state": revision["status"],
        },
        "published_revision": published_revision,
        "items": items,
        "unscheduled_sessions": unscheduled,
        "rooms": rooms,
        "tracks": tracks,
    }


@scheduling_router.get("/api/v1/admin/events/{event_id}/agenda", tags=["agenda"])
async def get_admin_agenda(event_id: str, request: Request) -> dict[str, object]:
    event, _ = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=False)
    revision = await _revision(_db(request), str(event["organization_id"]), event_id)
    if revision is None:
        raise HTTPException(status_code=404)
    return await _agenda_model(_db(request), event, revision)


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/setup",
    status_code=201,
    tags=["agenda"],
)
async def setup_admin_agenda(
    event_id: str,
    request: Request,
    body: AgendaSetup,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    event, auth = await _event_scope(
        request, event_id, Permission.AGENDA_MANAGE, mutation=True
    )
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
        return await _agenda_model(db, event, revision)
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
    return await _agenda_model(db, event, revision)


async def _create_agenda_resource(
    event_id: str,
    request: Request,
    body: AgendaResourceCreate,
    *,
    resource: str,
) -> dict[str, object]:
    event, auth = await _event_scope(
        request, event_id, Permission.AGENDA_MANAGE, mutation=True
    )
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
    return await _agenda_model(db, event, revision)


async def _update_agenda_resource(
    event_id: str,
    resource_id: str,
    request: Request,
    body: AgendaResourceUpdate,
    *,
    resource: str,
) -> dict[str, object]:
    event, auth = await _event_scope(
        request, event_id, Permission.AGENDA_MANAGE, mutation=True
    )
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
    return await _agenda_model(db, event, revision)


@scheduling_router.post("/api/v1/admin/events/{event_id}/agenda/rooms", tags=["agenda"])
async def create_agenda_room(
    event_id: str, request: Request, body: AgendaResourceCreate
) -> dict[str, object]:
    return await _create_agenda_resource(event_id, request, body, resource="room")


@scheduling_router.patch(
    "/api/v1/admin/events/{event_id}/agenda/rooms/{room_id}", tags=["agenda"]
)
async def update_agenda_room(
    event_id: str, room_id: str, request: Request, body: AgendaResourceUpdate
) -> dict[str, object]:
    return await _update_agenda_resource(
        event_id, room_id, request, body, resource="room"
    )


@scheduling_router.post("/api/v1/admin/events/{event_id}/agenda/tracks", tags=["agenda"])
async def create_agenda_track(
    event_id: str, request: Request, body: AgendaResourceCreate
) -> dict[str, object]:
    return await _create_agenda_resource(event_id, request, body, resource="track")


@scheduling_router.get(
    "/api/v1/admin/events/{event_id}/agenda/tracks",
    response_model=EventTrackList,
    tags=["agenda"],
)
async def list_event_tracks(event_id: str, request: Request) -> EventTrackList:
    event, _ = await _event_scope(
        request, event_id, Permission.FORM_MANAGE, mutation=False
    )
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
    return EventTrackList(event_id=event_id, data=tracks)


@scheduling_router.patch(
    "/api/v1/admin/events/{event_id}/agenda/tracks/{track_id}", tags=["agenda"]
)
async def update_agenda_track(
    event_id: str, track_id: str, request: Request, body: AgendaResourceUpdate
) -> dict[str, object]:
    return await _update_agenda_resource(
        event_id, track_id, request, body, resource="track"
    )


def _agenda_date(timestamp_ms: int, time_zone: str) -> str:
    try:
        zone = ZoneInfo(time_zone)
    except Exception:
        zone = UTC
    return datetime.fromtimestamp(timestamp_ms / 1000, zone).strftime("%Y-%m-%d")


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/auto-schedule", tags=["agenda"]
)
async def auto_schedule_agenda(
    event_id: str,
    request: Request,
    body: AgendaAutoSchedule,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    event, auth = await _event_scope(
        request, event_id, Permission.AGENDA_MANAGE, mutation=True
    )
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
        return await _agenda_model(db, event, revision)
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
            """SELECT ac.id,s.proposal_title
               FROM accepted_sessions ac JOIN submissions s ON s.id=ac.submission_id
               WHERE ac.organization_id=?1 AND ac.event_id=?2 AND NOT EXISTS (
                 SELECT 1 FROM agenda_items ai WHERE ai.revision_id=?3
                   AND ai.accepted_session_id=ac.id)
               ORDER BY s.proposal_title,ac.id"""
        )
        .bind(organization_id, event_id, revision["id"])
        .all()
    )
    existing = result_rows(
        await db.prepare(
            """SELECT ai.id,ai.room_id,ai.starts_at_ms,ai.ends_at_ms,
                      COALESCE(group_concat(ais.event_speaker_id, ','),'') AS speaker_ids
               FROM agenda_items ai LEFT JOIN agenda_item_speakers ais
                 ON ais.agenda_item_id=ai.id AND ais.revision_id=ai.revision_id
               WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
               GROUP BY ai.id"""
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
                    and (
                        value["room_id"] == room_id
                        or bool(value["speakers"] & speaker_ids)
                    )
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
    model = await _agenda_model(db, event, revision)
    model["auto_schedule"] = {
        "scheduled_count": len(planned),
        "remaining_count": len(sessions) - len(planned),
    }
    return model


async def _slot(request: Request, event, revision, body: AgendaCandidate) -> AgendaSlot:
    speaker_ids = await _speaker_ids(
        _db(request), str(event["organization_id"]), str(event["id"]), body.session_id
    )
    if not speaker_ids:
        raise HTTPException(status_code=400)
    event_date = body.event_date or _agenda_date(
        body.start_at_ms, str(event["time_zone"])
    )
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


def _conflict_view(conflicts) -> list[dict[str, object]]:
    messages = {
        "room": "The room is already in use during this time.",
        "speaker": "A speaker is already scheduled during this time.",
        "track": "This exclusive track already has a session during this time.",
    }
    return [
        {"code": item.kind, "message": messages[item.kind], "conflicting_item_id": item.item_id}
        for item in conflicts
    ]


@scheduling_router.post("/api/v1/admin/events/{event_id}/agenda/preview", tags=["agenda"])
async def preview_agenda(
    event_id: str, request: Request, body: AgendaCandidate
) -> dict[str, object]:
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
    return {"valid": not conflicts, "conflicts": _conflict_view(conflicts)}


async def _saved_item(db, organization_id: str, event_id: str, item_id: str):
    row = row_mapping(
        await db.prepare(
            """SELECT ai.id,ai.accepted_session_id AS session_id,s.proposal_title AS title,
                  ai.starts_at_ms AS start_at_ms,ai.ends_at_ms AS end_at_ms,
                  ai.room_id,r.name AS room_name,ai.track_id,t.name AS track_name,ai.version
           FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
           JOIN submissions s ON s.id=ac.submission_id JOIN event_rooms r ON r.id=ai.room_id
           LEFT JOIN event_tracks t ON t.id=ai.track_id
           WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.id=?3"""
        )
        .bind(organization_id, event_id, item_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return row


async def _save_item(
    event_id: str,
    request: Request,
    body: AgendaCandidate,
    key_value: str | None,
    item_id: str | None,
):
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
    if item_id is not None and int(row["version"]) != body.version + 1:
        raise HTTPException(status_code=409)
    return row


@scheduling_router.post(
    "/api/v1/admin/events/{event_id}/agenda/items", status_code=201, tags=["agenda"]
)
async def create_agenda_item(
    event_id: str,
    request: Request,
    body: AgendaCandidate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if body.item_id is not None:
        raise HTTPException(status_code=400)
    return await _save_item(event_id, request, body, idempotency_key, None)


@scheduling_router.patch("/api/v1/admin/events/{event_id}/agenda/items/{item_id}", tags=["agenda"])
async def move_agenda_item(
    event_id: str,
    item_id: str,
    request: Request,
    body: AgendaCandidate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
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
    event, auth = await _event_scope(
        request, event_id, Permission.AGENDA_MANAGE, mutation=True
    )
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


@scheduling_router.post("/api/v1/admin/events/{event_id}/agenda/publish", tags=["agenda"])
async def publish_agenda(event_id: str, request: Request, body: AgendaPublish) -> dict[str, object]:
    event, auth = await _event_scope(request, event_id, Permission.AGENDA_MANAGE, mutation=True)
    db, organization_id = _db(request), str(event["organization_id"])
    draft = await _revision(db, organization_id, event_id)
    if (
        draft is None
        or str(draft["id"]) != body.revision_id
        or int(draft["version"]) != body.version
    ):
        raise HTTPException(status_code=409)
    item_rows = result_rows(
        await db.prepare(
            """SELECT ai.*,s.proposal_title,s.proposal_abstract,r.name AS room_name
               FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
               JOIN submissions s ON s.id=ac.submission_id
               JOIN event_rooms r ON r.id=ai.room_id
               WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
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
               ORDER BY ais.agenda_item_id,ais.event_speaker_id"""
        )
        .bind(organization_id, event_id, body.revision_id)
        .all()
    )
    by_item: dict[str, list[str]] = {}
    for row in speaker_rows:
        by_item.setdefault(str(row["agenda_item_id"]), []).append(str(row["event_speaker_id"]))
    now, next_revision_id = utc_now_ms(), new_id()
    batch = CommandBatch(db)
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
        batch.outbox(
            OutboxMessage(
                topic="agenda.calendar.sync_requested",
                aggregate_type="agenda_item",
                aggregate_id=str(row["id"]),
                deduplication_key=f"agenda:{row['id']}:publish:{body.version + 1}",
                payload={
                    "schema_version": 1,
                    "agenda_item_id": str(row["id"]),
                    "revision_id": body.revision_id,
                },
                available_at_ms=now,
                created_at_ms=now,
                organization_id=organization_id,
                event_id=event_id,
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
    return {
        "published_revision_id": body.revision_id,
        "published_version": body.version + 1,
        "draft_revision_id": next_revision_id,
    }


@scheduling_router.get("/api/v1/events/{event_id}/schedule", tags=["agenda"])
async def get_schedule(event_id: str, request: Request) -> dict[str, object]:
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
    roles = auth.actor.organization_roles.get(
        str(event["organization_id"]), frozenset()
    ) | auth.actor.event_roles.get((str(event["organization_id"]), event_id), frozenset())
    permission = (
        Permission.SPEAKER_TASK_READ_OWN
        if any(role.value == "speaker" for role in roles)
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
            """SELECT ai.id,s.proposal_title AS title,ai.starts_at_ms AS start_at_ms,
                  ai.ends_at_ms AS end_at_ms,r.name AS room_name,t.name AS track_name,
                  COALESCE(group_concat(ss.snapshot_name, ', '),'') AS speaker_names
           FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
           JOIN submissions s ON s.id=ac.submission_id JOIN event_rooms r ON r.id=ai.room_id
           LEFT JOIN event_tracks t ON t.id=ai.track_id
           LEFT JOIN submission_speakers ss ON ss.submission_id=ac.submission_id
           WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
           GROUP BY ai.id ORDER BY ai.starts_at_ms,ai.id"""
        )
        .bind(event["organization_id"], event_id, revision["id"])
        .all()
    )
    return {
        "event": {"id": event["id"], "name": event["name"], "time_zone": event["time_zone"]},
        "revision": {
            "id": revision["id"],
            "version": revision["version"],
            "revision_number": revision["revision_number"],
        },
        "items": items,
    }


@scheduling_router.get("/api/v1/public/events/{event_id}/schedule", tags=["public-program"])
async def get_public_schedule(
    event_id: str, request: Request, response: Response
) -> dict[str, object]:
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
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
        return {
            "event": {
                "id": event["id"],
                "name": event["name"],
                "time_zone": event["time_zone"],
                "accent_color": event["accent_color"],
                "logo_url": event["logo_url"],
                "cover_image_url": event["cover_image_url"],
                "website_url": event["website_url"],
            },
            "revision": None,
            "items": [],
        }
    items = result_rows(
        await db.prepare(
            """SELECT ai.id,s.proposal_title AS title,s.proposal_abstract AS description,
                      ai.starts_at_ms AS start_at_ms,ai.ends_at_ms AS end_at_ms,
                      r.name AS room_name,t.name AS track_name,
                      COALESCE(group_concat(ss.snapshot_name, ', '),'') AS speaker_names
               FROM agenda_items ai JOIN accepted_sessions ac ON ac.id=ai.accepted_session_id
               JOIN submissions s ON s.id=ac.submission_id JOIN event_rooms r ON r.id=ai.room_id
               LEFT JOIN event_tracks t ON t.id=ai.track_id
               LEFT JOIN submission_speakers ss ON ss.submission_id=ac.submission_id
               WHERE ai.organization_id=?1 AND ai.event_id=?2 AND ai.revision_id=?3
                 AND ac.content_status='approved'
               GROUP BY ai.id ORDER BY ai.starts_at_ms,ai.id"""
        )
        .bind(event["organization_id"], event_id, revision["id"])
        .all()
    )
    return {
        "event": {
            "id": event["id"],
            "name": event["name"],
            "time_zone": event["time_zone"],
            "accent_color": event["accent_color"],
            "logo_url": event["logo_url"],
            "cover_image_url": event["cover_image_url"],
            "website_url": event["website_url"],
        },
        "revision": {
            "id": revision["id"],
            "version": revision["version"],
            "revision_number": revision["revision_number"],
        },
        "items": items,
    }

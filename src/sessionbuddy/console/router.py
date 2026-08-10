from time import perf_counter
from typing import Literal, cast

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from sessionbuddy.communications.runtime import requeue_exhausted_deliveries
from sessionbuddy.console import embedded_assets
from sessionbuddy.console.models import (
    CommunicationRequeueRequest,
    CommunicationRequeueResult,
    DatabaseStatus,
    EngineRoomStatus,
    public_engine_room_status,
)
from sessionbuddy.observability import record_timing
from sessionbuddy.platform.auth.http import database, require_permission
from sessionbuddy.platform.authorization import Permission, ResourceContext
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch
from sessionbuddy.platform.db.types import utc_now_ms

EnvironmentName = Literal["local", "development", "preview", "staging", "production"]

engine_room_router = APIRouter()


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


@engine_room_router.get(
    "/engine-room",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def engine_room() -> HTMLResponse:
    return HTMLResponse(_asset("engine_room.html"), headers={"Cache-Control": "no-store"})


@engine_room_router.get(
    "/engine-room/assets/console.css",
    response_class=Response,
    include_in_schema=False,
)
async def engine_room_css() -> Response:
    return Response(
        _asset("console.css"),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300"},
    )


@engine_room_router.get(
    "/engine-room/assets/console.js",
    response_class=Response,
    include_in_schema=False,
)
async def engine_room_js() -> Response:
    return Response(
        _asset("console.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@engine_room_router.get(
    "/api/v1/engine-room/status",
    response_model=EngineRoomStatus,
    tags=["operations"],
    operation_id="getEngineRoomStatus",
)
async def engine_room_status(request: Request) -> EngineRoomStatus:
    return public_engine_room_status(environment=_environment(request))


@engine_room_router.get(
    "/api/v1/engine-room/database",
    response_model=DatabaseStatus,
    tags=["operations"],
    operation_id="getFoundationDatabaseStatus",
)
async def engine_room_database(request: Request) -> DatabaseStatus:
    """Exercise the configured D1 binding without returning tenant or schema data."""
    env = request.scope.get("env")
    db = getattr(env, "DB", None) if env is not None else None
    if db is None:
        # Host-ASGI tests deliberately have no platform binding. This endpoint must
        # never pretend that an unbound database is healthy.
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="database binding unavailable")
    started = perf_counter()
    await db.prepare("SELECT 1 AS ready").first("ready")
    query_ms = (perf_counter() - started) * 1000
    record_timing(request, "db", query_ms)
    return DatabaseStatus(
        environment=_environment(request),
        query_ms=round(query_ms, 3),
    )


@engine_room_router.post(
    "/api/v1/engine-room/communications/requeue-exhausted",
    response_model=CommunicationRequeueResult,
    tags=["operations"],
    operation_id="requeueExhaustedCommunications",
)
async def requeue_exhausted_communications(
    body: CommunicationRequeueRequest, request: Request
) -> CommunicationRequeueResult:
    """Requeue transient failures for one organization after an operator repair."""
    authenticated = await require_permission(
        request,
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(body.organization_id),
        mutation=True,
    )
    queue = getattr(request.scope.get("env"), "COMMUNICATION_QUEUE", None)
    if queue is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="communication queue unavailable")
    now = utc_now_ms()
    result = await requeue_exhausted_deliveries(
        database(request), queue, body.organization_id, now, limit=body.limit
    )
    audit = CommandBatch(database(request))
    audit.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="communication.exhausted.requeue",
            target_type="organization",
            target_id=body.organization_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=body.organization_id,
            metadata={
                "selected_count": result.selected,
                "requeued_count": result.requeued,
                "published_count": result.published,
                "publish_failure_count": result.publish_failures,
            },
        )
    )
    await audit.execute()
    return CommunicationRequeueResult(
        selected=result.selected,
        requeued=result.requeued,
        published=result.published,
        publish_failures=result.publish_failures,
    )


def _environment(request: Request) -> EnvironmentName:
    env = request.scope.get("env")
    raw = getattr(env, "APP_ENV", "local") if env is not None else "local"
    allowed = {"local", "development", "preview", "staging", "production"}
    return cast(EnvironmentName, raw if raw in allowed else "local")

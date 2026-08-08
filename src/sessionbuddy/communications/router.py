from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request

from sessionbuddy.platform.auth import (
    authenticate_request,
    authorization_denial_status,
    guard_mutation,
)
from sessionbuddy.platform.authorization import Permission, authorize

from .models import (
    CommunicationStatusList,
    DispatchResponse,
    ManualSendRequest,
    ManualSendResponse,
    RecipientPreviewRequest,
    RecipientPreviewResponse,
    ReminderQueuedResponse,
)
from .service import CommunicationsService

ServiceProvider = Callable[[Request], CommunicationsService]


def _enforce(request: Request, authenticated, permission: Permission, context) -> None:
    decision = authorize(authenticated.actor, permission, context)
    if not decision.allowed:
        raise HTTPException(status_code=authorization_denial_status(decision.reason))
    guard_mutation(request, authenticated.session_id)


def create_communications_router(service_provider: ServiceProvider) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/v1/admin/events/{event_id}/communications/preview",
        response_model=RecipientPreviewResponse,
        operation_id="previewEventCommunication",
        tags=["communications"],
    )
    async def preview(event_id: str, body: RecipientPreviewRequest, request: Request):
        authenticated = await authenticate_request(request)
        service = service_provider(request)
        context = await service.context_for_event(authenticated.actor, event_id)
        _enforce(request, authenticated, Permission.COMMUNICATION_SEND, context)
        return await service.preview(event_id, body)

    @router.post(
        "/api/v1/admin/events/{event_id}/communications/send",
        response_model=ManualSendResponse,
        status_code=202,
        operation_id="queueEventCommunication",
        tags=["communications"],
    )
    async def send(
        event_id: str,
        body: ManualSendRequest,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        if not idempotency_key or not 16 <= len(idempotency_key) <= 255:
            raise HTTPException(status_code=400)
        authenticated = await authenticate_request(request)
        service = service_provider(request)
        context = await service.context_for_event(authenticated.actor, event_id)
        _enforce(request, authenticated, Permission.COMMUNICATION_SEND, context)
        return await service.queue_manual_send(event_id, body, idempotency_key)

    @router.post(
        "/api/v1/admin/events/{event_id}/speaker-tasks/{task_id}/reminders",
        response_model=ReminderQueuedResponse,
        status_code=202,
        operation_id="queueSpeakerTaskReminder",
        tags=["communications"],
    )
    async def remind(
        event_id: str,
        task_id: str,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        if not idempotency_key or not 16 <= len(idempotency_key) <= 255:
            raise HTTPException(status_code=400)
        authenticated = await authenticate_request(request)
        service = service_provider(request)
        context = await service.context_for_event(authenticated.actor, event_id)
        _enforce(request, authenticated, Permission.COMMUNICATION_SEND, context)
        return await service.queue_task_reminder(event_id, task_id, idempotency_key)

    @router.get(
        "/api/v1/admin/events/{event_id}/communications",
        response_model=CommunicationStatusList,
        operation_id="listEventCommunications",
        tags=["communications"],
    )
    async def statuses(event_id: str, request: Request):
        authenticated = await authenticate_request(request)
        service = service_provider(request)
        context = await service.context_for_event(authenticated.actor, event_id)
        decision = authorize(authenticated.actor, Permission.COMMUNICATION_SEND, context)
        if not decision.allowed:
            raise HTTPException(status_code=authorization_denial_status(decision.reason))
        return await service.statuses(event_id)

    @router.post(
        "/api/v1/admin/events/{event_id}/communications/dispatch-local",
        response_model=DispatchResponse,
        operation_id="dispatchLocalEventCommunications",
        tags=["communications", "demo"],
    )
    async def dispatch_local(event_id: str, request: Request):
        authenticated = await authenticate_request(request)
        service = service_provider(request)
        context = await service.context_for_event(authenticated.actor, event_id)
        _enforce(request, authenticated, Permission.COMMUNICATION_SEND, context)
        return await service.dispatch_local(event_id)

    return router

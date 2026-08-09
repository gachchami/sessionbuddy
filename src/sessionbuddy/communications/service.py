from typing import Protocol

from sessionbuddy.platform.authorization import Actor, ResourceContext

from .models import (
    CommunicationStatusList,
    DispatchResponse,
    ManualSendRequest,
    ManualSendResponse,
    RecipientPreviewRequest,
    RecipientPreviewResponse,
    ReminderQueuedResponse,
    SpeakerMessagePreviewRequest,
    SpeakerMessageSendRequest,
)


class CommunicationsService(Protocol):
    async def context_for_event(self, actor: Actor, event_id: str) -> ResourceContext: ...
    async def preview(
        self, event_id: str, body: RecipientPreviewRequest
    ) -> RecipientPreviewResponse: ...
    async def queue_manual_send(
        self, event_id: str, body: ManualSendRequest, idempotency_key: str
    ) -> ManualSendResponse: ...

    async def preview_speaker_message(
        self, event_id: str, body: SpeakerMessagePreviewRequest
    ) -> RecipientPreviewResponse: ...

    async def queue_speaker_message(
        self, event_id: str, body: SpeakerMessageSendRequest, idempotency_key: str
    ) -> ManualSendResponse: ...

    async def queue_task_reminder(
        self, event_id: str, task_id: str, idempotency_key: str
    ) -> ReminderQueuedResponse: ...

    async def statuses(
        self, event_id: str, *, cursor: str | None = None, limit: int = 25
    ) -> CommunicationStatusList: ...

    async def dispatch_local(self, event_id: str) -> DispatchResponse: ...

import asgi
from workers import WorkerEntrypoint

from sessionbuddy.api.app import app
from sessionbuddy.communications.runtime import (
    D1DeliveryRepository,
    DeliveryEnvelope,
    ReminderEnvelope,
    ResendProvider,
    consume_delivery,
    consume_reminder,
)
from sessionbuddy.communications.runtime import ReminderWorkflow as _ReminderWorkflow
from sessionbuddy.platform.db.d1 import to_python
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.speaker_operations.asset_boundary import consume_scan_job
from sessionbuddy.speaker_operations.scanner_adapter import SignedScannerAdapter

ReminderWorkflow = _ReminderWorkflow


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)

    async def queue(self, batch, _environment=None, _context=None):
        # The deployed Python runtime currently forwards the JavaScript-style
        # env and ctx arguments even though WorkerEntrypoint also exposes self.env.
        for message in batch.messages:
            body = to_python(message.body)
            if str(batch.queue) == "sessionbuddy-asset-scans":
                disposition = await consume_scan_job(
                    self.env.DB,
                    self.env.ASSETS,
                    SignedScannerAdapter(self.env),
                    body,
                    now_ms=utc_now_ms(),
                )
                message.ack() if disposition.ack else message.retry()
                continue
            try:
                reminder = ReminderEnvelope.parse(body)
            except (TypeError, ValueError):
                reminder = None
            if reminder is not None:
                await consume_reminder(self.env.DB, reminder, utc_now_ms())
                message.ack()
                continue
            try:
                DeliveryEnvelope.parse(body)
                provider = ResendProvider(
                    str(getattr(self.env, "RESEND_API_KEY", "")),
                    str(getattr(self.env, "RESEND_FROM_ADDRESS", "")),
                )
                acknowledged = await consume_delivery(
                    body,
                    D1DeliveryRepository(self.env.DB),
                    provider,
                    utc_now_ms(),
                )
            except Exception:
                acknowledged = False
            message.ack() if acknowledged else message.retry()

import hashlib

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
from sessionbuddy.platform.storage import parse_signed_scan_response, scan_request_headers
from sessionbuddy.wave3.asset_boundary import ScanResult, consume_scan_job

ReminderWorkflow = _ReminderWorkflow


class SignedScannerAdapter:
    def __init__(self, environment) -> None:
        self.environment = environment

    async def scan(self, stored, *, job):
        from workers import fetch

        scanner_url = str(getattr(self.environment, "SCANNER_URL", ""))
        app_env = str(getattr(self.environment, "APP_ENV", "production"))
        if not scanner_url.startswith("https://") and not (
            app_env == "local" and scanner_url.startswith("http://scanner:")
        ):
            raise RuntimeError("scanner binding is unavailable")
        if isinstance(stored, (bytes, bytearray, memoryview)):
            content = bytes(stored)
        elif callable(getattr(stored, "arrayBuffer", None)):
            content = bytes(await stored.arrayBuffer())
        else:
            reader = stored.getReader()
            chunks = []
            while True:
                chunk = await reader.read()
                if bool(chunk.done):
                    break
                chunks.append(bytes(to_python(chunk.value)))
            content = b"".join(chunks)
        scanner_secret = str(getattr(self.environment, "SCANNER_HMAC_KEY", "")).encode()
        if len(scanner_secret) < 32:
            raise RuntimeError("scanner signing secret is unavailable")
        headers = scan_request_headers(
            scanner_secret,
            job_id=job.job_id,
            timestamp_ms=utc_now_ms(),
            content=content,
        )
        response = await fetch(
            scanner_url.rstrip("/") + "/scan",
            method="POST",
            headers=headers,
            body=content,
        )
        response_body = await response.bytes()
        if response.status != 200:
            raise RuntimeError("scanner request failed")
        parsed = parse_signed_scan_response(
            scanner_secret, response_body, response.headers.get("x-scan-signature")
        )
        if parsed.job_id != job.job_id:
            raise RuntimeError("scanner job mismatch")
        return ScanResult(
            provider_event_id=hashlib.sha256(response_body).hexdigest(),
            verdict=parsed.verdict,
            engine=parsed.engine,
            signature_code=parsed.signature,
        )


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)

    async def queue(self, batch):
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

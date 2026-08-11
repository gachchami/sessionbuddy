import json

import asgi
from workers import WorkerEntrypoint

from sessionbuddy.api.app import app
from sessionbuddy.cfp.staged_uploads import purge_expired_staged_assets
from sessionbuddy.communications.runtime import (
    D1DeliveryRepository,
    DeliveryEnvelope,
    MailpitProvider,
    ReminderEnvelope,
    ResendProvider,
    consume_delivery,
    consume_reminder,
    dispatch_stuck_deliveries,
    park_unconfigured_delivery,
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

    async def scheduled(self, _controller):
        result = await dispatch_stuck_deliveries(
            self.env.DB,
            self.env.COMMUNICATION_QUEUE,
            utc_now_ms(),
        )
        print(
            json.dumps(
                {
                    "event": "communication_delivery_dispatch",
                    "level": (
                        "error"
                        if result.publish_failures
                        or result.exhausted
                        or result.oldest_pending_age_ms >= 30 * 60 * 1000
                        else "info"
                    ),
                    "recovered": result.recovered_sending,
                    "published": result.published,
                    "publish_failures": result.publish_failures,
                    "exhausted": result.exhausted,
                    "oldest_pending_age_ms": result.oldest_pending_age_ms,
                },
                separators=(",", ":"),
            )
        )
        purge = await purge_expired_staged_assets(self.env.DB, self.env.ASSETS, utc_now_ms())
        print(
            json.dumps(
                {
                    "event": "cfp_staged_upload_purge",
                    "level": "error" if purge.delete_failures else "info",
                    "deleted_rows": purge.deleted_rows,
                    "deleted_objects": purge.deleted_objects,
                    "delete_failures": purge.delete_failures,
                },
                separators=(",", ":"),
            )
        )

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
            except Exception:
                # A retry can never fix a malformed body; ack it out of the
                # queue and leave a trace instead of retrying to the DLQ.
                print(
                    json.dumps(
                        {
                            "event": "communication_delivery_failed",
                            "level": "error",
                            "reason": "malformed_envelope",
                        },
                        separators=(",", ":"),
                    )
                )
                message.ack()
                continue
            try:
                app_env = str(getattr(self.env, "APP_ENV", "production")).strip().lower()
                mailpit_api_url = str(getattr(self.env, "MAILPIT_API_URL", "")).strip()
                api_key = str(getattr(self.env, "RESEND_API_KEY", ""))
                from_address = str(getattr(self.env, "RESEND_FROM_ADDRESS", ""))
                if app_env == "local" and mailpit_api_url and from_address:
                    provider = MailpitProvider(mailpit_api_url, from_address)
                    acknowledged = await consume_delivery(
                        body,
                        D1DeliveryRepository(self.env.DB),
                        provider,
                        utc_now_ms(),
                    )
                elif api_key and from_address:
                    provider = ResendProvider(api_key, from_address)
                    acknowledged = await consume_delivery(
                        body,
                        D1DeliveryRepository(self.env.DB),
                        provider,
                        utc_now_ms(),
                    )
                else:
                    # A retry can never succeed without provider credentials;
                    # park the durable row as a permanent failure instead of
                    # burning Worker CPU on a retry storm.
                    print(
                        json.dumps(
                            {
                                "event": "communication_delivery_failed",
                                "level": "error",
                                "reason": "provider_unconfigured",
                            },
                            separators=(",", ":"),
                        )
                    )
                    acknowledged = await park_unconfigured_delivery(
                        body, D1DeliveryRepository(self.env.DB), utc_now_ms()
                    )
            except Exception as error:  # noqa: BLE001 - queue boundary
                # The old bare handler swallowed every failure silently; the
                # eval-run outage (Cloudflare 1101/1102 after an invitation
                # send) was undiagnosable for exactly that reason.
                print(
                    json.dumps(
                        {
                            "event": "communication_delivery_failed",
                            "level": "error",
                            "reason": type(error).__name__,
                        },
                        separators=(",", ":"),
                    )
                )
                acknowledged = False
            message.ack() if acknowledged else message.retry()

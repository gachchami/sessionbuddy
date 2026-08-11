"""Independent Cloudflare Worker for activity dispatch and projection."""

from workers import Response, WorkerEntrypoint

from sessionbuddy.platform.activity import (
    activity_log,
    dispatch_pending_activities,
    distribute_activity,
)
from sessionbuddy.platform.db.d1 import to_python
from sessionbuddy.platform.db.types import utc_now_ms


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = str(request.url).split("?", 1)[0].rstrip("/")
        if path.endswith("/health"):
            return Response("ok", headers={"content-type": "text/plain"})
        if (
            path.endswith("/internal/dispatch")
            and str(getattr(self.env, "APP_ENV", "production")).strip().lower()
            == "local"
        ):
            result = await dispatch_pending_activities(
                self.env.DB, self.env.ACTIVITY_QUEUE, utc_now_ms()
            )
            return Response(
                activity_log(
                    "activity_dispatch",
                    claimed=result.claimed,
                    published=result.published,
                    failed=result.failed,
                ),
                headers={"content-type": "application/json"},
            )
        return Response("Not found", status=404)

    async def scheduled(self, _controller, _environment=None, _context=None):
        result = await dispatch_pending_activities(
            self.env.DB, self.env.ACTIVITY_QUEUE, utc_now_ms()
        )
        print(activity_log(
            "activity_dispatch",
            level="error" if result.failed else "info",
            claimed=result.claimed,
            published=result.published,
            failed=result.failed,
        ))

    async def queue(self, batch, _environment=None, _context=None):
        for message in batch.messages:
            result = await distribute_activity(
                self.env.DB, to_python(message.body), utc_now_ms()
            )
            if result.reason:
                print(activity_log(
                    "activity_distribution",
                    level="error" if not result.acknowledged else "warning",
                    reason=result.reason,
                ))
            message.ack() if result.acknowledged else message.retry()

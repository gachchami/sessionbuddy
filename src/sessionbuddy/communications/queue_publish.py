"""Post-commit queue publication for messages already persisted in the outbox.

Every caller shares one rule: once a communication row is committed with
status 'queued', the domain mutation has succeeded and the HTTP response must
say so. Publishing the queue envelope afterwards is a best-effort wake-up —
the scheduled dispatcher in `sessionbuddy.communications.runtime` republishes
any row that stays 'queued'. Raising here would turn a committed save into a
false-negative 5xx whose retry then fails on idempotency or optimistic
version conflicts.
"""

from __future__ import annotations

from fastapi import Request

from sessionbuddy.observability import record_degradation

__all__ = ["publish_committed_messages"]


async def publish_committed_messages(request: Request, message_ids: list[str]) -> None:
    """Best-effort queue wake-up for committed communication rows.

    Attempts every message independently: one provider failure must not strand
    the remaining envelopes until the recovery sweep. Failures are absorbed
    but recorded on the request's completion telemetry so a queue outage is
    visible immediately, not only as recovery-sweep latency.
    """
    queue = getattr(request.scope.get("env"), "COMMUNICATION_QUEUE", None)
    if queue is None:
        # Lightweight unit and local harnesses may intentionally omit a queue;
        # a real local Worker binds the same consumer used when deployed.
        return
    for message_id in message_ids:
        try:
            await queue.send({"schema_version": 1, "message_id": message_id})
        except Exception:
            # The durable queued row remains visible to operators for replay
            # and to the scheduled dispatcher for republication.
            record_degradation(request, "communication_queue_publish_failed")

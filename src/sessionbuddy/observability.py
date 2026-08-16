from __future__ import annotations

import json
import time
import uuid
from typing import Any

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SAFE_PHASES = ("authn", "authz", "validation", "db", "domain", "serialization")
SAFE_DEGRADATIONS = (
    "account_person_mirror_skipped",
    "account_person_mirror_unverified",
    "account_profile_update_unverified",
    "account_session_rotation_unconfirmed",
    "account_stale_session_cleanup_failed",
    "asset_scan_queue_publish_failed",
    "communication_queue_publish_failed",
    "decision_correction_conflict",
    "submission_decision_concurrent_reconciled",
    "speaker_import_row_failed",
    "speaker_profile_audit_failed",
    "speaker_restore_audit_failed",
)


def record_timing(request: Request, phase: str, duration_ms: float) -> None:
    """Record a safe duration for Server-Timing and structured completion telemetry."""
    if phase not in SAFE_PHASES:
        raise ValueError(f"Unsupported timing phase: {phase}")
    timings: dict[str, float] = request.state.timings
    timings[phase] = timings.get(phase, 0.0) + max(0.0, duration_ms)


def record_degradation(request: Request, code: str) -> None:
    """Flag a deliberately absorbed failure on this request's completion record.

    A handler that swallows an error to protect an already-committed mutation
    still owes operators a signal; otherwise the only evidence is the delay
    before a scheduled recovery pass picks the work up. Codes come from a fixed
    allowlist so a degradation record can never carry a recipient address,
    payload, or provider error string.
    """
    if code not in SAFE_DEGRADATIONS:
        raise ValueError(f"Unsupported degradation code: {code}")
    degradations: list[str] | None = getattr(request.state, "degradations", None)
    if degradations is None:
        degradations = []
        request.state.degradations = degradations
    if code not in degradations:
        degradations.append(code)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestObservabilityMiddleware:
    """Add request telemetry without Starlette's task-spawning HTTP adapter."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_ns = time.perf_counter_ns()
        request = Request(scope, receive=receive)
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if _valid_request_id(supplied_request_id)
            else str(uuid.uuid4())
        )
        request.state.request_id = request_id
        request.state.request_started_ns = started_ns
        request.state.timings = {}
        request.state.degradations = []
        status_code = 500

        async def send_with_observability(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                elapsed_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["Server-Timing"] = _server_timing(
                    elapsed_ms,
                    request.state.timings,
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_observability)
        finally:
            total_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            timings: dict[str, float] = request.state.timings
            degradations: list[str] = request.state.degradations
            if status_code >= 500:
                level = "error"
            elif degradations:
                level = "warning"
            else:
                level = "info"
            event: dict[str, Any] = {
                "event": "http.request.completed",
                "level": level,
                "request_id": request_id,
                "method": request.method,
                "route": _route_template(request),
                "status_class": f"{status_code // 100}xx",
                "duration_ms": round(total_ms, 3),
                "timings_ms": {key: round(value, 3) for key, value in timings.items()},
            }
            # Only present when set, so successful requests keep their exact
            # historical shape for log-based dashboards and tests.
            if degradations:
                event["degradations"] = sorted(degradations)
            # Python Workers reliably retain stdout/stderr in Workers Logs.
            # Logging's default warning threshold can otherwise suppress these
            # completion records and make a user-facing reference unsearchable.
            print(json.dumps(event, separators=(",", ":"), sort_keys=True))


def _valid_request_id(value: str) -> bool:
    return 1 <= len(value) <= 128 and all(
        character.isalnum() or character in "-_." for character in value
    )


def _server_timing(total_ms: float, timings: dict[str, float]) -> str:
    values = [f"app;dur={total_ms:.1f}"]
    values.extend(f"{phase};dur={timings[phase]:.1f}" for phase in SAFE_PHASES if phase in timings)
    return ", ".join(values)

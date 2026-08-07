from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("sessionbuddy.request")

SAFE_PHASES = ("authn", "authz", "validation", "db", "domain", "serialization")


def record_timing(request: Request, phase: str, duration_ms: float) -> None:
    """Record a safe duration for Server-Timing and structured completion telemetry."""
    if phase not in SAFE_PHASES:
        raise ValueError(f"Unsupported timing phase: {phase}")
    timings: dict[str, float] = request.state.timings
    timings[phase] = timings.get(phase, 0.0) + max(0.0, duration_ms)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_ns = time.perf_counter_ns()
        supplied_request_id = request.headers.get("x-request-id", "")
        request_id = (
            supplied_request_id
            if _valid_request_id(supplied_request_id)
            else str(uuid.uuid4())
        )
        request.state.request_id = request_id
        request.state.timings = {}
        response: Response | None = None
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            total_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            timings: dict[str, float] = request.state.timings
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers["Server-Timing"] = _server_timing(total_ms, timings)

            event: dict[str, Any] = {
                "event": "http.request.completed",
                "request_id": request_id,
                "method": request.method,
                "route": _route_template(request),
                "status_class": f"{status_code // 100}xx",
                "duration_ms": round(total_ms, 3),
                "timings_ms": {key: round(value, 3) for key, value in timings.items()},
            }
            logger.info(json.dumps(event, separators=(",", ":"), sort_keys=True))


def _valid_request_id(value: str) -> bool:
    return 1 <= len(value) <= 128 and all(
        character.isalnum() or character in "-_." for character in value
    )


def _server_timing(total_ms: float, timings: dict[str, float]) -> str:
    values = [f"app;dur={total_ms:.1f}"]
    values.extend(f"{phase};dur={timings[phase]:.1f}" for phase in SAFE_PHASES if phase in timings)
    return ", ".join(values)

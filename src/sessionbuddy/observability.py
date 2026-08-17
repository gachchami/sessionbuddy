from __future__ import annotations

import hashlib
import json
import os
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
    "accepted_participant_reconciliation_failed",
    "asset_scan_queue_publish_failed",
    "communication_queue_publish_failed",
    "decision_correction_conflict",
    "submission_decision_concurrent_reconciled",
    "speaker_import_row_failed",
    "speaker_profile_audit_failed",
    "speaker_restore_audit_failed",
)
SAFE_CONFLICTS = (
    "evaluation_round_version",
    "evaluation_round_version_absent",
)
SAFE_INTEGRITY_SIGNALS = ("sessionbuddy.signed_cursor.shape_failure",)
SAFE_CURSOR_CONTRACTS = (
    "admin_events",
    "cfp_submissions",
    "communication_status",
    "evaluation_keyset",
    "speaker_onboarding",
    "other",
)
SAFE_CURSOR_FIELDS = ("as_of", "due", "id", "position", "starts", "sub", "ts", "other")
SAFE_CURSOR_CONSTRAINTS = ("empty", "fields", "length", "required", "type", "other")


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


def record_conflict(request: Request, code: str) -> None:
    """Attach one bounded conflict class to durable request telemetry.

    Expected optimistic-concurrency refusals are not degradations and must not
    create audit rows: the whole point is that the losing write leaves no
    database residue. Recording an allowlisted class on the request completion
    event makes the refusal searchable in Workers Logs by route and request ID
    without exposing tenant IDs, version values, payloads, or idempotency keys.
    """
    if code not in SAFE_CONFLICTS:
        raise ValueError(f"Unsupported conflict code: {code}")
    conflicts: list[str] | None = getattr(request.state, "conflicts", None)
    if conflicts is None:
        conflicts = []
        request.state.conflicts = conflicts
    if code not in conflicts:
        conflicts.append(code)


def record_failure(request: Request, exception: Exception) -> None:
    """Attach privacy-safe failure evidence to the completion event.

    Exception messages can contain SQL, form values, provider responses, or
    other tenant data, so they never enter telemetry. The exception class,
    final code location, and a stable stack fingerprint are enough to group a
    500 and find the responsible source while the request ID connects it to
    the user-visible reference.
    """
    frames: list[tuple[str, int, str]] = []
    traceback = exception.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        frames.append((_safe_code_path(code.co_filename), traceback.tb_lineno, code.co_name))
        traceback = traceback.tb_next
    exception_type = type(exception).__name__
    fingerprint_input = "|".join(
        [exception_type, *(f"{filename}:{line}:{function}" for filename, line, function in frames)]
    )
    location = (
        f"{frames[-1][0]}:{frames[-1][1]}:{frames[-1][2]}" if frames else "unavailable"
    )
    request.state.failure = {
        "exception_type": exception_type[:128],
        "location": location[:256],
        "fingerprint": hashlib.sha256(fingerprint_input.encode()).hexdigest()[:16],
    }


def _safe_code_path(filename: str) -> str:
    """Return a disambiguating source path without its host-specific prefix."""
    normalized = filename.replace("\\", "/")
    for marker in ("sessionbuddy/", "scripts/", "tests/"):
        index = normalized.find(marker)
        if index >= 0:
            return normalized[index:][:192]
    return os.path.basename(normalized)[:192]


def record_integrity_signal(
    request: Request,
    signal: str,
    *,
    cursor_contract: str,
    field: str,
    constraint: str,
) -> None:
    """Emit one bounded-cardinality integrity event without cursor contents."""
    if signal not in SAFE_INTEGRITY_SIGNALS:
        return
    cursor_contract = cursor_contract if cursor_contract in SAFE_CURSOR_CONTRACTS else "other"
    field = field if field in SAFE_CURSOR_FIELDS else "other"
    constraint = constraint if constraint in SAFE_CURSOR_CONSTRAINTS else "other"
    event = {
        "event": signal,
        "level": "error",
        "request_id": getattr(request.state, "request_id", "unavailable"),
        "route": _route_template(request),
        "cursor_contract": cursor_contract,
        "field": field,
        "constraint": constraint,
    }
    print(json.dumps(event, separators=(",", ":"), sort_keys=True))


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
        request.state.conflicts = []
        request.state.failure = None
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
        except Exception as exception:
            # Starlette's ServerErrorMiddleware owns the application-level
            # catch-all handler outside user middleware. Capture here, before
            # our finally emits, then re-raise so the normal 500 response and
            # user-facing request reference remain unchanged.
            record_failure(request, exception)
            raise
        finally:
            total_ms = (time.perf_counter_ns() - started_ns) / 1_000_000
            timings: dict[str, float] = request.state.timings
            degradations: list[str] = request.state.degradations
            conflicts: list[str] = request.state.conflicts
            failure: dict[str, str] | None = request.state.failure
            if status_code >= 500:
                level = "error"
            elif degradations or conflicts:
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
            if conflicts:
                event["conflicts"] = sorted(conflicts)
            if failure:
                event["failure"] = failure
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

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient

from sessionbuddy.communications.queue_publish import publish_committed_messages
from sessionbuddy.observability import (
    RequestObservabilityMiddleware,
    record_conflict,
    record_degradation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def request_for(environment: SimpleNamespace) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/example",
            "headers": [],
            "env": environment,
        }
    )
    request.state.request_id = "queue-publish-request"
    request.state.timings = {}
    return request


class RecordingQueue:
    def __init__(self, fail_on_attempts: frozenset[int] = frozenset()) -> None:
        self.fail_on_attempts = fail_on_attempts
        self.messages: list[dict[str, object]] = []

    async def send(self, message: dict[str, object]) -> None:
        self.messages.append(message)
        if len(self.messages) in self.fail_on_attempts:
            raise RuntimeError("synthetic queue outage")


async def test_publish_sends_versioned_envelopes_for_every_committed_message() -> None:
    queue = RecordingQueue()
    request = request_for(SimpleNamespace(COMMUNICATION_QUEUE=queue))

    await publish_committed_messages(request, ["message-a", "message-b"])

    assert queue.messages == [
        {"schema_version": 1, "message_id": "message-a"},
        {"schema_version": 1, "message_id": "message-b"},
    ]
    assert getattr(request.state, "degradations", []) == []


async def test_publish_absorbs_failures_and_still_attempts_every_message() -> None:
    queue = RecordingQueue(fail_on_attempts=frozenset({1, 3}))
    request = request_for(SimpleNamespace(COMMUNICATION_QUEUE=queue))

    await publish_committed_messages(request, ["message-a", "message-b", "message-c"])

    assert [message["message_id"] for message in queue.messages] == [
        "message-a",
        "message-b",
        "message-c",
    ]
    # Recorded once, not once per failure: the completion event needs a flag,
    # not a counter that could leak batch sizes.
    assert request.state.degradations == ["communication_queue_publish_failed"]


async def test_publish_is_a_no_op_without_a_bound_queue() -> None:
    request = request_for(SimpleNamespace())

    await publish_committed_messages(request, ["message-a"])

    assert getattr(request.state, "degradations", []) == []


def test_degradation_codes_come_from_a_fixed_allowlist() -> None:
    request = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(ValueError, match="Unsupported degradation code"):
        record_degradation(request, "tenant-secret")


def test_conflict_codes_come_from_a_fixed_allowlist() -> None:
    request = SimpleNamespace(state=SimpleNamespace())

    with pytest.raises(ValueError, match="Unsupported conflict code"):
        record_conflict(request, "tenant-secret")


async def test_completion_event_reports_degradations_only_when_present(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def degraded_app(scope, _receive, send) -> None:
        scope["route"] = SimpleNamespace(path="/api/v1/degraded/example")
        if scope["path"].endswith("/degraded"):
            record_degradation(Request(scope), "communication_queue_publish_failed")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    application = RequestObservabilityMiddleware(degraded_app)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        degraded = await client.get("/api/v1/degraded")
        healthy = await client.get("/api/v1/healthy")

    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"event":"http.request.completed"' in line
    ]
    assert degraded.status_code == 200 and healthy.status_code == 200
    assert len(events) == 2
    assert events[0]["level"] == "warning"
    assert events[0]["degradations"] == ["communication_queue_publish_failed"]
    # Healthy requests keep their exact historical shape: no key at all.
    assert events[1]["level"] == "info"
    assert "degradations" not in events[1]


async def test_completion_event_reports_safe_conflicts_without_payload_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def conflicted_app(scope, _receive, send) -> None:
        scope["route"] = SimpleNamespace(path="/api/v1/admin/evaluation-rounds/{round_id}/open")
        record_conflict(Request(scope), "evaluation_round_version")
        await send({"type": "http.response.start", "status": 409, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    application = RequestObservabilityMiddleware(conflicted_app)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/admin/evaluation-rounds/round-secret/open")

    event = next(
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"event":"http.request.completed"' in line
    )
    assert response.status_code == 409
    assert event["level"] == "warning"
    assert event["conflicts"] == ["evaluation_round_version"]
    assert event["route"] == "/api/v1/admin/evaluation-rounds/{round_id}/open"
    assert "round-secret" not in json.dumps(event)


def test_missing_version_has_a_distinct_allowlisted_conflict_class() -> None:
    request = SimpleNamespace(state=SimpleNamespace())

    record_conflict(request, "evaluation_round_version_absent")

    assert request.state.conflicts == ["evaluation_round_version_absent"]


def test_source_wiring_request_handlers_publish_only_through_the_shared_helper() -> None:
    """Every post-commit communication publish must route through
    publish_committed_messages, so no handler can reintroduce the
    commit-then-500 shape by touching the queue binding directly."""
    for relative in (
        "cfp/router.py",
        "evaluation/router.py",
        "platform/auth/access.py",
    ):
        source = (PROJECT_ROOT / "src" / "sessionbuddy" / relative).read_text(
            encoding="utf-8"
        )
        assert "COMMUNICATION_QUEUE" not in source, relative
        assert "publish_committed_messages" in source, relative

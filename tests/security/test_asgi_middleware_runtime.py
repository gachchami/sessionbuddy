import inspect
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

import sessionbuddy.observability as observability_module
import sessionbuddy.security as security_module
from sessionbuddy.observability import RequestObservabilityMiddleware, record_timing
from sessionbuddy.security import SecurityHeadersMiddleware


def test_request_middleware_does_not_use_base_http_middleware() -> None:
    assert not issubclass(RequestObservabilityMiddleware, BaseHTTPMiddleware)
    assert not issubclass(SecurityHeadersMiddleware, BaseHTTPMiddleware)
    assert "BaseHTTPMiddleware" not in inspect.getsource(observability_module)
    assert "BaseHTTPMiddleware" not in inspect.getsource(security_module)


async def test_pure_asgi_middleware_preserves_streaming_headers_state_and_telemetry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def streaming_app(scope, receive, send) -> None:
        request = Request(scope, receive=receive)
        assert request.state.request_id == "streaming-request"
        assert isinstance(request.state.request_started_ns, int)
        assert request.state.timings == {}
        record_timing(request, "db", 1.25)
        scope["route"] = SimpleNamespace(path="/api/v1/stream/{item_id}")
        await send(
            {
                "type": "http.response.start",
                "status": 206,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"cache-control", b"public, max-age=300"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"stream-", "more_body": True})
        await send({"type": "http.response.body", "body": b"complete"})

    application = RequestObservabilityMiddleware(SecurityHeadersMiddleware(streaming_app))
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/stream/example",
            headers={"x-request-id": "streaming-request"},
        )

    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"event":"http.request.completed"' in line
    ]
    assert response.status_code == 206
    assert response.content == b"stream-complete"
    assert response.headers["x-request-id"] == "streaming-request"
    assert response.headers["server-timing"].startswith("app;dur=")
    assert "db;dur=1.2" in response.headers["server-timing"]
    assert response.headers["cache-control"] == "public, max-age=300"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert len(events) == 1
    assert events[0]["request_id"] == "streaming-request"
    assert events[0]["route"] == "/api/v1/stream/{item_id}"
    assert events[0]["status_class"] == "2xx"
    assert events[0]["timings_ms"] == {"db": 1.25}


async def test_pure_asgi_middleware_applies_headers_and_completion_to_error_response(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def failing_app(scope, _receive, send) -> None:
        scope["route"] = SimpleNamespace(path="/api/v1/failure")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"error":"safe"}'})

    application = RequestObservabilityMiddleware(SecurityHeadersMiddleware(failing_app))
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/failure",
            headers={"x-request-id": "failed-request"},
        )

    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"event":"http.request.completed"' in line
    ]
    assert response.status_code == 503
    assert response.headers["x-request-id"] == "failed-request"
    assert response.headers["server-timing"].startswith("app;dur=")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["strict-transport-security"] == (
        "max-age=31536000; includeSubDomains"
    )
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert len(events) == 1
    assert events[0]["level"] == "error"
    assert events[0]["route"] == "/api/v1/failure"
    assert events[0]["status_class"] == "5xx"


async def test_unhandled_failure_still_emits_exactly_one_completion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def raising_app(scope, _receive, _send) -> None:
        scope["route"] = SimpleNamespace(path="/api/v1/unhandled")
        raise RuntimeError("synthetic downstream failure")

    application = RequestObservabilityMiddleware(SecurityHeadersMiddleware(raising_app))
    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/unhandled",
            headers={"x-request-id": "unhandled-request"},
        )

    events = [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if '"event":"http.request.completed"' in line
    ]
    assert response.status_code == 500
    assert len(events) == 1
    assert events[0]["level"] == "error"
    assert events[0]["request_id"] == "unhandled-request"
    assert events[0]["route"] == "/api/v1/unhandled"
    assert events[0]["status_class"] == "5xx"


async def test_embed_response_remains_frameable_without_x_frame_options() -> None:
    async def embed_app(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"embed"})

    async with AsyncClient(
        transport=ASGITransport(app=SecurityHeadersMiddleware(embed_app)),
        base_url="http://test",
    ) as client:
        response = await client.get("/embeds/events/example/schedule")

    assert response.status_code == 200
    assert "x-frame-options" not in response.headers
    assert "frame-ancestors *" in response.headers["content-security-policy"]


async def test_magic_link_confirmation_preserves_same_origin_for_csrf_check() -> None:
    async def document_app(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"confirm"})

    async with AsyncClient(
        transport=ASGITransport(app=SecurityHeadersMiddleware(document_app)),
        base_url="http://test",
    ) as client:
        confirmation = await client.get("/auth/verify")
        ordinary = await client.get("/sign-in")

    assert confirmation.headers["referrer-policy"] == "same-origin"
    assert ordinary.headers["referrer-policy"] == "no-referrer"

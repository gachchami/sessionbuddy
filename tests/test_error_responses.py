import json

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from sessionbuddy.api.app import app, http_error, validation_error
from sessionbuddy.platform.upload_contracts import UploadPolicyError


def error_request(path: str = "/api/v1/example") -> Request:
    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 443),
        }
    )
    request.state.request_id = "actionable-error-test"
    return request


async def test_http_exception_preserves_actionable_server_detail() -> None:
    response = await http_error(
        error_request(),
        HTTPException(status_code=409, detail="Applications are closed."),
    )

    assert json.loads(response.body) == {
        "error": {"code": "conflict", "message": "Applications are closed."},
        "request_id": "actionable-error-test",
    }


async def test_upload_refusals_preserve_their_stable_recovery_code() -> None:
    response = await http_error(
        error_request("/api/v1/speaker/events/event-a/upload-authorizations"),
        UploadPolicyError(
            415,
            "upload_type_not_allowed",
            "This file type is not allowed for that upload.",
        ),
    )

    assert json.loads(response.body) == {
        "error": {
            "code": "upload_type_not_allowed",
            "message": "This file type is not allowed for that upload.",
        },
        "request_id": "actionable-error-test",
    }


async def test_validation_error_names_fields_without_echoing_input() -> None:
    response = await validation_error(
        error_request(),
        RequestValidationError(
            [
                {
                    "type": "string_too_long",
                    "loc": ("body", "proposal_title"),
                    "msg": "String should have at most 200 characters",
                    "input": "private proposal text",
                }
            ]
        ),
    )

    body = json.loads(response.body)
    assert body["error"] == {
        "code": "validation_failed",
        "message": "String should have at most 200 characters",
        "field": "proposal_title",
        "metadata": {
            "details": [
                {
                    "field": "proposal_title",
                    "message": "String should have at most 200 characters",
                    "type": "string_too_long",
                }
            ]
        },
    }
    assert "private proposal text" not in response.body.decode()


async def test_browser_navigation_gets_a_real_not_found_page() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/missing-page",
            headers={"accept": "text/html", "x-request-id": "missing-page-test"},
        )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "We could not find that page." in response.text
    assert "missing-page-test" in response.text
    assert "resource_not_found" not in response.text
    assert 'href="/"' in response.text
    assert 'href="/admin"' not in response.text


async def test_missing_api_always_keeps_the_json_error_contract() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/missing",
            headers={"accept": "text/html", "x-request-id": "missing-api-test"},
        )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "error": {"code": "resource_not_found", "message": "Resource not found"},
        "request_id": "missing-api-test",
    }


async def test_unhandled_api_failure_is_safe_json(monkeypatch) -> None:
    def fail_status(*_args, **_kwargs):
        raise RuntimeError("database password and internal stack must stay private")

    monkeypatch.setattr(
        "sessionbuddy.console.router.public_engine_room_status", fail_status
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/v1/engine-room/status",
            headers={"x-request-id": "api-failure-test"},
        )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Something went wrong on our side. Try again.",
        },
        "request_id": "api-failure-test",
    }
    assert "password" not in response.text
    assert response.headers["x-request-id"] == "api-failure-test"


async def test_unhandled_browser_failure_is_a_recovery_page(monkeypatch) -> None:
    async def fail_setup_check(_database):
        raise RuntimeError("private database implementation detail")

    class Environment:
        DB = object()

    async def inject_environment(scope, receive, send):
        scope["env"] = Environment()
        await app(scope, receive, send)

    monkeypatch.setattr("sessionbuddy.api.app.setup_is_configured", fail_setup_check)
    async with AsyncClient(
        transport=ASGITransport(app=inject_environment, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/",
            headers={"accept": "text/html", "x-request-id": "page-failure-test"},
        )

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "We could not load this page." in response.text
    assert "page-failure-test" in response.text
    assert "implementation detail" not in response.text
    assert ">Try again<" in response.text

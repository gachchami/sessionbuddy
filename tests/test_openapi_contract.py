import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app
from sessionbuddy.api.openapi_contract import OPENAPI_JSON

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _client(app_env: str | None) -> AsyncClient:
    async def inject_environment(scope, receive, send):
        if app_env is not None:
            scope["env"] = SimpleNamespace(APP_ENV=app_env)
        await app(scope, receive, send)

    return AsyncClient(
        transport=ASGITransport(app=inject_environment),
        base_url="https://test",
    )


def test_generated_openapi_contract_matches_runtime_and_checked_document() -> None:
    generated = json.loads(OPENAPI_JSON)
    checked = json.loads(
        (PROJECT_ROOT / "openapi" / "openapi.json").read_text(encoding="utf-8")
    )

    assert generated == checked == app.openapi()
    assert "/api/v1/openapi.json" not in generated["paths"]
    assert "/docs" not in generated["paths"]


def test_speaker_portal_submission_timestamp_is_required_by_contract() -> None:
    schema = app.openapi()["components"]["schemas"]["SpeakerSubmissionView"]

    assert schema["properties"]["submitted_at_ms"]["type"] == "integer"
    assert "submitted_at_ms" in schema["required"]


@pytest.mark.parametrize("app_env", ["local", "development", "preview"])
async def test_openapi_contract_is_available_only_in_nonproduction_environments(
    app_env: str,
) -> None:
    async with _client(app_env) as client:
        response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert response.content == OPENAPI_JSON
    assert response.headers["content-type"] == "application/json"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("app_env", ["", "production", "staging", "unknown"])
async def test_openapi_contract_fails_closed_outside_approved_environments(
    app_env: str,
) -> None:
    async with _client(app_env) as client:
        response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


async def test_openapi_contract_fails_closed_without_environment_binding() -> None:
    async with _client(None) as client:
        response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


async def test_openapi_request_never_generates_schema_at_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_generation():
        raise AssertionError("OpenAPI schema generation ran during a request")

    monkeypatch.setattr(app, "openapi", unexpected_generation)
    async with _client("development") as client:
        response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert response.content == OPENAPI_JSON


@pytest.mark.parametrize("app_env", ["local", "development", "preview"])
async def test_read_only_api_docs_are_available_in_approved_environments(
    app_env: str,
) -> None:
    async with _client(app_env) as client:
        page = await client.get("/docs")
        stylesheet = await client.get("/docs/assets/api-docs.css")
        javascript = await client.get("/docs/assets/api-docs.js")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert "API contract, without side effects." in page.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert stylesheet.headers["cache-control"] == "no-store"
    assert javascript.status_code == 200
    assert javascript.headers["content-type"].startswith("text/javascript")
    assert javascript.headers["cache-control"] == "no-store"
    assert 'window.SessionBuddyApi.request("/api/v1/openapi.json"' in javascript.text


@pytest.mark.parametrize("app_env", ["", "production", "staging", "unknown"])
async def test_api_docs_fail_closed_outside_approved_environments(
    app_env: str,
) -> None:
    async with _client(app_env) as client:
        page = await client.get("/docs")
        stylesheet = await client.get("/docs/assets/api-docs.css")
        javascript = await client.get("/docs/assets/api-docs.js")

    for response in (page, stylesheet, javascript):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "resource_not_found"


async def test_api_docs_fail_closed_without_environment_binding() -> None:
    async with _client(None) as client:
        response = await client.get("/docs")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"

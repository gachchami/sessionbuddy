from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from sessionbuddy.console import embedded_assets, foundation_console_router
from sessionbuddy.console.models import BrowserTelemetryPayload


@pytest.fixture
def console_app() -> FastAPI:
    app = FastAPI()
    app.include_router(foundation_console_router)
    return app


@pytest.mark.asyncio
async def test_console_is_semantic_accessible_and_labels_synthetic(console_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        response = await client.get("/foundation")

    assert response.status_code == 200
    html = response.text
    assert '<html lang="en">' in html
    assert '<meta name="viewport"' in html
    assert 'href="#content"' in html
    assert 'id="content"' in html
    assert 'role="status"' in html
    assert "Synthetic / local" in html
    assert "<h1>Runtime and performance</h1>" in html
    assert 'id="build-progress"' in html
    assert '<th scope="col">' in html


@pytest.mark.asyncio
async def test_console_assets_are_dependency_free_and_responsive(console_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        css = await client.get("/foundation/assets/console.css")
        js = await client.get("/foundation/assets/console.js")

    assert css.status_code == js.status_code == 200
    assert "@media (max-width: 48rem)" in css.text
    assert "prefers-reduced-motion" in css.text
    assert "innerHTML" not in js.text
    assert "document.createTextNode" in js.text
    assert "__sessionbuddyTelemetryDraft" in js.text
    for forbidden in ("email", "cookie", "form_data", "raw_url", "query_string"):
        assert forbidden not in js.text.lower()


@pytest.mark.asyncio
async def test_public_status_model_is_safe_and_explicit_about_missing_data(
    console_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=console_app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/foundation/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_classification"] == "synthetic/local"
    assert payload["measurements_are_live"] is False
    assert {item["state"] for item in payload["coverage"]} >= {"available", "planned"}
    assert not ({"tenant_id", "user_id", "event_id", "email"} & payload.keys())
    assert {item["state"] for item in payload["build_progress"]} == {
        "complete",
        "in_progress",
        "pending",
    }
    assert sum(item["state"] == "complete" for item in payload["build_progress"]) == 3


def test_browser_telemetry_contract_rejects_private_or_arbitrary_dimensions() -> None:
    valid = {
        "schema_version": 1,
        "page_template": "/foundation",
        "navigation_type": "navigate",
        "device_class": "desktop",
        "sampled": False,
        "lcp_ms": 850.2,
        "cls": 0.01,
    }
    assert BrowserTelemetryPayload.model_validate(valid).lcp_ms == 850.2

    with pytest.raises(ValidationError):
        BrowserTelemetryPayload.model_validate({**valid, "email": "private@example.test"})
    with pytest.raises(ValidationError):
        BrowserTelemetryPayload.model_validate({**valid, "page_template": "/events/secret"})


def test_static_page_has_no_external_dependencies() -> None:
    static_dir = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"
    html = (static_dir / "foundation.html").read_text()
    assert "https://" not in html
    assert "http://" not in html


def test_embedded_worker_assets_match_sources() -> None:
    static_dir = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"
    for filename, constant in embedded_assets.ASSETS.items():
        assert getattr(embedded_assets, constant) == (static_dir / filename).read_text()

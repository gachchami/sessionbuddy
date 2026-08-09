from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from sessionbuddy.api.app import app
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.speaker_operations.models import SpeakerProfileUpdate
from sessionbuddy.speaker_operations.router import (
    _cursor,
    _next_cursor,
    _speaker_asset_version_view,
)


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def test_speaker_portal_shell_is_local_and_safe(client) -> None:
    page = await client.get("/speaker")
    javascript = await client.get("/speaker/assets/speaker-portal.js")
    stylesheet = await client.get("/speaker/assets/speaker.css")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert ">Sign in</button>" in page.text
    assert javascript.status_code == 200
    assert "innerHTML" not in javascript.text
    assert stylesheet.status_code == 200


async def test_speaker_portal_api_fails_closed_without_database(client) -> None:
    response = await client.get("/api/v1/speaker/portal")

    assert response.status_code == 503


def test_asset_history_maps_database_columns_to_public_contract() -> None:
    view = _speaker_asset_version_view(
        {
            "generation": 2,
            "original_filename": "slides-v2.pdf",
            "content_type": "application/pdf",
            "byte_size": 4096,
            "is_current": 1,
            "uploaded_at_ms": 1_700_000_000_000,
        }
    )

    assert view.filename == "slides-v2.pdf"
    assert view.state == "current"


def test_profile_update_allows_only_absolute_web_links() -> None:
    with pytest.raises(ValidationError):
        SpeakerProfileUpdate(
            display_name="Speaker",
            biography="Biography",
            links=["javascript:alert(1)"],
            version=1,
        )


async def test_admin_onboarding_shell_is_local_and_safe(client) -> None:
    page = await client.get("/admin/events/event-a/onboarding")
    javascript = await client.get("/admin/onboarding/assets/onboarding.js")
    stylesheet = await client.get("/admin/onboarding/assets/onboarding.css")

    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert javascript.status_code == 200
    assert "innerHTML" not in javascript.text
    assert stylesheet.status_code == 200


def test_dashboard_cursor_is_signed_and_filter_bound() -> None:
    request = SimpleNamespace(scope={"env": SimpleNamespace(CSRF_HMAC_KEY="x" * 32)})
    as_of = utc_now_ms()
    value = _next_cursor(
        request,
        event_id="event-a",
        state="open",
        task_type="profile",
        due_at_ms=1_000,
        task_id="task-a",
        as_of=as_of,
    )

    assert _cursor(
        request,
        value,
        event_id="event-a",
        state="open",
        task_type="profile",
    ) == (1_000, "task-a", as_of, as_of + 900_000)
    with pytest.raises(HTTPException):
        _cursor(
            request,
            value,
            event_id="event-b",
            state="open",
            task_type="profile",
        )

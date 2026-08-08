import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app


@pytest.mark.asyncio
async def test_missing_environment_never_enables_privileged_demo_sessions() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin = await client.post("/api/v1/demo/session")
        speaker = await client.post("/api/v1/demo/speaker-session")
        agenda = await client.post("/api/v1/demo/agenda-context?event_id=event")
    assert {admin.status_code, speaker.status_code, agenda.status_code} == {404}


@pytest.mark.asyncio
async def test_missing_environment_never_enables_local_private_upload_capability() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/v1/uploads/intent/content?token=attacker",
            content=b"private",
            headers={"content-type": "application/pdf"},
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"

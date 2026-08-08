import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app


@pytest.mark.asyncio
async def test_communications_routes_fail_closed_without_runtime_dependencies() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        statuses = await client.get("/api/v1/admin/events/event/communications")
        preview = await client.post(
            "/api/v1/admin/events/event/communications/preview",
            json={"template_id": "template", "recipient_user_ids": ["user"]},
        )
        reminder = await client.post(
            "/api/v1/admin/events/event/speaker-tasks/task/reminders",
            json={},
            headers={"Idempotency-Key": "a-secure-demo-key"},
        )
    assert {statuses.status_code, preview.status_code, reminder.status_code} == {503}


@pytest.mark.asyncio
async def test_communications_openapi_is_strict_and_explicit() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document = (await client.get("/api/v1/openapi.json")).json()
    paths = document["paths"]
    assert (
        paths["/api/v1/admin/events/{event_id}/communications/send"]["post"]["operationId"]
        == "queueEventCommunication"
    )
    schema = document["components"]["schemas"]["ManualSendRequest"]
    assert schema["additionalProperties"] is False
    assert "confirmed" in schema["required"]

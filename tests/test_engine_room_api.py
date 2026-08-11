from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import sessionbuddy.api.app as api_app_module
from sessionbuddy.api.app import app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as value:
        yield value


async def test_versioned_health_contract(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health", headers={"X-Request-ID": "test-request"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "api_version": "v1"}
    assert response.headers["x-request-id"] == "test-request"
    assert response.headers["server-timing"].startswith("app;dur=")
    assert response.headers["cache-control"] == "no-store"


async def test_root_serves_public_product_homepage(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "From open call to published agenda." in response.text
    assert 'href="/sign-in?redirect=%2Fadmin"' in response.text
    assert "Keep every role aligned" in response.text
    assert 'href="/engine-room"' not in response.text


@pytest.mark.parametrize(
    ("active_role", "destination"),
    [
        ("organizer", "/admin"),
        ("speaker", "/speaker"),
        ("reviewer", "/reviews"),
        (None, "/account"),
    ],
)
async def test_authenticated_root_redirects_to_active_role_dashboard(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    active_role: str | None,
    destination: str,
) -> None:
    async def session(_request):
        return SimpleNamespace(profile_complete=True, active_role=active_role)

    monkeypatch.setattr(api_app_module, "current_access_session", session)
    client.cookies.set("sessionbuddy-local", "test-session")
    response = await client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == destination
    assert response.headers["cache-control"] == "no-store"
    assert "From open call to published agenda." not in response.text


async def test_incomplete_profile_root_redirects_to_account_onboarding(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def session(_request):
        return SimpleNamespace(profile_complete=False, active_role="organizer")

    monkeypatch.setattr(api_app_module, "current_access_session", session)
    client.cookies.set("sessionbuddy-local", "test-session")
    response = await client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/account?onboarding=1&next=%2F"


async def test_landing_page_styles_are_embedded(client: AsyncClient) -> None:
    response = await client.get("/landing/assets/landing.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["cache-control"] == "public, max-age=300"
    assert ".hero-grid" in response.text
    assert "focus-visible" in response.text
    assert "prefers-reduced-motion" in response.text


async def test_invalid_request_id_is_replaced(client: AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "bad request id"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] != "bad request id"


async def test_security_headers_are_centralized(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


async def test_not_found_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/missing")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == {"code": "resource_not_found", "message": "Resource not found"}
    assert body["request_id"] == response.headers["x-request-id"]


async def test_openapi_contains_engine_room_and_cfp_routes(client: AsyncClient) -> None:
    response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    expected = {
        "/api/v1/bootstrap",
        "/api/v1/setup/status",
        "/api/v1/account/profile",
        "/api/v1/auth/magic-links",
        "/api/v1/auth/session",
        "/api/v1/auth/verify",
        "/api/v1/admin/events/{event_id}/invitations",
        "/api/v1/admin/events/{event_id}/invitations/{invitation_id}",
        "/api/v1/admin/events/{event_id}/members",
        "/api/v1/admin/events/{event_id}/members/{user_id}/roles/{role}",
        "/api/v1/admin/organizations",
        "/api/v1/admin/organizations/{organization_id}",
        "/api/v1/admin/organizations/{organization_id}/events",
        "/api/v1/admin/events/{event_id}",
        "/api/v1/admin/events/{event_id}/cfp",
        "/api/v1/admin/events/{event_id}/cfp/publish",
        "/api/v1/admin/events/{event_id}/submissions",
        "/api/v1/admin/events/{event_id}/evaluation-rounds",
        "/api/v1/admin/events/{event_id}/evaluation-rounds/current",
        "/api/v1/admin/events/{event_id}/evaluators",
        "/api/v1/evaluator/assignments",
        "/api/v1/evaluator/assignments/{assignment_id}/evaluation",
        "/api/v1/evaluator/assignments/{assignment_id}/conflict",
        "/api/v1/admin/evaluation-assignments/{assignment_id}/reassign",
        "/api/v1/admin/evaluation-rounds/{round_id}/results",
        "/api/v1/admin/evaluation-rounds/{round_id}/close",
        "/api/v1/admin/evaluation-rounds/{round_id}/submissions/{submission_id}/decision",
        "/api/v1/admin/events/{event_id}/onboarding",
        "/api/v1/admin/events/{event_id}/agenda",
        "/api/v1/admin/events/{event_id}/agenda/setup",
        "/api/v1/admin/events/{event_id}/agenda/rooms",
        "/api/v1/admin/events/{event_id}/agenda/rooms/{room_id}",
        "/api/v1/admin/events/{event_id}/agenda/tracks",
        "/api/v1/admin/events/{event_id}/agenda/tracks/{track_id}",
        "/api/v1/admin/events/{event_id}/agenda/auto-schedule",
        "/api/v1/admin/events/{event_id}/agenda/preview",
        "/api/v1/admin/events/{event_id}/agenda/items",
        "/api/v1/admin/events/{event_id}/agenda/items/{item_id}",
        "/api/v1/admin/events/{event_id}/agenda/publish",
        "/api/v1/events/{event_id}/schedule",
        "/api/v1/public/events/{event_id}/schedule",
        "/api/v1/public/events/{event_id}/speakers",
        "/api/v1/public/events/{event_id}/speakers/{event_speaker_id}/headshot",
        "/api/v1/admin/events/{event_id}/resources",
        "/api/v1/admin/events/{event_id}/speaker-targets",
        "/api/v1/admin/events/{event_id}/speaker-tasks",
        "/api/v1/admin/events/{event_id}/integrations/accelevents/tokens",
        "/api/v1/speaker/resources",
        "/api/v1/speaker/tasks/{task_id}/response",
        "/v1/event/{event_id}/sessions",
        "/v1/event/{event_id}/speakers",
        "/api/v1/admin/events/{event_id}/communications",
        "/api/v1/admin/events/{event_id}/communications/dispatch-local",
        "/api/v1/admin/events/{event_id}/communications/preview",
        "/api/v1/admin/events/{event_id}/communications/send",
        "/api/v1/admin/events/{event_id}/speaker-tasks/{task_id}/reminders",
        "/api/v1/engine-room/database",
        "/api/v1/engine-room/status",
        "/api/v1/engine-room/communications/requeue-exhausted",
        "/api/v1/forms/{slug}",
        "/api/v1/forms/{slug}/draft",
        "/api/v1/forms/{slug}/submissions",
        "/api/v1/session",
        "/api/v1/session/logout",
        "/api/v1/session/refresh",
        "/api/v1/speaker/portal",
        "/api/v1/speaker/profile",
        "/api/v1/speaker/events/{event_id}/assets",
        "/api/v1/speaker/events/{event_id}/upload-authorizations",
        "/api/v1/speaker/events/{event_id}/upload-intents/{intent_id}/complete",
        "/api/v1/speaker/events/{event_id}/assets/{asset_id}/download-grants",
        "/api/v1/admin/events/{event_id}/assets/{asset_id}/download-grants",
        "/api/v1/assets/download",
        "/health",
        "/api/v1/health",
    }
    assert expected.issubset(set(response.json()["paths"]))


async def test_database_probe_fails_closed_without_binding(client: AsyncClient) -> None:
    response = await client.get("/api/v1/engine-room/database")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "dependency_unavailable",
        "message": "A required dependency is unavailable",
    }
    assert "database" not in response.text


async def test_deployed_environment_comes_from_request_binding() -> None:
    class Environment:
        APP_ENV = "development"

    async def inject_environment(scope, receive, send):
        scope["env"] = Environment()
        await app(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="http://test"
    ) as bound_client:
        response = await bound_client.get("/api/v1/engine-room/status")

    assert response.status_code == 200
    assert response.json()["environment"] == "development"
    assert response.json()["data_classification"] == "synthetic/non-production"


async def test_openapi_operations_have_stable_unique_ids_and_models(client: AsyncClient) -> None:
    document = (await client.get("/api/v1/openapi.json")).json()
    operations = [
        operation
        for methods in document["paths"].values()
        for method, operation in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    operation_ids = [operation["operationId"] for operation in operations]
    assert len(operation_ids) == len(set(operation_ids))
    generated_suffixes = ("_health_get", "_status_get", "_database_get")
    assert all(not value.endswith(generated_suffixes) for value in operation_ids)
    assert all(
        any(status.startswith("2") for status in operation["responses"]) for operation in operations
    )

import pytest
from httpx import ASGITransport, AsyncClient

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


async def test_openapi_contains_only_expected_foundation_routes(client: AsyncClient) -> None:
    response = await client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert set(response.json()["paths"]) == {
        "/health",
        "/api/v1/health",
    }

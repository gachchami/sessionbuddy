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


async def test_root_redirects_to_review_console(client: AsyncClient) -> None:
    response = await client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/foundation"


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
        "/api/v1/foundation/database",
        "/api/v1/foundation/status",
        "/health",
        "/api/v1/health",
    }


async def test_database_probe_fails_closed_without_binding(client: AsyncClient) -> None:
    response = await client.get("/api/v1/foundation/database")

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
        response = await bound_client.get("/api/v1/foundation/status")

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
    assert all("200" in operation["responses"] for operation in operations)

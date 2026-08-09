from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app


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
    assert 'href="/admin"' in response.text


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

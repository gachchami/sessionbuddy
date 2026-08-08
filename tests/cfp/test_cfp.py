from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from sessionbuddy.api.app import app
from sessionbuddy.cfp.models import FormPublish, ProgramCreate, SubmissionCreate
from sessionbuddy.console.models import BrowserTelemetryPayload


def test_cfp_write_models_are_strict_and_bounded() -> None:
    program = ProgramCreate(
        organization_id="11111111-1111-4111-8111-111111111111",
        event_id="22222222-2222-4222-8222-222222222222",
        name="  AI Engineer Summit  ",
    )
    assert program.name == "AI Engineer Summit"

    with pytest.raises(ValidationError):
        FormPublish(slug="Not A Slug", welcome_text="Welcome")
    with pytest.raises(ValidationError):
        SubmissionCreate(
            speaker_name="Speaker",
            proposal_title="Title",
            proposal_abstract="Abstract",
            unexpected="private data",
        )

    telemetry = BrowserTelemetryPayload(
        schema_version=1,
        page_template="/cfp-integration",
        navigation_type="navigate",
        device_class="desktop",
        sampled=False,
        critical_api_ms=25,
    )
    assert telemetry.page_template == "/cfp-integration"


async def test_demo_page_and_admin_routes_fail_closed_outside_local() -> None:
    environment = SimpleNamespace(
        APP_ENV="development",
        DB=object(),
        SESSION_HMAC_KEY="s" * 32,
        CSRF_HMAC_KEY="c" * 32,
    )

    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="http://test"
    ) as client:
        page = await client.get("/cfp-integration")
        context = await client.post("/api/v1/demo/context")
        session = await client.post("/api/v1/demo/session")
        admin = await client.post(
            "/api/v1/admin/programs",
            headers={"Idempotency-Key": "x" * 16},
            json={
                "organization_id": "11111111-1111-4111-8111-111111111111",
                "event_id": "22222222-2222-4222-8222-222222222222",
                "name": "Demo",
            },
        )

    assert {page.status_code, context.status_code, session.status_code} == {404}
    assert admin.status_code == 401


async def test_local_admin_route_requires_authenticated_session() -> None:
    environment = SimpleNamespace(
        APP_ENV="local",
        DB=object(),
        SESSION_HMAC_KEY="s" * 32,
        CSRF_HMAC_KEY="c" * 32,
    )

    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/admin/programs",
            headers={"Idempotency-Key": "x" * 16},
            json={
                "organization_id": "11111111-1111-4111-8111-111111111111",
                "event_id": "22222222-2222-4222-8222-222222222222",
                "name": "Demo",
            },
        )

    assert response.status_code == 401


async def test_cfp_page_is_local_dependency_free_demo() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/cfp-integration")
        css = await client.get("/cfp-integration/assets/cfp_integration.css")
        javascript = await client.get("/cfp-integration/assets/cfp_integration.js")

    assert page.status_code == css.status_code == javascript.status_code == 200
    assert "local synthetic demo" in page.text
    assert "Signed out. Admin actions are locked." in page.text
    assert "Sign in as local demo admin" in page.text
    assert "<button disabled>Create program</button>" in page.text
    assert "https://" not in page.text
    assert "innerHTML" not in javascript.text
    assert "__sessionbuddyTelemetryDraft" in javascript.text
    assert "X-Demo-Actor" not in javascript.text
    assert "x-csrf-token" in javascript.text


async def test_product_pages_are_separate_safe_surfaces() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin = await client.get("/admin/programs")
        public = await client.get("/cfp/example-event")
        submissions = await client.get(
            "/admin/programs/11111111-1111-4111-8111-111111111111/submissions"
        )
        admin_js = await client.get("/product/assets/admin-programs.js")
        public_js = await client.get("/product/assets/public-cfp.js")
        submissions_js = await client.get("/product/assets/admin-submissions.js")
        css = await client.get("/product/assets/product.css")

    assert {admin.status_code, public.status_code, submissions.status_code, css.status_code} == {
        200
    }
    assert "Program management" in admin.text
    assert "Submit a proposal" in public.text
    assert "Submissions" in submissions.text
    for javascript in (admin_js.text, public_js.text, submissions_js.text):
        assert "innerHTML" not in javascript
        assert "__sessionbuddyTelemetryDraft" in javascript
    assert 'page_template: "/admin/programs"' in admin_js.text
    assert 'page_template: "/cfp/{slug}"' in public_js.text
    assert "const form = event.currentTarget" in public_js.text
    assert "event.currentTarget.querySelectorAll" not in public_js.text
    assert 'page_template: "/admin/programs/{program_id}/submissions"' in submissions_js.text
    assert "@media (max-width: 48rem)" in css.text

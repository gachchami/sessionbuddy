import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from sessionbuddy.api.app import app
from sessionbuddy.cfp.models import FormPublish, ProgramCreate, SubmissionCreate
from sessionbuddy.cfp.router import (
    _published_form_view,
    _timed_first,
    _validate_draft_schema,
    _validate_submission_schema,
)
from sessionbuddy.console.models import BrowserTelemetryPayload


class CloudflareFirstStatement:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def first(self, *args: object):
        self.calls.append(args)
        if args == (None,):
            raise RuntimeError("D1_COLUMN_NOTFOUND: Column not found (null)")
        return {"id": "event-1"} if not args else "event-1"


async def test_timed_first_omits_the_column_argument_for_full_rows() -> None:
    request = SimpleNamespace(state=SimpleNamespace(timings={}))
    statement = CloudflareFirstStatement()

    row = await _timed_first(request, statement)
    identifier = await _timed_first(request, statement, "id")

    assert row == {"id": "event-1"}
    assert identifier == "event-1"
    assert statement.calls == [(), ("id",)]
    assert request.state.timings["db"] >= 0


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
        FormPublish(
            slug="unsafe-core",
            welcome_text="Welcome",
            fields=[
                {"key": "speaker_name", "type": "text", "label": "Name"},
                {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
                {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
                {
                    "key": "proposal_abstract",
                    "type": "textarea",
                    "label": "Abstract",
                    "required": True,
                },
            ],
        )
    with pytest.raises(ValidationError):
        SubmissionCreate(
            speaker_name="Speaker",
            proposal_title="Title",
            proposal_abstract="Abstract",
            unexpected="private data",
        )

    telemetry = BrowserTelemetryPayload(
        schema_version=1,
        page_template="/admin/programs",
        navigation_type="navigate",
        device_class="desktop",
        sampled=False,
        critical_api_ms=25,
    )
    assert telemetry.page_template == "/admin/programs"


def test_dynamic_form_conditions_skip_hidden_required_fields() -> None:
    schema = FormPublish(
        slug="conditional-cfp",
        welcome_text="Welcome",
        fields=[
            {"key": "speaker_name", "type": "text", "label": "Name", "required": True},
            {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
            {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
            {
                "key": "proposal_abstract",
                "type": "textarea",
                "label": "Abstract",
                "required": True,
            },
            {
                "key": "format",
                "type": "select",
                "label": "Format",
                "choices": ["talk", "workshop"],
            },
            {"key": "materials", "type": "url", "label": "Materials", "required": True},
        ],
        conditions=[
            {
                "source_key": "format",
                "operator": "equals",
                "value": "workshop",
                "target_key": "materials",
            }
        ],
    ).model_dump(mode="json")
    submission = SubmissionCreate(
        speaker_name="Speaker",
        speaker_email="speaker@example.com",
        proposal_title="Title",
        proposal_abstract="Abstract",
        answers={"format": "talk"},
    )

    _validate_submission_schema(schema, submission)


def test_required_dynamic_answers_are_enforced_by_the_backend() -> None:
    schema = FormPublish(
        slug="server-validation",
        welcome_text="Welcome",
        fields=[
            {"key": "speaker_name", "type": "text", "label": "Name", "required": True},
            {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
            {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
            {
                "key": "proposal_abstract",
                "type": "textarea",
                "label": "Abstract",
                "required": True,
            },
            {"key": "terms", "type": "checkbox", "label": "Agree", "required": True},
            {"key": "notes", "type": "text", "label": "Notes", "required": True},
        ],
    ).model_dump(mode="json")
    base = {
        "speaker_name": "Speaker",
        "speaker_email": "speaker@example.com",
        "proposal_title": "Title",
        "proposal_abstract": "Abstract",
    }
    with pytest.raises(HTTPException) as unchecked:
        _validate_submission_schema(
            schema,
            SubmissionCreate(**base, answers={**base, "terms": False, "notes": "Ready"}),
        )
    assert unchecked.value.status_code == 422
    with pytest.raises(HTTPException) as whitespace:
        _validate_submission_schema(
            schema,
            SubmissionCreate(**base, answers={**base, "terms": True, "notes": "   "}),
        )
    assert whitespace.value.status_code == 422


def test_drafts_allow_incomplete_answers_but_reject_invalid_types_and_sizes() -> None:
    schema = FormPublish(slug="draft-validation", welcome_text="Welcome").model_dump(mode="json")
    _validate_draft_schema(schema, {"proposal_title": ""})
    with pytest.raises(HTTPException) as invalid:
        _validate_draft_schema(schema, {"speaker_email": "not-an-email"})
    assert invalid.value.status_code == 422


def test_published_form_uses_default_accent_for_pre_branding_events() -> None:
    form = _published_form_view(
        {
            "id": "form-1",
            "program_id": "program-1",
            "event_id": "event-1",
            "event_name": "Legacy Event",
            "accent_color": None,
            "logo_url": None,
            "version": 1,
            "slug": "legacy-event",
            "welcome_text": "Welcome",
            "schema_json": json.dumps({"fields": [], "conditions": [], "routing_rules": []}),
            "opens_at_ms": None,
            "closes_at_ms": None,
            "submission_limit": None,
            "success_title": "Proposal received",
            "success_message": "Thank you.",
            "redirect_to_portal": 1,
            "submissions_received": 0,
        },
        now_ms=1,
    )

    assert form.accent_color == "#3159d9"


async def test_admin_routes_require_authentication_outside_local() -> None:
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
        admin = await client.post(
            "/api/v1/admin/programs",
            headers={"Idempotency-Key": "x" * 16},
            json={
                "organization_id": "11111111-1111-4111-8111-111111111111",
                "event_id": "22222222-2222-4222-8222-222222222222",
                "name": "Example Program",
            },
        )
        workspace = await client.get(
            "/api/v1/admin/events/22222222-2222-4222-8222-222222222222/cfp"
        )

    assert admin.status_code == 401
    assert workspace.status_code == 401


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
                "name": "Example Program",
            },
        )

    assert response.status_code == 401


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
        sign_in = await client.get("/sign-in")
        setup = await client.get("/setup")
        setup_css = await client.get("/setup/assets/setup.css")
        setup_js = await client.get("/setup/assets/setup.js")
        access = await client.get("/admin/events/22222222-2222-4222-8222-222222222222/access")
        events = await client.get("/admin/events")
        events_js = await client.get("/admin/events/assets/events.js")
        admin_home = await client.get("/admin")
        event_overview = await client.get(
            "/admin/events/22222222-2222-4222-8222-222222222222"
        )
        speaker_directory = await client.get("/admin/speakers")
        account = await client.get("/account")
        app_shell_js = await client.get("/app-shell/assets/app-shell.js")
        css = await client.get("/product/assets/product.css")

    assert {admin.status_code, public.status_code, submissions.status_code, css.status_code} == {
        200
    }
    assert sign_in.status_code == access.status_code == events.status_code == 200
    assert setup.status_code == setup_css.status_code == setup_js.status_code == 200
    assert "Create the first organization and administrator" in setup.text
    assert {
        admin_home.status_code,
        event_overview.status_code,
        speaker_directory.status_code,
        account.status_code,
        app_shell_js.status_code,
    } == {200}
    assert "secure sign-in link" in sign_in.text
    assert "People and invitations" in access.text
    assert "Create events and keep their details up to date" in events.text
    assert "Organization settings" in events.text
    assert "Save changes" in events.text
    assert "data-auth-shell" in events.text
    assert "Call for speakers" in admin.text
    assert "CFP link" in admin.text
    assert "data-auth-shell" in admin.text
    assert "Submit a proposal" in public.text
    assert "Submissions" in submissions.text
    for javascript in (admin_js.text, public_js.text, submissions_js.text):
        assert "innerHTML" not in javascript
        assert "__sessionbuddyTelemetryDraft" in javascript
    assert 'page_template: "/admin/events/{event_id}/cfp"' in admin_js.text
    assert 'fields.id = "form-fields"' in admin_js.text
    assert "conditions" in admin_js.text
    assert "/admin/events/${encodeURIComponent(event.id)}" in events_js.text
    assert 'button("Edit"' in events_js.text
    assert 'page_template: "/cfp/{slug}"' in public_js.text
    assert "const form = event.currentTarget" in public_js.text
    assert "event.currentTarget.querySelectorAll" not in public_js.text
    assert 'page_template: "/admin/programs/{program_id}/submissions"' in submissions_js.text
    assert "@media (max-width: 48rem)" in css.text

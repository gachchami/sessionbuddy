from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from sessionbuddy.api.app import app
from sessionbuddy.competition.models import AdminEventSpeakerUpdate
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Persona
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.speaker_operations import router as speaker_router
from sessionbuddy.speaker_operations.models import UploadAuthorizationCreate
from sessionbuddy.speaker_operations.router import (
    _cursor,
    _next_cursor,
    _open_call_view,
    _speaker_asset_version_view,
    _speaker_message_content,
    get_speaker_portal,
)


class _CallStatement:
    def __init__(self, database, query):
        self.database = database
        self.query = query
        self.values = ()

    def bind(self, *values):
        self.values = values
        return self

    async def first(self, column=None):
        self.database.queries.append((self.query, self.values))
        if "FROM call_for_speaker_forms" in self.query:
            return self.database.form
        if "COUNT(*)" in self.query:
            return self.database.submitted if column else {"count_value": self.database.submitted}
        return None


class _CallDatabase:
    def __init__(self, form=None, submitted=0):
        self.form = form
        self.submitted = submitted
        self.queries: list[tuple[str, tuple]] = []

    def prepare(self, query):
        return _CallStatement(self, query)


def _call_request() -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/speaker/portal",
        "headers": [],
        "query_string": b"",
        "state": {"timings": {}},
    })


async def _call_view(database):
    return await _open_call_view(
        _call_request(),
        database,
        organization_id="organization-a",
        event_id="event-a",
        submitter_user_id="speaker-a",
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
            "id": "version-2",
            "generation": 2,
            "original_filename": "slides-v2.pdf",
            "content_type": "application/pdf",
            "byte_size": 4096,
            "is_current": 1,
            "uploaded_at_ms": 1_700_000_000_000,
            "version_comment": "Corrected the final diagram",
        }
    )

    assert view.filename == "slides-v2.pdf"
    assert view.state == "current"
    assert view.version_comment == "Corrected the final diagram"

    note_less = _speaker_asset_version_view(
        {
            "id": "version-3",
            "generation": 3,
            "original_filename": "slides-v3.pdf",
            "content_type": "application/pdf",
            "byte_size": 4096,
            "is_current": 1,
            "uploaded_at_ms": 1_700_000_000_001,
            "version_comment": "Legacy upload",
        }
    )
    assert note_less.version_comment is None


def test_upload_authorization_allows_blank_note_for_initial_upload() -> None:
    values = {
        "kind": "slides",
        "filename": "slides.pdf",
        "content_type": "application/pdf",
        "byte_size": 1024,
        "checksum_sha256": "0" * 64,
    }
    assert UploadAuthorizationCreate(**values).version_comment == ""
    assert UploadAuthorizationCreate(**values, version_comment="   ").version_comment == ""
    request = UploadAuthorizationCreate(**values, version_comment="  Final diagrams  ")
    assert request.version_comment == "Final diagrams"


def test_profile_update_allows_only_absolute_web_links() -> None:
    with pytest.raises(ValidationError):
        AdminEventSpeakerUpdate(
            display_name="Speaker",
            biography_override="Biography",
            links=["javascript:alert(1)"],
            version=1,
            participation_version=1,
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


def test_asset_slot_versions_by_speaker_session_and_kind_not_task_id() -> None:
    source = (
        Path(__file__).parents[2] / "src/sessionbuddy/speaker_operations/router.py"
    ).read_text()
    slot_query = source.split("slot = row_mapping(", 1)[1].split("asset_id =", 1)[0]
    assert "COALESCE(task_id" not in slot_query
    assert "AND kind = ?5" in slot_query
    assert "COALESCE(submission_id, '') = COALESCE(?6, '')" in source


def test_speaker_portal_supports_explicit_multi_event_selection() -> None:
    source = (
        Path(__file__).parents[2] / "src/sessionbuddy/speaker_operations/router.py"
    ).read_text()
    assert "event_id: str | None = Query" in source
    assert "(?2 IS NULL OR es.event_id = ?2)" in source
    assert "events=[" in source


def test_speaker_dates_and_bulk_delivery_are_explicit_in_the_ui() -> None:
    static = Path(__file__).parents[2] / "src/sessionbuddy/static"
    portal = (static / "speaker_portal.js").read_text()
    content = (static / "speaker_content.js").read_text()
    messages = (static / "speaker_messages.js").read_text()
    invitation = (static / "co_speaker_invitation.js").read_text()
    message_page = (static / "speaker_messages.html").read_text()

    assert "Event time (${timezone})" in portal
    assert 'make("h4", "Updates")' in portal
    assert "Show older updates" in portal
    assert "sessionbuddy:read-update" in portal
    assert "notification-category" in portal
    assert "Event time (${state.timeZone})" in content
    assert "Event time (${eventTimeZone})" not in messages
    assert "Times use <strong id=\"message-time-zone\"" in message_page
    assert "your local time: ${zone}" in invitation
    assert 'byId("confirm-message-send").showModal()' in messages
    assert "await sendPreviewedMessage()" in messages
    assert "personalized emails will be queued immediately" in message_page
    assert 'selection_status !== "invited"' not in messages
    assert "Invited — awaiting acceptance" in messages
    # Every event participant is a possible recipient, and each row must say
    # where that person stands: an organizer sending straight after a decision
    # round has to see "not selected" before the mail goes out.
    assert "accepted speakers, people whose proposal is still undecided" in message_page
    assert "Proposal not selected" in messages
    assert "Accepted speaker" in messages
    assert 'byId("recipient-status")' in messages
    assert 'showComposeError("Select at least one recipient.")' in messages
    assert 'showComposeError(window.SessionBuddyApi.message(error))' in messages
    assert 'aria-describedby="message-compose-error"' in message_page


async def test_open_call_is_absent_when_the_event_has_no_published_form() -> None:
    assert await _call_view(_CallDatabase(form=None)) is None


async def test_open_call_offers_submission_while_the_window_is_open() -> None:
    now = utc_now_ms()
    database = _CallDatabase(
        form={
            "id": "form-a",
            "slug": "devflow-2027",
            "opens_at_ms": now - 60_000,
            "closes_at_ms": now + 60_000,
            "submission_limit": 3,
        },
        submitted=1,
    )

    call = await _call_view(database)

    assert call is not None
    assert call.accepting_submissions is True
    assert call.slug == "devflow-2027"
    assert call.submitted_count == 1
    assert call.remaining_submissions == 2
    # The portal must only ever see this speaker's own submission count.
    count_query = next(query for query, _ in database.queries if "COUNT(*)" in query)
    assert "submitter_user_id=?2" in count_query
    assert ("form-a", "speaker-a") in [values for _, values in database.queries]


async def test_open_call_explains_a_closed_window_instead_of_offering_submission() -> None:
    now = utc_now_ms()
    database = _CallDatabase(
        form={
            "id": "form-a",
            "slug": "devflow-2027",
            "opens_at_ms": now - 120_000,
            "closes_at_ms": now - 60_000,
            "submission_limit": None,
        }
    )

    call = await _call_view(database)

    assert call is not None
    assert call.accepting_submissions is False
    assert call.availability_message == "Applications are closed."
    assert call.remaining_submissions is None


async def test_open_call_stops_offering_submission_at_the_speaker_limit() -> None:
    database = _CallDatabase(
        form={
            "id": "form-a",
            "slug": "devflow-2027",
            "opens_at_ms": None,
            "closes_at_ms": None,
            "submission_limit": 2,
        },
        submitted=2,
    )

    call = await _call_view(database)

    assert call is not None
    assert call.accepting_submissions is False
    assert call.remaining_submissions == 0
    assert "limit" in call.availability_message


async def test_open_call_only_considers_published_forms_on_active_events() -> None:
    database = _CallDatabase(form=None)

    await _call_view(database)

    form_query = next(query for query, _ in database.queries if "call_for_speaker_forms" in query)
    assert "f.status='published'" in form_query
    assert "e.status='active'" in form_query
    assert "f.organization_id=?1" in form_query and "f.event_id=?2" in form_query


def test_portal_links_rows_to_exact_proposals_and_new_work_to_the_public_cfp() -> None:
    static = Path(__file__).parents[2] / "src/sessionbuddy/static"
    portal = (static / "speaker_portal.js").read_text()
    page = (static / "speaker_portal.html").read_text()

    assert 'id="my-proposals-link"' not in page
    assert "New proposal" not in page
    assert "portal.open_call.public_path" not in portal
    assert (
        "`/speaker/proposals/${encodeURIComponent(submission.form_slug)}/"
        "${encodeURIComponent(submission.id)}`" in portal
    )
    assert 'id="open-proposal-composer"' not in page
    assert "renderOpenCall(portal.open_call || null)" in portal
    assert "innerHTML" not in portal


def test_proposal_surfaces_hold_the_idempotency_key_across_retries() -> None:
    # A lost response leaves the proposal stored. Retrying under a fresh key
    # would be a second proposal rather than a replay of the first.
    static = Path(__file__).parents[2] / "src/sessionbuddy/static"
    public = (static / "public_cfp.js").read_text()

    assert '"idempotency-key": state.pendingSubmission.key' in public
    assert "state.pendingSubmission?.attempt !== attempt" in public
    # The key must still move when the proposal changes; reusing it with a
    # different fingerprint is a 409 server-side.
    create_call = public.split("submissions`", 1)[-1][:400]
    assert '"idempotency-key": idempotencyKey()' not in create_call


def test_delivered_message_content_strips_active_markup_and_allowlists_links() -> None:
    body, links = _speaker_message_content(
        "<p>Hello speaker.</p><script>alert('no')</script>"
        '<a href="javascript:alert(1)">Unsafe</a>'
        '<a href="https://sessionbuddy.example/speaker">Open portal</a>'
        " Plain https://docs.example.test/guide."
    )

    assert body == "Hello speaker.\nUnsafeOpen portal Plain https://docs.example.test/guide."
    assert "alert('no')" not in body
    assert links == [
        "https://sessionbuddy.example/speaker",
        "https://docs.example.test/guide",
    ]


async def test_speaker_notifications_are_scoped_to_authenticated_owner(monkeypatch) -> None:
    class Statement:
        def __init__(self, database, query):
            self.database = database
            self.query = query
            self.values = ()

        def bind(self, *values):
            self.values = values
            return self

        async def first(self, column=None):
            return None

        async def all(self):
            if "FROM communication_messages" in self.query:
                self.database.notification_scope = self.values
                self.database.notification_query = self.query
                return {
                    "results": [{
                        "id": "message-a",
                        "subject": "Speaker briefing",
                        "deterministic_key": "speaker-bulk:briefing:speaker-a",
                        "html_body": '<p>Bring your badge.</p><a href="https://safe.example/brief">Brief</a>',
                        "delivered_at_ms": 1_700_000_000_000,
                    }]
                }
            return {"results": []}

    class Database:
        notification_scope = None
        notification_query = ""

        def prepare(self, query):
            return Statement(self, query)

    database = Database()
    row = {
        "event_speaker_id": "event-speaker-a",
        "organization_id": "organization-a",
        "event_id": "event-a",
        "user_id": "speaker-a",
        "display_name": "Speaker A",
        "job_title": "Engineer",
        "company": "Example",
        "biography": "Biography",
        "location": "Remote",
        "links_json": "[]",
        "version": 1,
        "event_name": "Event A",
        "starts_at_ms": 1_700_000_000_000,
        "ends_at_ms": 1_700_086_400_000,
        "time_zone": "America/New_York",
        "selection_status": "accepted",
    }
    authenticated = AuthenticatedContext(
        actor=Actor(user_id="speaker-a", active_persona=Persona.SPEAKER),
        session_id="session-a",
    )

    async def speaker_row(_request, _event_id):
        return authenticated, row

    async def allow(*_args, **_kwargs):
        return None

    monkeypatch.setattr(speaker_router, "_speaker_row", speaker_row)
    monkeypatch.setattr(speaker_router, "require_permission", allow)
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/speaker/portal",
        "headers": [],
        "query_string": b"event_id=event-a",
        "env": SimpleNamespace(DB=database),
        "state": {"timings": {}},
    })

    result = await get_speaker_portal(request, "event-a")

    assert database.notification_scope == ("organization-a", "event-a", "speaker-a")
    assert "recipient_user_id=?3" in database.notification_query
    assert "status='delivered'" in database.notification_query
    assert "deterministic_key NOT LIKE 'auth:%'" in database.notification_query
    assert result.notifications[0].category == "announcement"
    assert result.notifications[0].body_text == "Bring your badge.\nBrief"
    assert result.notifications[0].links == ["https://safe.example/brief"]

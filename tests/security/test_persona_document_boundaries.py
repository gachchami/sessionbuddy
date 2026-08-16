from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from route_inventory import document_routes

from sessionbuddy.api.app import app
from sessionbuddy.platform.auth import access as auth_access
from sessionbuddy.platform.auth import http as auth_http
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import (
    Actor,
    AuthorizationDecision,
    Persona,
    ResourceGrant,
)


class DocumentLookup:
    def __init__(self, row: dict[str, object] | None = None, error: Exception | None = None):
        self.row = row
        self.error = error
        self.resource_id = ""
        self.queries: list[str] = []

    def prepare(self, query: str):
        self.query = query
        self.queries.append(query)
        return self

    def bind(self, resource_id: str, *_scope: str):
        self.resource_id = resource_id
        return self

    async def first(self):
        if self.error is not None:
            raise self.error
        return self.row


class NestedSpeakerDocumentLookup(DocumentLookup):
    def __init__(self, valid_speaker_id: str = "speaker-1"):
        super().__init__()
        self.valid_speaker_id = valid_speaker_id
        self.bound: tuple[str, ...] = ()

    def bind(self, *values: str):
        self.bound = values
        return self

    async def first(self):
        if "FROM events" in self.query:
            return {"organization_id": "organization-1", "event_id": "event-1"}
        if "FROM event_speakers" in self.query:
            return {"id": self.valid_speaker_id} if self.bound == (
                self.valid_speaker_id,
                "organization-1",
                "event-1",
            ) else None
        raise AssertionError(f"unexpected document lookup: {self.query}")


existing_event = DocumentLookup(
    {"organization_id": "organization-1", "event_id": "event-1"}
)


async def local_app(scope, receive, send):
    scope["env"] = SimpleNamespace(APP_ENV="local", DB=existing_event)
    await app(scope, receive, send)


@pytest.mark.parametrize(
    "path",
    [
        "/speaker",
        "/reviews",
    ],
)
async def test_organizer_session_cannot_load_persona_documents_or_their_shells(
    monkeypatch, path: str
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert response.headers["cache-control"] == "no-store"
    assert response.text == ""


@pytest.mark.parametrize("path", ["/speaker", "/reviews"])
async def test_document_denial_remains_structured_json_for_api_accept(
    monkeypatch, path: str
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(path, headers={"accept": "application/json"})

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "forbidden"
    assert "<!doctype html>" not in response.text.lower()


async def test_unknown_browser_document_uses_html_404_without_a_persona_guess() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/this-document-does-not-exist", headers={"accept": "text/html"}
        )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Page not found" in response.text
    assert "We could not find that page." in response.text
    assert 'href="/"' in response.text
    assert 'href="/admin"' not in response.text
    assert "resource_not_found" not in response.text


@pytest.mark.parametrize(
    ("path", "accept"),
    [
        ("/this-document-does-not-exist", "application/json"),
        ("/api/v1/this-endpoint-does-not-exist", "text/html"),
    ],
)
async def test_unknown_api_style_request_retains_structured_json_404(
    path: str, accept: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path, headers={"accept": accept})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "resource_not_found"
    assert "<!doctype html>" not in response.text.lower()


@pytest.mark.parametrize(
    ("persona", "path", "portal_marker"),
    [
        (Persona.SPEAKER, "/speaker", 'class="app-body speaker-portal-page"'),
        (Persona.REVIEWER, "/reviews", "SessionBuddy · Reviews"),
    ],
)
async def test_matching_persona_session_loads_its_document_shell(
    monkeypatch, persona: Persona, path: str, portal_marker: str
) -> None:
    async def matching_context(_request):
        return AuthenticatedContext(
            actor=Actor(f"{persona.value}-user", active_persona=persona),
            session_id=f"{persona.value}-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", matching_context)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", f"{persona.value}-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert portal_marker in response.text


@pytest.mark.parametrize(
    ("path", "portal_marker", "sign_in_marker"),
    [
        ("/speaker", 'class="app-body speaker-portal-page"', "Speaker sign in"),
        ("/reviews", "SessionBuddy · Reviews", "data-auth-shell"),
    ],
)
async def test_anonymous_browser_keeps_the_portal_sign_in_shell(
    path: str, portal_marker: str, sign_in_marker: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert portal_marker in response.text
    assert sign_in_marker in response.text


ADMIN_DOCUMENT_PATHS = (
    "/admin",
    "/admin/events",
    "/admin/events/event-1",
    "/admin/events/event-1/cfp",
    "/admin/events/event-1/submissions",
    "/admin/events/event-1/agenda",
    "/admin/events/event-1/workspace",
    "/admin/events/event-1/speaker-content",
    "/admin/events/event-1/onboarding",
    "/admin/events/event-1/access",
    "/admin/events/event-1/reviewers",
    "/admin/events/event-1/speakers",
    "/admin/events/event-1/speakers/speaker-1",
    "/admin/events/event-1/messages",
    "/admin/people",
    "/admin/evaluation-rounds/round-1",
)


@pytest.mark.parametrize("path", ADMIN_DOCUMENT_PATHS)
@pytest.mark.parametrize("persona", [Persona.SPEAKER, Persona.REVIEWER])
async def test_non_organizer_session_cannot_load_admin_document_shells(
    monkeypatch, path: str, persona: Persona
) -> None:
    async def wrong_persona_context(_request):
        return AuthenticatedContext(
            actor=Actor(f"{persona.value}-user", active_persona=persona),
            session_id=f"{persona.value}-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", wrong_persona_context)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", f"{persona.value}-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/speaker" if persona is Persona.SPEAKER else "/reviews"
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.text == ""


@pytest.mark.parametrize("path", ADMIN_DOCUMENT_PATHS)
async def test_organizer_session_can_load_admin_document_shells(
    monkeypatch, path: str
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(
        auth_http,
        "authorize",
        lambda *_args: AuthorizationDecision(True, "allowed"),
    )
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in response.text.lower()


EVENT_DOCUMENT_PATHS = tuple(
    path for path in ADMIN_DOCUMENT_PATHS if path.startswith("/admin/events/event-1")
)


@pytest.mark.parametrize("path", EVENT_DOCUMENT_PATHS)
async def test_unknown_event_document_returns_plain_non_cacheable_404(
    monkeypatch, path: str
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(auth_http, "database", lambda _request: DocumentLookup())
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "<title>Event unavailable" in response.text
    assert "This event isn’t available." in response.text
    assert "data-auth-shell" not in response.text
    assert "event-1" not in response.text


@pytest.mark.parametrize(
    "path",
    ["/admin/events/event-1", "/admin/events/event-1/speakers/speaker-1"],
)
async def test_unavailable_event_document_does_not_disclose_authorization_reason(
    monkeypatch, path: str,
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(
        auth_http,
        "authorize",
        lambda *_args: AuthorizationDecision(False, "wrong_persona"),
    )
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 404
    assert "<title>Event unavailable" in response.text
    assert "This event isn’t available." in response.text
    assert 'href="/">Open active workspace' in response.text
    assert "Access denied" not in response.text
    assert "data-auth-shell" not in response.text


async def test_event_speaker_document_resolves_the_nested_record_before_rendering(
    monkeypatch,
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(
        auth_http, "database", lambda _request: NestedSpeakerDocumentLookup()
    )
    monkeypatch.setattr(
        auth_http,
        "authorize",
        lambda *_args: AuthorizationDecision(True, "allowed"),
    )
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(
            "/admin/events/event-1/speakers/missing-speaker",
            headers={"accept": "text/html"},
        )

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "This speaker record isn’t available." in response.text
    assert 'href="/admin/events/event-1/speakers"' in response.text
    assert "data-auth-shell" not in response.text


async def test_anonymous_event_document_keeps_sign_in_shell_without_event_lookup(
    monkeypatch,
) -> None:
    def unexpected_database(_request):
        raise AssertionError("anonymous document requests must not resolve event ids")

    monkeypatch.setattr(auth_http, "database", unexpected_database)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/admin/events/not-a-real-event", headers={"accept": "text/html"}
        )

    assert response.status_code == 200
    assert "data-auth-shell" in response.text


async def test_event_lookup_failure_is_not_misreported_as_not_found(monkeypatch) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(
        auth_http,
        "database",
        lambda _request: DocumentLookup(error=RuntimeError("database unavailable")),
    )
    async with AsyncClient(
        transport=ASGITransport(app=local_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(
            "/admin/events/event-1", headers={"accept": "text/html"}
        )

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert "<title>Something went wrong" in response.text
    assert "Page not found" not in response.text


async def test_organizer_can_load_an_authorized_round_document(monkeypatch) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    lookup = DocumentLookup(
        {"organization_id": "organization-1", "event_id": "event-1"}
    )
    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(auth_http, "database", lambda _request: lookup)
    monkeypatch.setattr(
        auth_http,
        "authorize",
        lambda *_args: AuthorizationDecision(True, "allowed"),
    )
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(
            "/admin/evaluation-rounds/round-1", headers={"accept": "text/html"}
        )

    assert response.status_code == 200
    assert lookup.resource_id == "round-1"
    assert "FROM evaluation_rounds" in lookup.query


@pytest.mark.parametrize(
    ("row", "decision"),
    (
        (None, AuthorizationDecision(True, "allowed")),
        (
            {"organization_id": "organization-1", "event_id": "event-1"},
            AuthorizationDecision(False, "wrong_persona"),
        ),
    ),
)
async def test_unavailable_round_document_returns_non_disclosing_404(
    monkeypatch, row, decision
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(auth_http, "database", lambda _request: DocumentLookup(row))
    monkeypatch.setattr(auth_http, "authorize", lambda *_args: decision)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(
            "/admin/evaluation-rounds/round-1", headers={"accept": "text/html"}
        )

    assert response.status_code == 404
    assert "<title>Review round unavailable" in response.text
    assert "This review round isn’t available." in response.text
    assert 'href="/">Open active workspace' in response.text
    assert "Access denied" not in response.text
    assert "data-auth-shell" not in response.text


async def test_anonymous_round_document_keeps_sign_in_shell_without_lookup(
    monkeypatch,
) -> None:
    def unexpected_database(_request):
        raise AssertionError("anonymous document requests must not resolve round ids")

    monkeypatch.setattr(auth_http, "database", unexpected_database)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/admin/evaluation-rounds/not-a-round", headers={"accept": "text/html"}
        )

    assert response.status_code == 200
    assert "data-auth-shell" in response.text


async def test_round_lookup_failure_is_not_misreported_as_not_found(monkeypatch) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(
        auth_http,
        "database",
        lambda _request: DocumentLookup(error=RuntimeError("database unavailable")),
    )
    async with AsyncClient(
        transport=ASGITransport(app=local_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(
            "/admin/evaluation-rounds/round-1", headers={"accept": "text/html"}
        )

    assert response.status_code == 500
    assert "<title>Something went wrong" in response.text
    assert "Page not found" not in response.text


@pytest.mark.parametrize(
    ("grants", "expected_status"),
    (
        ({"event-1": frozenset({ResourceGrant.MANAGE})}, 200),
        ({}, 404),
    ),
)
async def test_event_document_uses_real_event_manage_policy(
    monkeypatch, grants, expected_status: int
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor(
                "organizer-user",
                active_persona=Persona.ORGANIZER,
                resource_grants=grants,
            ),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(
            "/admin/events/event-1", headers={"accept": "text/html"}
        )

    assert response.status_code == expected_status


async def test_reviewer_access_document_matches_resource_access_manage_policy(
    monkeypatch,
) -> None:
    async def edit_only_context(_request):
        return AuthenticatedContext(
            actor=Actor(
                "organizer-user",
                active_persona=Persona.ORGANIZER,
                resource_grants={"event-1": frozenset({ResourceGrant.EDIT})},
            ),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", edit_only_context)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        overview = await client.get(
            "/admin/events/event-1", headers={"accept": "text/html"}
        )
        reviewers = await client.get(
            "/admin/events/event-1/reviewers", headers={"accept": "text/html"}
        )

    assert overview.status_code == 200
    assert reviewers.status_code == 404


async def test_reviewer_invitation_read_matches_archived_document_policy(
    monkeypatch,
) -> None:
    calls: list[dict[str, object]] = []

    class EmptyInvitationDB:
        def prepare(self, _query: str):
            return self

        def bind(self, *_values):
            return self

        async def all(self):
            return {"results": []}

    async def managed_event(_request, event_id, **options):
        calls.append({"event_id": event_id, **options})
        return EmptyInvitationDB(), "organization-1", object()

    monkeypatch.setattr(auth_access, "_managed_event", managed_event)
    result = await auth_access.list_invitations("event-1", SimpleNamespace())

    assert result.data == []
    assert calls == [
        {"event_id": "event-1", "mutation": False, "include_archived": True}
    ]


@pytest.mark.parametrize(
    ("operation", "include_archived"),
    (
        (auth_access.resend_invitation, False),
        (auth_access.revoke_invitation, True),
    ),
)
async def test_reviewer_invitation_mutations_match_archived_document_policy(
    monkeypatch, operation, include_archived: bool
) -> None:
    class ManagedEventReached(Exception):
        pass

    calls: list[dict[str, object]] = []

    async def managed_event(_request, event_id, **options):
        calls.append({"event_id": event_id, **options})
        raise ManagedEventReached

    monkeypatch.setattr(auth_access, "_managed_event", managed_event)
    with pytest.raises(ManagedEventReached):
        await operation("event-1", "invitation-1", SimpleNamespace())

    expected = {"event_id": "event-1", "mutation": True}
    if include_archived:
        expected["include_archived"] = True
    assert calls == [expected]


@pytest.mark.parametrize(
    ("path", "allows_archived"),
    (
        ("/admin/events/event-1", True),
        ("/admin/events/event-1/access", True),
        ("/admin/events/event-1/reviewers", True),
        ("/admin/events/event-1/cfp", False),
        ("/admin/events/event-1/submissions", False),
        ("/admin/events/event-1/agenda", False),
        ("/admin/events/event-1/workspace", False),
        ("/admin/events/event-1/speaker-content", False),
        ("/admin/events/event-1/onboarding", False),
        ("/admin/events/event-1/speakers", False),
        ("/admin/events/event-1/speakers/speaker-1", False),
        ("/admin/events/event-1/messages", False),
    ),
)
async def test_event_documents_declare_their_archived_event_policy(
    monkeypatch, path: str, allows_archived: bool
) -> None:
    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    lookup = DocumentLookup(
        {"organization_id": "organization-1", "event_id": "event-1"}
    )
    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    monkeypatch.setattr(auth_http, "database", lambda _request: lookup)
    monkeypatch.setattr(
        auth_http,
        "authorize",
        lambda *_args: AuthorizationDecision(True, "allowed"),
    )
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 200
    event_queries = [query for query in lookup.queries if "FROM events" in query]
    assert len(event_queries) == 1
    assert ("status!='archived'" not in event_queries[0]) is allows_archived


PUBLIC_EVENT_DOCUMENT_PATHS = (
    "/events/missing-event/schedule",
    "/events/missing-event/sessions",
    "/events/missing-event/speakers",
    "/events/missing-event/gallery",
    "/embeds/events/missing-event/schedule",
    "/embeds/events/missing-event/sessions",
    "/embeds/events/missing-event/itinerary",
    "/embeds/events/missing-event/speakers",
    "/embeds/events/missing-event/gallery",
)


@pytest.mark.parametrize("path", PUBLIC_EVENT_DOCUMENT_PATHS)
async def test_unknown_public_event_document_returns_contextual_404(
    monkeypatch, path: str
) -> None:
    monkeypatch.setattr(auth_http, "database", lambda _request: DocumentLookup())
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "<title>Event unavailable" in response.text
    assert "This event isn’t available." in response.text
    assert "data-auth-shell" not in response.text


def test_scoped_document_route_inventory_is_complete() -> None:
    actual = {
        path
        for path in document_routes(app)
        if not path.startswith("/api/")
        and ("{event_id}" in path or "{round_id}" in path)
    }
    assert actual == {
        "/admin/events/{event_id}",
        "/admin/events/{event_id}/cfp",
        "/admin/events/{event_id}/submissions",
        "/admin/events/{event_id}/agenda",
        "/admin/events/{event_id}/workspace",
        "/admin/events/{event_id}/speaker-content",
        "/admin/events/{event_id}/onboarding",
        "/admin/events/{event_id}/access",
        "/admin/events/{event_id}/reviewers",
        "/admin/events/{event_id}/speakers",
        "/admin/events/{event_id}/speakers/{event_speaker_id}",
        "/admin/events/{event_id}/messages",
        "/admin/evaluation-rounds/{round_id}",
        "/events/{event_id}/schedule",
        "/events/{event_id}/sessions",
        "/events/{event_id}/speakers",
        "/events/{event_id}/gallery",
        "/embeds/events/{event_id}/schedule",
        "/embeds/events/{event_id}/sessions",
        "/embeds/events/{event_id}/itinerary",
        "/embeds/events/{event_id}/speakers",
        "/embeds/events/{event_id}/gallery",
    }


SCOPED_DOCUMENT_TEMPLATES = sorted(
    path
    for path in document_routes(app)
    if not path.startswith("/api/")
    and ("{event_id}" in path or "{round_id}" in path)
)


@pytest.mark.parametrize("template", SCOPED_DOCUMENT_TEMPLATES)
async def test_every_discovered_scoped_document_earns_its_missing_resource_404(
    monkeypatch, template: str
) -> None:
    path = (
        template.replace("{event_id}", "missing-event")
        .replace("{round_id}", "missing-round")
        .replace("{event_speaker_id}", "missing-speaker")
    )
    monkeypatch.setattr(auth_http, "database", lambda _request: DocumentLookup())

    async def organizer_context(_request):
        return AuthenticatedContext(
            actor=Actor("organizer-user", active_persona=Persona.ORGANIZER),
            session_id="organizer-session",
        )

    monkeypatch.setattr(auth_http, "authenticate_request", organizer_context)
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        if path.startswith("/admin/"):
            client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 404
    assert "data-auth-shell" not in response.text

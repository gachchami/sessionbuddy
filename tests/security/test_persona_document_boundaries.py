from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app
from sessionbuddy.platform.auth import http as auth_http
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Persona


async def local_app(scope, receive, send):
    scope["env"] = SimpleNamespace(APP_ENV="local")
    await app(scope, receive, send)


@pytest.mark.parametrize(
    ("path", "portal_marker", "sign_in_marker"),
    [
        ("/speaker", 'class="app-body speaker-portal-page"', "Speaker sign in"),
        ("/reviews", "SessionBuddy · Reviews", "data-auth-shell"),
    ],
)
async def test_organizer_session_cannot_load_persona_documents_or_their_shells(
    monkeypatch, path: str, portal_marker: str, sign_in_marker: str
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

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in response.text.lower()
    assert "<title>Access denied" in response.text
    assert "This page is not available for your active role." in response.text
    assert "Open active workspace" in response.text
    assert 'href="/"' in response.text
    assert 'href="/admin"' not in response.text
    assert '"code":"forbidden"' not in response.text
    assert portal_marker not in response.text
    assert sign_in_marker not in response.text


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
    "/admin/events/event-1/speakers",
    "/admin/events/event-1/speakers/speaker-1",
    "/admin/events/event-1/messages",
    "/admin/speakers",
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

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "This page is not available for your active role." in response.text
    assert "data-auth-shell" not in response.text


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
    async with AsyncClient(
        transport=ASGITransport(app=local_app), base_url="http://test"
    ) as client:
        client.cookies.set("sessionbuddy-local", "organizer-session-cookie")
        response = await client.get(path, headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in response.text.lower()

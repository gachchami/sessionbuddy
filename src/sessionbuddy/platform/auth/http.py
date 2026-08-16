"""FastAPI integration for opaque sessions, CSRF, and centralized RBAC."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from sessionbuddy.platform.authorization import Permission, Persona, ResourceContext, authorize
from sessionbuddy.platform.authorization.types import Actor
from sessionbuddy.platform.db.d1 import row_mapping
from sessionbuddy.platform.db.types import utc_now_ms

from .d1 import D1AuthorizationFacts, D1SessionStore
from .request_guard import guard_cookie_mutation
from .service import authenticate_session


@dataclass(frozen=True, slots=True)
class AuthenticatedContext:
    actor: Actor
    session_id: str


@dataclass(frozen=True, slots=True)
class EventDocumentScope:
    organization_id: str
    event_id: str


NON_DISCLOSING_DENIAL_REASONS = frozenset(
    {
        "resource_not_found",
        "tenant_membership_required",
        "ownership_required",
        "assignment_required",
        "resource_access_required",
    }
)


def authorization_denial_status(reason: str) -> int:
    """Map record-scope denials to the same response as an absent resource."""
    return 404 if reason in NON_DISCLOSING_DENIAL_REASONS else 403


def environment(request: Request):
    return request.scope.get("env")


def database(request: Request):
    db = getattr(environment(request), "DB", None)
    if db is None:
        raise HTTPException(status_code=503)
    return db


def secret(request: Request, name: str) -> bytes:
    value = getattr(environment(request), name, "")
    encoded = str(value).encode()
    if len(encoded) < 32:
        raise HTTPException(status_code=503)
    return encoded


def allowed_origins(request: Request) -> frozenset[str]:
    raw = str(getattr(environment(request), "ALLOWED_ORIGINS", ""))
    configured = {value.strip() for value in raw.split(",") if value.strip()}
    # The public URL is the origin used by links we issue. Treat it as a
    # first-class configured origin so temporary preview/tunnel URLs cannot
    # send speakers to a page whose authenticated forms are then rejected.
    public_base = str(getattr(environment(request), "PUBLIC_BASE_URL", "")).strip()
    parts = urlsplit(public_base)
    if parts.scheme in {"http", "https"} and parts.netloc:
        configured.add(f"{parts.scheme}://{parts.netloc}")
    return frozenset(configured)


def browser_request_origin(request: Request) -> str | None:
    """Return the browser origin, falling back to an allow-listable Referer.

    Some HTTPS reverse proxies omit ``Origin`` on ordinary form posts.  A
    present Origin remains authoritative; the Referer fallback is used only
    when Origin is absent and is reduced to its scheme and authority.
    """
    origin = request.headers.get("origin")
    if origin is not None:
        return origin
    referer = request.headers.get("referer")
    if not referer:
        return None
    parts = urlsplit(referer)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def browser_request_is_same_origin(request: Request) -> bool:
    """Validate browser provenance across direct and HTTPS-proxied requests."""
    candidate = browser_request_origin(request)
    if candidate is not None:
        return candidate in allowed_origins(request)
    # Fetch Metadata headers cannot be set by browser JavaScript. Cloudflare
    # quick tunnels may remove Origin and Referer, but preserve this signal.
    return request.headers.get("sec-fetch-site", "").strip().lower() == "same-origin"


def session_cookie_value(request: Request) -> str | None:
    """Read only the cookie name valid for this runtime environment."""
    app_env = str(getattr(environment(request), "APP_ENV", "production")).strip().lower()
    if app_env == "local":
        return request.cookies.get("sessionbuddy-local")
    return request.cookies.get("__Host-session")


async def require_permission(
    request: Request,
    permission: Permission,
    context: ResourceContext,
    *,
    mutation: bool,
    mutation_media_types: frozenset[str] = frozenset({"application/json"}),
) -> AuthenticatedContext:
    authenticated = await authenticate_request(request)
    decision = authorize(authenticated.actor, permission, context)
    if not decision.allowed:
        raise HTTPException(status_code=authorization_denial_status(decision.reason))
    if mutation:
        guard_mutation(request, authenticated.session_id, mutation_media_types)
    return authenticated


async def authenticate_request(request: Request) -> AuthenticatedContext:
    cached = getattr(request.state, "authenticated_context", None)
    if isinstance(cached, AuthenticatedContext):
        return cached
    cookie = session_cookie_value(request)
    db = database(request)
    result = await authenticate_session(
        cookie_value=cookie,
        signing_secret=secret(request, "SESSION_HMAC_KEY"),
        session_store=D1SessionStore(db),
        authorization_facts=D1AuthorizationFacts(db),
        now_ms=utc_now_ms(),
    )
    if not result.authenticated or result.actor is None or result.session_id is None:
        raise HTTPException(status_code=401)
    authenticated = AuthenticatedContext(result.actor, result.session_id)
    request.state.authenticated_context = authenticated
    return authenticated


async def require_document_persona(request: Request, persona: Persona) -> None:
    """Reject an authenticated session using the wrong active persona.

    Anonymous visitors may still receive a portal shell that presents its
    sign-in state. Protected API calls remain independently authenticated.
    """
    if not session_cookie_value(request):
        return
    try:
        authenticated = await authenticate_request(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return
        raise
    if authenticated.actor.active_persona is not persona:
        raise HTTPException(status_code=403)


async def _require_document_scope(
    request: Request,
    resource_id: str | None,
    query: str,
    permission: Permission = Permission.EVENT_MANAGE,
) -> EventDocumentScope | None:
    """Resolve a document's event scope and apply its non-disclosing access gate."""
    if resource_id is None or not session_cookie_value(request):
        return None
    try:
        authenticated = await authenticate_request(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return
        raise
    row = row_mapping(
        await database(request).prepare(query).bind(resource_id).first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    # The document and its critical API must use the same permission. A denial
    # here is record-scope, so it is always 404 rather than depending on the
    # policy engine's internal reason vocabulary.
    decision = authorize(
        authenticated.actor,
        permission,
        ResourceContext(str(row["organization_id"]), str(row["event_id"])),
    )
    if not decision.allowed:
        raise HTTPException(status_code=404)
    scope = EventDocumentScope(str(row["organization_id"]), str(row["event_id"]))
    request.state.document_scope = "event"
    request.state.document_event_id = scope.event_id
    return scope


async def require_document_event(
    request: Request,
    event_id: str | None,
    permission: Permission = Permission.EVENT_MANAGE,
    *,
    include_archived: bool = False,
) -> EventDocumentScope | None:
    """Refuse an event-scoped page whose event the caller cannot open.

    Document routes are served before the page makes a single API call, and
    the app shell derives the event frame from the URL alone. Anonymous
    visitors retain the sign-in shell; authenticated denials are deliberately
    indistinguishable from an unknown event.
    """
    query = (
        """SELECT organization_id,id AS event_id FROM events
           WHERE id=?1 LIMIT 1"""
        if include_archived
        else """SELECT organization_id,id AS event_id FROM events
                WHERE id=?1 AND status!='archived' LIMIT 1"""
    )
    return await _require_document_scope(
        request,
        event_id,
        query,
        permission,
    )


async def require_document_event_speaker(
    request: Request,
    scope: EventDocumentScope | None,
    event_speaker_id: str | None,
) -> None:
    """Require one speaker-participation record within an event document.

    Pending invitation ids deliberately do not qualify. They appear in the roster's
    union result, but have no detail document or speaker-participation mutations.
    Withdrawn speakers do qualify because this document owns their restore action.
    """
    if scope is None or event_speaker_id is None:
        return
    request.state.document_scope = "event_speaker"
    request.state.document_event_id = scope.event_id
    row = row_mapping(
        await database(request)
        .prepare(
            """SELECT id FROM event_speakers
               WHERE id=?1 AND organization_id=?2 AND event_id=?3 LIMIT 1"""
        )
        .bind(event_speaker_id, scope.organization_id, scope.event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)


async def require_public_document_event(request: Request, event_id: str) -> None:
    """Return a public event document only for an active public event."""
    row = row_mapping(
        await database(request)
        .prepare("SELECT id FROM events WHERE id=?1 AND status='active' LIMIT 1")
        .bind(event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)


async def require_document_round(request: Request, round_id: str | None) -> None:
    """Refuse an evaluation-round page whose parent event cannot be opened.

    Round document URLs do not carry an event id, so resolve the round's tenant
    and event before rendering the application shell. As with event documents,
    anonymous requests retain the sign-in shell and authenticated denials are
    deliberately indistinguishable from an unknown round.
    """
    await _require_document_scope(
        request,
        round_id,
        "SELECT organization_id,event_id FROM evaluation_rounds WHERE id=?1 LIMIT 1",
    )


def guard_mutation(
    request: Request,
    session_id: str,
    allowed_media_types: frozenset[str] = frozenset({"application/json"}),
) -> None:
    guard = guard_cookie_mutation(
        origin=request.headers.get("origin"),
        referer=request.headers.get("referer"),
        allowed_origins=allowed_origins(request),
        content_type=request.headers.get("content-type"),
        csrf_token=request.headers.get("x-csrf-token"),
        session_id=session_id,
        csrf_secret=secret(request, "CSRF_HMAC_KEY"),
        allowed_media_types=allowed_media_types,
    )
    if not guard.allowed:
        raise HTTPException(status_code=403)

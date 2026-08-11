"""FastAPI integration for opaque sessions, CSRF, and centralized RBAC."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from sessionbuddy.platform.authorization import Permission, ResourceContext, authorize
from sessionbuddy.platform.authorization.types import Actor
from sessionbuddy.platform.db.types import utc_now_ms

from .d1 import D1AuthorizationFacts, D1SessionStore
from .request_guard import guard_cookie_mutation
from .service import authenticate_session


@dataclass(frozen=True, slots=True)
class AuthenticatedContext:
    actor: Actor
    session_id: str


NON_DISCLOSING_DENIAL_REASONS = frozenset(
    {
        "resource_not_found",
        "tenant_membership_required",
        "ownership_required",
        "assignment_required",
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
    cookie = request.cookies.get("__Host-session") or request.cookies.get("sessionbuddy-local")
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

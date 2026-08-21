"""Password-free sign-in for fixed demo personas in gated environments.

The browser sends only a role. The server owns the mapping from role to user
id, so this endpoint cannot be turned into arbitrary account impersonation by
supplying an email or an id. Sessions are created through the same helper as
password sign-in, so cookie policy, CSRF binding, and expiry are identical.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch
from sessionbuddy.platform.db.d1 import row_mapping
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit

from .demo import DEMO_ROLES, configured_personas, demo_login_enabled, persona_for_role
from .http import (
    authenticate_request,
    browser_request_is_same_origin,
    database,
    environment,
)
from .session_factory import (
    confirm_session_established,
    establish_session,
    revoke_session,
    role_compatible_redirect,
    set_session_cookie,
    valid_redirect,
)

demo_router = APIRouter()

# Keyed on role and client address rather than an account, because every
# visitor shares the same three identities. This uses its own binding so demo
# traffic cannot exhaust the credential limiter's budget; the enforced ceiling
# comes from DEMO_AUTH_RATE_LIMITER in wrangler.jsonc, and the values here must
# be kept in step with it.
DEMO_SIGN_IN_RATE_LIMIT = RateLimitPolicy("auth.demo", limit=30, window_seconds=60)
DEMO_RATE_LIMIT_BINDING = "DEMO_AUTH_RATE_LIMITER"


class DemoPersonaView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["organizer", "reviewer", "speaker"]
    label: str
    description: str
    destination: str


class DemoPersonaList(BaseModel):
    data: list[DemoPersonaView]


class DemoSignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["organizer", "reviewer", "speaker"]
    redirect_path: str = Field(default="/", max_length=512)


class DemoSessionCreated(BaseModel):
    authenticated: bool = True
    user_id: str
    csrf_token: str
    redirect_path: str


def _require_demo_environment(request: Request):
    """Absent capability is indistinguishable from an absent route."""
    runtime = environment(request)
    if not demo_login_enabled(runtime):
        raise HTTPException(status_code=404)
    return runtime


def _request_source(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client is not None else "unknown"
    )


@demo_router.get(
    "/api/v1/auth/demo-personas",
    response_model=DemoPersonaList,
    operation_id="listDemoPersonas",
    tags=["authentication"],
)
async def list_demo_personas(request: Request) -> DemoPersonaList:
    runtime = _require_demo_environment(request)
    personas = configured_personas(runtime)
    if not personas:
        raise HTTPException(status_code=404)
    db = database(request)
    # Only advertise a persona whose account is present, active, and still
    # holds the role, so a control can never be offered that would then fail.
    available = []
    for persona in personas:
        row = row_mapping(
            await db.prepare(
                """SELECT u.id FROM users u
                   JOIN user_roles r ON r.user_id=u.id AND r.role=?2 AND r.status='active'
                   WHERE u.id=?1 AND u.status='active' AND u.deleted_at_ms IS NULL
                   LIMIT 1"""
            )
            .bind(persona.user_id, persona.role)
            .first()
        )
        if row is not None:
            available.append(
                DemoPersonaView(
                    role=persona.role,
                    label=persona.label,
                    description=persona.description,
                    destination=persona.destination,
                )
            )
    if not available:
        raise HTTPException(status_code=404)
    return DemoPersonaList(data=available)


@demo_router.post(
    "/api/v1/auth/demo-sign-in",
    response_model=DemoSessionCreated,
    operation_id="createDemoSession",
    tags=["authentication"],
)
async def demo_sign_in(
    body: DemoSignIn, request: Request, response: Response
) -> DemoSessionCreated:
    runtime = _require_demo_environment(request)
    # No session exists yet, so there is no CSRF token to bind against. Browser
    # provenance is therefore checked directly, as the password flow relies on
    # its credential requirement instead.
    if not browser_request_is_same_origin(request):
        raise HTTPException(status_code=403)
    if not valid_redirect(body.redirect_path):
        raise HTTPException(status_code=422)
    if body.role not in DEMO_ROLES:  # pragma: no cover - schema already constrains this
        raise HTTPException(status_code=422)
    await enforce_rate_limit(
        request,
        binding_name=DEMO_RATE_LIMIT_BINDING,
        policy=DEMO_SIGN_IN_RATE_LIMIT,
        subject=f"{body.role}:{_request_source(request)}",
    )
    persona = persona_for_role(runtime, body.role)
    if persona is None:
        raise HTTPException(status_code=404)

    db, now = database(request), utc_now_ms()
    account = row_mapping(
        await db.prepare(
            """SELECT u.id, u.authorization_version
               FROM users u
               JOIN user_roles r ON r.user_id=u.id AND r.role=?2 AND r.status='active'
               WHERE u.id=?1 AND u.status='active' AND u.deleted_at_ms IS NULL
               LIMIT 1"""
        )
        .bind(persona.user_id, persona.role)
        .first()
    )
    if account is None:
        raise HTTPException(status_code=404)

    batch = CommandBatch(db)
    # Signing into a demo persona while already authenticated replaces the
    # browser's session. Retire the previous one rather than leaving a valid
    # row behind that no cookie references any more.
    replaced_session_id: str | None = None
    try:
        existing = await authenticate_request(request)
    except HTTPException:
        existing = None
    if existing is not None:
        replaced_session_id = existing.session_id
        revoke_session(batch, db, existing.session_id, "demo_switch", now)

    established = establish_session(
        batch=batch,
        db=db,
        request=request,
        user_id=str(account["id"]),
        role=persona.role,
        authorization_version=int(account["authorization_version"]),
        now_ms=now,
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=str(account["id"]),
            action="session.demo_sign_in",
            target_type="session",
            target_id=established.session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"role": persona.role, "replaced_session": bool(replaced_session_id)},
        )
    )
    results = await batch.execute()
    # No cookie is issued unless the session and its active role both landed.
    await confirm_session_established(results, established, db)
    set_session_cookie(response, request, established.session_token)
    return DemoSessionCreated(
        user_id=str(account["id"]),
        csrf_token=established.csrf_token,
        redirect_path=role_compatible_redirect(body.redirect_path, persona.role),
    )

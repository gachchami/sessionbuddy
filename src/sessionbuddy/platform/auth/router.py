"""Versioned HTTP session lifecycle endpoints."""

from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch
from sessionbuddy.platform.db.d1 import row_mapping
from sessionbuddy.platform.db.types import new_id, utc_now_ms

from .cookies import sign_session_cookie
from .csrf import issue_csrf_token
from .http import authenticate_request, database, environment, guard_mutation, secret
from .tokens import generate_token, hash_token

session_router = APIRouter()


class SessionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[True] = True
    user_id: str
    session_id: str
    csrf_token: str


def _cookie_settings(request: Request) -> tuple[str, bool]:
    deployed = getattr(environment(request), "APP_ENV", "local") != "local"
    return ("__Host-session", True) if deployed else ("sessionbuddy-local", False)


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    name, secure = _cookie_settings(request)
    response.set_cookie(
        key=name,
        value=sign_session_cookie(token, secret(request, "SESSION_HMAC_KEY")),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


@session_router.get(
    "/api/v1/session",
    response_model=SessionView,
    operation_id="getCurrentSession",
    tags=["authentication"],
)
async def current_session(request: Request) -> SessionView:
    authenticated = await authenticate_request(request)
    return SessionView(
        user_id=authenticated.actor.user_id,
        session_id=authenticated.session_id,
        csrf_token=issue_csrf_token(authenticated.session_id, secret(request, "CSRF_HMAC_KEY")),
    )


@session_router.post(
    "/api/v1/session/refresh",
    response_model=SessionView,
    operation_id="refreshCurrentSession",
    tags=["authentication"],
)
async def refresh_session(request: Request, response: Response) -> SessionView:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    db = database(request)
    previous = row_mapping(
        await db.prepare(
            """SELECT user_id, authorization_version, absolute_expires_at_ms
               FROM sessions WHERE id = ?1 AND revoked_at_ms IS NULL LIMIT 1"""
        )
        .bind(authenticated.session_id)
        .first()
    )
    if previous is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401)
    now = utc_now_ms()
    session_id = new_id()
    token = generate_token()
    csrf_token = issue_csrf_token(session_id, secret(request, "CSRF_HMAC_KEY"))
    absolute_expiry = int(previous["absolute_expires_at_ms"])
    idle_expiry = min(now + 12 * 60 * 60 * 1000, absolute_expiry)
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE sessions SET revoked_at_ms = ?1, revoke_reason = 'rotated'
               WHERE id = ?2 AND revoked_at_ms IS NULL"""
        ).bind(now, authenticated.session_id)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO sessions
               (id, user_id, token_hash, csrf_secret_hash, authorization_version,
                created_at_ms, last_seen_at_ms, idle_expires_at_ms, absolute_expires_at_ms,
                rotated_from_session_id)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6, ?7, ?8, ?9)"""
        ).bind(
            session_id,
            previous["user_id"],
            hash_token(token),
            hash_token(csrf_token),
            previous["authorization_version"],
            now,
            idle_expiry,
            absolute_expiry,
            authenticated.session_id,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="session.rotate",
            target_type="session",
            target_id=session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"rotation": 1},
        )
    )
    await batch.execute()
    _set_session_cookie(response, request, token)
    return SessionView(
        user_id=authenticated.actor.user_id,
        session_id=session_id,
        csrf_token=csrf_token,
    )


@session_router.post(
    "/api/v1/session/logout",
    status_code=204,
    operation_id="logoutCurrentSession",
    tags=["authentication"],
)
async def logout_session(request: Request, response: Response) -> None:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    db = database(request)
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE sessions SET revoked_at_ms = ?1, revoke_reason = 'logout'
               WHERE id = ?2 AND revoked_at_ms IS NULL"""
        ).bind(now, authenticated.session_id)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="session.logout",
            target_type="session",
            target_id=authenticated.session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
        )
    )
    await batch.execute()
    name, secure = _cookie_settings(request)
    response.delete_cookie(name, path="/", secure=secure, httponly=True, samesite="lax")

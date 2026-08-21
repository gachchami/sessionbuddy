"""One shared implementation of browser session establishment.

Password sign-in, magic-link redemption, and demo persona sign-in all need the
same session semantics: an opaque token, a bound CSRF secret, the same idle and
absolute expiries, the same cookie policy, and an active-role row that matches
the role the caller actually authenticated into.  Keeping one implementation
here means a new entry point cannot accidentally receive weaker session
security than the flows that already exist.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from fastapi import HTTPException, Request, Response

from sessionbuddy.platform.db.commands import CommandBatch
from sessionbuddy.platform.db.types import new_id

from .cookies import sign_session_cookie
from .csrf import issue_csrf_token
from .http import secret
from .tokens import generate_token, hash_token

IDLE_SESSION_MS = 12 * 60 * 60 * 1000
ABSOLUTE_SESSION_MS = 30 * 24 * 60 * 60 * 1000
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60

ROLE_DESTINATIONS = {
    "organizer": "/admin",
    "reviewer": "/reviews",
    "speaker": "/speaker",
}


def valid_redirect(value: str) -> bool:
    return value.startswith("/") and not value.startswith("//") and "\\" not in value


def role_destination(role: str | None) -> str:
    destination = ROLE_DESTINATIONS.get(role or "")
    if destination is None:
        raise HTTPException(status_code=403)
    return destination


def role_compatible_redirect(redirect_path: str, role: str) -> str:
    """Keep role-scoped workspaces from leaking across sign-in personas."""
    requested_role = next(
        (
            candidate
            for candidate, prefix in ROLE_DESTINATIONS.items()
            if redirect_path == prefix or redirect_path.startswith(f"{prefix}/")
        ),
        None,
    )
    if requested_role is not None and requested_role != role:
        return role_destination(role)
    return role_destination(role) if redirect_path == "/" else redirect_path


def deployed_environment(request: Request) -> bool:
    return getattr(request.scope.get("env"), "APP_ENV", "production") != "local"


def session_cookie_name(request: Request) -> str:
    return "__Host-session" if deployed_environment(request) else "sessionbuddy-local"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    """Apply the one cookie policy every authenticated flow shares."""
    deployed = deployed_environment(request)
    response.set_cookie(
        session_cookie_name(request),
        sign_session_cookie(token, secret(request, "SESSION_HMAC_KEY")),
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=deployed,
        samesite="lax",
        path="/",
    )


@dataclass(frozen=True, slots=True)
class EstablishedSession:
    """A session queued for creation, plus where to find its result."""

    session_id: str
    session_token: str
    csrf_token: str
    statement_index: int


def establish_session(
    *,
    batch: CommandBatch,
    db,
    request: Request,
    user_id: str,
    role: str,
    authorization_version: int,
    now_ms: int,
) -> EstablishedSession:
    """Queue the inserts for one new session and return its identifiers.

    The caller owns the batch so that session creation stays in the same atomic
    unit as the credential bookkeeping and audit record for its own flow.

    Both inserts select from ``user_roles``, so a session and its active role
    are created together or not at all. Without that, a role revoked between
    the authorization read and this write would leave a valid cookie pointing
    at a session with no persona: authenticated, but unable to do anything.
    The caller must pass the result of ``batch.execute()`` to
    ``confirm_session_established`` before issuing a cookie.
    """
    session_id, session_token = new_id(), generate_token()
    csrf_token = issue_csrf_token(session_id, secret(request, "CSRF_HMAC_KEY"))
    statement_index = batch.statement_count
    batch.add_statement(
        db.prepare(
            """INSERT INTO sessions
               (id,user_id,token_hash,csrf_secret_hash,authorization_version,created_at_ms,
                last_seen_at_ms,idle_expires_at_ms,absolute_expires_at_ms)
               SELECT ?1,?2,?3,?4,?5,?6,?6,?7,?8 FROM user_roles
               WHERE user_id=?2 AND role=?9 AND status='active'"""
        ).bind(
            session_id,
            user_id,
            hash_token(session_token),
            hash_token(csrf_token),
            authorization_version,
            now_ms,
            now_ms + IDLE_SESSION_MS,
            now_ms + ABSOLUTE_SESSION_MS,
            role,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO session_active_roles(session_id,user_id,role,selected_at_ms)
               SELECT ?1,?2,?3,?4 FROM user_roles
               WHERE user_id=?2 AND role=?3 AND status='active'"""
        ).bind(session_id, user_id, role, now_ms)
    )
    return EstablishedSession(session_id, session_token, csrf_token, statement_index)


def _changes_at(results: object, index: int) -> int | None:
    """Read one statement's affected-row count from a D1 batch result."""
    if not isinstance(results, Sequence) or isinstance(results, str | bytes):
        return None
    try:
        entry = results[index]
    except (IndexError, KeyError, TypeError):
        return None
    meta = entry.get("meta") if isinstance(entry, Mapping) else None
    changes = meta.get("changes") if isinstance(meta, Mapping) else None
    return changes if isinstance(changes, int) else None


async def confirm_session_established(
    results: object, session: EstablishedSession, db
) -> None:
    """Refuse to issue a cookie unless the session row actually exists.

    The conditional insert makes the write atomic; this makes the *response*
    honest about it. Row counts are preferred because they cost nothing, with a
    direct read as a fallback if the provider's result shape ever changes.
    """
    changes = _changes_at(results, session.statement_index)
    if changes is None:
        row = await db.prepare("SELECT id FROM sessions WHERE id=?1 LIMIT 1").bind(
            session.session_id
        ).first()
        if row is not None:
            return
    elif changes >= 1:
        return
    # The account lost the requested role between authorization and write.
    raise HTTPException(status_code=409)


def revoke_session(batch: CommandBatch, db, session_id: str, reason: str, now_ms: int) -> None:
    """Retire a session that is being replaced rather than leaving it live."""
    batch.add_statement(
        db.prepare(
            """UPDATE sessions SET revoked_at_ms=?1, revoke_reason=?2
               WHERE id=?3 AND revoked_at_ms IS NULL"""
        ).bind(now_ms, reason, session_id)
    )

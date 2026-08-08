"""Authentication pipeline joining cookies, live sessions, and authorization facts."""

from dataclasses import dataclass
from typing import Literal

from sessionbuddy.platform.authorization.protocols import AuthorizationFacts
from sessionbuddy.platform.authorization.types import Actor

from .cookies import verify_session_cookie
from .protocols import SessionStore
from .sessions import validate_session
from .tokens import hash_token


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    actor: Actor | None
    session_id: str | None
    reason: Literal[
        "authenticated",
        "cookie_invalid",
        "session_missing",
        "session_inactive",
        "actor_missing",
        "identity_mismatch",
    ]

    @property
    def authenticated(self) -> bool:
        return self.actor is not None


async def authenticate_session(
    *,
    cookie_value: str | None,
    signing_secret: bytes,
    session_store: SessionStore,
    authorization_facts: AuthorizationFacts,
    now_ms: int,
) -> AuthenticationResult:
    token = verify_session_cookie(cookie_value or "", signing_secret)
    if token is None:
        return AuthenticationResult(None, None, "cookie_invalid")
    record = await session_store.resolve(hash_token(token), now_ms)
    if record is None:
        return AuthenticationResult(None, None, "session_missing")
    if not validate_session(record, now_ms).active:
        return AuthenticationResult(None, record.id, "session_inactive")
    actor = await authorization_facts.actor_for_session(record.id)
    if actor is None:
        return AuthenticationResult(None, record.id, "actor_missing")
    if actor.user_id != record.user_id:
        return AuthenticationResult(None, record.id, "identity_mismatch")
    return AuthenticationResult(actor, record.id, "authenticated")

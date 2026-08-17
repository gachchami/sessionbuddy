from sessionbuddy.platform.auth.cookies import sign_session_cookie
from sessionbuddy.platform.auth.service import authenticate_session
from sessionbuddy.platform.auth.sessions import SessionRecord
from sessionbuddy.platform.auth.tokens import hash_token
from sessionbuddy.platform.authorization.policy import authorize
from sessionbuddy.platform.authorization.types import Actor, Permission, Persona, ResourceContext


class Sessions:
    def __init__(self, token: str, record: SessionRecord | None) -> None:
        self.token_hash = hash_token(token)
        self.record = record

    async def resolve(self, token_hash: bytes, now_ms: int) -> SessionRecord | None:
        return self.record if token_hash == self.token_hash else None

    async def revoke(self, session_id: str, reason: str, now_ms: int) -> bool:
        return False


class Facts:
    def __init__(self, actor: Actor | None) -> None:
        self.actor = actor

    async def actor_for_session(self, session_id: str) -> Actor | None:
        return self.actor

    async def resource_context(
        self, organization_id: str, event_id: str | None, resource_id: str | None
    ) -> ResourceContext:
        return ResourceContext(organization_id, event_id)


def live_session(**changes) -> SessionRecord:
    values = {
        "id": "session-a",
        "user_id": "user-a",
        "user_status": "active",
        "authorization_version": 1,
        "current_authorization_version": 1,
        "idle_expires_at_ms": 2_000,
        "absolute_expires_at_ms": 3_000,
        "revoked_at_ms": None,
    }
    values.update(changes)
    return SessionRecord(**values)


async def test_verified_session_flows_into_tenant_authorization() -> None:
    secret = b"s" * 32
    token = "opaque-token"  # noqa: S105 - synthetic test fixture
    actor = Actor(
        "user-a",
        active_persona=Persona.ORGANIZER,
        owned_resource_ids=frozenset({"org-a"}),
    )
    result = await authenticate_session(
        cookie_value=sign_session_cookie(token, secret),
        signing_secret=secret,
        session_store=Sessions(token, live_session()),
        authorization_facts=Facts(actor),
        now_ms=1_000,
    )

    assert result.authenticated
    assert authorize(
        result.actor, Permission.EVENT_MANAGE, ResourceContext("org-a", "event-a")
    ).allowed
    assert not authorize(
        result.actor, Permission.EVENT_MANAGE, ResourceContext("org-b", "event-b")
    ).allowed


async def test_tampered_cookie_and_revoked_session_fail_before_authorization() -> None:
    secret = b"s" * 32
    token = "opaque-token"  # noqa: S105 - synthetic test fixture
    actor = Actor(
        "user-a",
        active_persona=Persona.ORGANIZER,
        owned_resource_ids=frozenset({"event-a"}),
    )
    common = {
        "signing_secret": secret,
        "authorization_facts": Facts(actor),
        "now_ms": 1_000,
    }
    tampered = await authenticate_session(
        cookie_value=sign_session_cookie(token, secret) + "x",
        session_store=Sessions(token, live_session()),
        **common,
    )
    revoked = await authenticate_session(
        cookie_value=sign_session_cookie(token, secret),
        session_store=Sessions(token, live_session(revoked_at_ms=900)),
        **common,
    )

    assert (tampered.authenticated, tampered.reason) == (False, "cookie_invalid")
    assert (revoked.authenticated, revoked.reason) == (False, "session_inactive")


async def test_session_identity_cannot_be_rebound_by_facts_adapter() -> None:
    secret = b"s" * 32
    token = "opaque-token"  # noqa: S105 - synthetic test fixture
    result = await authenticate_session(
        cookie_value=sign_session_cookie(token, secret),
        signing_secret=secret,
        session_store=Sessions(token, live_session()),
        authorization_facts=Facts(Actor("attacker")),
        now_ms=1_000,
    )

    assert (result.authenticated, result.reason) == (False, "identity_mismatch")

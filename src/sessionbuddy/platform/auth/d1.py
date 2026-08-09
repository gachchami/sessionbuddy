"""D1 adapters for live sessions and authorization facts."""

from collections.abc import Sequence

from sessionbuddy.platform.authorization.types import Actor, ResourceContext, Role
from sessionbuddy.platform.db.d1 import result_rows, to_python

from .sessions import SessionRecord


class D1SessionStore:
    def __init__(self, db) -> None:
        self._db = db

    async def resolve(self, token_hash: bytes, now_ms: int) -> SessionRecord | None:
        selected = self._db.prepare(
            """SELECT s.id, s.user_id, u.status AS user_status,
                          s.authorization_version, u.authorization_version
                            AS current_authorization_version,
                          s.idle_expires_at_ms, s.absolute_expires_at_ms, s.revoked_at_ms
                   FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash = ?1 LIMIT 1"""
        ).bind(token_hash)
        # Extend a genuinely active session at most once every five minutes.
        # The SELECT and conditional touch share one D1 batch/round trip, so
        # correct sliding-idle semantics do not add another network waterfall.
        touched = self._db.prepare(
            """UPDATE sessions
               SET last_seen_at_ms=?1,
                   idle_expires_at_ms=MIN(?2,absolute_expires_at_ms)
               WHERE token_hash=?3 AND revoked_at_ms IS NULL
                 AND idle_expires_at_ms>?1 AND absolute_expires_at_ms>?1
                 AND last_seen_at_ms<=?4
                 AND EXISTS (
                   SELECT 1 FROM users u
                   WHERE u.id=sessions.user_id AND u.status='active'
                     AND u.authorization_version=sessions.authorization_version
                 )"""
        ).bind(now_ms, now_ms + 12 * 60 * 60 * 1000, token_hash, now_ms - 5 * 60 * 1000)
        batch = to_python(await self._db.batch([selected, touched]))
        first_result = batch[0] if isinstance(batch, Sequence) and batch else None
        rows = result_rows(first_result) if first_result is not None else []
        row = rows[0] if rows else None
        return SessionRecord(**row) if row is not None else None

    async def revoke(self, session_id: str, reason: str, now_ms: int) -> bool:
        result = (
            await self._db.prepare(
                """UPDATE sessions SET revoked_at_ms = ?1, revoke_reason = ?2
               WHERE id = ?3 AND revoked_at_ms IS NULL"""
            )
            .bind(now_ms, reason, session_id)
            .run()
        )
        converted = getattr(result, "to_py", lambda: result)()
        return bool(converted.get("meta", {}).get("changes", 0))


class D1AuthorizationFacts:
    def __init__(self, db) -> None:
        self._db = db

    async def actor_for_session(self, session_id: str) -> Actor | None:
        # Fetch the principal and every active authorization fact in one D1
        # request.  The previous implementation made three sequential remote
        # reads here; every authenticated page then made another profile read.
        # That waterfall amplified latency and could exhaust a Python Worker
        # request during rapid browser navigation.
        rows = result_rows(
            await self._db.prepare(
                """WITH principal AS (
                     SELECT u.id AS user_id, u.status AS user_status
                     FROM sessions s
                     JOIN users u ON u.id = s.user_id
                     WHERE s.id = ?1
                   ), organization_facts AS (
                     SELECT p.user_id, p.user_status, om.organization_id, om.role
                     FROM principal p
                     JOIN organization_memberships om ON om.user_id = p.user_id
                     WHERE om.status = 'active'
                     LIMIT 100
                   ), event_facts AS (
                     SELECT p.user_id, p.user_status, em.organization_id,
                            em.event_id, em.role
                     FROM principal p
                     JOIN event_memberships em ON em.user_id = p.user_id
                     WHERE em.status = 'active'
                     LIMIT 500
                   )
                   SELECT 'principal' AS fact_type, p.user_id, p.user_status,
                          NULL AS organization_id, NULL AS event_id, NULL AS role
                   FROM principal p
                   UNION ALL
                   SELECT 'organization', o.user_id, o.user_status,
                          o.organization_id, NULL, o.role
                   FROM organization_facts o
                   UNION ALL
                   SELECT 'event', e.user_id, e.user_status,
                          e.organization_id, e.event_id, e.role
                   FROM event_facts e"""
            )
            .bind(session_id)
            .all()
        )
        if not rows:
            return None
        principal = rows[0]
        user_id = str(principal["user_id"])
        organization_roles: dict[str, set[Role]] = {}
        for row in rows:
            if row["fact_type"] != "organization":
                continue
            # `member` establishes organization membership for FK and account
            # lifecycle purposes but intentionally grants no application role.
            if str(row["role"]) == Role.ORGANIZATION_ADMIN:
                organization_roles.setdefault(str(row["organization_id"]), set()).add(
                    Role.ORGANIZATION_ADMIN
                )
        event_roles: dict[tuple[str, str], set[Role]] = {}
        for row in rows:
            if row["fact_type"] != "event":
                continue
            key = (str(row["organization_id"]), str(row["event_id"]))
            event_roles.setdefault(key, set()).add(Role(str(row["role"])))
        return Actor(
            user_id=user_id,
            active=principal["user_status"] == "active",
            organization_roles={key: frozenset(value) for key, value in organization_roles.items()},
            event_roles={key: frozenset(value) for key, value in event_roles.items()},
        )

    async def resource_context(
        self, organization_id: str, event_id: str | None, resource_id: str | None
    ) -> ResourceContext:
        return ResourceContext(organization_id=organization_id, event_id=event_id)

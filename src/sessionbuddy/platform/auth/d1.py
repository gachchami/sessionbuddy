"""D1 adapters for live sessions and authorization facts."""

from sessionbuddy.platform.authorization.types import Actor, ResourceContext, Role
from sessionbuddy.platform.db.d1 import result_rows, row_mapping

from .sessions import SessionRecord


class D1SessionStore:
    def __init__(self, db) -> None:
        self._db = db

    async def resolve(self, token_hash: bytes, now_ms: int) -> SessionRecord | None:
        row = row_mapping(
            await self._db.prepare(
                """SELECT s.id, s.user_id, u.status AS user_status,
                          s.authorization_version, u.authorization_version
                            AS current_authorization_version,
                          s.idle_expires_at_ms, s.absolute_expires_at_ms, s.revoked_at_ms
                   FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash = ?1 LIMIT 1"""
            )
            .bind(token_hash)
            .first()
        )
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
        user = row_mapping(
            await self._db.prepare(
                """SELECT u.id, u.status FROM sessions s
                   JOIN users u ON u.id = s.user_id WHERE s.id = ?1 LIMIT 1"""
            )
            .bind(session_id)
            .first()
        )
        if user is None:
            return None
        org_rows = result_rows(
            await self._db.prepare(
                """SELECT organization_id, role FROM organization_memberships
                   WHERE user_id = ?1 AND status = 'active' LIMIT 100"""
            )
            .bind(user["id"])
            .all()
        )
        event_rows = result_rows(
            await self._db.prepare(
                """SELECT organization_id, event_id, role FROM event_memberships
                   WHERE user_id = ?1 AND status = 'active' LIMIT 500"""
            )
            .bind(user["id"])
            .all()
        )
        organization_roles: dict[str, set[Role]] = {}
        for row in org_rows:
            # `member` establishes organization membership for FK and account
            # lifecycle purposes but intentionally grants no application role.
            if str(row["role"]) == Role.ORGANIZATION_ADMIN:
                organization_roles.setdefault(str(row["organization_id"]), set()).add(
                    Role.ORGANIZATION_ADMIN
                )
        event_roles: dict[tuple[str, str], set[Role]] = {}
        for row in event_rows:
            key = (str(row["organization_id"]), str(row["event_id"]))
            event_roles.setdefault(key, set()).add(Role(str(row["role"])))
        return Actor(
            user_id=str(user["id"]),
            active=user["status"] == "active",
            organization_roles={key: frozenset(value) for key, value in organization_roles.items()},
            event_roles={key: frozenset(value) for key, value in event_roles.items()},
        )

    async def resource_context(
        self, organization_id: str, event_id: str | None, resource_id: str | None
    ) -> ResourceContext:
        return ResourceContext(organization_id=organization_id, event_id=event_id)

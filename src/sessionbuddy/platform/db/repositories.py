"""Tenant-scoped foundation repositories."""

from .d1 import D1Database, result_rows, row_mapping
from .scopes import EventScope, OrganizationScope


class OrganizationRepository:
    def __init__(self, db: D1Database, scope: OrganizationScope) -> None:
        self.__db = db
        self.__scope = scope

    async def get_organization(self) -> dict[str, object] | None:
        row = (
            await self.__db.prepare(
                """SELECT id, name, status, version, updated_at_ms FROM organizations
               WHERE id = ?1"""
            )
            .bind(str(self.__scope.organization_id))
            .first()
        )
        return row_mapping(row)

    async def list_events(self, *, limit: int = 25) -> list[dict[str, object]]:
        _bounded_page_size(limit)
        result = (
            await self.__db.prepare(
                """SELECT id, name, status, starts_at_ms, ends_at_ms, time_zone, version,
                      updated_at_ms
               FROM events WHERE organization_id = ?1
               ORDER BY updated_at_ms DESC, id DESC LIMIT ?2"""
            )
            .bind(str(self.__scope.organization_id), limit)
            .all()
        )
        return result_rows(result)


class EventRepository:
    def __init__(self, db: D1Database, scope: EventScope) -> None:
        self.__db = db
        self.__scope = scope

    async def get_event(self) -> dict[str, object] | None:
        row = (
            await self.__db.prepare(
                """SELECT id, name, status, starts_at_ms, ends_at_ms, time_zone,
                      delivery_mode, version, updated_at_ms
               FROM events WHERE organization_id = ?1 AND id = ?2"""
            )
            .bind(str(self.__scope.organization_id), str(self.__scope.event_id))
            .first()
        )
        return row_mapping(row)

def _bounded_page_size(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise ValueError("page size must be between 1 and 100")

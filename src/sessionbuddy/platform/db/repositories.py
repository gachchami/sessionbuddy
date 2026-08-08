"""Tenant-scoped foundation repositories."""

from .d1 import D1Database, result_rows, row_mapping
from .scopes import EventScope, OrganizationScope, ProgramScope


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

    async def get_program(self, program_id: str) -> dict[str, object] | None:
        row = (
            await self.__db.prepare(
                """SELECT id, name, status, version, updated_at_ms FROM programs
               WHERE organization_id = ?1 AND event_id = ?2 AND id = ?3"""
            )
            .bind(str(self.__scope.organization_id), str(self.__scope.event_id), program_id)
            .first()
        )
        return row_mapping(row)

    async def list_programs(
        self,
        *,
        limit: int = 25,
        before_updated_at_ms: int | None = None,
        before_id: str | None = None,
    ) -> list[dict[str, object]]:
        _bounded_page_size(limit)
        if (before_updated_at_ms is None) != (before_id is None):
            raise ValueError("cursor values must be provided together")
        query = """SELECT id, name, status, version, updated_at_ms FROM programs
                   WHERE organization_id = ?1 AND event_id = ?2"""
        values: list[object] = [
            str(self.__scope.organization_id),
            str(self.__scope.event_id),
        ]
        if before_updated_at_ms is not None:
            query += " AND (updated_at_ms < ?3 OR (updated_at_ms = ?3 AND id < ?4))"
            values.extend((before_updated_at_ms, before_id))
        query += f" ORDER BY updated_at_ms DESC, id DESC LIMIT ?{len(values) + 1}"
        values.append(limit)
        result = await self.__db.prepare(query).bind(*values).all()
        return result_rows(result)


class ProgramRepository:
    def __init__(self, db: D1Database, scope: ProgramScope) -> None:
        self.__event_repository = EventRepository(db, scope)
        self.__scope = scope

    async def get_program(self) -> dict[str, object] | None:
        return await self.__event_repository.get_program(str(self.__scope.program_id))


def _bounded_page_size(limit: int) -> None:
    if not 1 <= limit <= 100:
        raise ValueError("page size must be between 1 and 100")

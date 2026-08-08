from dataclasses import FrozenInstanceError

import pytest

from sessionbuddy.platform.db.commands import (
    AuditEvent,
    CommandBatch,
    IdempotencyRecord,
    OutboxMessage,
)
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows
from sessionbuddy.platform.db.repositories import EventRepository
from sessionbuddy.platform.db.scopes import EventScope
from sessionbuddy.platform.db.types import EventId, OrganizationId, UserId, new_id


class Proxy:
    def __init__(self, value):
        self.value = value

    def to_py(self):
        return self.value


class Statement:
    def __init__(self, db, sql):
        self.db = db
        self.sql = sql
        self.values = ()

    def bind(self, *values):
        self.values = values
        self.db.bound.append(self)
        return self

    async def first(self, column=None):
        return self.db.first_value

    async def all(self):
        return self.db.all_value

    async def run(self):
        return {"success": True}


class Database:
    def __init__(self):
        self.bound = []
        self.first_value = None
        self.all_value = {"results": []}
        self.batch_value = {"success": True}
        self.fail_batch = False

    def prepare(self, sql):
        return Statement(self, sql)

    async def batch(self, statements):
        self.batch_statements = statements
        if self.fail_batch:
            raise RuntimeError("SQL token=secret leaked")
        return self.batch_value


def scope(org="org-a", event="event-a"):
    return EventScope(
        actor_user_id=UserId("actor"),
        organization_id=OrganizationId(org),
        event_id=EventId(event),
        permissions=frozenset({"program:read"}),
    )


def test_scopes_are_frozen():
    authorized = scope()
    with pytest.raises(FrozenInstanceError):
        authorized.event_id = EventId("event-b")


@pytest.mark.asyncio
async def test_event_lookup_binds_every_tenant_boundary():
    db = Database()
    db.first_value = Proxy({"id": "program-1", "private": "not-selected"})
    row = await EventRepository(db, scope()).get_program("program-1")
    statement = db.bound[-1]
    assert "organization_id = ?1 AND event_id = ?2 AND id = ?3" in statement.sql
    assert statement.values == ("org-a", "event-a", "program-1")
    assert row == {"id": "program-1", "private": "not-selected"}


@pytest.mark.asyncio
async def test_cursor_query_is_tenant_scoped_keyset_and_bounded():
    db = Database()
    await EventRepository(db, scope()).list_programs(
        limit=50, before_updated_at_ms=1000, before_id="program-z"
    )
    statement = db.bound[-1]
    assert "organization_id = ?1 AND event_id = ?2" in statement.sql
    assert "updated_at_ms < ?3" in statement.sql
    assert "OFFSET" not in statement.sql
    assert statement.values == ("org-a", "event-a", 1000, "program-z", 50)
    with pytest.raises(ValueError):
        await EventRepository(db, scope()).list_programs(limit=101)
    with pytest.raises(ValueError):
        await EventRepository(db, scope()).list_programs(before_id="program-z")


def test_result_conversion_rejects_unsafe_shapes():
    assert result_rows(Proxy({"results": [Proxy({"id": "a"})]})) == [{"id": "a"}]
    with pytest.raises(PersistenceError):
        result_rows({"results": "not rows"})


@pytest.mark.asyncio
async def test_command_batch_has_required_atomic_shape_and_redacts_provider_error():
    db = Database()
    record = IdempotencyRecord(
        principal_key="actor",
        route_key="POST /api/v1/things",
        idempotency_key="a" * 32,
        request_fingerprint=b"fingerprint",
        expires_at_ms=2000,
        organization_id="org-a",
        event_id="event-a",
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, 1000)
    domain = db.prepare("INSERT INTO domain VALUES (?1)").bind("domain-id")
    batch.add_statement(domain)
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id="actor",
            action="thing.create",
            target_type="thing",
            target_id="domain-id",
            result="succeeded",
            correlation_id="request-1",
            occurred_at_ms=1000,
            organization_id="org-a",
            event_id="event-a",
            metadata={"changed_fields": ["name"]},
        )
    )
    batch.outbox(
        OutboxMessage(
            topic="thing.created",
            aggregate_type="thing",
            aggregate_id="domain-id",
            deduplication_key="thing:domain-id:v1",
            payload={"thing_id": "domain-id"},
            available_at_ms=1000,
            created_at_ms=1000,
            organization_id="org-a",
            event_id="event-a",
        )
    )
    batch.complete_idempotency(
        record, status=201, resource_type="thing", resource_id="domain-id", completed_at_ms=1000
    )
    await batch.execute()
    assert len(db.batch_statements) == 5
    assert [
        "idempotency_records",
        "domain",
        "audit_events",
        "outbox_messages",
        "idempotency_records",
    ] == [
        next(
            name
            for name in ("idempotency_records", "domain", "audit_events", "outbox_messages")
            if name in statement.sql
        )
        for statement in db.batch_statements
    ]
    db.fail_batch = True
    with pytest.raises(PersistenceError, match="database command failed") as error:
        await batch.execute()
    assert "secret" not in str(error.value)


def test_audit_metadata_blocks_sensitive_fields():
    db = Database()
    batch = CommandBatch(db)
    with pytest.raises(ValueError, match="not allow-listed"):
        batch.audit(
            AuditEvent(
                actor_type="user",
                action="auth",
                target_type="session",
                result="succeeded",
                correlation_id="request-1",
                occurred_at_ms=1000,
                metadata={"session_token": "canary"},
            )
        )


def test_new_ids_are_canonical_uuid4():
    from uuid import UUID

    value = new_id()
    parsed = UUID(value)
    assert parsed.version == 4
    assert str(parsed) == value

"""Builders for the atomic idempotency/domain/audit/outbox command shape."""

import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal

from .d1 import D1Database, D1PreparedStatement, execute_batch
from .types import new_id


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    principal_key: str
    route_key: str
    idempotency_key: str
    request_fingerprint: bytes
    expires_at_ms: int
    organization_id: str | None = None
    event_id: str | None = None
    id: str = field(default_factory=new_id)

    @property
    def key_hash(self) -> bytes:
        return sha256(self.idempotency_key.encode()).digest()


@dataclass(frozen=True, slots=True)
class OutboxMessage:
    topic: str
    aggregate_type: str
    aggregate_id: str
    deduplication_key: str
    payload: dict[str, object]
    available_at_ms: int
    created_at_ms: int
    organization_id: str | None = None
    event_id: str | None = None
    payload_version: int = 1
    id: str = field(default_factory=new_id)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    actor_type: Literal["user", "system", "anonymous"]
    action: str
    target_type: str
    result: Literal["succeeded", "denied", "failed"]
    correlation_id: str
    occurred_at_ms: int
    organization_id: str | None = None
    event_id: str | None = None
    actor_user_id: str | None = None
    target_id: str | None = None
    reason_code: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    id: str = field(default_factory=new_id)


class CommandBatch:
    """Creates, then executes, one ordered D1 transaction consistency unit."""

    def __init__(self, db: D1Database) -> None:
        self.__db = db
        self.__statements: list[D1PreparedStatement] = []

    def begin_idempotency(self, record: IdempotencyRecord, created_at_ms: int) -> None:
        self.__statements.append(
            self.__db.prepare(
                """INSERT INTO idempotency_records
               (id, principal_key, organization_id, event_id, route_key,
                idempotency_key_hash, request_fingerprint, state, created_at_ms, expires_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'in_progress', ?8, ?9)"""
            ).bind(
                record.id,
                record.principal_key,
                record.organization_id,
                record.event_id,
                record.route_key,
                record.key_hash,
                record.request_fingerprint,
                created_at_ms,
                record.expires_at_ms,
            )
        )

    def add_statement(self, statement: D1PreparedStatement) -> None:
        self.__statements.append(statement)

    def audit(self, event: AuditEvent) -> None:
        safe = _safe_metadata(event.metadata)
        self.__statements.append(
            self.__db.prepare(
                """INSERT INTO audit_events
               (id, organization_id, event_id, actor_user_id, actor_type, action,
                target_type, target_id, result, reason_code, correlation_id,
                metadata_json, occurred_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)"""
            ).bind(
                event.id,
                event.organization_id,
                event.event_id,
                event.actor_user_id,
                event.actor_type,
                event.action,
                event.target_type,
                event.target_id,
                event.result,
                event.reason_code,
                event.correlation_id,
                _canonical_json(safe),
                event.occurred_at_ms,
            )
        )

    def outbox(self, message: OutboxMessage) -> None:
        self.__statements.append(
            self.__db.prepare(
                """INSERT INTO outbox_messages
               (id, organization_id, event_id, topic, payload_version, aggregate_type,
                aggregate_id, deduplication_key, payload_json, available_at_ms, created_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)"""
            ).bind(
                message.id,
                message.organization_id,
                message.event_id,
                message.topic,
                message.payload_version,
                message.aggregate_type,
                message.aggregate_id,
                message.deduplication_key,
                _canonical_json(message.payload),
                message.available_at_ms,
                message.created_at_ms,
            )
        )

    def complete_idempotency(
        self,
        record: IdempotencyRecord,
        *,
        status: int,
        resource_type: str,
        resource_id: str,
        completed_at_ms: int,
    ) -> None:
        self.__statements.append(
            self.__db.prepare(
                """UPDATE idempotency_records SET state = 'completed', response_status = ?1,
               response_resource_type = ?2, response_resource_id = ?3, completed_at_ms = ?4
               WHERE id = ?5 AND state = 'in_progress'"""
            ).bind(status, resource_type, resource_id, completed_at_ms, record.id)
        )

    async def execute(self) -> object:
        return await execute_batch(self.__db, self.__statements)


_FORBIDDEN_METADATA_FRAGMENTS = (
    "token",
    "cookie",
    "secret",
    "password",
    "authorization",
    "url",
    "email",
    "answer",
)


def _safe_metadata(metadata: dict[str, object]) -> dict[str, object]:
    for key in metadata:
        normalized = key.lower().replace("-", "_")
        if any(fragment in normalized for fragment in _FORBIDDEN_METADATA_FRAGMENTS):
            raise ValueError(f"audit metadata field is not allow-listed: {key}")
    return metadata


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)

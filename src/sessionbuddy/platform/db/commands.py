"""Builders for atomic idempotency, domain, and audit command batches."""

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


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    """A successful CRUD fact recorded in the domain transaction."""

    actor_type: Literal["user", "system", "anonymous"]
    operation: Literal["create", "read", "update", "delete"]
    resource_type: str
    resource_id: str
    occurred_at_ms: int
    actor_id: str | None = None
    organization_id: str | None = None
    event_id: str | None = None
    id: str = field(default_factory=lambda: _public_reference("activity", new_id()))


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
        if event.result == "succeeded" and event.target_id:
            self.activity(
                ActivityRecord(
                    actor_type=event.actor_type,
                    actor_id=event.actor_user_id,
                    operation=_crud_operation(event.action),
                    resource_type=event.target_type,
                    resource_id=event.target_id,
                    organization_id=event.organization_id,
                    event_id=event.event_id,
                    occurred_at_ms=event.occurred_at_ms,
                )
            )

    def activity(self, activity: ActivityRecord) -> None:
        """Append the activity and UNPROCESSED marker to this atomic batch."""
        resource_type = _activity_entity_type(activity.resource_type)
        resource_public_id = _public_reference(resource_type, activity.resource_id)
        actor_public_id = (
            _public_reference("user", activity.actor_id) if activity.actor_id else None
        )
        if activity.actor_id and actor_public_id:
            self.__statements.append(
                self.__db.prepare(
                    """INSERT OR IGNORE INTO activity_entities
                       (public_id,entity_type,internal_id) VALUES(?1,'user',?2)"""
                ).bind(actor_public_id, activity.actor_id)
            )
        self.__statements.append(
            self.__db.prepare(
                """INSERT OR IGNORE INTO activity_entities
                   (public_id,entity_type,internal_id) VALUES(?1,?2,?3)"""
            ).bind(resource_public_id, resource_type, activity.resource_id)
        )
        self.__statements.extend(
            [
                self.__db.prepare(
                    """INSERT INTO activities
                       (id,actor_type,actor_id,operation,resource_type,resource_id,
                        occurred_at_ms)
                       VALUES(?1,?2,?3,?4,?5,?6,?7)"""
                ).bind(
                    activity.id,
                    activity.actor_type,
                    actor_public_id,
                    activity.operation,
                    resource_type,
                    resource_public_id,
                    activity.occurred_at_ms,
                ),
                self.__db.prepare(
                    """INSERT INTO activity_status
                       (activity_id,status,updated_at_ms)
                       VALUES(?1,'UNPROCESSED',?2)"""
                ).bind(activity.id, activity.occurred_at_ms),
                self.__db.prepare(
                    """INSERT INTO activity_routing
                       (activity_id,organization_id,event_id) VALUES(?1,?2,?3)"""
                ).bind(activity.id, activity.organization_id, activity.event_id),
            ]
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


def _crud_operation(action: str) -> Literal["create", "read", "update", "delete"]:
    final = action.rsplit(".", 1)[-1]
    if final in {
        "create", "created", "bootstrap", "invite", "authorize", "upload",
    }:
        return "create"
    if final in {"remove", "revoke", "delete", "unschedule", "withdraw"}:
        return "delete"
    return "update"


def _activity_entity_type(value: str) -> str:
    return {"submission": "proposal", "identity_invitation": "invitation"}.get(
        value, value
    )


def _public_reference(entity_type: str, internal_id: str) -> str:
    prefix = {
        "activity": "A",
        "user": "U",
        "proposal": "P",
        "event": "E",
        "invitation": "I",
        "review": "R",
        "evaluation": "R",
        "session": "S",
    }.get(entity_type, "X")
    number = int.from_bytes(sha256(internal_id.encode()).digest()[:8], "big")
    return f"{prefix}{number}"

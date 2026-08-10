"""Async, tenant-scoped persistence primitives for Cloudflare D1."""

from .commands import AuditEvent, CommandBatch, IdempotencyRecord
from .d1 import D1Database, D1PreparedStatement, execute_batch
from .repositories import EventRepository, OrganizationRepository
from .scopes import EventScope, OrganizationScope

__all__ = [
    "AuditEvent",
    "CommandBatch",
    "D1Database",
    "D1PreparedStatement",
    "EventRepository",
    "EventScope",
    "IdempotencyRecord",
    "OrganizationRepository",
    "OrganizationScope",
    "execute_batch",
]

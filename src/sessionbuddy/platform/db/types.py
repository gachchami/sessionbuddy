"""Opaque identifiers and runtime-safe value helpers."""

from datetime import UTC, datetime
from typing import NewType
from uuid import uuid4

UserId = NewType("UserId", str)
OrganizationId = NewType("OrganizationId", str)
EventId = NewType("EventId", str)


def new_id() -> str:
    """Return an opaque canonical UUIDv4 string."""
    return str(uuid4())


def utc_now_ms() -> int:
    """Return the current UTC Unix epoch in milliseconds."""
    return int(datetime.now(UTC).timestamp() * 1000)

"""Persistence ports used by future authentication services."""

from typing import Protocol

from .sessions import SessionRecord


class ChallengeStore(Protocol):
    async def consume(self, token_hash: bytes, now_ms: int) -> str | None:
        """Atomically consume a live challenge and return its identity reference."""
        ...


class SessionStore(Protocol):
    async def resolve(self, token_hash: bytes, now_ms: int) -> SessionRecord | None: ...

    async def revoke(self, session_id: str, reason: str, now_ms: int) -> bool: ...

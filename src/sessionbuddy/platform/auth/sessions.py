"""Pure live-session validation shared by HTTP dependencies and tests."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class SessionRecord:
    id: str
    user_id: str
    user_status: str
    authorization_version: int
    current_authorization_version: int
    idle_expires_at_ms: int
    absolute_expires_at_ms: int
    revoked_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class SessionDecision:
    active: bool
    reason: Literal[
        "active",
        "revoked",
        "idle_expired",
        "absolute_expired",
        "user_inactive",
        "authorization_stale",
    ]


def validate_session(record: SessionRecord, now_ms: int) -> SessionDecision:
    if record.revoked_at_ms is not None:
        return SessionDecision(False, "revoked")
    if record.absolute_expires_at_ms <= now_ms:
        return SessionDecision(False, "absolute_expired")
    if record.idle_expires_at_ms <= now_ms:
        return SessionDecision(False, "idle_expired")
    if record.user_status != "active":
        return SessionDecision(False, "user_inactive")
    if record.authorization_version != record.current_authorization_version:
        return SessionDecision(False, "authorization_stale")
    return SessionDecision(True, "active")

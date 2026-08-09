"""Authorization-derived scopes; these are never populated from request bodies."""

from dataclasses import dataclass

from .types import EventId, OrganizationId, UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class OrganizationScope:
    actor_user_id: UserId
    organization_id: OrganizationId
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class EventScope(OrganizationScope):
    event_id: EventId
    membership_id: str | None = None

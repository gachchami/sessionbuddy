from typing import Protocol

from .types import Actor, ResourceContext


class AuthorizationFacts(Protocol):
    async def actor_for_session(self, session_id: str) -> Actor | None: ...

    async def resource_context(
        self, organization_id: str, event_id: str | None, resource_id: str | None
    ) -> ResourceContext: ...

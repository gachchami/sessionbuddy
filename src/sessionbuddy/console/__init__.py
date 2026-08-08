"""Read-only foundation review console.

Mount ``engine_room_router`` on the application composition root. The
module deliberately has no access to bindings, tenant data, or secrets.
"""

from sessionbuddy.console.router import engine_room_router

__all__ = ["engine_room_router"]

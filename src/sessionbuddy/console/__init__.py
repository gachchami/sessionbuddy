"""Read-only foundation review console.

Mount ``foundation_console_router`` on the application composition root. The
module deliberately has no access to bindings, tenant data, or secrets.
"""

from sessionbuddy.console.router import foundation_console_router

__all__ = ["foundation_console_router"]

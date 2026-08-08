import re
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

_CLOUDFLARE_ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")


def content_security_policy(environment: object | None) -> str:
    connect_sources = ["'self'"]
    account_id = str(getattr(environment, "CLOUDFLARE_ACCOUNT_ID", "")).strip().lower()
    if _CLOUDFLARE_ACCOUNT_ID.fullmatch(account_id):
        connect_sources.append(f"https://{account_id}.r2.cloudflarestorage.com")
    return (
        "default-src 'self'; "
        f"connect-src {' '.join(connect_sources)}; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = content_security_policy(
            request.scope.get("env")
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

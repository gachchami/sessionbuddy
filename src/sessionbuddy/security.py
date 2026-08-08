import re
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

_CLOUDFLARE_ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")


def content_security_policy(environment: object | None, path: str = "") -> str:
    connect_sources = ["'self'"]
    account_id = str(getattr(environment, "CLOUDFLARE_ACCOUNT_ID", "")).strip().lower()
    if _CLOUDFLARE_ACCOUNT_ID.fullmatch(account_id):
        connect_sources.append(f"https://{account_id}.r2.cloudflarestorage.com")
    frame_ancestors = "*" if path.startswith("/embeds/") else "'none'"
    return (
        "default-src 'self'; "
        f"connect-src {' '.join(connect_sources)}; "
        "img-src 'self' data: https:; "
        "style-src 'self'; style-src-attr 'unsafe-inline'; "
        "frame-src https://www.youtube.com https://www.youtube-nocookie.com "
        "https://player.vimeo.com https://docs.google.com https://drive.google.com "
        "https://calendar.google.com; "
        f"object-src 'none'; base-uri 'none'; frame-ancestors {frame_ancestors}; "
        "form-action 'self'"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = content_security_policy(
            request.scope.get("env"), request.url.path
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        if not request.url.path.startswith("/embeds/"):
            response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

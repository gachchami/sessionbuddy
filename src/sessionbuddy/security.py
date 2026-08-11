import re

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class SecurityHeadersMiddleware:
    """Apply response headers directly at the ASGI response-start boundary."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = content_security_policy(
                    scope.get("env"), path
                )
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
                )
                if not path.startswith("/embeds/"):
                    headers["X-Frame-Options"] = "DENY"
                if path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_security_headers)

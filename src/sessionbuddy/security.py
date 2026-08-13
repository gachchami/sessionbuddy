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
        # 'inline-speculation-rules' permits only <script type=speculationrules>
        # (the app shell's same-origin prerender hints); it does not loosen the
        # policy for executable inline scripts, which stay blocked by 'self'.
        "script-src 'self' 'inline-speculation-rules'; "
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
                # Chromium derives a form submission's Origin from the
                # document referrer policy. `no-referrer` turns the explicit
                # same-origin magic-link confirmation POST into `Origin:
                # null`, which our login-CSRF boundary must reject. The token
                # has already been removed from the fragment before the user
                # submits, so same-origin disclosure is both sufficient and
                # narrowly scoped to this confirmation document.
                headers["Referrer-Policy"] = (
                    "same-origin" if path == "/auth/verify" else "no-referrer"
                )
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
                )
                headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
                if not path.startswith("/embeds/"):
                    headers["X-Frame-Options"] = "DENY"
                if path.startswith("/api/") and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_security_headers)

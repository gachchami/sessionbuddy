"""Validation for post-authentication local redirects."""

from collections.abc import Collection
from urllib.parse import unquote, urlsplit

ROLE_DESTINATIONS = {
    "organizer": "/admin",
    "reviewer": "/reviews",
    "speaker": "/speaker",
}


def is_allowed_redirect(value: str, allowed_paths: Collection[str]) -> bool:
    """Accept only explicitly allowed, normalized application-relative paths."""
    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if "\\" in value:
        return False

    decoded = value
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if "\\" in decoded or any(ord(character) < 32 for character in decoded):
        return False
    if not decoded.startswith("/") or decoded.startswith("//"):
        return False

    parts = urlsplit(decoded)
    if parts.scheme or parts.netloc or parts.fragment:
        return False
    segments = parts.path.split("/")
    if any(segment in {".", ".."} for segment in segments):
        return False

    normalized_allowed = {path.rstrip("/") or "/" for path in allowed_paths}
    candidate = parts.path.rstrip("/") or "/"
    return candidate in normalized_allowed

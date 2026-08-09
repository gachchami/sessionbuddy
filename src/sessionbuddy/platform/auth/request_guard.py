"""Origin, CSRF, and media-type checks for cookie-authenticated mutations."""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from .csrf import verify_csrf_token


@dataclass(frozen=True, slots=True)
class MutationGuardDecision:
    allowed: bool
    reason: Literal["allowed", "origin_denied", "csrf_invalid", "media_type_invalid"]


def guard_cookie_mutation(
    *,
    origin: str | None,
    referer: str | None,
    allowed_origins: Collection[str],
    content_type: str | None,
    csrf_token: str | None,
    session_id: str,
    csrf_secret: bytes,
    allowed_media_types: Collection[str] = ("application/json",),
) -> MutationGuardDecision:
    candidate = origin or _referer_origin(referer)
    if candidate not in set(allowed_origins):
        return MutationGuardDecision(False, "origin_denied")
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if media_type not in set(allowed_media_types):
        return MutationGuardDecision(False, "media_type_invalid")
    if not csrf_token or not verify_csrf_token(csrf_token, session_id, csrf_secret):
        return MutationGuardDecision(False, "csrf_invalid")
    return MutationGuardDecision(True, "allowed")


def _referer_origin(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"

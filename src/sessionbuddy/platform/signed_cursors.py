"""One signed, scope-bound cursor envelope for all keyset pagination."""

import base64
import binascii
import hmac
import json
from collections.abc import Mapping, Set

from fastapi import HTTPException, Request

from sessionbuddy.platform.auth.http import secret
from sessionbuddy.platform.db.types import utc_now_ms

SIGNED_CURSOR_TTL_MS = 15 * 60 * 1000
_INVALID_CURSOR = "Invalid or expired cursor"


def encode_signed_cursor(
    request: Request,
    *,
    scope: Mapping[str, object],
    position: Mapping[str, object],
    ttl_ms: int = SIGNED_CURSOR_TTL_MS,
    now_ms: int | None = None,
    expires_at_ms: int | None = None,
) -> str:
    """Encode an exact versioned payload without changing consumer field names."""
    if set(scope).intersection(position) or "exp" in scope or "exp" in position:
        raise ValueError("Cursor scope and position fields must be distinct")
    issued_at = utc_now_ms() if now_ms is None else now_ms
    expires = issued_at + ttl_ms if expires_at_ms is None else expires_at_ms
    payload = json.dumps(
        {**scope, **position, "exp": expires, "v": 1},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.digest(secret(request, "CSRF_HMAC_KEY"), payload, "sha256")
    return ".".join((_encode(payload), _encode(signature)))


def decode_signed_cursor(
    request: Request,
    value: str | None,
    *,
    scope: Mapping[str, object],
    position_fields: Set[str],
    now_ms: int | None = None,
) -> dict[str, object] | None:
    """Verify signature, exact scope/shape, version, and expiry or return HTTP 400."""
    if value is None:
        return None
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        payload = _decode(encoded_payload)
        signature = _decode(encoded_signature)
        expected = hmac.digest(secret(request, "CSRF_HMAC_KEY"), payload, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        decoded = json.loads(payload.decode())
        expected_fields = set(scope) | set(position_fields) | {"exp", "v"}
        if not isinstance(decoded, dict) or set(decoded) != expected_fields:
            raise ValueError
        if any(decoded.get(name) != expected for name, expected in scope.items()):
            raise ValueError
        expires = decoded.get("exp")
        version = decoded.get("v")
        current = utc_now_ms() if now_ms is None else now_ms
        if (
            type(expires) is not int
            or type(version) is not int
            or version != 1
            or expires < current
        ):
            raise ValueError
        return decoded
    except (
        ValueError,
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        raise HTTPException(status_code=400, detail=_INVALID_CURSOR) from exc


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    if not value:
        raise ValueError
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)

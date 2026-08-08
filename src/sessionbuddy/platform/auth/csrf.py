"""Stateless CSRF proof bound to a live opaque session."""

import base64
import hashlib
import hmac


def _signature(session_id: str, secret: bytes) -> bytes:
    return hmac.new(secret, b"csrf:v1:" + session_id.encode(), hashlib.sha256).digest()


def issue_csrf_token(session_id: str, secret: bytes) -> str:
    if not session_id or len(secret) < 32:
        raise ValueError("session ID and a 256-bit secret are required")
    return "v1." + base64.urlsafe_b64encode(_signature(session_id, secret)).rstrip(b"=").decode()


def verify_csrf_token(token: str, session_id: str, secret: bytes) -> bool:
    try:
        version, encoded = token.split(".", 1)
        if version != "v1" or not encoded:
            return False
        supplied = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        expected = _signature(session_id, secret)
        return len(supplied) == len(expected) and hmac.compare_digest(supplied, expected)
    except (ValueError, UnicodeError):
        return False

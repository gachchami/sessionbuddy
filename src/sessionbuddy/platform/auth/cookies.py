"""Versioned, tamper-evident opaque session cookie values."""

import base64
import hashlib
import hmac


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_session_cookie(token: str, secret: bytes) -> str:
    if not token or len(secret) < 32:
        raise ValueError("session token and a 256-bit signing secret are required")
    payload = token.encode("utf-8")
    signature = hmac.new(secret, b"session-cookie:v1:" + payload, hashlib.sha256).digest()
    return f"v1.{_encode(payload)}.{_encode(signature)}"


def verify_session_cookie(value: str, secret: bytes) -> str | None:
    if len(secret) < 32:
        return None
    try:
        version, encoded_token, encoded_signature = value.split(".", 2)
        if version != "v1":
            return None
        token = _decode(encoded_token)
        supplied = _decode(encoded_signature)
        expected = hmac.new(secret, b"session-cookie:v1:" + token, hashlib.sha256).digest()
        if len(supplied) != len(expected) or not hmac.compare_digest(supplied, expected):
            return None
        return token.decode("utf-8")
    except (ValueError, UnicodeError):
        return None

"""Opaque authentication token helpers.

Only hashes of generated tokens belong in persistence. Raw values are bearer
secrets and must never be logged.
"""

import hashlib
import secrets


def normalize_email(value: str) -> str:
    """Apply the project's deliberately conservative identity normalization."""
    return value.strip().lower()


def generate_token() -> str:
    """Return at least 256 bits of URL-safe cryptographic randomness."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """Return a fixed-size digest suitable for lookup and constant-time compare."""
    return hashlib.sha256(token.encode("utf-8")).digest()

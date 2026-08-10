"""Password verifier primitives with no native-extension dependency."""

import hashlib
import hmac
import secrets
import unicodedata
from base64 import urlsafe_b64decode, urlsafe_b64encode

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
VERIFIER_BYTES = 32
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128

_COMMON_PASSWORDS = frozenset(
    {
        "123456789012345",
        "correcthorsebatterystaple",
        "letmeinletmeinletmein",
        "passwordpassword",
        "qwertyqwertyqwerty",
        "sessionbuddysessionbuddy",
    }
)


class PasswordPolicyError(ValueError):
    """The proposed password does not satisfy the server-side policy."""


def normalize_password(password: str) -> str:
    return unicodedata.normalize("NFC", password)


def validate_password(password: str) -> str:
    normalized = normalize_password(password)
    if not MIN_PASSWORD_LENGTH <= len(normalized) <= MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters."
        )
    if normalized.casefold() in _COMMON_PASSWORDS:
        raise PasswordPolicyError("Choose a password that is not commonly used.")
    return normalized


def _material(password: str, pepper: bytes) -> bytes:
    # HMAC pre-hashing prevents the pepper from being appended ambiguously and
    # keeps the PBKDF2 input a fixed size for long Unicode passphrases.
    return hmac.new(pepper, normalize_password(password).encode("utf-8"), hashlib.sha256).digest()


def hash_password(password: str, pepper: bytes) -> str:
    normalized = validate_password(password)
    salt = secrets.token_bytes(SALT_BYTES)
    verifier = hashlib.pbkdf2_hmac(
        "sha256", _material(normalized, pepper), salt, PBKDF2_ITERATIONS, dklen=VERIFIER_BYTES
    )
    encoded_salt = urlsafe_b64encode(salt).rstrip(b"=").decode("ascii")
    encoded_verifier = urlsafe_b64encode(verifier).rstrip(b"=").decode("ascii")
    return f"$pbkdf2-sha256$i={PBKDF2_ITERATIONS}${encoded_salt}${encoded_verifier}"


def _decode(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_password(password: str, verifier_phc: str, pepper: bytes) -> bool:
    try:
        empty, algorithm, parameter, encoded_salt, encoded_verifier = verifier_phc.split("$")
        if empty or algorithm != "pbkdf2-sha256" or not parameter.startswith("i="):
            return False
        iterations = int(parameter.removeprefix("i="))
        if not 100_000 <= iterations <= 2_000_000:
            return False
        salt = _decode(encoded_salt)
        expected = _decode(encoded_verifier)
        if len(salt) < SALT_BYTES or len(expected) != VERIFIER_BYTES:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", _material(password, pepper), salt, iterations, dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def needs_rehash(verifier_phc: str) -> bool:
    return not verifier_phc.startswith(f"$pbkdf2-sha256$i={PBKDF2_ITERATIONS}$")

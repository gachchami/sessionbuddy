import pytest

from sessionbuddy.platform.auth.passwords import (
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    verify_password,
)


def test_password_hash_is_salted_slow_and_self_describing() -> None:
    pepper = b"p" * 32
    first = hash_password("a long and private passphrase", pepper)
    second = hash_password("a long and private passphrase", pepper)

    assert first != second
    assert first.startswith("$pbkdf2-sha256$i=600000$")
    assert verify_password("a long and private passphrase", first, pepper)
    assert not verify_password("a different private passphrase", first, pepper)
    assert not verify_password("a long and private passphrase", first, b"x" * 32)
    assert not needs_rehash(first)


@pytest.mark.parametrize("password", ["short", "passwordpassword", "qwertyqwertyqwerty"])
def test_password_policy_rejects_short_or_common_passwords(password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        hash_password(password, b"p" * 32)


def test_malformed_verifiers_fail_closed() -> None:
    assert not verify_password("any sufficiently long password", "not-a-phc-string", b"p" * 32)

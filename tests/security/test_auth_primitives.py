import hashlib
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from sessionbuddy.platform.auth.cookies import sign_session_cookie, verify_session_cookie
from sessionbuddy.platform.auth.csrf import issue_csrf_token, verify_csrf_token
from sessionbuddy.platform.auth.http import allowed_origins
from sessionbuddy.platform.auth.models import CookiePolicy, SessionPolicy
from sessionbuddy.platform.auth.redirects import is_allowed_redirect
from sessionbuddy.platform.auth.request_guard import guard_cookie_mutation
from sessionbuddy.platform.auth.sessions import SessionRecord, validate_session
from sessionbuddy.platform.auth.tokens import generate_token, hash_token, normalize_email


def test_public_base_url_is_always_an_allowed_mutation_origin() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "env": SimpleNamespace(
                ALLOWED_ORIGINS="https://console.example.test",
                PUBLIC_BASE_URL="https://preview.example.test/path",
            ),
        }
    )

    assert allowed_origins(request) == frozenset(
        {"https://console.example.test", "https://preview.example.test"}
    )


def test_email_normalization_is_conservative() -> None:
    assert normalize_email("  Alice.Example+tag@Example.COM ") == "alice.example+tag@example.com"


def test_tokens_are_random_and_only_hashes_need_persisting() -> None:
    first, second = generate_token(), generate_token()
    assert first != second
    assert len(hash_token(first)) == hashlib.sha256().digest_size
    assert hash_token(first) != hash_token(second)


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.test/portal",
        "//evil.test",
        "/%2e%2e/admin",
        "/%252e%252e/admin",
        "/portal\\evil",
        "/portal\x00",
    ],
)
def test_redirect_rejects_attack_variants(target: str) -> None:
    assert not is_allowed_redirect(target, {"/portal", "/admin"})


def test_redirect_requires_exact_allow_list_match() -> None:
    assert is_allowed_redirect("/portal?from=login", {"/portal"})
    assert not is_allowed_redirect("/portal/secret", {"/portal"})
    assert not is_allowed_redirect("portal", {"/portal"})


def test_csrf_is_session_bound_and_tamper_evident() -> None:
    secret = b"s" * 32
    token = issue_csrf_token("session-a", secret)
    assert verify_csrf_token(token, "session-a", secret)
    assert not verify_csrf_token(token, "session-b", secret)
    assert not verify_csrf_token(token + "x", "session-a", secret)
    assert not verify_csrf_token(token, "session-a", b"x" * 32)


def test_csrf_requires_strong_secret() -> None:
    with pytest.raises(ValueError):
        issue_csrf_token("session", b"short")


def test_session_and_cookie_policy_defaults() -> None:
    assert SessionPolicy().idle_lifetime_seconds == 43_200
    cookie = CookiePolicy()
    assert (cookie.name, cookie.secure, cookie.http_only, cookie.same_site, cookie.domain) == (
        "__Host-session",
        True,
        True,
        "lax",
        None,
    )


def test_session_cookie_is_versioned_and_tamper_evident() -> None:
    secret = b"s" * 32
    signed = sign_session_cookie("opaque-token", secret)
    assert signed.startswith("v1.")
    assert verify_session_cookie(signed, secret) == "opaque-token"
    assert verify_session_cookie(signed + "x", secret) is None
    assert verify_session_cookie(signed, b"x" * 32) is None
    assert verify_session_cookie("v2.invalid.invalid", secret) is None


def test_session_cookie_requires_strong_secret() -> None:
    with pytest.raises(ValueError):
        sign_session_cookie("token", b"short")


def session_record(**changes) -> SessionRecord:
    values = {
        "id": "session-a",
        "user_id": "user-a",
        "user_status": "active",
        "authorization_version": 3,
        "current_authorization_version": 3,
        "idle_expires_at_ms": 2_000,
        "absolute_expires_at_ms": 3_000,
        "revoked_at_ms": None,
    }
    values.update(changes)
    return SessionRecord(**values)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"revoked_at_ms": 900}, "revoked"),
        ({"idle_expires_at_ms": 1_000}, "idle_expired"),
        ({"absolute_expires_at_ms": 1_000}, "absolute_expired"),
        ({"user_status": "suspended"}, "user_inactive"),
        ({"current_authorization_version": 4}, "authorization_stale"),
    ],
)
def test_session_validation_fails_closed(changes, reason) -> None:
    decision = validate_session(session_record(**changes), now_ms=1_000)
    assert not decision.active
    assert decision.reason == reason


def test_live_session_is_accepted() -> None:
    assert validate_session(session_record(), now_ms=1_000).active


def test_cookie_mutation_guard_requires_origin_json_and_bound_csrf() -> None:
    secret = b"c" * 32
    token = issue_csrf_token("session-a", secret)
    allowed = guard_cookie_mutation(
        origin="https://app.example",
        referer=None,
        allowed_origins={"https://app.example"},
        content_type="application/json; charset=utf-8",
        csrf_token=token,
        session_id="session-a",
        csrf_secret=secret,
    )
    assert allowed.allowed

    common = {
        "referer": None,
        "allowed_origins": {"https://app.example"},
        "content_type": "application/json",
        "csrf_token": token,
        "session_id": "session-a",
        "csrf_secret": secret,
    }
    assert guard_cookie_mutation(origin="https://evil.example", **common).reason == "origin_denied"
    assert (
        guard_cookie_mutation(
            origin="https://app.example", **(common | {"content_type": "text/plain"})
        ).reason
        == "media_type_invalid"
    )
    assert (
        guard_cookie_mutation(
            origin="https://app.example", **(common | {"csrf_token": "bad"})
        ).reason
        == "csrf_invalid"
    )


def test_cookie_mutation_guard_accepts_same_origin_referer_fallback() -> None:
    secret = b"c" * 32
    decision = guard_cookie_mutation(
        origin=None,
        referer="https://app.example/settings?tab=security",
        allowed_origins={"https://app.example"},
        content_type="application/json",
        csrf_token=issue_csrf_token("session-a", secret),
        session_id="session-a",
        csrf_secret=secret,
    )
    assert decision.allowed


def test_cookie_mutation_guard_accepts_explicit_upload_media_type() -> None:
    secret = b"c" * 32
    decision = guard_cookie_mutation(
        origin="https://app.example",
        referer=None,
        allowed_origins={"https://app.example"},
        content_type="image/png",
        csrf_token=issue_csrf_token("session-a", secret),
        session_id="session-a",
        csrf_secret=secret,
        allowed_media_types={"image/png", "image/jpeg", "image/webp"},
    )
    assert decision.allowed

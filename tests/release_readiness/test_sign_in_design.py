from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_sign_in_preserves_passwordless_flow_with_clear_copy() -> None:
    markup = source("sign_in.html")
    javascript = source("sign_in.js")

    assert 'id="sign-in-form"' in markup
    assert 'type="email" autocomplete="email"' in markup
    assert 'id="password-sign-in">Sign in</button>' in markup
    assert 'id="send-sign-in-link" class="secondary" type="button"' in markup
    assert 'id="sign-in-sent"' in markup
    assert 'role="status" aria-live="polite"' in markup
    assert '"/api/v1/auth/magic-links"' in javascript
    assert '"/api/v1/auth/password/sign-in"' in javascript
    assert "redirect_path: redirect" in javascript
    assert 'confirmation.focus()' in javascript


def test_sign_in_is_minimal_and_explains_default_role_behavior() -> None:
    markup = source("sign_in.html")
    stylesheet = source("product.css")

    assert 'class="sign-in-story"' not in markup
    assert "You’ll open your default workspace." in markup
    assert 'class="auth-role-note"' not in markup
    assert "Organizer" not in markup and "Reviewer" not in markup and "Speaker" not in markup
    assert ".sign-in-card::before" in stylesheet
    assert "@media (max-width: 48rem)" in stylesheet

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


def test_sign_in_uses_the_program_pass_identity_and_mobile_layout() -> None:
    markup = source("sign_in.html")
    stylesheet = source("product.css")

    assert 'class="sign-in-story"' in markup
    assert 'class="program-pass" aria-label="SessionBuddy access pass"' in markup
    assert "Organizer · Reviewer · Speaker" in markup
    assert ".program-pass" in stylesheet
    assert "@media (max-width: 48rem)" in stylesheet
    assert ".sign-in-card { grid-row: 1; }" in stylesheet

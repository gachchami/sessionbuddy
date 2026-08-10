from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_profile_form_is_registration_ready_without_editable_roles() -> None:
    markup = (STATIC / "account.html").read_text(encoding="utf-8")

    assert 'name="first_name" autocomplete="given-name"' in markup
    assert 'name="last_name" autocomplete="family-name"' in markup
    assert 'name="password" type="password" autocomplete="new-password"' in markup
    assert 'name="password_confirmation" type="password"' in markup
    assert 'id="profile-roles"' in markup
    assert 'name="role"' not in markup


def test_profile_password_is_optional_matched_and_never_repopulated() -> None:
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert 'password.value === confirmation.value' in javascript
    assert 'password: values.password || null' in javascript
    assert 'event.currentTarget.elements.password.value = ""' in javascript
    assert "profile.password" not in javascript


def test_profile_access_summary_includes_account_roles() -> None:
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert "for (const role of session.account_roles || [])" in javascript
    assert 'active ? "Active account role" : "Account role"' in javascript
    assert 'role === "organizer" ? "/admin"' in javascript

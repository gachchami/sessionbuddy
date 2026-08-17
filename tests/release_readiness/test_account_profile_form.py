from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_profile_form_is_registration_ready_without_editable_roles() -> None:
    markup = (STATIC / "account.html").read_text(encoding="utf-8")

    assert 'name="first_name" autocomplete="given-name"' in markup
    assert 'name="last_name" autocomplete="family-name"' in markup
    assert 'name="password" type="password" autocomplete="new-password"' in markup
    assert 'name="password_confirmation" type="password"' in markup
    assert 'id="profile-roles"' in markup
    assert 'id="headshot-input" type="file" accept="image/jpeg,image/png,image/webp"' in markup
    assert 'name="description" maxlength="1000"' in markup
    assert 'name="website_url" type="url"' in markup
    assert 'name="linkedin_url" type="url"' in markup
    assert 'name="x_url" type="url"' in markup
    assert 'name="public_profile_enabled" type="checkbox"' in markup
    assert "Allow anyone with your profile link" in markup
    assert "Your email is verified and cannot be changed here.</small>" in markup
    assert "Creating it signs out your other sessions." in markup
    assert '<h2 id="access-title">Roles and Access</h2>' in markup
    assert 'account-profile-panel" aria-labelledby="profile-title" hidden' in markup
    assert 'organizer-section--account-access" aria-labelledby="access-title" hidden' in markup
    assert '<script>if (location.pathname' not in markup
    assert 'name="role"' not in markup


def test_profile_password_is_optional_matched_and_never_repopulated() -> None:
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert 'password.value === confirmation.value' in javascript
    assert 'password: values.password || null' in javascript
    assert 'const form = event.currentTarget;' in javascript
    assert 'form.elements.password.value = ""' in javascript
    assert "profile.password" not in javascript


def test_password_rotation_keeps_the_current_page_authenticated() -> None:
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    token_update = javascript.index(
        "if (profile.csrf_token) session.csrf_token = profile.csrf_token;"
    )
    headshot_upload = javascript.index('api("/api/v1/account/headshot"', token_update)
    assert token_update < headshot_upload
    assert "Profile and password saved. Your other sessions were signed out." in javascript
    assert "Sign in again to continue." not in javascript


def test_profile_password_configuration_failure_has_an_actionable_recovery() -> None:
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert "error.status === 503 && values.password" in javascript
    assert "Leave both password fields blank to save the rest of your profile now" in javascript
    assert "ask the administrator to check password configuration" in javascript


def test_profile_access_section_selects_a_default_account_role() -> None:
    markup = (STATIC / "account.html").read_text(encoding="utf-8")
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")

    assert 'id="default-role-form"' in markup
    assert 'input.name = "default_role"' in javascript
    assert 'input.checked = role === session.default_role' in javascript
    assert '"/api/v1/account/default-role"' in javascript
    assert ".default-role-choice:has(input:checked)" in stylesheet
    assert ".default-role-list { grid-template-columns: 1fr; }" in stylesheet


def test_headshot_has_preview_upload_and_remove_controls() -> None:
    markup = (STATIC / "account.html").read_text(encoding="utf-8")
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert "previewReader.readAsDataURL(previewSelection)" in javascript
    assert 'method: "PUT"' in javascript
    assert 'method: "DELETE"' in javascript
    assert '"/api/v1/account/headshot"' in javascript
    assert "A chosen image is uploaded when you save your profile." in markup
    assert '<button id="save-profile" type="submit" disabled>Save profile</button>' in markup
    assert 'id="upload-headshot"' not in markup


def test_profile_load_is_quiet_and_mobile_layout_is_single_column() -> None:
    markup = (STATIC / "account.html").read_text(encoding="utf-8")
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")

    assert 'id="status" class="status status-compact"' in markup
    assert "Your account is up to date." not in javascript
    mobile = stylesheet.split("@media (max-width: 42rem)", 1)[1].split(
        "@media (max-width: 48rem)", 1
    )[0]
    assert ".account-headshot { grid-template-columns: 1fr" in mobile
    assert ".account-link-fields .form-grid { grid-template-columns: 1fr; }" in mobile
    assert ".account-headshot .actions > .account-headshot__choose" in mobile
    assert ".account-headshot .actions > button { flex: 0 0 auto; }" in mobile


def test_setup_display_name_is_a_reviewable_profile_draft() -> None:
    markup = (STATIC / "account.html").read_text(encoding="utf-8")
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert "profile.display_name.trim().split(/\\s+/)" in javascript
    assert 'id="name-draft-help" class="help" hidden' in markup
    assert "Nothing changes until you save your profile." in markup
    assert markup.count('aria-describedby="name-draft-help"') == 2
    assert 'byId("name-draft-help").hidden = !inferredNameDraft' in javascript
    assert "Review the suggested name fields" in javascript
    assert "inferredNameDraft = true" in javascript

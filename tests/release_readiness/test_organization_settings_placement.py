from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def test_source_wiring_home_switches_organization_without_editing_organization_settings() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'id="organization-picker"' in page
    assert 'id="edit-organization"' not in page
    assert 'id="organization-dialog"' not in page
    assert 'byId("organization-picker").addEventListener("change"' in script
    assert '["owner", "manage"].includes(permission)' in script
    assert 'includes("organization_admin")' not in script
    assert 'organization-form' not in script
    assert 'history.replaceState(null, ""' in script


def test_source_wiring_organization_settings_has_a_dedicated_organizer_surface() -> None:
    home = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    page = (STATIC / "organization_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "organization_admin.js").read_text(encoding="utf-8")

    assert 'href="/admin/organization"' in home
    assert '/admin/organization/assets/organization.js' in page
    assert 'account-profile-panel' not in page
    assert 'Roles and Access' not in page
    assert 'id="organization-settings"' in page
    assert 'id="organization-settings-list"' in page
    assert 'grant.permission === "owner" ? "Owner" : "Admin"' in script
    assert '["owner", "manage"].includes(permission)' in script
    assert 'includes("organization_admin")' not in script
    update_path = (
        "/api/v1/admin/organizations/"
        "${encodeURIComponent(form.dataset.organizationId)}"
    )
    assert update_path in script
    assert 'method: "PATCH"' in script
    assert 'body: JSON.stringify({ name:' in script


def test_source_wiring_organization_settings_keep_the_access_and_transfer_contract() -> None:
    page = (STATIC / "organization_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "organization_admin.js").read_text(encoding="utf-8")

    # One organization is edited at a time; its name is the section heading and
    # the switcher only appears for multi-organization accounts.
    assert 'id="organization-switcher"' in page
    assert 'id="organization-context"' in page
    assert 'byId("organization-settings-title").textContent = organization.name' in script
    assert 'byId("organization-switcher-label").hidden = state.organizations.length < 2' in script
    # The mutation contract is unchanged.
    assert "/ownership-transfers`" in script
    assert "function disarmTransfer(form)" in script
    assert 'button.textContent = `Confirm transfer to ${email}`' in script
    idempotency = (
        '"Idempotency-Key": form.dataset.requestKey'
        " || (form.dataset.requestKey = crypto.randomUUID())"
    )
    assert idempotency in script
    rename_body = (
        "body: JSON.stringify({ name: form.elements.name.value.trim(),"
        " version: Number(form.dataset.version) })"
    )
    assert rename_body in script

from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def test_home_switches_organization_without_editing_organization_settings() -> None:
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


def test_organization_settings_has_a_dedicated_organizer_surface() -> None:
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

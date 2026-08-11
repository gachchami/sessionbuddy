from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def test_events_page_only_switches_organization() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "events_admin.js").read_text(encoding="utf-8")

    assert 'id="organization-picker"' in page
    assert 'class="context-bar organizer-context-bar"' not in page
    assert 'id="organization-title" class="sr-only"' in page
    assert 'id="edit-organization"' not in page
    assert 'id="organization-dialog"' not in page
    assert 'byId("organization").addEventListener("change"' in script
    assert '["owner", "manage"].includes(permission)' in script
    assert 'includes("organization_admin")' not in script
    assert 'values.status === "archived"' in script
    assert "body.status = intendedStatus" in script
    assert 'organization-form' not in script


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

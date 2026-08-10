from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def test_events_page_only_switches_organization() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "events_admin.js").read_text(encoding="utf-8")

    assert 'id="organization-picker"' in page
    assert 'class="context-bar organizer-context-bar"' not in page
    assert 'Events · <span id="organization-title"' in page
    assert 'id="edit-organization"' not in page
    assert 'id="organization-dialog"' not in page
    assert 'byId("organization").addEventListener("change"' in script
    assert 'organization-form' not in script


def test_account_page_exposes_settings_only_to_organization_admins() -> None:
    page = (STATIC / "account.html").read_text(encoding="utf-8")
    script = (STATIC / "account.js").read_text(encoding="utf-8")

    assert 'id="organization-settings"' in page
    assert 'id="organization-settings-list"' in page
    assert 'includes("organization_admin")' in script
    update_path = (
        "/api/v1/admin/organizations/"
        "${encodeURIComponent(form.dataset.organizationId)}"
    )
    assert update_path in script
    assert 'method: "PATCH"' in script
    assert 'body: JSON.stringify({ name:' in script

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_source_wiring_home_links_to_the_dedicated_event_editor() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'id="new-event" class="button" href="/admin/events/new" hidden' in page
    assert (
        "`/admin/events/new?organization_id=${encodeURIComponent(state.organizationId)}`" in script
    )
    assert 'id="event-table" class="organizer-home-table" role="table"' in page
    assert 'id="event-dialog"' not in page
    assert 'id="event-form"' not in page
    assert "openEventDialog" not in script


def test_source_wiring_home_only_offers_creation_to_organization_owners_or_managers() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'href="/admin/events/new" hidden' in page
    assert "window.SessionBuddyAccess?.canManageOrganization" in script
    assert '["owner", "manage"].includes(permission)' in script
    assert 'byId("new-event").hidden = !manager' in script
    assert 'access.roles.includes("organization_admin")' not in script

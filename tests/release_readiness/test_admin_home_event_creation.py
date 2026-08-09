from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_home_opens_event_creation_without_changing_the_background_page() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'id="new-event" type="button"' in page
    assert 'href="/admin/events#event-form"' not in page
    assert 'id="event-dialog"' in page
    assert 'id="event-form"' in page
    assert 'id="event-form-status"' in page
    assert 'byId("event-dialog").showModal()' in script
    assert 'create.addEventListener("click", openEventDialog)' in script


def test_home_event_form_creates_in_place_and_refreshes_the_dashboard() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    for name in (
        "organization_id",
        "name",
        "starts_at",
        "ends_at",
        "time_zone",
        "delivery_mode",
        "location",
        "description",
    ):
        assert f'name="{name}"' in page
    endpoint = (
        "/api/v1/admin/organizations/"
        "${encodeURIComponent(values.organization_id)}/events"
    )
    assert endpoint in script
    assert "await loadDashboard();" in script
    assert "location.assign" not in script
    assert "location.replace" not in script
    assert 'byId("event-form-status")' in script


def test_home_only_offers_creation_to_organization_administrators() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'id="new-event" type="button" hidden' in page
    assert 'access.roles.includes("organization_admin")' in script
    assert 'byId("new-event").hidden = state.organizations.length === 0' in script

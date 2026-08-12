from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_home_links_to_the_canonical_full_event_form() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'id="new-event" class="button" href="/admin/events#event-form"' in page
    assert 'class="organizer-home-event-list" aria-label="Events"' in page
    assert 'id="event-dialog"' not in page
    assert 'id="event-form"' not in page
    assert "openEventDialog" not in script


def test_canonical_events_route_opens_its_complete_form_from_the_hash() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "events_admin.js").read_text(encoding="utf-8")

    for name in (
        "name",
        "start_date",
        "start_time",
        "end_date",
        "end_time",
        "time_zone",
        "delivery_mode",
        "location",
        "description",
        "accent_color",
        "logo_file",
        "cover_file",
        "website_url",
        "email_sender_name",
        "email_reply_to",
    ):
        assert f'name="{name}"' in page
    assert 'location.hash === "#event-form"' in script
    assert "openEventDialog();" in script
    assert (
        '<span class="field-label">Description '
        '<span class="required-marker" aria-hidden="true">*</span></span>'
        '<textarea name="description"' in page
    )
    assert 'textarea name="description" rows="4"' in page
    assert 'maxlength="2000" required' in page


def test_home_only_offers_creation_to_organization_owners_or_managers() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'href="/admin/events#event-form" hidden' in page
    assert '["owner", "manage"].includes(permission)' in script
    assert 'access.roles.includes("organization_admin")' not in script
    assert 'byId("new-event").hidden = state.organizations.length === 0' in script

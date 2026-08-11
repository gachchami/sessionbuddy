from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_home_is_a_minimal_paginated_event_index() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert "organizer-home-identity--minimal" in page
    assert 'href="/admin/organization">Settings</a>' in page
    assert 'class="organizer-home-event-list"' in page
    assert 'id="load-more-events"' in page
    assert "Organization workspace" not in page
    assert "organizer-home-event-row" in styles


def test_home_does_not_mix_event_operations_into_the_organization_dashboard() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'href="/admin/events#event-form"' in page
    assert 'href="/admin/organization"' in page
    for removed in ("People", "Speakers", "Reviewers", "Sessions", "Proposals"):
        assert f"<small>{removed}</small>" not in page
    assert "/metrics" not in script


def test_home_uses_assigned_organization_and_permission_gated_creation() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'id="new-event" class="button" href="/admin/events#event-form" hidden' in page
    assert "const organization = organizations[0]" in script
    assert 'byId("new-event").hidden = state.organizations.length === 0' in script
    assert "Create organization" not in page + script


def test_home_fetches_only_the_paginated_event_index() -> None:
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'view: "all", order: "upcoming"' in script
    assert "/events?${params}" in script
    assert "recent_speakers" not in script
    assert "function eventRow(event)" in script


def test_home_has_responsive_mobile_layout() -> None:
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert ".organizer-home-event-row { grid-template-columns: minmax(0, 1fr) auto;" in styles

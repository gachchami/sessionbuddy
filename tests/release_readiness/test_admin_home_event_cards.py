from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_home_matches_approved_dashboard_structure() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert 'class="organizer-home-identity"' in page
    assert "Your program at a glance" in page
    assert "Upcoming events" in page
    assert "Recent activity" in page
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in styles
    assert ".organizer-home-lower { display: grid;" in styles


def test_home_metrics_use_real_api_fields() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    for metric_id in ("metric-events", "metric-speakers", "metric-sessions", "metric-proposals"):
        assert f'id="{metric_id}"' in page
    assert "metrics.event_count ?? 0" in script
    assert "metrics.speaker_count ?? 0" in script
    assert "metrics.session_count ?? 0" in script
    assert "metrics.proposal_count ?? 0" in script


def test_home_uses_assigned_organization_and_permission_gated_creation() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'id="new-event" class="button" href="/admin/events#event-form" hidden' in page
    assert "const organization = organizations[0]" in script
    assert 'byId("new-event").hidden = state.organizations.length === 0' in script
    assert "Create organization" not in page + script


def test_home_uses_bounded_upcoming_events_and_real_activity() -> None:
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert "events?view=active&order=upcoming&limit=3" in script
    assert 'event.status !== "archived"' in script
    assert "Number(event.ends_at_ms) >= Date.now()" in script
    assert "metrics.recent_speakers || []" in script
    assert "function eventRow(event)" in script
    assert "function activityRow(speaker)" in script


def test_home_has_responsive_mobile_layout() -> None:
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert ".organizer-home-metrics { grid-template-columns: repeat(2, minmax(0,1fr));" in styles
    assert ".organizer-home-lower { grid-template-columns: 1fr; }" in styles
    assert ".organizer-home-event-row__date { grid-column: 2; }" in styles

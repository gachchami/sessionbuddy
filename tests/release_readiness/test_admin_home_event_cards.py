from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_source_wiring_home_is_the_filterable_paginated_event_ledger() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    styles = (STATIC / "admin_home.css").read_text(encoding="utf-8")

    assert 'id="organization-picker"' in page
    assert 'data-event-filter="all" aria-pressed="true"' in page
    assert 'id="event-search"' in page
    assert 'id="event-sort"' in page
    assert 'class="organizer-home-table" role="table" aria-label="Events"' in page
    assert 'id="load-more-events"' in page
    assert "Organization workspace" not in page
    assert ".organizer-home-event-row" in styles


def test_source_wiring_home_has_visible_low_density_event_actions() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'href="/admin/events/new"' in page
    assert 'href="/admin/organization"' in page
    assert "name.href = `/admin/events/${encodeURIComponent(event.id)}`" in script
    assert 'settings.textContent = "Manage"' in script
    assert 'duplicate.textContent = "Clone"' in script
    assert 'textContent = "Open"' not in script
    assert "/metrics" not in script


def test_source_wiring_home_uses_url_selected_organization_and_exact_permissions() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'id="new-event" class="button" href="/admin/events/new" hidden' in page
    assert 'params.get("organization_id")' in script
    assert "window.SessionBuddyAccess?.canManageOrganization" in script
    assert 'byId("new-event").hidden = !manager' in script
    assert "Create organization" not in page + script


def test_source_wiring_home_mirrors_server_list_state_and_handles_stale_cursors() -> None:
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert "new URLSearchParams({ view: state.view, order: state.order })" in script
    assert 'params.set("q", state.query)' in script
    assert 'params.set("cursor", cursor)' in script
    assert 'history.replaceState(null, ""' in script
    assert "window.SessionBuddyApi.isStaleCursor(error)" in script
    assert "recent_speakers" not in script


def test_home_keeps_one_semantic_dom_for_table_cards_and_activity() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")
    styles = (STATIC / "admin_home.css").read_text(encoding="utf-8")

    assert (
        page.index('id="events"')
        < page.index('id="recent-changes-slot"')
        < page.index('id="event-pagination"')
    )
    assert 'row.setAttribute("role", "row")' in script
    assert 'node.setAttribute("role", "cell")' in script
    assert 'node.setAttribute("aria-labelledby", headerId)' in script
    assert ".organizer-home-table__header { position:absolute;" in styles
    mobile_header_rule = styles.split(".organizer-home-table__header { position:absolute;", 1)[
        1
    ].split("}", 1)[0]
    assert "display:none" not in mobile_header_rule.replace(" ", "")


def test_source_wiring_home_recent_changes_is_manager_only_and_uses_shared_formatter() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")
    formatter = (STATIC / "activity_format.js").read_text(encoding="utf-8")

    assert "/app-shell/assets/activity-format.js?v=" in page
    assert "if (!canManage)" in script
    assert "slot.replaceChildren();" in script
    assert 'activity.operation !== "read"' not in script
    assert ".slice(0, 4)" in script
    # The rail composes actor / verb / subject spans (accepted marker-column design)
    # from the same shared formatter, aliased locally, instead of one sentence string.
    assert "const format = window.SessionBuddyActivityFormat;" in script
    assert "format.verb(activity.operation)" in script
    assert "format.resourceLabel(activity.resource_type)" in script
    assert "No recent activity." in script
    assert "Recent activity is temporarily unavailable." in script
    for label in ("call_for_speaker_form", "evaluation_round", "accepted_session", "agenda_item"):
        assert f"{label}:" in formatter

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_events_page_uses_an_operational_management_layout() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "events_admin.js").read_text(encoding="utf-8")
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert 'class="event-filter-tabs"' in page
    assert 'id="event-search" type="search"' in page
    assert 'class="entity-grid event-grid event-management-list"' in page
    assert (
        'item.className = "entity-card organizer-card organizer-event-list-card '
        'event-management-card"' in script
    )
    assert 'dateTile.className = "event-date-tile"' in script
    assert "function visibleEvents()" in script
    assert "function renderEventList()" in script
    assert ".event-management-list .event-management-card" in styles


def test_home_keeps_visual_cards_while_events_uses_rows() -> None:
    home = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    events = (STATIC / "events_admin.html").read_text(encoding="utf-8")

    assert 'class="home-event-grid"' in home
    assert 'class="entity-grid event-grid event-management-list"' in events

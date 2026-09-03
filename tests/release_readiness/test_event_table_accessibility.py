from pathlib import Path

STATIC = Path("src/sessionbuddy/static")


def test_event_management_table_has_complete_aria_structure() -> None:
    markup = (STATIC / "admin_home.html").read_text()
    javascript = (STATIC / "admin_home.js").read_text()

    assert 'class="organizer-home-table" role="table" aria-label="Events"' in markup
    assert 'class="organizer-home-table__header" role="rowgroup"' in markup
    assert markup.count('role="columnheader"') == 5
    assert 'id="event-list" class="organizer-home-table__body" role="rowgroup"' in markup
    assert 'row.setAttribute("role", "row")' in javascript
    assert 'node.setAttribute("role", "cell")' in javascript
    assert 'node.setAttribute("aria-labelledby", headerId)' in javascript
    assert 'aria-hidden="true"><span>Event' not in markup


def test_event_empty_state_is_outside_the_aria_table_and_live() -> None:
    markup = (STATIC / "admin_home.html").read_text()
    javascript = (STATIC / "admin_home.js").read_text()

    table_end = markup.index("</div>\n        <p", markup.index('role="table"'))
    empty_position = markup.index('id="event-list-empty"')
    assert empty_position > table_end
    assert 'id="event-list-empty" class="empty organizer-home-empty" aria-live="polite"' in markup
    # Rows are rendered into the aria rowgroup via eventRow, now grouped by month
    # (accepted timeline design); the empty state stays outside the table.
    assert "list.replaceChildren(...renderEventGroups(groups))" in javascript
    assert "group.events.map(eventRow)" in javascript
    assert "empty.textContent = state.query" in javascript


def test_source_wiring_mobile_cards_keep_the_table_headers_available_to_assistive_technology() -> (
    None
):
    styles = (STATIC / "admin_home.css").read_text()

    mobile_header = styles.split(".organizer-home-table__header { position:absolute;", 1)[1].split(
        "}", 1
    )[0]
    assert "clip-path:inset(50%)" in mobile_header
    assert "display:none" not in mobile_header.replace(" ", "")

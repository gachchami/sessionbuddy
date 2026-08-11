from pathlib import Path

STATIC = Path("src/sessionbuddy/static")


def test_event_management_table_has_complete_aria_structure() -> None:
    markup = (STATIC / "events_admin.html").read_text()

    assert 'class="event-table-frame" role="table" aria-label="Events"' in markup
    assert 'class="event-table-header" role="row"' in markup
    assert markup.count('role="columnheader"') == 5
    assert 'id="event-list" class="event-management-list" role="rowgroup"' in markup
    assert 'aria-hidden="true"><span>Event' not in markup


def test_event_empty_state_is_outside_the_aria_table_and_live() -> None:
    markup = (STATIC / "events_admin.html").read_text()
    javascript = (STATIC / "events_admin.js").read_text()

    table_end = markup.index("</div>\n      </div>", markup.index('role="table"'))
    empty_position = markup.index('id="event-list-empty"')
    assert empty_position > table_end
    assert 'id="event-list-empty" class="empty" role="status" aria-live="polite"' in markup
    assert 'list.append(empty)' not in javascript
    assert 'empty.textContent = "No events yet. Create your first event."' in javascript

from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_admin_supports_all_views_drag_and_keyboard_editor() -> None:
    html, javascript = read("agenda_admin.html"), read("agenda.js")
    for view in ("list", "day", "week", "track", "room"):
        assert f'value="{view}"' in html
    assert "draggable = true" in javascript
    assert 'dataTransfer.setData("text/plain"' in javascript
    assert 'id="editor"' in html and 'type="datetime-local"' in html
    assert "previewDrop" in javascript and "schedulePreview" in javascript


def test_save_is_optimistic_with_visible_rollback_and_server_preview() -> None:
    javascript, css = read("agenda.js"), read("agenda.css")
    assert "/agenda/preview" in javascript
    assert "optimistic(candidate); render(); await save(candidate)" in javascript
    assert "state.model = before; render()" in javascript
    assert "rolled back" in javascript
    assert ".rollback" in css and ".preview-conflict" in css


def test_admin_and_schedule_are_accessible_responsive_and_safe() -> None:
    admin, schedule = read("agenda_admin.html"), read("schedule.html")
    javascript = read("agenda.js") + read("schedule.js")
    css = read("agenda.css") + read("schedule.css")
    for html in (admin, schedule):
        assert 'class="skip-link"' in html
        assert 'aria-live="polite"' in html
        assert '<main id="' in html
    assert "@media(max-width:" in css
    assert "prefers-reduced-motion" in css
    assert "innerHTML" not in javascript
    assert "eventId = match ? decodeURIComponent" in javascript


def test_read_only_schedule_has_staff_speaker_views_and_empty_error_states() -> None:
    html, javascript = read("schedule.html"), read("schedule.js")
    for view in ("list", "day", "track", "room"):
        assert f'data-view="{view}"' in html
    assert "/api/v1/events/${encodeURIComponent(eventId)}/schedule" in javascript
    assert 'id="empty"' in html
    assert ".catch((error)" in javascript

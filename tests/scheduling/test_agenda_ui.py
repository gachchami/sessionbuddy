from pathlib import Path

import pytest
from pydantic import ValidationError

from sessionbuddy.scheduling.models import AgendaAutoSchedule, AgendaResourceCreate, AgendaSetup

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


def test_fresh_event_can_create_its_first_agenda_and_rooms() -> None:
    html, javascript = read("agenda_admin.html"), read("agenda.js")
    assert 'id="agenda-setup-form"' in html
    assert 'name="room_names"' in html
    assert 'name="track_names"' in html
    assert "/agenda/setup" in javascript
    assert 'if (error.status === 404) showSetup()' in javascript


def test_existing_agenda_can_manage_resources_and_build_a_draft() -> None:
    html, javascript = read("agenda_admin.html"), read("agenda.js")
    assert 'id="auto-schedule-form"' in html
    assert 'id="room-form"' in html and 'id="track-form"' in html
    assert "/agenda/auto-schedule" in javascript
    assert "/agenda/${kind}s" in javascript
    assert "Review the draft before publishing" in javascript
    assert "Publishing agenda" in javascript
    assert "published_revision" in javascript
    assert "weekday" in javascript


def test_scheduled_session_can_be_returned_to_unscheduled_list() -> None:
    html, javascript = read("agenda_admin.html"), read("agenda.js")
    assert 'id="unschedule-item"' in html
    assert 'method: "DELETE"' in javascript
    assert '"content-type": "application/json"' in javascript
    assert "Session moved back to unscheduled sessions." in javascript


def test_auto_schedule_and_resource_inputs_are_bounded() -> None:
    assert AgendaResourceCreate(name="  Main stage  ").name == "Main stage"
    assert AgendaAutoSchedule(session_minutes=45, gap_minutes=15).session_minutes == 45
    with pytest.raises(ValidationError):
        AgendaAutoSchedule(session_minutes=5)
    with pytest.raises(ValidationError):
        AgendaAutoSchedule(room_ids=["room-a", "room-a"])


def test_agenda_editor_interprets_dates_in_the_event_time_zone() -> None:
    javascript = read("agenda.js")
    assert "partsInTimeZone" in javascript
    assert "state.model.event.time_zone" in javascript
    assert "That local time does not exist" in javascript
    assert "getTimezoneOffset" not in javascript


def test_agenda_setup_requires_unique_nonblank_rooms() -> None:
    assert AgendaSetup(room_names=[" Main stage "], track_names=["General"]).room_names == [
        "Main stage"
    ]
    with pytest.raises(ValidationError):
        AgendaSetup(room_names=[])
    with pytest.raises(ValidationError):
        AgendaSetup(room_names=["Main stage", "main STAGE"])


def test_save_is_optimistic_with_visible_rollback_and_server_preview() -> None:
    javascript, css = read("agenda.js"), read("agenda.css")
    assert "/agenda/preview" in javascript
    assert "optimistic(candidate)" in javascript and "await save(candidate)" in javascript
    assert "state.model = before" in javascript
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
    assert "The organizer has not published the schedule yet." in javascript
    assert ".catch((error)" in javascript
    assert 'id="download-calendar"' in html
    assert "BEGIN:VCALENDAR" in javascript

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_cfp_availability_displays_and_uses_the_event_time_zone() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'id="cfp-time-zone-context"' in page
    assert 'id="cfp-time-zone"' in page
    assert page.count('aria-describedby="cfp-time-zone-context"') == 2
    assert "/organizations/${encodeURIComponent(workspace.organization_id)}/events" in script
    assert "state.eventTimeZone = currentEvent.time_zone" in script
    assert 'byId("cfp-time-zone").textContent = state.eventTimeZone' in script
    assert 'timeZone: state.eventTimeZone' in script
    assert "return toLocalInput(timestamp) === value ? timestamp : Number.NaN" in script


def test_agenda_datetime_sections_repeat_the_event_time_zone_context() -> None:
    page = (STATIC / "agenda_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "agenda.js").read_text(encoding="utf-8")

    assert 'id="auto-schedule-time-zone"' in page
    assert 'aria-describedby="auto-schedule-time-zone"' in page
    assert 'id="editor-time-zone"' in page
    assert page.count('aria-describedby="editor-time-zone"') == 2
    assert page.count("data-event-time-zone") == 2
    assert 'document.querySelectorAll("[data-event-time-zone]")' in script
    assert "node.textContent = model.event.time_zone" in script


def test_evaluation_round_dates_use_the_event_time_zone() -> None:
    page = (STATIC / "admin_submissions.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_submissions.js").read_text(encoding="utf-8")

    assert 'id="round-time-zone-context"' in page
    assert page.count('aria-describedby="round-time-zone-context"') == 2
    assert 'id="round-time-zone"' in page
    assert "state.timeZone = await loadEventTimeZone()" in script
    assert 'byId("round-time-zone").textContent = state.timeZone' in script
    assert "const reviewOpens = inputMillis" in script
    assert "const reviewCloses = inputMillis" in script
    assert "new Date(String(values.get(\"review_" not in script


def test_speaker_task_due_date_uses_the_event_time_zone() -> None:
    page = (STATIC / "event_workspace.html").read_text(encoding="utf-8")
    script = (STATIC / "event_workspace.js").read_text(encoding="utf-8")

    assert 'id="task-time-zone-context"' in page
    assert 'aria-describedby="task-time-zone-context"' in page
    assert 'id="task-time-zone"' in page
    assert "state.timeZone = await loadEventTimeZone()" in script
    assert 'byId("task-time-zone").textContent = state.timeZone' in script
    assert "const due = inputMillis(values.due_at)" in script
    assert "new Date(values.due_at).getTime()" not in script

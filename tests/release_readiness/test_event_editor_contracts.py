from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_event_editor_has_three_routed_modes_and_complete_sections() -> None:
    page = source("event_editor.html")
    script = source("event_editor.js")

    for section_id in ("general", "date-time", "branding", "email", "lifecycle"):
        assert f'id="{section_id}"' in page
    assert 'state.mode = eventId ? "edit" : sourceId ? "duplicate" : "create"' in script
    assert (
        "/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/events" in script
    )
    assert "/api/v1/admin/events/${encodeURIComponent(state.source.id)}/duplicate" in script
    assert 'method: state.mode === "edit" ? "PATCH" : "POST"' in script


def test_event_editor_discloses_and_converts_in_the_event_time_zone() -> None:
    page = source("event_editor.html")
    script = source("event_editor.js")

    assert 'id="event-time-zone-context"' in page
    assert 'id="event-time-zone"' in page
    assert page.count('aria-describedby="event-time-zone-context"') == 5
    assert '<select name="time_zone"' in page
    assert 'list="event-time-zones"' not in page
    assert "Intl.supportedValuesOf" in script
    assert 'api("/api/v1/account/profile")' in script
    assert "zonedDateTimeToMillis" in script
    assert "formatToParts(new Date(timestamp))" in script
    assert "new Date(values.start" not in script


def test_event_editor_preserves_work_on_conflict_and_access_loss() -> None:
    page = source("event_editor.html")
    script = source("event_editor.js")

    assert "Your access to this event changed. Your unsaved edits are shown below" in page
    assert 'id="conflict-panel"' in page
    assert "Keep mine" in script
    assert "Use latest" in script
    assert "state.baseline" in script
    assert "state.latest" in script
    assert "setReadOnly()" in script
    assert 'window.removeEventListener("beforeunload", beforeUnload)' in script


def test_event_editor_uses_approved_lifecycle_copy_and_dirty_rules() -> None:
    page = source("event_editor.html")
    script = source("event_editor.js")

    assert (
        "The event becomes eligible to publish its call for proposals and schedule. "
        "Each is published separately."
        in script
    )
    assert (
        "It disappears from active and public listings. A published call for proposals "
        "and public schedule go offline"
        in page
    )
    assert "Save your changes before archiving." in script
    assert "Save your changes before restoring." in script
    assert 'state.dirty ? "Save and activate" : "Activate event"' in script


def test_event_editor_uploads_are_dirty_until_the_event_is_saved() -> None:
    script = source("event_editor.js")

    assert "/event-assets/${kind}" in script
    assert "Upload complete · Save changes to use this image." in script
    assert "Not saved — you no longer have access to save this event" in script
    assert "state.unsavedUploads.add(kind)" in script

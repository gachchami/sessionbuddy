from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_home_uses_an_operational_event_management_layout() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")
    styles = (STATIC / "admin_home.css").read_text(encoding="utf-8")

    assert 'class="organizer-home-filters"' in page
    assert page.index('data-event-filter="all"') < page.index('data-event-filter="active"')
    assert 'data-event-filter="all" aria-pressed="true"' in page
    assert 'id="event-search" name="q" type="search"' in page
    assert 'id="event-sort"' in page
    assert '<option value="upcoming">Upcoming first</option>' in page
    assert 'class="organizer-home-table" role="table" aria-label="Events"' in page
    assert 'class="organizer-home-table__body" role="rowgroup"' in page
    assert 'row.className = "organizer-home-event-row"' in script
    assert "const requestId = cursor ? state.eventsRequestId : ++state.eventsRequestId" in script
    assert "if (requestId !== state.eventsRequestId) return false" in script
    assert 'params.set("order", state.order)' in script
    assert "event.proposal_count" in script
    assert "name.href = `/admin/events/${encodeURIComponent(event.id)}`" in script
    assert "settings.href = `/admin/events/${encodeURIComponent(event.id)}/settings`" in script
    assert "duplicate.href = `/admin/events/new?source=${encodeURIComponent(event.id)}`" in script
    assert 'textContent = "Open"' not in script
    assert ".organizer-home-table__header" in styles
    assert ".organizer-home-event-actions" in styles


def test_event_editor_keeps_runtime_failures_with_the_form() -> None:
    page = (STATIC / "event_editor.html").read_text(encoding="utf-8")
    script = (STATIC / "event_editor.js").read_text(encoding="utf-8")

    assert 'id="editor-status"' in page
    assert "function setStatus" in script
    assert 'form.setAttribute("aria-busy", String(busy))' in script
    assert "Someone else saved this event while you were editing." in script
    assert "error.status === 401" in script
    assert "preserveDraft()" in script
    assert "savedDraft()" in script
    assert "redirectIfSignedOut(error)" in script
    assert 'headers["idempotency-key"] = state.mutation.key' in script
    assert "crypto.getRandomValues(new Uint8Array(32))" in script
    assert 'id="email-default"' in page
    assert "state.session.default_email_sender_name" in script
    assert "state.session.default_email_address" in script
    assert 'id="conflict-panel"' in page
    assert 'mine.textContent = "Keep mine"' in script
    assert 'latest.textContent = "Use latest"' in script
    assert "event-location-map" not in page
    assert "google.com/maps" not in script


def test_event_editor_uses_the_operate_layout_and_lifecycle_controls() -> None:
    page = (STATIC / "event_editor.html").read_text(encoding="utf-8")
    script = (STATIC / "event_editor.js").read_text(encoding="utf-8")
    styles = (STATIC / "event_editor.css").read_text(encoding="utf-8")

    assert 'class="event-editor__layout"' in page
    assert 'class="event-editor__main"' in page
    assert 'class="event-editor__support"' in page
    assert 'class="event-editor__date-grid"' in page
    assert page.count("<fieldset>") == 2
    assert ">Branding</strong>" in page
    assert 'id="activate-event"' in page
    assert 'id="archive-event"' in page
    assert 'id="restore-draft"' in page
    assert 'id="restore-active"' in page
    assert 'id="duplicate-event"' in page
    assert 'id="save-draft"' in page
    assert 'id="save-event"' in page
    assert (
        'byId("activate-event").textContent = state.dirty ? "Save and activate" : "Activate event"'
        in script
    )
    assert "Update the event dates before activating." in script
    assert 'saved.status === "archived" ? "Event archived."' in script
    assert ".event-editor__save-bar" in styles
    assert "position: sticky" in styles
    assert "grid-template-columns: minmax(0, 46rem) minmax(16rem, 22rem)" in styles


def test_event_branding_uploads_have_live_preview_and_save_boundary() -> None:
    page = (STATIC / "event_editor.html").read_text(encoding="utf-8")
    script = (STATIC / "event_editor.js").read_text(encoding="utf-8")
    styles = (STATIC / "event_editor.css").read_text(encoding="utf-8")

    for preview_id in ("logo-preview", "cover-preview", "logo-status", "cover-status"):
        assert f'id="{preview_id}"' in page
    assert 'id="upload-logo"' in page
    assert 'id="upload-cover"' in page
    assert "Recommended: 512 × 512 px (1:1)" in page
    assert "Recommended: 1600 × 600 px (8:3)" in page
    assert "function updateImages()" in script
    assert "state.unsavedUploads.add(kind)" in script
    assert '"Upload complete · Save changes to use this image."' in script
    assert "state.unsavedUploads.clear()" in script
    assert ".event-editor__cover-preview:not([hidden])" in styles
    assert "aspect-ratio: 1" in styles
    assert "aspect-ratio: 8 / 3" in styles
    assert "inline-size: 6rem" in styles


def test_home_is_the_event_index_and_editor_is_the_management_destination() -> None:
    home = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    editor = (STATIC / "event_editor.html").read_text(encoding="utf-8")

    assert 'class="organizer-home-table" role="table" aria-label="Events"' in home
    assert 'href="/admin/events/new"' in home
    assert 'id="event-editor-form"' in editor
    assert 'id="lifecycle"' in editor
    assert not (STATIC / "events_admin.html").exists()
    assert not (STATIC / "events_admin.js").exists()

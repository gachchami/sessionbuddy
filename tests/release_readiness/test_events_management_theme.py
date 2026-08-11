from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_events_page_uses_an_operational_management_layout() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "events_admin.js").read_text(encoding="utf-8")
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert 'class="event-filter-tabs"' in page
    assert page.index('data-event-filter="all"') < page.index('data-event-filter="active"')
    assert 'data-event-filter="all" aria-pressed="true"' in page
    assert 'eventFilter: "all"' in script
    assert 'id="event-search" type="search"' in page
    assert 'id="event-sort"' in page
    assert '<option value="upcoming">Upcoming first</option>' in page
    assert 'class="event-table-header"' in page
    assert 'class="event-table-frame" role="table" aria-label="Events"' in page
    assert 'class="event-management-list" role="rowgroup"' in page
    assert 'item.className = "event-table-row"' in script
    assert 'monogram.className = "event-monogram"' in script
    assert "function visibleEvents()" in script
    assert "function renderEventList()" in script
    assert "const requestId = ++state.eventsRequestId" in script
    assert "if (requestId !== state.eventsRequestId) return false" in script
    assert "const requestId = state.eventsRequestId" in script
    assert "if (requestId !== state.eventsRequestId) return;" in script
    assert "++state.eventsRequestId" in script
    assert 'params.set("order", state.eventOrder)' in script
    assert "event.proposal_count" in script
    assert "/duplicate`" in script
    assert 'name="duplicate_source_event_id"' in page
    assert 'name="duplicate_source_version"' in page
    assert "body.source_version = Number(values.duplicate_source_version)" in script
    assert 'name="retain_source_logo"' in page
    assert 'name="retain_source_cover"' in page
    assert "function duplicateEvent(event)" in script
    assert 'id="save-event-draft"' in page
    assert "Save a private draft, or create the event as active." in page
    assert ">Create active event</button>" in page
    assert 'if (!eventId) body.status = createStatus' in script
    assert 'event.submitter?.value === "draft"' in script
    assert 'state.editingDraft = event.status === "draft"' in script
    assert "body.status = intendedStatus" in script
    assert 'state.editingDraft\n        ? values.status === "archived"' in script
    assert ': values.status || state.events.get(eventId)?.status\n      : createStatus' in script
    assert 'byId("event-status-label").hidden = false' in script
    assert 'values.status === "archived"' in script
    assert '"Event archived."' in script
    assert '"Event activated."' in script
    assert 'intendedStatus === "active" && endsAt <= Date.now()' in script
    assert "Update the event dates before activating." in script
    assert ".event-table-header, .event-table-row" in styles
    assert ".event-table-actions" in styles
    assert '`/admin/events/${encodeURIComponent(event.id)}/cfp`' in script
    assert 'cfp.classList.add("event-action--cfp")' in script
    assert 'more.className = "event-row-more"' in script
    assert 'link("Manage CFP", `/admin/events/${encodeURIComponent(event.id)}/cfp`)' in script
    assert ".event-row-more__menu" in styles


def test_event_dialog_keeps_runtime_failures_with_the_form() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "events_admin.js").read_text(encoding="utf-8")

    assert 'id="event-dialog-status"' in page
    assert "function setDialogStatus" in script
    assert "if (state.submitting) return;" in script
    assert "This event changed elsewhere." in script
    assert 'error.status === 401' in script
    assert "preserveEventDraft(form)" in script
    assert "restoreEventDraft()" in script
    assert "redirectIfSignedOut(error)" in script
    assert 'headers["idempotency-key"] = state.createMutation.key' in script
    assert "crypto.getRandomValues(new Uint8Array(32))" in script
    assert 'id="event-email-default"' in page
    assert "session.default_email_sender_name" in script
    assert "session.default_email_address" in script
    assert "event-location-map" not in page
    assert "google.com/maps" not in script


def test_event_dialog_uses_the_organizer_run_sheet_layout() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert 'aria-describedby="event-form-help event-dialog-status"' in page
    assert 'class="event-form-layout"' in page
    assert 'class="event-form-primary"' in page
    assert 'class="event-form-secondary"' in page
    assert 'class="event-date-grid"' in page
    assert page.count('class="event-date-group"') == 2
    assert ">Branding</strong>" in page
    assert ".organizer-dialog--event" in styles
    assert "grid-template-columns: minmax(0, 1.5fr)" in styles
    assert "height: min(100dvh, 100%)" in styles


def test_event_branding_composes_a_live_public_page_preview() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "events_admin.js").read_text(encoding="utf-8")
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert 'class="public-brand-preview"' in page
    for preview_id in (
        "public-brand-preview-card",
        "public-brand-preview-cover",
        "public-brand-preview-logo",
        "public-brand-preview-title",
        "public-brand-preview-date",
        "public-brand-preview-location",
        "public-brand-preview-website",
    ):
        assert f'id="{preview_id}"' in page
    assert "function updatePublicBrandPreview()" in script
    assert 'setProperty("--event-preview-accent", accent)' in script
    assert 'setPublicPreviewImage("logo", source)' in script
    assert 'setPublicPreviewImage("cover", source)' in script
    assert ".public-brand-preview__cover" in styles
    assert "aspect-ratio: 16 / 9" in styles
    assert "max-width: 12rem" in styles
    assert "max-height: 3rem" in styles
    assert "border-top: 4px solid var(--event-preview-accent" in styles


def test_home_and_events_use_distinct_event_list_surfaces() -> None:
    home = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    events = (STATIC / "events_admin.html").read_text(encoding="utf-8")

    assert 'aria-label="Organization destinations"' in home
    assert 'class="organizer-home-event-list"' not in home
    assert 'class="event-table-frame" role="table" aria-label="Events"' in events
    assert 'class="event-management-list" role="rowgroup"' in events

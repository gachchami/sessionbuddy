from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_speaker_directory_has_no_preload_fallback_action_flash() -> None:
    html = source("speaker_directory.html")
    javascript = source("speaker_directory.js")
    assert 'id="invite-speaker" type="button" hidden' in html
    assert "Choose event to invite" not in html
    assert "Choose event to invite" not in javascript
    assert 'byId("invite-speaker").hidden = false' in javascript
    assert 'byId("role-filter-field").hidden = true' in javascript
    assert "/invitations`" in javascript
    assert 'role" type="hidden" value="speaker"' in html
    assert 'id="import-speakers"' in html
    assert "speakerInvitationsFromCsv" in javascript


def test_invited_and_registered_speakers_are_eligible_for_custom_tasks() -> None:
    javascript = source("speaker_content.js")
    html = source("speaker_content.html")

    assert '["invited", "submitted", "accepted"].includes(item.selection_status)' in javascript
    assert "Choose one or more invited, registered, or accepted speakers." in html
    assert 'input.type = "checkbox"' in javascript
    assert 'input.name = "event_speaker_id"' in javascript
    assert "Choose at least one invited, registered, or accepted speaker." in javascript


def test_organizer_can_create_constrained_file_request_tasks() -> None:
    javascript = source("speaker_content.js")
    html = source("speaker_content.html")
    assert '<option value="headshot">Headshot upload</option>' in html
    assert '<option value="slides">Slides upload</option>' in html
    assert '<option value="supporting_document">Supporting document upload</option>' in html
    assert 'name="upload_enabled"' in html
    assert 'name="max_file_mb"' in html
    assert "allowed_content_types: rules?.types || []" in javascript
    assert "max_file_bytes: maxFileBytes" in javascript
    assert "uploadEnabled.disabled = !rules" in javascript
    assert "uploadEnabled.checked = Boolean(rules)" in javascript


def test_manual_speaker_invitation_collects_biography() -> None:
    html = source("speaker_directory.html")
    assert '<textarea name="biography"' in html


def test_speaker_participation_status_and_csv_duplicate_review_are_exposed() -> None:
    html = source("speaker_directory.html")
    javascript = source("speaker_directory.js")
    assert 'name="confirmation_status"' in html
    assert "Awaiting confirmation" in html
    assert "Confirmed" in html
    assert "Declined" in html
    assert 'id="speaker-import-duplicates"' in html
    assert "Review and confirm the possible duplicate identities" in javascript
    assert "Object.fromEntries(new FormData(form))" in javascript


def test_dashboard_reconciles_within_five_seconds_and_after_reconnect() -> None:
    javascript = source("admin_onboarding.js")
    assert "const REFRESH_MS = 5000" in javascript
    assert "setInterval(() => refresh(), REFRESH_MS)" in javascript
    assert 'addEventListener("online", () => refresh' in javascript
    assert 'addEventListener("pageshow"' in javascript
    assert 'addEventListener("visibilitychange"' in javascript
    assert "BroadcastChannel" in javascript
    # Push is an invalidation only: every hint returns to the snapshot loader.
    assert 'addEventListener("message", () => refresh())' in javascript
    assert 'sessionbuddy:onboarding-invalidated", () => refresh()' in javascript


def test_dashboard_has_actor_scoped_links_and_graceful_reminder_action() -> None:
    javascript = source("admin_onboarding.js")
    html = source("admin_onboarding.html")
    assert "/speaker-tasks/${encodeURIComponent(row.task_id)}/reminders" in javascript
    assert "`/speakers/${encodeURIComponent(row.person_id)}`" in javascript
    assert "Reminder delivery is not connected in this environment yet" in javascript
    assert 'scope="col">Action' in html
    assert 'id="status"' in html and 'aria-live="polite"' in html


def test_dashboard_all_tasks_filter_is_explicit_and_completed_rows_have_no_reminder() -> None:
    javascript = source("admin_onboarding.js")
    html = source("admin_onboarding.html")
    assert '<option value="all">All tasks</option>' in html
    assert 'params.set("state", filters.state)' in javascript
    assert '["open", "overdue", "due_soon"].includes(row.state)' in javascript


def test_portal_covers_safe_asset_scan_states_and_major_sections() -> None:
    javascript = source("speaker_portal.js")
    html = source("speaker_portal.html")
    for section in (
        'id="submissions"',
        'id="event-sections"',
        'id="portfolio-summary"',
    ):
        assert section in html
    # Tasks now live inside each generated event group rather than in one
    # page-level panel, so the renderer owns that contract.
    assert 'subHeading("Needs attention", outstanding.length)' in javascript
    assert 'completion.state === "rejected"' in javascript
    assert 'completion.state === "clean"' in javascript
    assert "rejected by the safety scan" in javascript
    assert "object_key" not in javascript
    assert "innerHTML" not in javascript
    assert 'url.hostname.endsWith(".r2.cloudflarestorage.com")' in javascript
    assert 'id="profile"' not in html
    assert 'id="welcome-name"' in html
    assert 'id="public-profile-link"' in html
    assert 'task.destination_path === "#profile"' in javascript
    assert 'action.href = "/account"' in javascript
    assert 'id="speaker-profile-tools"' not in html
    assert 'id="speaker-headshot-form"' not in html
    assert 'Completed (${completed.length})' in javascript
    assert html.count('name="version_comment"') == 0
    assert (
        "`/speaker/proposals/${encodeURIComponent(submission.form_slug)}"
        "/${encodeURIComponent(submission.id)}`"
    ) in javascript
    assert 'createUploadForm("slides", submission.id)' not in javascript
    assert 'createUploadForm("supporting_document", submission.id)' not in javascript
    assert "form.dataset.submissionId || null" in javascript
    assert "version_comment: versionComment" in javascript
    assert "Upload received. Retrying safety checks" in javascript
    assert "pendingCompletion.intentId" in javascript
    assert "File received. Safety checks are temporarily unavailable" in javascript
    assert "this file is not public or current yet" in javascript
    assert "You do not need to choose or upload the file again" in javascript
    assert 'id="empty-state"' in html
    assert "Your speaker workspace is ready" in html
    assert "You do not have any proposals yet" in html
    assert "Event organizers share each call for proposals directly" in html
    assert "Explore open calls" not in html
    assert 'id="saved-proposal-drafts"' in html
    assert 'api("/api/v1/speaker/proposal-drafts")' in javascript
    assert "renderProposalDrafts(drafts)" in javascript
    assert "connected events" not in html
    assert ': "No proposals yet.");' in javascript
    assert 'error.status === 404 && state.csrf' in javascript
    assert 'byId("empty-state").hidden = false' in javascript


def test_dashboard_and_portal_preserve_accessible_responsive_patterns() -> None:
    dashboard = source("admin_onboarding.html")
    portal = source("speaker_portal.html")
    dashboard_css = source("admin_onboarding.css")
    portal_css = source("speaker.css")
    for html in (dashboard, portal):
        assert 'class="skip-link"' in html
        assert '<main id="' in html
        assert 'aria-live="polite"' in html
    assert "@media (max-width: 48rem)" in dashboard_css
    assert "@media (max-width: 48rem)" in portal_css
    assert "prefers-reduced-motion" in dashboard_css
    assert "prefers-reduced-motion" in portal_css

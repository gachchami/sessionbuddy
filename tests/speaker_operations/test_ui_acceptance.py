from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


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
        'id="tasks"',
        'id="submissions"',
    ):
        assert section in html
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
    assert 'return "/account"' in javascript
    assert 'Completed (${completed.length})' in javascript
    assert html.count('name="version_comment"') == 0
    assert 'createUploadForm("slides", submission.id)' in javascript
    assert 'createUploadForm("supporting_document", submission.id)' in javascript
    assert "form.dataset.submissionId || null" in javascript
    assert "version_comment: versionComment" in javascript


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

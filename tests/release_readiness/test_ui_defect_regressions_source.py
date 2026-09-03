"""Source-level wiring checks for user-facing defect fixes in the static pages.

These are `source` tests: they assert that the packaged page code carries the
guards and copy that the browser harness exercises, so a refactor cannot drop
them silently. They do not claim rendered behavior.
"""

from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "sessionbuddy" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_source_api_client_exposes_authored_user_errors() -> None:
    javascript = read("api_client.js")
    assert 'function userError(text, options = {}) {' in javascript
    assert 'code: "user_message"' in javascript
    assert "    userError,\n" in javascript
    # Every page that authored an Error for the status region now routes it
    # through the shared helper so `message()` cannot replace it with a fallback.
    for name in (
        "admin_home.js",
        "organization_admin.js",
        "speaker_messages.js",
        "admin_submissions.js",
        "speaker_content.js",
        "speaker_directory.js",
    ):
        assert "window.SessionBuddyApi.userError(" in read(name), name


def test_source_app_shell_labels_organization_settings_and_calls() -> None:
    javascript = read("app_shell.js")
    organization_label = (
        'if (location.pathname === "/admin/organization") return "Organization settings";'
    )
    assert organization_label in javascript
    assert 'calls: "Calls for proposals"' in javascript


def test_source_event_editor_reports_truthful_save_state_and_counters() -> None:
    javascript = read("event_editor.js")
    assert 'state.mode === "edit" ? "All changes saved" : "Not saved yet"' in javascript
    assert "window.SessionBuddyApi.refreshCharacterCounters?.(form); }" in javascript
    handoff_helper = (
        "function handoff(message) { try { "
        'sessionStorage.setItem("sessionbuddy:handoff-status", message); } catch (_) {} }'
    )
    assert handoff_helper in javascript
    # Only the guarded helper may write the handoff banner.
    assert 'sessionStorage.setItem("sessionbuddy:handoff-status"' not in javascript.replace(
        handoff_helper, ""
    )


def test_source_public_schedule_guards_controls_and_filtered_empty_state() -> None:
    javascript = read("schedule.js")
    assert "if (!state.model) return;" in javascript
    assert '"No sessions match your search or filters."' in javascript
    assert "try { localStorage.setItem(storageKey" in javascript


def test_source_admin_home_ignores_superseded_refresh_failures() -> None:
    javascript = read("admin_home.js")
    assert "error.eventsRequestId = requestId;" in javascript
    assert "error.eventsRequestId !== state.eventsRequestId) return false;" in javascript


def test_source_status_regions_that_receive_focus_are_focusable() -> None:
    for name in ("speaker_directory.html", "admin_submissions.html"):
        page = read(name)
        status = page.split('id="status"', 1)[1].split(">", 1)[0]
        assert 'tabindex="-1"' in status, name


def test_source_mutating_forms_disable_their_submit_button() -> None:
    directory = read("speaker_directory.js")
    assert "if (saveButton) saveButton.disabled = true;" in directory
    assert "if (notesButton) notesButton.disabled = true;" in directory
    content = read("speaker_content.js")
    assert 'form.dataset.requestKey ||= idempotencyKey();' in content
    assert '"idempotency-key": form.dataset.requestKey' in content


def test_source_onboarding_background_poll_preserves_paging_and_focus() -> None:
    javascript = read("admin_onboarding.js")
    assert "refresh({ background: true })" in javascript
    assert 'byId("results-panel").contains(document.activeElement)' in javascript
    assert 'formatDate(row.last_activity_at_ms, "No activity yet")' in javascript


def test_source_organization_settings_never_render_an_empty_page() -> None:
    javascript = read("organization_admin.js")
    assert "Promise.allSettled(" in javascript
    assert "function markPlaceholdersFailed()" in javascript
    assert "function disarmTransfer(form)" in javascript
    assert "data-loading-placeholder" in read("organization_admin.html")


def test_source_agenda_uses_shared_error_copy_and_event_local_weeks() -> None:
    javascript = read("agenda.js")
    assert "status(error.message" not in javascript
    assert "function weekLabel(date)" in javascript
    assert "const local = new Date(`${eventDate(item.start_at_ms)}T00:00:00Z`);" in javascript


def test_source_setup_keeps_the_deployment_key_until_bootstrap_succeeds() -> None:
    javascript = read("setup.js")
    before, after = javascript.split("setupCompleted = true;", 1)
    assert 'form.elements.deployment_key.value = "";' not in before
    assert 'form.elements.deployment_key.value = "";' in after


def test_source_product_css_defines_every_variable_it_consumes() -> None:
    import re

    css = read("product.css")
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    # A `var()` with no fallback silently drops the declaration when the name
    # is undefined; references that carry a fallback are allowed to be scoped.
    consumed = set(re.findall(r"var\((--[a-z0-9-]+)\)", css))
    # Page-scoped variables are set by JS or page stylesheets, not product.css.
    external = {"--event-accent", "--event-preview-accent", "--sb-chrome-top", "--track-color"}
    missing = sorted(name for name in consumed - defined if name not in external)
    assert missing == [], missing


def test_source_navigation_paints_a_stable_frame_and_skeletons_before_data() -> None:
    shell = read("app_shell.js")
    assert "function rememberFrame(frame)" in shell
    assert 'frame: currentFrame || String(session.frame || "")' in shell
    assert '"sb-sidebar sb-sidebar--placeholder"' in shell
    for name in ("admin_home.html", "admin_submissions.html", "speaker_directory.html"):
        assert 'class="sb-skeleton sb-skeleton--title"' in read(name), name
    assert ".sb-skeleton::after" in read("product.css")


def test_source_pages_start_data_reads_alongside_the_session_check() -> None:
    home = read("admin_home.js")
    assert 'api("/api/v1/auth/session"),\n      api("/api/v1/admin/organizations")' in home
    for name, marker in (
        ("event_overview.js", "const eventPromise = api("),
        ("admin_submissions.js", "const submissionsPromise = api("),
        ("speaker_directory.js", "const organizationsPromise = profileScoped"),
        ("agenda.js", "const loading = load();"),
        ("speaker_messages.js", "const targetsPromise = api("),
        ("speaker_content.js", "const timeZonePromise = loadEventTimeZone();"),
        ("event_workspace.js", "const eventPromise = api("),
        ("admin_programs.js", "const earlyWorkspace = routedEventId"),
    ):
        javascript = read(name)
        assert marker in javascript, name
        # The data read is issued before the session response is awaited.
        assert javascript.index(marker) < javascript.index("await sessionPromise"), name


def test_source_event_workspace_sections_swap_in_place_with_lifecycle_hooks() -> None:
    shell = read("app_shell.js")
    assert "window.SessionBuddyPage = Object.freeze({" in shell
    assert "async function softNavigate(url, { push = true, consented = false } = {})" in shell
    push = 'history.pushState({ sessionbuddy: "workspace", sbIndex: historyIndex }, "", url.href)'
    assert push in shell
    # Unsaved work and unexpected responses always fall back to a document navigation.
    assert "function pageRefusesToLeave()" in shell
    assert "const abandon = () => {" in shell
    for name in (
        "event_overview.html", "admin_programs.html", "admin_submissions.html",
        "speaker_directory.html", "admin_onboarding.html", "speaker_content.html",
        "access_admin.html", "speaker_messages.html", "agenda_admin.html",
        "event_workspace.html", "event_editor.html",
    ):
        assert '<main id="main" data-sb-swappable' in read(name), name
    for name, marker in (
        (
            "admin_programs.js",
            "saveLocalDraft();\n    }\n    window.clearTimeout(state.availabilityTimer);",
        ),
        ("agenda.js", "clearTimeout(state.previewTimer);\n    state.previewToken += 1;"),
        ("admin_submissions.js", "onLeave(() => clearTimeout(previewTimer))"),
        ("speaker_messages.js", "onLeave(() => clearTimeout(compatibilityTimer))"),
    ):
        assert marker in read(name), name
    # Pages with global state release it through the page signal.
    onboarding = read("admin_onboarding.js")
    assert "{ signal: pageSignal }" in onboarding
    assert "window.SessionBuddyPage?.onLeave(() => {" in onboarding
    for name in ("event_editor.js", "admin_programs.js"):
        javascript = read(name)
        assert '"beforeunload"' in javascript, name
        assert "signal: window.SessionBuddyPage?.signal" in javascript, name
        assert 'addEventListener("sessionbuddy:navigate"' in javascript, name


def test_source_page_requests_end_with_their_page_and_drawer_keeps_brand() -> None:
    client = read("api_client.js")
    scoped = (
        "const pageSignal = behavior.persistent ? undefined : window.SessionBuddyPage?.signal;"
    )
    assert scoped in client
    cancelled = 'throw new ApiError("This request was cancelled.", { code: "aborted", cause });'
    assert cancelled in client
    shell = read("app_shell.js")
    # Shell-owned calls must survive a section swap.
    for path in (
        "/api/v1/session/logout",
        "/api/v1/session/active-role",
        "/api/v1/public/events",
        "/api/v1/setup/status",
    ):
        assert path in shell
    assert shell.count("{ persistent: true }") == 4
    assert "topbar.replaceChildren(menuButton, topbarBrand, crumb, account);" in shell
    stylesheet = read("app_shell.css")
    assert ".sb-topbar__brand { display: none;" in stylesheet
    assert ".sb-topbar__brand { display: inline-flex; }" in stylesheet

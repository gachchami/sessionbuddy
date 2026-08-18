from pathlib import Path

import pytest

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


def test_manual_invitation_refreshes_the_event_roster_before_reporting_success() -> None:
    javascript = source("speaker_directory.js")
    invite = javascript.index('byId("invite-speaker-form").addEventListener')
    refresh = javascript.index("await refreshEventScopedRoster()", invite)
    success = javascript.index("The roster is up to date.", refresh)
    assert invite < refresh < success


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
    account_html = source("account.html")
    assert '<option value="headshot">' not in html
    assert "Profile photo" in account_html
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
    assert "speakerImportRows = rows;" in javascript
    assert "import one row or skip the entire group" in javascript
    assert "Review import rows" in html
    assert 'JSON.stringify({ mode: "preview", rows: speakerImportRows })' in javascript
    assert 'JSON.stringify({ mode: "execute", rows })' in javascript
    assert '"idempotency-key": speakerImportBatchKey' in javascript
    assert "Import this row" in javascript
    assert "Import as a separate person" in javascript


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


def test_dashboard_queues_user_refreshes_and_marks_stale_results_inert() -> None:
    javascript = source("admin_onboarding.js")
    html = source("admin_onboarding.html")
    assert "requestedFilters: commitFilters ? filters : null" in javascript
    assert "panel.inert = pending" in javascript
    assert "state.committedFilters = filters" in javascript
    assert "query(filters, append ? state.cursor : null)" in javascript
    assert "if (commitFilters) showFilterRefreshPending(false)" in javascript
    assert 'id="filter-refresh-status"' in html
    assert html.index('id="filter-refresh-status"') < html.index('id="results-panel"')


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
    assert 'outstanding.length ? "Needs attention" : "Task history"' in javascript
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
    assert "renderTasks(tasks, event.time_zone, taskList, submissions)" in javascript
    assert 'outstanding.length ? "Needs attention" : "Task history"' in javascript
    assert html.count('name="version_comment"') == 0
    assert (
        "`/speaker/proposals/${encodeURIComponent(submission.form_slug)}"
        "/${encodeURIComponent(submission.id)}`"
    ) in javascript
    assert 'createUploadForm("slides", submission.id)' not in javascript
    assert 'createUploadForm("supporting_document", submission.id)' not in javascript
    assert "form.dataset.submissionId || null" in javascript
    assert "version_comment: versionComment" in javascript
    assert 'form.dataset.replacement = isReplacement ? "true" : "false"' in javascript
    assert "comment.required = isReplacement" not in javascript
    assert 'make("span", " Optional", "optional")' in javascript
    assert '"Optional for the first upload."' in javascript
    assert 'const versionComment = form.elements.version_comment.value.trim()' in javascript
    assert 'form.dataset.replacement === "true" && !enteredVersionComment' not in javascript
    assert "Upload received. Retrying safety checks" in javascript
    assert "pendingCompletion.intentId" in javascript
    assert "File received. Safety checks are temporarily unavailable" in javascript
    assert "this file is not public or current yet" in javascript
    assert "You do not need to choose or upload the file again" in javascript
    assert 'id="empty-state"' in html
    assert "Your speaker workspace is ready" not in html
    assert "No speaker events yet" in html
    assert "Browse open calls below" in html
    assert "Invitations awaiting your response" in html
    assert "Explore open calls" not in html
    assert 'id="saved-proposal-drafts"' in html
    assert 'api("/api/v1/speaker/proposal-drafts")' in javascript
    assert "renderProposalDrafts(drafts)" in javascript
    assert "connected events" not in html
    assert "No proposals are connected to this account yet." in javascript
    assert "error.status === 404 && state.csrf" in javascript
    assert 'byId("empty-state").hidden = false' in javascript


def test_organizer_headshot_download_is_not_exposed_as_a_disabled_control() -> None:
    javascript = source("speaker_content.js")
    function = javascript.split("async function downloadProfileHeadshot", 1)[1].split(
        "async function loadAssets", 1
    )[0]
    assert "button.disabled = true" not in function
    assert 'button.dataset.busy = "true"' in function
    assert 'button.textContent = "Downloading headshot…"' in function


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


def test_bulk_reminders_require_confirmation_and_are_safe_to_retry() -> None:
    html = source("admin_onboarding.html")
    javascript = source("admin_onboarding.js")
    assert 'id="confirm-bulk-reminders"' in html
    assert "Remind loaded outstanding" in html
    assert "at most one reminder per UTC day" in html
    assert 'byId("confirm-bulk-reminders").showModal()' in javascript
    assert "reminderKey(row.task_id)" in javascript
    assert "Not queued:" in javascript
    assert "crypto.randomUUID" not in javascript


def test_replacement_upload_form_is_reachable_without_a_second_disclosure() -> None:
    """The version-2 form must not be buried behind two collapsed disclosures.

    It used to be a <details> nested inside the file-entry <details>, appended
    after the whole version history, which put its submit button last on the
    longest page in the portal.
    """
    javascript = source("speaker_portal.js")
    assert '"summary", "Upload new version"' not in javascript
    assert 'const replace = make("section", undefined, "asset-replace")' in javascript
    assert (
        'replace.setAttribute("aria-label", `Upload a new version of ${asset.filename}`)'
        in javascript
    )
    # The form is appended to the card before the version history, never after.
    replace_at = javascript.index("details.append(replace)")
    versions_at = javascript.index("details.append(versions)", replace_at - 4000)
    assert replace_at < versions_at


def test_replacement_upload_form_carries_a_stable_asset_scoped_identity() -> None:
    javascript = source("speaker_portal.js")
    assert (
        "function createUploadForm(kind, submissionId, rules, task = null, "
        "isReplacement = false, assetId = null, eventId = null)" in javascript
    )
    assert "form.id = `asset-upload-form-${assetId}`" in javascript
    assert "form.dataset.assetId = assetId" in javascript
    assert (
        "createUploadForm(asset.kind, asset.submission_id, rules, null, true, asset.id, eventId)"
        in javascript
    )


def test_portal_only_renders_upload_controls_for_actionable_server_rules() -> None:
    javascript = source("speaker_portal.js")
    assert "function uploadContract(value)" in javascript
    assert "const rules = uploadContract(task.upload_rules)" in javascript
    assert "if (rules)" in javascript
    assert "This upload request isn’t configured. Ask an organizer to update it." in javascript
    assert "const uploadRules" not in javascript


def test_repeat_task_assignment_replays_instead_of_duplicating() -> None:
    javascript = source("speaker_content.js")
    # Clearing the mutation state on success minted fresh idempotency keys for
    # an identical resubmit, and the server dedupes by key alone.
    assert "state.taskMutation = null" not in javascript
    assert "state.taskMutation.submitted = true" in javascript
    assert "no duplicate was created" in javascript


def test_dynamic_workspaces_do_not_animate_programmatic_scrolling() -> None:
    css = source("product.css")
    assert "scroll-behavior: smooth" not in css


def test_embedded_console_assets_match_their_static_sources() -> None:
    """The Worker serves console/embedded_assets.py, not static/.

    An edit to static/ that is not re-embedded ships nothing, so keep the two
    in lockstep: run `python scripts/embed_console_assets.py` after editing.
    """
    import importlib.util

    # Loaded by path: importing sessionbuddy.console pulls in the router (and
    # FastAPI) for what is a pure-data module.
    spec = importlib.util.spec_from_file_location(
        "_embedded_assets_under_test",
        ROOT / "src" / "sessionbuddy" / "console" / "embedded_assets.py",
    )
    assert spec and spec.loader
    embedded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(embedded)
    stale = [
        name
        for name, constant in embedded.ASSETS.items()
        if getattr(embedded, constant) != source(name)
    ]
    assert not stale, f"re-run scripts/embed_console_assets.py for: {', '.join(stale)}"


def _baseline_sql() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "migrations_baseline").glob("*.sql"))
    )


def test_speaker_task_dedup_indexes_are_open_scoped_and_discriminated() -> None:
    sql = _baseline_sql()
    assert "content_fingerprint BLOB" in sql
    for name in (
        "uq_speaker_tasks_open_system_identity",
        "uq_speaker_tasks_open_system_slides",
        "uq_speaker_tasks_open_content",
    ):
        clause = sql[sql.index(f"CREATE UNIQUE INDEX {name}") :].split(";", 1)[0]
        assert "state='open'" in clause
        expected = "IS NOT NULL" if name.endswith("content") else "IS NULL"
        assert f"content_fingerprint {expected}" in clause


def test_speaker_task_dedup_indexes_enforce_system_and_organizer_identity() -> None:
    """Exercise the real index definitions against SQLite.

    Uses a replica of the columns they key on: the point under test is the
    index semantics, not speaker_tasks' foreign keys.
    """
    import re
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.execute(
        """CREATE TABLE speaker_tasks(
             id TEXT PRIMARY KEY, organization_id TEXT, event_id TEXT,
             event_speaker_id TEXT, submission_id TEXT, task_type TEXT,
             state TEXT, content_fingerprint BLOB)"""
    )
    statements = re.findall(
        r"CREATE UNIQUE INDEX uq_speaker_tasks_open_\w+.*?;", _baseline_sql(), re.S
    )
    assert len(statements) == 3
    for statement in statements:
        connection.execute(statement)

    def add(task_id, speaker, submission, task_type, state="open", fingerprint=None):
        connection.execute(
            "INSERT INTO speaker_tasks VALUES(?,'org','event',?,?,?,?,?)",
            (task_id, speaker, submission, task_type, state, fingerprint),
        )

    add("profile-a", "sp1", "sub1", "profile")
    with pytest.raises(sqlite3.IntegrityError):
        add("profile-b", "sp1", "sub2", "profile")
    add("slides-a", "sp1", "sub1", "slides")
    add("slides-b", "sp1", "sub2", "slides")
    with pytest.raises(sqlite3.IntegrityError):
        add("slides-duplicate", "sp1", "sub1", "slides")

    # Identical organizer submissions collide on their content fingerprint,
    # while a manual headshot is outside the system-task index.
    add("manual-headshot", "sp1", None, "headshot", fingerprint=b"headshot-request")
    add("t9", "sp1", None, "custom", fingerprint=b"fingerprint-a")
    with pytest.raises(sqlite3.IntegrityError):
        add("t10", "sp1", None, "custom", fingerprint=b"fingerprint-a")
    add("t11", "sp1", None, "custom", fingerprint=b"fingerprint-b")


def test_source_wiring_organizer_task_creation_matches_on_request_content() -> None:
    router = (ROOT / "src" / "sessionbuddy" / "competition" / "router.py").read_text(
        encoding="utf-8"
    )
    assert "AND content_fingerprint=?3 LIMIT 1" in router
    assert "return AdminSpeakerTaskView.model_validate(duplicate)" in router
    assert "content_fingerprint)" in router


def test_acceptance_slides_guard_is_scoped_to_the_submission() -> None:
    helper = (
        ROOT / "src" / "sessionbuddy" / "speaker_operations" / "acceptance_tasks.py"
    ).read_text(encoding="utf-8")
    router = (ROOT / "src" / "sessionbuddy" / "evaluation" / "router.py").read_text(
        encoding="utf-8"
    )
    competition = (ROOT / "src" / "sessionbuddy" / "competition" / "router.py").read_text(
        encoding="utf-8"
    )
    flags = helper[helper.index("SPEAKER_TASK_FLAGS_SQL = ") :].split('"""')[1]
    assert flags.count("has_profile_task") == 1
    assert flags.count("has_headshot_task") == 1
    assert flags.count("has_slides_task") == 1
    assert "AND st.submission_id=s.id" in flags
    # One definition serves both decision paths, their additional participants,
    # and participation restoration without copying the task-existence clauses.
    assert router.count("SPEAKER_TASK_FLAGS_SQL.join(") == 3
    assert competition.count("SPEAKER_TASK_FLAGS_SQL.join(") == 1


def test_asset_comment_visibility_defaults_to_internal() -> None:
    sql = _baseline_sql()
    table = sql[sql.index("CREATE TABLE speaker_asset_comments") :].split(");", 1)[0]
    assert "visibility TEXT NOT NULL DEFAULT 'internal'" in table
    assert "CHECK (visibility IN ('internal', 'shared'))" in table


def test_source_wiring_speaker_comment_permission_is_defined_and_granted() -> None:
    types = (ROOT / "src" / "sessionbuddy" / "platform" / "authorization" / "types.py").read_text(
        encoding="utf-8"
    )
    policy = (ROOT / "src" / "sessionbuddy" / "platform" / "authorization" / "policy.py").read_text(
        encoding="utf-8"
    )
    assert 'SPEAKER_ASSET_COMMENT_OWN = "speaker.asset.comment_own"' in types
    speaker_block = policy[policy.index("SPEAKER_PERMISSIONS") :].split("}", 1)[0]
    assert "Permission.SPEAKER_ASSET_COMMENT_OWN," in speaker_block
    # The organizer-only comment permission must not leak into the speaker role.
    assert "Permission.SPEAKER_ASSET_COMMENT," not in speaker_block


def test_source_wiring_speaker_asset_endpoints_are_scoped_to_shared_comments() -> None:
    router = (ROOT / "src" / "sessionbuddy" / "speaker_operations" / "router.py").read_text(
        encoding="utf-8"
    )
    read = router[router.index("async def read_speaker_asset") :].split(
        "@speaker_operations_router", 1
    )[0]
    # Reads: only shared comments, and only this speaker's asset.
    assert "AND c.visibility='shared'" in read
    assert "AND a.event_speaker_id = ?3" in read
    write = router[router.index("async def create_speaker_asset_comment") :].split(
        "@speaker_operations_router", 1
    )[0]
    # Writes: always shared, never the organizers' private channel.
    assert "VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'shared',?9)" in write
    assert "Permission.SPEAKER_ASSET_COMMENT_OWN" in write
    # Ownership is proven in the same statement that resolves the version.
    assert "AND a.event_speaker_id=?5" in write
    # A reply may only attach to a comment on the same version.
    assert "AND version_id=?4 AND id=?5 AND visibility='shared'" in write


def test_comment_parent_must_belong_to_the_same_version() -> None:
    """The composite foreign key is what actually forbids cross-version replies.

    Asserting it at the database means no endpoint can forget to check.
    """
    import sqlite3

    sql = _baseline_sql()
    table = sql[sql.index("CREATE TABLE speaker_asset_comments") :]
    table = table[: table.index(");") + 2]
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    # Drop the FKs that point outside this table; the self-reference is the
    # constraint under test.
    trimmed = "\n".join(
        line
        for line in table.splitlines()
        if not line.strip().startswith(
            (
                "FOREIGN KEY (organization_id,event_id,asset_id)",
                "REFERENCES speaker_assets",
                "FOREIGN KEY (organization_id,event_id,asset_id,version_id)",
                "REFERENCES speaker_asset_versions",
                "FOREIGN KEY (author_user_id)",
                "REFERENCES users",
            )
        )
    )
    connection.execute(trimmed)
    # Immutability lives in a trigger, not a constraint, so bring it along.
    trigger = sql[sql.index("CREATE TRIGGER trg_speaker_asset_comments_immutable") :]
    connection.executescript(trigger[: trigger.index("END;") + 4])

    def add(comment_id, version_id, parent=None):
        connection.execute(
            """INSERT INTO speaker_asset_comments
               (id,organization_id,event_id,asset_id,version_id,author_user_id,
                parent_comment_id,body_text,visibility,created_at_ms)
               VALUES(?,'org','event','asset',?, 'user',?, 'text','shared',1)""",
            (comment_id, version_id, parent),
        )

    add("c1", "v1")
    add("c2", "v1", parent="c1")  # same version: allowed
    with pytest.raises(sqlite3.IntegrityError):
        add("c3", "v2", parent="c1")  # cross-version parent: rejected

    # Comments are append-only.
    with pytest.raises(sqlite3.DatabaseError):
        connection.execute("UPDATE speaker_asset_comments SET body_text='edited' WHERE id='c1'")


def test_speaker_portal_renders_a_shared_discussion_with_a_reply_form() -> None:
    javascript = source("speaker_portal.js")
    assert "async function assetDiscussion(asset, cache, eventId)" in javascript
    assert (
        "/assets/${encodeURIComponent(asset.id)}/versions/${encodeURIComponent(version.id)}/comments"
        in javascript
    )
    assert 'const discussion = make("details", undefined, "asset-discussion")' in javascript
    assert 'replyForm.setAttribute("aria-label"' in javascript
    assert "comment.version_id === version.id" in javascript
    assert 'parent.name = "parent_comment_id"' in javascript
    assert "parent_comment_id: parent.value || null" in javascript


def test_organizer_can_choose_a_comment_audience() -> None:
    """Without this control the shared thread can never be started.

    Every organizer comment defaults to 'internal', so the speaker-facing
    discussion stays empty until an organizer explicitly shares one.
    """
    javascript = source("speaker_content.js")
    assert '["internal", "Internal note (organizers only)"]' in javascript
    assert 'reply.textContent = "Reply"' in javascript
    assert "parent.value = comment.id" in javascript
    assert 'replyContext.textContent = parent.value ? `Replying to:' in javascript
    assert '["shared", "Shared with the speaker"]' in javascript
    assert "visibility: visibility.value" in javascript
    assert "comment.version_id === versionSelect.value" in javascript
    assert 'visibility.value !== "shared" || comment.visibility === "shared"' in javascript
    # Existing comments must show which audience they reached.
    assert 'comment.visibility === "shared" ? "Shared with speaker" : "Internal"' in javascript

def test_files_and_activity_render_without_an_outer_disclosure() -> None:
    """No hidden parent between the event section and a file card.

    The file list used to sit inside a "Files and activity" <details>. Its
    descendants stayed in the accessibility representation while it was closed,
    so a reference to an inner control resolved but the control was not
    actionable, and nothing named the parent that had to be opened first.
    """
    javascript = source("speaker_portal.js")
    css = source("speaker.css")
    assert "event-group__more" not in javascript
    assert "event-group__more" not in css
    assert '"span", "Files and activity"' not in javascript
    assert 'make("section", undefined, "event-group__block event-group__files")' in javascript
    assert 'make("section", undefined, "event-group__block event-group__activity")' in javascript
    # Both blocks attach to the event section itself, not to a wrapper.
    assert javascript.count("section.append(assetBlock)") == 1
    assert javascript.count("section.append(activityBlock)") == 1


def test_file_card_is_the_only_disclosure_before_its_discussion() -> None:
    """File -> Discussion is two levels; anything deeper reintroduces the bug."""
    javascript = source("speaker_portal.js")
    table = javascript[javascript.index("function assetTable(assets, eventId, portal)") :]
    table = table[: table.index("function activityTable")]
    # Exactly two disclosures inside a file card: the card, and each version's
    # discussion. The replacement upload form is a plain <section>.
    assert table.count('make("details"') == 2
    assert 'make("details", undefined, "asset-history-card")' in table
    assert 'make("details", undefined, "asset-discussion")' in table
    assert 'make("section", undefined, "asset-replace")' in table


def test_each_portfolio_event_loads_and_labels_its_own_assets() -> None:
    javascript = source("speaker_portal.js")
    load_portfolio = javascript[javascript.index("async function loadPortfolio()") :]
    load_portfolio = load_portfolio[: load_portfolio.index("async function selectEvent")]
    # Assets are valid for organizer-created sessions with zero CFP submissions.
    assert "await loadAssetsFor(eventId);" in load_portfolio
    assert "portal.submissions?.length" not in load_portfolio
    # Secondary-event metadata must not borrow the active event's identity or zone.
    table = javascript[javascript.index("function assetTable(assets, eventId, portal)") :]
    table = table[: table.index("function activityTable")]
    assert "portal?.profile?.display_name" in table
    assert "portal?.event?.time_zone" in table
    assert "state.portal?.profile" not in table
    assert "state.portal?.event" not in table


def test_blocked_submits_are_explained_in_the_dom() -> None:
    """A refused submit must say why, somewhere assistive tech can reach.

    The browser's constraint-validation bubble is in neither the DOM nor the
    accessibility tree, so a blocked submit used to look like a dead button -
    and the retry that followed is how duplicate records got created.
    """
    javascript = source("api_client.js")
    css = source("product.css")
    # The message lands in a real node, associated with its control.
    assert 'node.className = "field-error"' in javascript
    assert 'control.setAttribute("aria-errormessage", node.id)' in javascript
    assert "if (label) label.after(node);" in javascript
    assert "control.validationMessage" in javascript
    # One form-level announcement, and the first invalid control gets focus.
    assert 'node.setAttribute("role", "alert")' in javascript
    assert "announceFormErrors(event.target)" in javascript
    # A message inside a collapsed disclosure is no message at all.
    assert 'let box = control.closest("details");' in javascript
    assert "box.open = true;" in javascript
    # Cleared once the control becomes valid again.
    assert "clearFieldError(control);" in javascript
    assert ".field-error {" in css and ".form-error-summary {" in css


def test_form_summary_is_built_from_the_invalid_handler() -> None:
    """Native validation never dispatches `submit`.

    The browser fires `invalid` per control and stops. A summary built only
    inside a submit listener would never appear on the click that was actually
    blocked - which is the click that made the button look dead.
    """
    javascript = source("api_client.js")
    assert "scheduleSummary(control.form);" in javascript
    # One announcement per form per frame, not one per invalid control.
    assert "const summaryPending = new Set();" in javascript
    assert "requestAnimationFrame(() => {" in javascript
    # Reading the current errors must not call checkValidity(), which dispatches
    # `invalid` again and would schedule an endless series of announcements.
    invalid_controls = javascript[javascript.index("const invalidControls") :]
    invalid_controls = invalid_controls[: invalid_controls.index(";")]
    assert "validity.valid" in invalid_controls
    assert "checkValidity" not in invalid_controls


def test_validation_state_is_cleared_on_reset_and_on_correction() -> None:
    javascript = source("api_client.js")
    reset = javascript[javascript.index('document.addEventListener("reset"') :]
    reset = reset[: reset.index("}, true);")]
    # Reset must clear everything this module rendered, not just aria-invalid.
    assert 'control.setCustomValidity?.("")' in reset
    assert 'control.removeAttribute?.("aria-invalid")' in reset
    assert "clearFieldError(control);" in reset
    assert "summary.hidden = true;" in reset
    assert 'form.classList.remove("validation-attempted")' in reset
    # Correcting a field must not leave "this form was not submitted" on screen.
    assert "refreshFormSummary(control.form);" in javascript


def test_withdrawn_speaker_restore_is_explicit_and_auditable() -> None:
    html = source("speaker_directory.html")
    javascript = source("speaker_directory.js")
    assert 'id="restore-speaker"' in html
    assert "participation.lifecycle_status !== \"withdrawn\"" in javascript
    assert "/restore`" in javascript
    assert "reactivated_session_count" in javascript

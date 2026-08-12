import re
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


@pytest.mark.parametrize(
    ("html_name", "javascript_name"),
    (
        ("landing.html", "app_shell.js"),
        ("engine_room.html", "console.js"),
        ("setup.html", "setup.js"),
        ("sign_in.html", "sign_in.js"),
        ("admin_home.html", "admin_home.js"),
        ("events_admin.html", "events_admin.js"),
        ("event_overview.html", "event_overview.js"),
        ("speaker_directory.html", "speaker_directory.js"),
        ("speaker_messages.html", "speaker_messages.js"),
        ("account.html", "account.js"),
        ("admin_programs.html", "admin_programs.js"),
        ("public_cfp.html", "public_cfp.js"),
        ("admin_submissions.html", "admin_submissions.js"),
        ("access_admin.html", "access_admin.js"),
        ("admin_onboarding.html", "admin_onboarding.js"),
        ("event_workspace.html", "event_workspace.js"),
        ("speaker_content.html", "speaker_content.js"),
        ("agenda_admin.html", "agenda.js"),
        ("schedule.html", "schedule.js"),
        ("speaker_gallery.html", "speaker_gallery.js"),
        ("speaker_portal.html", "speaker_portal.js"),
    ),
)
def test_literal_javascript_element_references_are_wired(
    html_name: str, javascript_name: str
) -> None:
    html = (STATIC / html_name).read_text(encoding="utf-8")
    javascript = (STATIC / javascript_name).read_text(encoding="utf-8")
    declared = set(re.findall(r'\bid=["\']([^"\']+)', html))
    declared.update(re.findall(r'\.id\s*=\s*["\']([^"\']+)', javascript))
    referenced = set(re.findall(r'\bbyId\(["\']([^"\']+)', javascript))

    assert referenced <= declared, (
        f"{javascript_name} references missing IDs in {html_name}: "
        f"{sorted(referenced - declared)}"
    )


def test_every_api_driven_page_loads_the_shared_client_first() -> None:
    for html_path in STATIC.glob("*.html"):
        html = html_path.read_text(encoding="utf-8")
        if "<script" not in html or "auth_link_error" in html_path.name:
            continue
        api_client = html.find('/app-shell/assets/api-client.js')
        assert api_client >= 0, f"{html_path.name} does not load the shared API client"
        page_scripts = [match.start() for match in re.finditer(r"<script\s+src=", html)]
        api_client_script = html.rfind("<script", 0, api_client)
        assert not page_scripts or api_client_script == page_scripts[0]


def test_browser_api_parsing_is_centralized() -> None:
    source_files = [
        path
        for path in STATIC.rglob("*.js")
        if path.name != "api_client.js" and "app/assets" not in path.as_posix()
    ]
    source_files.append(STATIC.parents[2] / "frontend" / "src" / "main.tsx")
    violations: list[str] = []
    for path in source_files:
        source = path.read_text(encoding="utf-8")
        if re.search(r"fetch\(\s*[`\"']/api/", source) or ".json()" in source:
            violations.append(str(path.relative_to(STATIC.parents[2])))
    assert not violations, f"API calls bypass the shared response handler: {violations}"


def test_public_speaker_directory_and_gallery_have_distinct_depth() -> None:
    gallery = (STATIC / "speaker_gallery.js").read_text()
    assert 'dataset.layout = galleryLayout ? "gallery" : "directory"' in gallery
    assert 'byId("speaker-search").addEventListener("input", render)' in gallery
    assert 'byId("speaker-profile")' in gallery
    assert "speaker.links" in gallery


def test_custom_speaker_task_retries_reuse_idempotency_keys() -> None:
    script = (STATIC / "speaker_content.js").read_text()
    assert "taskMutation" in script
    assert "the same request will not be duplicated" in script


def test_admin_file_history_shows_comments_and_downloads_exact_versions() -> None:
    script = (STATIC / "speaker_content.js").read_text()
    assert "version.version_comment" in script
    assert "/versions/${encodeURIComponent(version.id)}/download-grants" in script
    assert "link.download = version.filename" in script
    assert "asset.uploaded_by" in script
    assert "asset.scan_status" in script
    assert "asset.preview_url" in script
    assert "asset.direct_download_url" in script
    assert "downloadProfileHeadshot" in script


def test_changed_speaker_workflows_bust_cached_assets() -> None:
    directory = (STATIC / "speaker_directory.html").read_text()
    content = (STATIC / "speaker_content.html").read_text()
    messages = (STATIC / "speaker_messages.html").read_text()
    assert "/admin/people/assets/people.js?v=4" in directory
    assert "/admin/speaker-content/assets/speaker-content.js?v=2" in content
    assert "/admin/speakers/assets/messages.js?v=5" in messages
    pages = (directory, content, messages)
    assert all("/product/assets/product.css?v=68" in page for page in pages)


def test_speaker_message_retries_reuse_idempotency_key() -> None:
    script = (STATIC / "speaker_messages.js").read_text()
    assert "messageMutation" in script
    assert "already queued recipients will not be duplicated" in script
    assert "Delivery was confirmed from message history" in script


def test_event_branding_uses_a_validated_logo_upload() -> None:
    page = (STATIC / "events_admin.html").read_text()
    script = (STATIC / "events_admin.js").read_text()
    assert "Logo URL" not in page
    assert 'name="logo_file" type="file"' in page
    assert 'accept="image/png,image/jpeg,image/webp"' in page
    assert "2 * 1024 * 1024" in script
    assert "/event-assets/${kind}`" in script
    assert 'uploadSelectedAsset("logo")' in script
    assert 'name="cover_file" type="file"' in page
    assert "16:9 · 1600 × 900 · 2 MB" in page
    assert 'uploadSelectedAsset("cover")' in script


def test_event_images_use_an_explicit_preview_then_upload_flow() -> None:
    page = (STATIC / "events_admin.html").read_text()
    script = (STATIC / "events_admin.js").read_text()

    for kind in ("logo", "cover"):
        section = page.split(
            f'aria-labelledby="event-{kind}-label">', 1
        )[1].split("</section>", 1)[0]
        assert 'class="image-upload__file"' in section
        assert f'id="event-{kind}-file"' in section
        assert f'aria-labelledby="event-{kind}-label"' in section
        assert f'id="event-{kind}-preview-frame"' in section
        assert f'id="event-{kind}-status"' in section
        assert re.search(
            rf'<button[^>]*id="upload-event-{kind}"[^>]*type="button"[^>]*disabled',
            section,
        )
        assert f'byId("upload-event-{kind}").addEventListener("click"' in script


def test_event_creation_waits_for_selected_image_uploads() -> None:
    script = (STATIC / "events_admin.js").read_text()
    upload_handler = script.split("async function uploadSelectedAsset(kind) {", 1)[1]
    submit_handler = script.split(
        'byId("event-form").addEventListener("submit", async (event) => {', 1
    )[1]

    # Selecting an optional image makes it pending; its explicit upload action
    # must clear that state before the event-creation request is allowed.
    assert ".value = uploaded.asset_url" in upload_handler
    assert upload_handler.index(".value = uploaded.asset_url") < upload_handler.index(
        'input.value = ""'
    )
    guard_at = submit_handler.index(
        "form.elements.logo_file.files[0] || form.elements.cover_file.files[0]"
    )
    create_at = submit_handler.index(
        "/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/events"
    )
    assert guard_at < create_at
    upload_guard_message = (
        'Upload the selected logo or cover before '
        '${eventId ? "saving changes" : "creating the event"}.'
    )
    assert upload_guard_message in submit_handler

    # Uploading as an implicit side effect after event creation recreates the
    # partial-success bug this flow is intended to prevent.
    create_request = submit_handler[create_at:]
    assert "uploadSelectedAsset(" not in create_request


@pytest.mark.parametrize(
    ("logo_url", "cover_image_url"),
    (
        ("https://assets.example.test/logo.png", ""),
        ("", "https://assets.example.test/cover.webp"),
        (
            "https://assets.example.test/logo.png",
            "https://assets.example.test/cover.webp",
        ),
        ("", ""),
    ),
    ids=("logo-only", "cover-only", "logo-and-cover", "no-branding"),
)
def test_event_create_payload_supports_every_branding_state(
    logo_url: str, cover_image_url: str
) -> None:
    page = (STATIC / "events_admin.html").read_text()
    script = (STATIC / "events_admin.js").read_text()
    submit_handler = script.split(
        'byId("event-form").addEventListener("submit", async (event) => {', 1
    )[1]
    body_source = submit_handler.split("const body = {", 1)[1].split("};", 1)[0]

    # The staged upload references live in hidden form controls, so FormData
    # includes them without exposing implementation URLs as editable fields.
    assert '<input name="logo_url" type="hidden">' in page
    assert '<input name="cover_image_url" type="hidden">' in page

    payload_fields = dict(
        re.findall(
            r"(logo_url|cover_image_url|website_url): values\.(\w+) \|\| null",
            body_source,
        )
    )
    assert payload_fields == {
        "logo_url": "logo_url",
        "cover_image_url": "cover_image_url",
        "website_url": "website_url",
    }

    form_values = {
        "logo_url": logo_url,
        "cover_image_url": cover_image_url,
        "website_url": "https://conference.example.test",
    }
    create_payload = {
        target: form_values[source] or None
        for target, source in payload_fields.items()
    }
    assert create_payload == {
        "logo_url": logo_url or None,
        "cover_image_url": cover_image_url or None,
        "website_url": "https://conference.example.test",
    }


def test_event_create_button_tracks_pending_branding_uploads() -> None:
    page = (STATIC / "events_admin.html").read_text()
    script = (STATIC / "events_admin.js").read_text()
    availability = script.split("function updateSaveAvailability() {", 1)[1].split(
        "\n  }", 1
    )[0]
    upload_handler = script.split("async function uploadSelectedAsset(kind) {", 1)[1]
    upload_success = upload_handler.split("try {", 1)[1].split("} catch", 1)[0]

    # With neither image selected, Create starts enabled. Choosing either file
    # makes it pending and disables Create through the shared state function.
    assert re.search(r'<button[^>]*id="save-event"(?![^>]*disabled)[^>]*>', page)
    assert "form.elements.logo_file.files[0]" in availability
    assert "form.elements.cover_file.files[0]" in availability
    assert 'byId("save-event").disabled = pending || state.submitting;' in availability

    logo_change = script.split(
        'elements.logo_file.addEventListener("change", (event) => {', 1
    )[1].split(
        'elements.cover_file.addEventListener("change", (event) => {', 1
    )[0]
    cover_change = script.split(
        'elements.cover_file.addEventListener("change", (event) => {', 1
    )[1].split("async function uploadEventAsset", 1)[0]
    assert "updateSaveAvailability();" in logo_change
    assert "updateSaveAvailability();" in cover_change

    # Successful staging first stores the durable reference, then clears the
    # selected file and recomputes availability, enabling Create again only
    # after the upload has completed.
    stored_at = upload_success.index(".value = uploaded.asset_url")
    cleared_at = upload_success.index('input.value = ""')
    refreshed_at = upload_success.index("updateSaveAvailability();")
    assert stored_at < cleared_at < refreshed_at


def test_event_branding_upload_cards_keep_controls_and_previews_in_flow() -> None:
    page = (STATIC / "events_admin.html").read_text()
    stylesheet = (STATIC / "product.css").read_text()

    for kind in ("logo", "cover"):
        card = page.split(
            f'aria-labelledby="event-{kind}-label">', 1
        )[1].split("</section>", 1)[0]
        assert card.index(f'id="event-{kind}-file"') < card.index(
            f'id="event-{kind}-preview-frame"'
        )
        assert card.index(f'id="upload-event-{kind}"') < card.index(
            f'id="event-{kind}-preview-frame"'
        )
        assert 'class="secondary image-upload__submit"' in card

    upload_card_rule = stylesheet.rsplit(".image-upload {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column" in upload_card_rule
    assert "align-self: start" in upload_card_rule
    assert ".image-upload__file::file-selector-button" in stylesheet
    preview_rule = stylesheet.split("\n.image-upload__preview {", 1)[1].split("}", 1)[0]
    assert "max-height:" in preview_rule
    assert "overflow: hidden" in preview_rule


def test_cfp_workspace_loads_directly_without_retry_workarounds() -> None:
    # The 404-retry loop papered over the missing single-event read endpoint;
    # both are gone now, so the workspace loads in one call.
    script = (STATIC / "admin_programs.js").read_text()
    loader = script.split("async function loadWorkspace(eventId) {", 1)[1].split(
        "\n  }", 1
    )[0]
    assert "setTimeout" not in loader
    assert "return api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/cfp`)" in loader
    restore = script.split("async function restoreSession() {", 1)[1]
    assert "const workspace = await loadWorkspace(eventId);" in restore
    assert "/api/v1/admin/events/${encodeURIComponent(workspace.event_id)}" in restore


def test_live_cfp_updates_submit_and_reload_confirmation_email_settings() -> None:
    script = (STATIC / "admin_programs.js").read_text()

    assert (
        "editor.elements.confirmation_subject.value = form.confirmation_subject"
        in script
    )
    assert "editor.elements.confirmation_body.value = form.confirmation_body" in script
    assert "payload.confirmation_subject = values.confirmation_subject" in script
    assert "payload.confirmation_body = values.confirmation_body" in script
    slug_update = script.split('byId("save-url-header").addEventListener', 1)[1]
    assert "confirmation_subject: current.confirmation_subject" in slug_update
    assert "confirmation_body: current.confirmation_body" in slug_update


def test_public_event_pages_render_cover_images() -> None:
    for page_name, script_name in (
        ("public_cfp.html", "public_cfp.js"),
        ("schedule.html", "schedule.js"),
        ("speaker_gallery.html", "speaker_gallery.js"),
    ):
        assert 'id="event-cover"' in (STATIC / page_name).read_text()
        assert "cover_image_url" in (STATIC / script_name).read_text()


def test_speaker_message_personalization_hides_template_syntax() -> None:
    page = (STATIC / "speaker_messages.html").read_text()
    assert "Merge fields:" not in page
    assert "speaker.first_name" not in page
    assert 'data-merge-field="{{speaker.name}}"' in page
    assert ">Speaker name</button>" in page
    assert ">Portal link</button>" in page
    assert "Each selected speaker will see their own details" in page


def test_public_schedule_export_reports_success() -> None:
    script = (STATIC / "schedule.js").read_text()
    assert "Downloaded ${selected.length} session" in script
    assert "weekday" in script
    assert "schedule-search" in script
    assert "sessionsOnly" in script
    assert "`${visible.length} of ${source.length} session" in script


def test_cfp_signed_in_email_help_is_not_duplicated() -> None:
    script = (STATIC / "public_cfp.js").read_text()
    assert 'let help = byId("signed-in-email-help")' in script

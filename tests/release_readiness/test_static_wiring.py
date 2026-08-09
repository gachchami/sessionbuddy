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
    script = (STATIC / "event_workspace.js").read_text()
    assert "taskMutation" in script
    assert "the same request will not be duplicated" in script


def test_admin_file_history_shows_comments_and_downloads_exact_versions() -> None:
    script = (STATIC / "event_workspace.js").read_text()
    assert "version.version_comment" in script
    assert "/versions/${encodeURIComponent(version.id)}/download-grants" in script
    assert "link.download = version.filename" in script


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
    assert "Recommended: 1600 × 900" in page
    assert 'uploadSelectedAsset("cover")' in script


def test_event_images_use_an_explicit_preview_then_upload_flow() -> None:
    page = (STATIC / "events_admin.html").read_text()
    script = (STATIC / "events_admin.js").read_text()

    for kind in ("logo", "cover"):
        section = page.split(
            f'<section class="image-upload" aria-labelledby="event-{kind}-label">', 1
        )[1].split("</section>", 1)[0]
        assert 'class="button secondary image-upload__button"' in section
        assert f'for="event-{kind}-file">Choose file</label>' in section
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
    assert "Upload the selected logo or cover before creating the event." in submit_handler

    # Uploading as an implicit side effect after event creation recreates the
    # partial-success bug this flow is intended to prevent.
    create_request = submit_handler[create_at:]
    assert "uploadSelectedAsset(" not in create_request


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

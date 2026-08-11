from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_cfp_working_copy_has_focused_preview_and_recoverable_validation() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'id="cfp-validation-summary"' in page
    assert 'id="preview-cfp"' in page
    assert 'id="cfp-selection-preview"' in page
    assert "Preview form" in page
    preview_toggle = (
        'byId("preview-cfp").textContent = opening ? '
        '"Back to editing" : "Preview form";'
    )
    assert preview_toggle in script
    visible_fields = (
        'const visibleFields = fields.filter((field) => '
        '!["speaker_name", "speaker_email"].includes(field.key));'
    )
    assert visible_fields in script
    assert 'id="cfp-live-preview"' not in page
    assert "queuePreview()" not in script
    assert "cfp-preview-dialog" not in page
    assert 'id="cfp-summary"' in page
    assert 'byId("cfp-summary").hidden = false;' in script
    assert 'id="cfp-share-dialog"' in page
    assert "state.editing = true;" in script
    assert "showValidation(errors)" in script
    assert 'details.open = true' in script
    assert 'classList.add("has-errors")' in script
    assert "Use a unique field key." in script
    assert "A question cannot depend on itself." in script
    assert "circular dependency" in script


def test_cfp_drafts_and_unsaved_live_edits_have_browser_recovery() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert "if (!state.context) return;" in script
    assert "form_version: state.publishedForm?.version ?? null" in script
    assert "Unsaved live changes backed up in this browser" in script
    assert "restoreLocalDraft();" in script
    assert "if (error.status === 401)" in script
    assert "saveLocalDraft();" in script
    assert "Update live CFP" in script
    assert "state.userId" in script


def test_published_cfp_edit_action_is_outside_the_closed_share_dialog() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")

    page_heading = page.split('id="main"', 1)[1].split('id="cfp-share-dialog"', 1)[0]
    dialog = page.split('id="cfp-share-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert 'id="edit-cfp"' in page_heading
    assert 'id="edit-cfp"' not in dialog


def test_cfp_rich_text_editor_is_named_and_link_dialog_is_keyboard_safe() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'id="cfp-description-label"' in page
    assert 'id="cfp-description-help"' in page
    assert 'aria-labelledby="cfp-description-label"' in page
    assert 'aria-describedby="cfp-description-help"' in page
    assert 'id="cfp-link-dialog"' in page
    assert 'aria-labelledby="cfp-link-dialog-title"' in page
    assert 'aria-describedby="cfp-link-dialog-help"' in page
    assert 'id="cfp-link-error"' in page
    assert 'role="alert"' in page
    assert "prompt(" not in script
    assert 'dialog.addEventListener("close"' in script
    assert "trigger?.focus()" in script
    assert '["https:", "http:"].includes(url.protocol)' in script
    assert 'link.href = url.href' in script
    assert "pendingLinkRange.surroundContents(link)" in script


def test_event_autosave_is_limited_to_new_or_draft_events() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    script = (STATIC / "events_admin.js").read_text(encoding="utf-8")

    assert 'id="event-autosave-state"' in page
    assert (
        "const editingActive = Boolean(form.elements.event_id.value) && !state.editingDraft;"
        in script
    )
    assert "Live event changes are not autosaved" in script
    assert "Draft saved in this browser" in script
    assert "state.userId" in script

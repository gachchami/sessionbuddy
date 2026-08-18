from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_source_wiring_cfp_working_copy_has_focused_preview_and_recoverable_validation() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'id="cfp-validation-summary"' in page
    assert 'id="preview-cfp"' in page
    assert 'id="cfp-selection-preview"' in page
    assert "Preview form" in page
    preview_toggle = (
        'byId("preview-cfp").textContent = state.previewOpen ? '
        '"Back to editing" : "Preview form";'
    )
    assert preview_toggle in script
    visible_fields = (
        "const visibleFields = fields.filter((field) => "
        "!identityFieldKeys.includes(field.key));"
    )
    assert visible_fields in script
    assert 'id="cfp-readiness"' in page
    assert "function queuePreview()" in script
    assert 'classList.toggle("is-preview-mode", state.previewOpen)' in script
    assert 'Applicant view · preview only' in page
    assert 'state.selectedOutline === "confirmation"' in script
    assert 'id="cfp-notification-settings"' not in page
    confirmation = page.split('id="cfp-confirmation"', 1)[1].split("</section>", 1)[0]
    assert 'name="confirmation_subject"' in confirmation
    assert 'name="confirmation_body"' in confirmation
    assert 'id="undo-field-move"' in page
    assert 'earlier.setAttribute("aria-label", `Move ${field.label} earlier`)' in script
    assert 'later.setAttribute("aria-label", `Move ${field.label} later`)' in script
    assert 'document.createElementNS("http://www.w3.org/2000/svg", "svg")' in script
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
    assert 'currentEvent.status === "draft"' in script
    assert "currentEvent.draft_delivery_mode" in script


def test_source_wiring_cfp_drafts_and_unsaved_live_edits_have_browser_recovery() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert "if (!state.context) return;" in script
    assert "form_version: state.publishedForm?.version ?? null" in script
    assert "Unsaved live changes backed up in this browser" in script
    assert "restoreLocalDraft();" in script
    assert "if (error.status === 401)" in script
    assert "saveLocalDraft();" in script
    assert "Update live CFP" in script
    assert "state.userId" in script


def test_source_wiring_published_cfp_edit_action_is_outside_the_closed_share_dialog() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")

    page_heading = page.split('id="main"', 1)[1].split('id="cfp-share-dialog"', 1)[0]
    dialog = page.split('id="cfp-share-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert 'id="edit-cfp"' in page_heading
    assert 'id="edit-cfp"' not in dialog


def test_source_wiring_cfp_rich_text_editor_is_named_and_link_dialog_is_keyboard_safe() -> None:
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


def test_source_wiring_event_editor_preserves_unsaved_work_only_for_recovery() -> None:
    page = (STATIC / "event_editor.html").read_text(encoding="utf-8")
    script = (STATIC / "event_editor.js").read_text(encoding="utf-8")

    assert 'id="save-state"' in page
    assert "function preserveDraft()" in script
    assert 'if (error.status === 401) { preserveDraft();' in script
    assert "preserveDraft(); window.removeEventListener" in script
    assert "function removeDraft()" in script
    assert "removeDraft(); state.unsavedUploads.clear();" in script
    assert "Draft saved in this browser" not in script

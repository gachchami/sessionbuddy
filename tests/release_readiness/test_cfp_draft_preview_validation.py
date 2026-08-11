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
    assert "Back to editing" in page
    assert 'const visibleFields = fields.filter((field) => !["speaker_name", "speaker_email"].includes(field.key));' in script
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


def test_only_unpublished_cfp_drafts_autosave() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert "if (state.publishedForm || !state.context) return;" in script
    assert "if (state.publishedForm) return;" in script
    assert "Live changes are never autosaved" in script
    assert "Update live CFP" in script
    assert "state.userId" in script


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

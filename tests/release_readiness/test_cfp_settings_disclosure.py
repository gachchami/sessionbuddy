from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_source_wiring_cfp_settings_use_an_always_visible_question_focused_editor() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")

    assert '<section id="publish-settings" class="cfp-builder workflow-stage"' in page
    assert 'id="cfp-form-outline" class="cfp-section-nav"' in page
    assert 'id="toggle-cfp-outline"' not in page
    assert 'id="cfp-outline-items"' in page
    assert "Preview form" in page
    for section in ("basics", "availability", "questions", "confirmation", "routing"):
        assert f'id="cfp-{section}"' in page
    assert "Proposal form settings" not in page
    assert ".cfp-editor-layout" in stylesheet
    assert ".cfp-section-nav.is-folded" not in stylesheet
    assert ".cfp-editor-actions { z-index: 3;" in stylesheet


def test_source_wiring_cfp_url_keeps_the_application_route_fixed() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'id="cfp-live-url-editor"' in page
    assert 'aria-label="Edit public URL slug"' in page
    assert 'name="slug" type="hidden"' in page
    assert "slug.value = readableSlug.slice(0, 80);" in script
    assert 'name="slug" type="url"' not in page


def test_source_wiring_published_cfp_opens_in_the_editor_and_returns_to_summary_after_save() -> (
    None
):
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'id="edit-cfp"' in page
    assert 'id="cancel-cfp-edit"' in page
    for detail in ("welcome", "opens", "closes", "limit", "questions", "fields"):
        assert f'id="cfp-summary-{detail}"' in page
    assert 'byId("publish-settings").hidden = Boolean(published) && !state.editing;' in script
    assert 'byId("cfp-summary").hidden = false;' in script
    assert 'id="cfp-share-dialog"' in page
    assert "state.editing = true;" in script
    assert "state.editing = false;" in script


def test_cfp_save_and_public_submission_have_clear_progress_and_completion() -> None:
    admin_page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    admin_script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")
    public_page = (STATIC / "public_cfp.html").read_text(encoding="utf-8")
    public_script = (STATIC / "public_cfp.js").read_text(encoding="utf-8")

    assert 'id="cfp-saved-state"' in admin_page
    assert '"Saving…"' in admin_script
    assert '"Saved ✓"' in admin_script
    assert '"Saved just now"' in admin_script
    assert 'id="receipt"' in public_page and 'tabindex="-1"' in public_page
    assert '"Submitting…"' in public_script
    assert '"Submitted ✓"' in public_script
    assert "receipt.focus({ preventScroll: true });" in public_script
    assert 'id="change-cfp-email"' not in public_page
    assert "form.hidden = true;" in public_script
    assert 'byId("call-details").hidden = true;' in public_script
    assert '"Submission confirmed"' in public_script


def test_source_wiring_published_cfp_does_not_report_phantom_unpublished_changes() -> None:
    # CFP-S1 eval (2026-08-21, observation 4): a clean, freshly loaded
    # published form said "Unpublished changes" and told the organizer nothing
    # changes publicly until they update the live CFP. They obeyed, the
    # editor collapsed to the summary on every save, and the outline tab they
    # wanted next read as "unclickable". The clean state has to say it is clean.
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert ': published ? "Unpublished changes" : "Draft";' not in script
    assert 'published ? (state.dirty ? "Unpublished changes" : "Live") : "Draft"' in script
    assert '"Your live form is up to date."' in script
    assert '"Live form matches the editor"' in script
    assert '"Unsaved live changes are backed up in this browser"' not in script

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_public_cfp_browser_draft_expires_and_requires_verified_owner() -> None:
    script = (ROOT / "src/sessionbuddy/static/public_cfp.js").read_text()

    assert "BROWSER_DRAFT_TTL_MS = 30 * 60 * 1000" in script
    assert "Date.now() - saved.savedAt > BROWSER_DRAFT_TTL_MS" in script
    assert "ownerEmail: normalizedEmail" in script
    assert "saved.ownerEmail !== normalizedEmail(expectedEmail)" in script
    assert "restoreBrowserDraft(state.sessionEmail)" in script
    assert "const saved = restoreBrowserDraft();" not in script
    assert "hasMeaningfulProposalAnswers(saved.answers)" in script
    assert "clearBrowserDraft();" in script


def test_public_cfp_autosaves_and_warns_about_unsaved_changes() -> None:
    script = (ROOT / "src/sessionbuddy/static/public_cfp.js").read_text()

    assert 'addEventListener("input", queueBrowserDraft)' in script
    assert 'addEventListener("change", queueBrowserDraft)' in script
    assert 'window.addEventListener("beforeunload"' in script
    assert "saveBrowserDraft(false)" in script
    assert "submissionId: state.editingSubmission?.id || null" in script
    assert "refreshCharacterCounters?." in script


def test_public_cfp_core_field_limits_match_the_api_contract() -> None:
    script = (ROOT / "src/sessionbuddy/static/public_cfp.js").read_text()

    assert 'field.key === "speaker_email" || field.type === "email"' in script
    assert '["speaker_name", "proposal_title"].includes(field.key)' in script
    assert "? 320" in script
    assert "? 200" in script
    assert "event.target.validationMessage" in script

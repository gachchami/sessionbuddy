from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_public_cfp_browser_draft_expires_and_requires_verified_owner() -> None:
    script = (ROOT / "src/sessionbuddy/static/public_cfp.js").read_text()

    assert "BROWSER_DRAFT_TTL_MS = 30 * 60 * 1000" in script
    assert "Date.now() - saved.savedAt > BROWSER_DRAFT_TTL_MS" in script
    assert "ownerEmail: normalizedEmail" in script
    assert "saved.ownerEmail !== normalizedEmail(expectedEmail)" in script
    assert "const restored = restoreBrowserDraft(state.sessionEmail)" in script
    assert "const saved = restoreBrowserDraft();" not in script

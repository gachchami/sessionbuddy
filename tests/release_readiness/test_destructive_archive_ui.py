from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_active_event_archive_requires_an_explicit_consequence_confirmation() -> None:
    page = source("event_editor.html")
    script = source("event_editor.js")

    assert 'id="archive-dialog"' in page
    assert "It disappears from active and public listings" in page
    assert "published call for proposals and public schedule go offline" in page
    assert 'status !== "active"' in script
    assert 'byId("archive-dialog").showModal()' in script
    assert 'save("archived", "Archiving…")' in script
    assert "Save your changes before archiving." in script


def test_agenda_archive_actions_confirm_consequences_and_offer_restore() -> None:
    page = source("agenda_admin.html")
    script = source("agenda.js")

    assert 'id="resource-archive-dialog"' in page
    assert "can be restored later" in page
    assert "openResourceArchive(kind, value)" in script
    assert 'openResourceArchive("label", label)' in script
    assert 'updateResource(kind, value, "active")' in script
    assert 'saveLabel(label, "active")' in script
    assert "archived_rooms" in script
    assert "archived_tracks" in script
    assert "archived_labels" in script


def test_embed_controls_are_honest_browser_local_preset_preferences() -> None:
    page = source("event_workspace.html")
    script = source("event_workspace.js")

    assert 'id="embed-enabled"' in page
    assert "Presets are stored in this browser" in page
    assert "public agenda availability still follows publication" in page
    assert "embed-enabled" in script
    assert 'preset.enabled ? "enabled" : "disabled"' in script
    assert "localStorage.setItem(`sessionbuddy:embeds:${eventId}`" in script

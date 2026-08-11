from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_cfp_routing_track_uses_existing_agenda_tracks_as_suggestions() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert '<datalist id="event-track-options"></datalist>' in page
    assert "/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/tracks" in script
    assert "state.eventTracks = (response.data || []).map" in script
    assert 'destination.setAttribute("list", "event-track-options")' in script
    assert 'destination.placeholder = isTrack ? "Choose a track"' in script


def test_cfp_builder_automatically_syncs_event_tracks_into_submission_questions() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert "function syncEventTrackField()" in script
    assert 'key: "track"' in script
    assert 'label: "Track"' in script
    assert 'required: true' in script
    assert 'choices: [...state.eventTracks]' in script
    assert 'if (!state.eventTracks.length)' in script
    assert "syncEventTrackField();" in script


def test_custom_question_flags_use_short_mobile_friendly_labels() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'make("span", "Required")' in script
    assert 'make("span", "Show in blind review")' in script
    assert "Speakers must answer this question" not in script


def test_cfp_builder_uses_the_opening_block_as_the_public_summary() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert "editor.innerText.split(/\\n+/)" in script
    assert "welcome_text.value = opening.slice(0, 1000)" in script


def test_cfp_track_routing_requires_an_active_event_track() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'track: destinationType === "track" ? destination : null' in script
    assert 'if (rule.track && !state.eventTracks.includes(rule.track))' in script
    assert 'setCustomValidity("Choose an active track from this event.")' in script

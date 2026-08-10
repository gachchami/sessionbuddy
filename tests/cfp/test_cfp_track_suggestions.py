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


def test_cfp_track_routing_requires_an_active_event_track() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'track: destinationType === "track" ? destination : null' in script
    assert 'if (rule.track && !state.eventTracks.includes(rule.track))' in script
    assert 'setCustomValidity("Choose an active track from this event.")' in script

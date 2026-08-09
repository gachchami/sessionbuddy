from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def test_general_speaker_directory_is_organization_wide_and_filterable() -> None:
    page = (STATIC / "speaker_directory.html").read_text(encoding="utf-8")
    script = (STATIC / "speaker_directory.js").read_text(encoding="utf-8")

    assert 'id="speaker-event-filter"' in page
    assert '<option value="">All events</option>' in page
    assert "One profile per person" in page
    assert "/organizations/${encodeURIComponent(organization.id)}/speakers" in script
    assert "speaker.participations || speaker.events || []" in script
    assert "function uniquePeople(items)" in script
    assert "item.person_id || item.user_id || item.email.toLowerCase()" in script
    assert "participation.event_id === eventId" in script
    assert 'participationList.className = "speaker-participations"' in script


def test_only_an_explicit_event_route_changes_the_directory_context() -> None:
    script = (STATIC / "speaker_directory.js").read_text(encoding="utf-8")

    assert "const eventScoped = Boolean(pathMatch);" in script
    assert "if (eventScoped && activeEvent)" in script
    assert 'byId("event-filter-field").hidden = true;' in script
    assert "if (!eventScoped)" in script
    assert "/api/v1/admin/events/${encodeURIComponent(event.id)}/speaker-targets" in script
    update_path = (
        "/api/v1/admin/events/"
        "${encodeURIComponent(selectedSpeaker.event.id)}/speakers/"
    )
    assert update_path in script


def test_each_reusable_profile_exposes_all_matching_event_participations() -> None:
    script = (STATIC / "speaker_directory.js").read_text(encoding="utf-8")

    assert "for (const participation of participations)" in script
    assert "eventLink.textContent = participation.event_name" in script
    assert "`${participation.selection_status} · ${participation.proposal_title}`" in script
    assert "known.has(part.event_speaker_id)" in script

from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def test_general_people_directory_is_platform_scoped_and_filterable() -> None:
    page = (STATIC / "speaker_directory.html").read_text(encoding="utf-8")
    script = (STATIC / "speaker_directory.js").read_text(encoding="utf-8")

    assert '<h1 id="page-title">People</h1>' in page
    assert 'id="people-search-field"' in page
    assert '<option value="all">All fields</option>' in page
    assert '<option value="first_name">First name</option>' in page
    assert '<option value="last_name">Last name</option>' in page
    assert '<option value="name">Full name</option>' in page
    assert '<option value="email">Email</option>' in page
    assert '<option value="company">Company</option>' in page
    assert 'id="people-organization-filter"' in page
    assert 'id="people-role-filter"' in page
    assert 'class="people-table-frame" role="table"' in page
    assert "Speaker profiles" not in page
    assert 'id="invite-speaker" type="button" hidden' in page
    assert "Choose event to invite" not in page
    assert "/organizations/${encodeURIComponent(organization.id)}/people" in script
    assert "speaker.participations || speaker.events || []" in script
    assert "function uniquePeople(items)" in script
    assert "item.person_id || item.user_id || item.email.toLowerCase()" in script
    assert "participation.event_id === eventId" in script
    assert 'row.className = "people-table-row"' in script
    assert 'window.SessionBuddyPeopleSearch.matches' in script


def test_only_an_explicit_event_route_changes_the_directory_context() -> None:
    script = (STATIC / "speaker_directory.js").read_text(encoding="utf-8")

    assert "const eventScoped = Boolean(pathMatch);" in script
    assert "if (eventScoped && activeEvent)" in script
    assert 'byId("event-filter-field").hidden = true;' in script
    assert 'byId("organization-filter-field").hidden = true;' in script
    assert 'byId("role-filter-field").hidden = true;' in script
    assert "/api/v1/admin/events/${encodeURIComponent(selectedEventId)}/speaker-targets" in script
    assert "[organizations, event, targetsResponse] = await Promise.all" in script
    update_path = (
        "/api/v1/admin/events/"
        "${encodeURIComponent(selectedSpeaker.event.id)}/speakers/"
    )
    assert update_path in script


def test_each_reusable_profile_exposes_all_matching_event_participations() -> None:
    script = (STATIC / "speaker_directory.js").read_text(encoding="utf-8")

    assert "profile.participations.map((participation)" in script
    assert "eventLink.textContent = participation.event_name" in script
    assert "`${participation.selection_status} · ${participation.proposal_title}`" in script
    assert "known.has(part.event_speaker_id)" in script


def test_speaker_cards_share_one_person_profile_route() -> None:
    page = (STATIC / "speaker_directory.html").read_text(encoding="utf-8")
    script = (STATIC / "speaker_directory.js").read_text(encoding="utf-8")

    assert 'id="speaker-profile-view"' in page
    assert "const profileMatch = location.pathname.match" in script
    assert "`/speakers/${encodeURIComponent(item.person_id)}`" in script
    assert "`/api/v1/speaker-profiles/${encodeURIComponent(selectedPersonId)}`" in script
    assert 'form.hidden = !profile.can_edit;' in script
    legacy_redirect = (
        "location.replace(`/speakers/"
        "${encodeURIComponent(selection.person.person_id)}`)"
    )
    assert legacy_redirect in script


def test_speaker_directory_reads_named_resource_permissions_not_deleted_roles() -> None:
    script = (STATIC / "speaker_directory.js").read_text(encoding="utf-8")

    assert "item.roles" not in script
    assert "item.permissions || []" in script
    assert '["owner", "edit", "manage"].includes(permission)' in script

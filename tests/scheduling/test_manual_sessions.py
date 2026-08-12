from tests.agenda.test_session_content_history import _admin
from tests.security.test_production_identity_flow import (
    _client,
    _token,
    production_environment,  # noqa: F401
)


async def test_organizer_creates_session_for_pending_invitee_without_activating_them(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _admin(client, connection)
        headers = {"origin": "https://test", "x-csrf-token": csrf}
        invited = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=headers,
            json={
                "email": "marcus@example.com",
                "role": "speaker",
                "display_name": "Marcus Okafor",
            },
        )
        assert invited.status_code == 201, invited.text
        invitation_id = invited.json()["id"]
        setup = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/setup",
            headers={**headers, "idempotency-key": "manual-session-agenda-setup"},
            json={"room_names": ["Main stage"], "track_names": []},
        )
        assert setup.status_code == 201, setup.text
        participant = next(
            item for item in setup.json()["session_participants"] if item["id"] == invitation_id
        )
        assert participant == {
            "id": invitation_id,
            "display_name": "Marcus Okafor",
            "recipient_state": "invited",
        }

        unnamed = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=headers,
            json={"email": "unnamed@example.com", "role": "speaker"},
        )
        assert unnamed.status_code == 201, unnamed.text
        unnamed_session = await client.post(
            f"/api/v1/admin/events/{event_id}/sessions",
            headers={**headers, "idempotency-key": "unnamed-session-snapshot"},
            json={
                "title": "Unnamed participant session",
                "abstract": "Privacy regression coverage.",
                "participant_ids": [unnamed.json()["id"]],
            },
        )
        assert unnamed_session.status_code == 201, unnamed_session.text
        unnamed_id = next(
            item["session_id"]
            for item in unnamed_session.json()["unscheduled_sessions"]
            if item["title"] == "Unnamed participant session"
        )
        snapshot = connection.execute(
            "SELECT display_name_snapshot FROM accepted_session_participants "
            "WHERE accepted_session_id=?",
            (unnamed_id,),
        ).fetchone()[0]
        assert snapshot == "Invited speaker"
        deleted_unnamed = await client.delete(
            f"/api/v1/admin/events/{event_id}/sessions/{unnamed_id}",
            headers={**headers, "content-type": "application/json"},
        )
        assert deleted_unnamed.status_code == 204

        created = await client.post(
            f"/api/v1/admin/events/{event_id}/sessions",
            headers={**headers, "idempotency-key": "manual-session-create-marcus"},
            json={
                "title": "DevFlow",
                "abstract": "A practical session created by the program team.",
                "participant_ids": [invitation_id],
            },
        )
        assert created.status_code == 201, created.text
        session = next(
            item for item in created.json()["unscheduled_sessions"] if item["title"] == "DevFlow"
        )
        assert session["speaker_names"] == "Marcus Okafor"
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM event_speakers WHERE event_id=?",
                (event_id,),
            ).fetchone()[0]
            == 0
        )
        stored = connection.execute(
            """SELECT source_type,submission_id,decision_id
                 FROM accepted_sessions WHERE id=?""",
            (session["session_id"],),
        ).fetchone()
        assert tuple(stored) == ("organizer_created", None, None)
        link = connection.execute(
            """SELECT event_speaker_id,pending_invitation_id
                 FROM accepted_session_participants WHERE accepted_session_id=?""",
            (session["session_id"],),
        ).fetchone()
        assert tuple(link) == (None, invitation_id)

        room_id = created.json()["rooms"][0]["id"]
        scheduled = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/items",
            headers={**headers, "idempotency-key": "manual-session-schedule-marcus"},
            json={
                "session_id": session["session_id"],
                "start_at_ms": 1_900_000_000_000,
                "end_at_ms": 1_900_003_600_000,
                "room_id": room_id,
            },
        )
        assert scheduled.status_code == 201, scheduled.text

        invited_second = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=headers,
            json={
                "email": "lena@example.com",
                "role": "speaker",
                "display_name": "Lena Ortiz",
            },
        )
        assert invited_second.status_code == 201, invited_second.text
        second_invitation_id = invited_second.json()["id"]

        second = await client.post(
            f"/api/v1/admin/events/{event_id}/sessions",
            headers={**headers, "idempotency-key": "manual-session-create-second"},
            json={
                "title": "DevFlow office hours",
                "abstract": "A second session with the same pending invitee.",
                "participant_ids": [invitation_id, second_invitation_id],
            },
        )
        assert second.status_code == 201, second.text
        second_session = next(
            item
            for item in second.json()["unscheduled_sessions"]
            if item["title"] == "DevFlow office hours"
        )
        preview = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/preview",
            headers=headers,
            json={
                "session_id": second_session["session_id"],
                "start_at_ms": 1_900_000_000_000,
                "end_at_ms": 1_900_003_600_000,
                "room_id": room_id,
            },
        )
        assert preview.status_code == 200, preview.text
        assert any(conflict["code"] == "speaker" for conflict in preview.json()["conflicts"])
        removed_participant = await client.delete(
            f"/api/v1/admin/events/{event_id}/sessions/{second_session['session_id']}"
            f"/participants/{second_invitation_id}",
            headers={**headers, "content-type": "application/json"},
        )
        assert removed_participant.status_code == 204, removed_participant.text
        removed = await client.delete(
            f"/api/v1/admin/events/{event_id}/sessions/{second_session['session_id']}",
            headers={**headers, "content-type": "application/json"},
        )
        assert removed.status_code == 204, removed.text
        removed_again = await client.delete(
            f"/api/v1/admin/events/{event_id}/sessions/{second_session['session_id']}",
            headers={**headers, "content-type": "application/json"},
        )
        assert removed_again.status_code == 204, removed_again.text

        edited = await client.patch(
            f"/api/v1/admin/events/{event_id}/sessions/{session['session_id']}/content",
            headers=headers,
            json={
                "title": "DevFlow live",
                "abstract": "Updated organizer-created session content.",
                "content_status": "approved",
                "version": 1,
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["title"] == "DevFlow live"

        published = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/publish",
            headers={**headers, "idempotency-key": "publish-manual-session"},
            json={
                "revision_id": created.json()["revision"]["id"],
                "version": created.json()["revision"]["version"],
            },
        )
        assert published.status_code == 200, published.text
        public = await client.get(f"/api/v1/public/events/{event_id}/schedule")
        assert public.status_code == 200, public.text
        serialized = public.text
        assert "marcus@example.com" not in serialized

        token = await client.post(
            f"/api/v1/admin/events/{event_id}/integrations/accelevents/tokens",
            headers={**headers, "idempotency-key": "manual-session-integration-token"},
            json={"label": "Manual session export"},
        )
        assert token.status_code == 201, token.text
        exported = await client.post(
            f"/v1/event/{event_id}/sessions",
            headers={"x-access-token": token.json()["token"]},
        )
        assert exported.status_code == 200, exported.text
        assert any(item["title"] == "DevFlow live" for item in exported.json()["results"])
        organizer_cookies = dict(client.cookies)

    async with _client(environment) as accepting_speaker:
        accepted = await accepting_speaker.post(
            "/auth/verify",
            data={"token": _token(connection, "marcus@example.com")},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
    transitioned = connection.execute(
        """SELECT participant.event_speaker_id,participant.pending_invitation_id,
                  participant.display_name_snapshot
             FROM accepted_session_participants participant
            WHERE participant.accepted_session_id=?""",
        (session["session_id"],),
    ).fetchone()
    assert transitioned[0] is not None
    assert transitioned[1] is None
    assert transitioned[2] == "Marcus Okafor"
    # Published revisions are immutable. Acceptance succeeds without trying to
    # mutate the published agenda; participant-based reads still link Marcus.
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM agenda_item_speakers WHERE agenda_item_id=?",
            (scheduled.json()["id"],),
        ).fetchone()[0]
        == 0
    )
    # The next draft was cloned before Marcus accepted, so its denormalized
    # speaker index has the same intentional gap. Auto-scheduling must still
    # detect him through the participant source of truth.
    async with _client(environment) as organizer:
        organizer.cookies.update(organizer_cookies)
        headers = {"origin": "https://test", "x-csrf-token": csrf}
        follow_up = await organizer.post(
            f"/api/v1/admin/events/{event_id}/sessions",
            headers={**headers, "idempotency-key": "manual-session-after-acceptance"},
            json={
                "title": "DevFlow follow-up",
                "abstract": "A second organizer-created session for Marcus.",
                "participant_ids": [transitioned[0]],
            },
        )
        assert follow_up.status_code == 201, follow_up.text
        preview_follow_up = await organizer.post(
            f"/api/v1/admin/events/{event_id}/agenda/preview",
            headers=headers,
            json={
                "session_id": next(
                    item["session_id"]
                    for item in follow_up.json()["unscheduled_sessions"]
                    if item["title"] == "DevFlow follow-up"
                ),
                "start_at_ms": 1_900_000_000_000,
                "end_at_ms": 1_900_003_600_000,
                "room_id": room_id,
            },
        )
        assert preview_follow_up.status_code == 200, preview_follow_up.text
        assert any(
            conflict["code"] == "speaker"
            for conflict in preview_follow_up.json()["conflicts"]
        )
        scheduled_follow_up = await organizer.post(
            f"/api/v1/admin/events/{event_id}/agenda/auto-schedule",
            headers={**headers, "idempotency-key": "auto-schedule-after-acceptance"},
            json={
                "start_at_ms": 1_900_000_000_000,
                "session_minutes": 60,
                "gap_minutes": 0,
                "room_ids": [room_id],
            },
        )
        assert scheduled_follow_up.status_code == 200, scheduled_follow_up.text
        follow_up_item = next(
            item
            for item in scheduled_follow_up.json()["items"]
            if item["title"] == "DevFlow follow-up"
        )
        assert follow_up_item["start_at_ms"] >= 1_900_003_600_000
    async with _client(environment) as public_client:
        gallery = await public_client.get(f"/api/v1/public/events/{event_id}/speakers")
        assert gallery.status_code == 200, gallery.text
        marcus = next(
            item for item in gallery.json()["data"] if item["display_name"] == "Marcus Okafor"
        )
        assert any(item["title"] == "DevFlow live" for item in marcus["sessions"])


def test_manual_session_controls_are_exposed_in_the_agenda_editor() -> None:
    from pathlib import Path

    static = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"
    html = (static / "agenda_admin.html").read_text(encoding="utf-8")
    javascript = (static / "agenda.js").read_text(encoding="utf-8")
    assert 'id="manual-session-form"' in html
    assert 'name="participant_ids" multiple' in html
    assert "invited, awaiting acceptance" in javascript
    assert "/sessions`" in javascript

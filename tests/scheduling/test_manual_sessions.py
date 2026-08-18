from sessionbuddy.platform.upload_contracts import task_form_schema_json
from tests.agenda.test_session_content_history import _admin
from tests.security.test_production_identity_flow import (
    _client,
    _token,
    production_environment,  # noqa: F401
)


class _AssetBucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, body: bytes) -> None:
        self.objects[key] = body


async def test_organizer_session_provisions_active_speaker_onboarding_once(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        headers = {"origin": "https://test", "x-csrf-token": csrf}
        connection.execute(
            """INSERT INTO people
               (id,organization_id,display_name,biography,created_at_ms,updated_at_ms)
               VALUES ('manual-person',?,'Priya Raman','Profile already complete',1000,1000)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,selection_status,
                accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
               VALUES ('manual-speaker',?,?,'manual-person','onboarding','accepted',
                       1000,1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.commit()
        setup = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/setup",
            headers={**headers, "idempotency-key": "active-onboarding-setup"},
            json={"room_names": ["Main stage"], "track_names": []},
        )
        assert setup.status_code == 201, setup.text

        for suffix in ("first", "second"):
            created = await client.post(
                f"/api/v1/admin/events/{event_id}/sessions",
                headers={**headers, "idempotency-key": f"active-session-{suffix}"},
                json={
                    "title": f"Organizer session {suffix}",
                    "abstract": "A session outside the CFP workflow.",
                    "participant_ids": ["manual-speaker"],
                },
            )
            assert created.status_code == 201, created.text

        tasks = connection.execute(
            """SELECT task_type,state,completed_at_ms,form_schema_json
                 FROM speaker_tasks
                WHERE organization_id=? AND event_id=? AND event_speaker_id='manual-speaker'
                ORDER BY task_type""",
            (organization_id, event_id),
        ).fetchall()
        assert [(row[0], row[1]) for row in tasks] == [
            ("headshot", "open"),
            ("profile", "completed"),
            ("slides", "open"),
        ]
        assert tasks[0][2] is None
        assert tasks[1][2] is not None
        assert tasks[2][2] is None
        assert tasks[0][3] == task_form_schema_json("headshot")
        assert tasks[1][3] == task_form_schema_json("profile")
        assert tasks[2][3] == task_form_schema_json("slides")


async def test_organizer_headshot_completes_active_speaker_task(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    environment.APP_ENV = "local"
    environment.MALWARE_SCAN_MODE = "disabled"
    environment.ASSETS = _AssetBucket()
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        organizer_user_id = connection.execute(
            "SELECT user_id FROM user_roles WHERE role='organizer' LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO people
               (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
               VALUES ('headshot-person',?,?,'Priya Raman',1000,1000)""",
            (organization_id, organizer_user_id),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,selection_status,
                accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
               VALUES ('headshot-speaker',?,?,'headshot-person','onboarding','accepted',
                       1000,1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO speaker_tasks
               (id,organization_id,event_id,event_speaker_id,task_type,title,
                state,created_at_ms,updated_at_ms,form_schema_json)
               VALUES ('headshot-task',?,?,'headshot-speaker','headshot','Upload headshot',
                       'open',1000,1000,?)""",
                (organization_id, event_id, task_form_schema_json("headshot")),
        )
        connection.commit()

        uploaded = await client.put(
            f"/api/v1/admin/events/{event_id}/speakers/headshot-speaker/headshot",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "content-type": "image/png",
            },
            content=b"\x89PNG\r\n\x1a\norganizer-headshot",
        )
        assert uploaded.status_code == 204, uploaded.text

    task = connection.execute(
        "SELECT state,completed_at_ms FROM speaker_tasks WHERE id='headshot-task'"
    ).fetchone()
    assert task[0] == "completed"
    assert task[1] is not None


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
        assert public.headers["cache-control"] == "no-store"
        assert public.status_code == 200, public.text
        serialized = public.text
        assert "marcus@example.com" not in serialized
        xml_feed = await client.get(f"/api/v1/public/events/{event_id}/schedule.xml")
        assert xml_feed.status_code == 200
        assert xml_feed.headers["cache-control"] == "no-store"
        assert "<schedule" in xml_feed.text and "DevFlow live" in xml_feed.text
        calendar_feed = await client.get(f"/api/v1/public/events/{event_id}/schedule.ics")
        assert calendar_feed.status_code == 200
        assert calendar_feed.headers["cache-control"] == "no-store"
        assert "BEGIN:VCALENDAR" in calendar_feed.text
        assert "SUMMARY:DevFlow live" in calendar_feed.text

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
        connection.execute(
            "UPDATE accepted_sessions SET lifecycle_status='withdrawn',withdrawn_at_ms=2000 "
            "WHERE id=?",
            (session["session_id"],),
        )
        connection.commit()
        withdrawn_gallery = await public_client.get(
            f"/api/v1/public/events/{event_id}/speakers"
        )
        assert withdrawn_gallery.status_code == 200, withdrawn_gallery.text
        assert all(
            item["display_name"] != "Marcus Okafor"
            for item in withdrawn_gallery.json()["data"]
        )


def test_source_wiring_manual_session_controls_are_exposed_in_the_agenda_editor() -> None:
    from pathlib import Path

    static = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"
    html = (static / "agenda_admin.html").read_text(encoding="utf-8")
    javascript = (static / "agenda.js").read_text(encoding="utf-8")
    assert 'id="manual-session-form"' in html
    assert 'name="participant_ids" multiple' in html
    assert "invited, awaiting acceptance" in javascript
    assert "/sessions`" in javascript

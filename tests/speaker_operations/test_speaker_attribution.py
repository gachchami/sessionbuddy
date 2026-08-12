"""Speaker ↔ submission attribution, driven over HTTP.

Pins the eval-run P0: every surface that shows "the speaker's proposal"
(speaker targets, the single speaker record, the onboarding dashboard, the
home recent-speakers cards) must show the ACCEPTED submission when one
exists — not whichever submission happens to be newest. In the eval run a
later-submitted rejected proposal displaced the accepted session on all of
these surfaces at once.
"""

import json
import sqlite3

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)

EVENT_PAYLOAD = {
    "name": "Attribution Summit",
    "starts_at_ms": 1_900_000_000_000,
    "ends_at_ms": 1_900_086_400_000,
    "time_zone": "Asia/Kolkata",
    "delivery_mode": "hybrid",
    "location": "Pune",
    "description": "Attribution checks",
    "email_sender_name": "Program Team",
    "email_reply_to": "program@example.com",
}


async def _admin(client, connection: sqlite3.Connection) -> tuple[str, str, str]:
    bootstrap = await client.post(
        "/api/v1/bootstrap",
        headers={"x-bootstrap-token": _deployment_key(connection)},
        json={
            "organization_name": "Attribution Events",
            "admin_name": "Admin",
            "admin_email": "admin@example.com",
        },
    )
    assert bootstrap.status_code == 200
    organization_id = bootstrap.json()["organization_id"]
    requested = await client.post(
        "/api/v1/auth/magic-links",
        json={"email": "admin@example.com", "redirect_path": "/admin"},
    )
    assert requested.status_code == 202
    verified = await client.post(
        "/auth/verify",
        data={"token": _token(connection, "admin@example.com")},
        follow_redirects=False,
    )
    assert verified.status_code == 303
    session = (await client.get("/api/v1/auth/session")).json()
    created = await client.post(
        f"/api/v1/admin/organizations/{organization_id}/events",
        headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
        json=EVENT_PAYLOAD,
    )
    assert created.status_code == 201, created.text
    return session["csrf_token"], organization_id, created.json()["id"]


def _seed_speaker_with_two_submissions(
    connection: sqlite3.Connection,
    organization_id: str,
    event_id: str,
    *,
    speaker_id: str = "speaker-1",
    person_id: str = "person-1",
    link_user: bool = False,
) -> str:
    """One speaker, two proposals: the ACCEPTED one submitted FIRST, the
    rejected one submitted LATER (the eval-run shape that broke every
    attribution surface).

    `speaker_id` / `person_id` are overridable because the speaker-message
    endpoints validate `event_speaker_ids` as 36-character UUIDs, so those
    surfaces cannot be reached with the short literal ids used elsewhere.
    `link_user` additionally gives the speaker a signed-in identity and an
    active speaker membership, which the email preview joins against."""
    user_id = connection.execute(
        "SELECT id FROM users WHERE normalized_email='admin@example.com'"
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,
            status,published_at_ms,created_at_ms,updated_at_ms)
           VALUES ('form-1',?,?,1,'attribution-summit','Welcome','{"fields":[]}',
                   'published',1000,1000,1000)""",
        (organization_id, event_id),
    )
    for sid, title, submitted in (
        ("submission-accepted", "Accepted talk", 1_000),
        ("submission-rejected", "Rejected talk", 2_000),
    ):
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,
                proposal_title,proposal_abstract,speaker_name,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES (?,?,?,'form-1',?,?,'Abstract','Priya Raman','submitted',?,?,?)""",
            (sid, organization_id, event_id, f"public-{sid}", title,
             submitted, submitted, submitted),
        )
    connection.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,name,rubric_json,status,
            created_at_ms,updated_at_ms,closed_at_ms)
           VALUES ('round-1',?,?,'Final','{}','closed',1000,1000,1000)""",
        (organization_id, event_id),
    )
    for did, sid, decision in (
        ("decision-accept", "submission-accepted", "accepted"),
        ("decision-reject", "submission-rejected", "rejected"),
    ):
        connection.execute(
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,round_id,submission_id,decision,
                internal_reason,decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES (?,?,?,'round-1',?,?,'',?,3000,3000)""",
            (did, organization_id, event_id, sid, decision, user_id),
        )
    connection.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
           VALUES ('accepted-1',?,?,'submission-accepted','decision-accept',3000)""",
        (organization_id, event_id),
    )
    speaker_user_id = None
    if link_user:
        speaker_user_id = f"user-{person_id}"
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,version,authorization_version,
                created_at_ms,updated_at_ms,display_name)
               VALUES (?,'priya@example.com','priya@example.com','active',1,1,
                       1000,1000,'Priya Raman')""",
            (speaker_user_id,),
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,version,
                created_at_ms,updated_at_ms)
               VALUES (?,?,?,'member','active',1,1000,1000)""",
            (f"om-{person_id}", organization_id, speaker_user_id),
        )
        connection.execute(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,version,
                created_at_ms,updated_at_ms)
               VALUES (?,?,?,?,'speaker','active',1,1000,1000)""",
            (f"em-{person_id}", organization_id, event_id, speaker_user_id),
        )
    connection.execute(
        """INSERT INTO people
           (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
           VALUES (?,?,?,'Priya Raman',1000,1000)""",
        (person_id, organization_id, speaker_user_id),
    )
    connection.execute(
        """INSERT INTO event_speakers
           (id,organization_id,event_id,person_id,status,selection_status,
            accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
           VALUES (?,?,?,?,'onboarding','accepted',
                   3000,3000,1000,1000)""",
        (speaker_id, organization_id, event_id, person_id),
    )
    for ssid, sid in (
        ("link-accepted", "submission-accepted"),
        ("link-rejected", "submission-rejected"),
    ):
        connection.execute(
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,
                snapshot_name,created_at_ms)
               VALUES (?,?,?,?,?,'primary','Priya Raman',1000)""",
            (f"{ssid}-{speaker_id}", organization_id, event_id, sid, speaker_id),
        )
    connection.execute(
        """INSERT INTO speaker_tasks
           (id,organization_id,event_id,event_speaker_id,task_type,title,
            destination_type,state,due_at_ms,created_at_ms,updated_at_ms)
           VALUES (?,?,?,?,'profile','Complete bio and profile',
                   'profile','open',1900000000000,3000,3000)""",
        (f"task-{speaker_id}", organization_id, event_id, speaker_id),
    )
    connection.commit()
    return speaker_id


async def test_speaker_surfaces_attribute_the_accepted_submission(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        speaker_id = _seed_speaker_with_two_submissions(
            connection, organization_id, event_id
        )

        # Speaker targets list: the roster card's proposal line.
        targets = await client.get(
            f"/api/v1/admin/events/{event_id}/speaker-targets"
        )
        assert targets.status_code == 200, targets.text
        target = next(
            row for row in targets.json()["data"]
            if row["event_speaker_id"] == speaker_id
        )
        assert target["selection_status"] == "accepted"
        assert target["proposal_title"] == "Accepted talk", (
            "the roster shows the rejected proposal beside an 'accepted' badge"
        )

        # Single speaker record: the organizer edit endpoint re-reads the
        # same attribution query for its response.
        record = await client.patch(
            f"/api/v1/admin/events/{event_id}/speakers/{speaker_id}",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={
                "display_name": "Priya Raman",
                "job_title": "Principal Engineer",
                "company": "Latticework Systems",
                "biography": "",
                "location": "",
                "links": [],
                "version": 1,
                "confirmation_status": "confirmed",
            },
        )
        assert record.status_code == 200, record.text
        assert record.json()["proposal_title"] == "Accepted talk"
        assert record.json()["confirmation_status"] == "confirmed"
        refreshed_targets = await client.get(
            f"/api/v1/admin/events/{event_id}/speaker-targets"
        )
        assert refreshed_targets.status_code == 200, refreshed_targets.text
        refreshed = next(
            row
            for row in refreshed_targets.json()["data"]
            if row["event_speaker_id"] == speaker_id
        )
        assert refreshed["confirmation_status"] == "confirmed"

        # Onboarding dashboard rows: the SESSION column.
        dashboard = await client.get(
            f"/api/v1/admin/events/{event_id}/onboarding"
        )
        assert dashboard.status_code == 200, dashboard.text
        rows = [
            row for row in dashboard.json()["data"]
            if row["event_speaker_id"] == speaker_id
        ]
        assert rows, dashboard.text
        assert all(row["proposal_title"] == "Accepted talk" for row in rows), (
            "onboarding tasks are attributed to the rejected submission"
        )

        # Home metrics recent-speakers cards.
        metrics = await client.get(
            f"/api/v1/admin/organizations/{organization_id}/metrics"
        )
        assert metrics.status_code == 200, metrics.text
        recent = next(
            row for row in metrics.json()["recent_speakers"]
            if row["person_id"] == "person-1"
        )
        assert recent["proposal_title"] == "Accepted talk"

        # SessionBoard consumers historically receive the primary speaker
        # first. Pin that contract with a co-speaker whose name would otherwise
        # sort ahead of Priya alphabetically.
        connection.execute(
            """INSERT INTO people
               (id,organization_id,display_name,created_at_ms,updated_at_ms)
               VALUES ('person-co',?,'Aaron Co-speaker',1000,1000)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,selection_status,
                accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
               VALUES ('speaker-co',?,?,'person-co','onboarding','accepted',
                       3000,3000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,
                snapshot_name,created_at_ms)
               VALUES ('link-co',?,?,'submission-accepted','speaker-co','co_speaker',
                       'Aaron Co-speaker',1000)""",
            (organization_id, event_id),
        )
        connection.commit()
        token = await client.post(
            f"/api/v1/admin/events/{event_id}/integrations/accelevents/tokens",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "primary-first-export",
            },
            json={"label": "Primary-first export"},
        )
        assert token.status_code == 201, token.text
        exported = await client.post(
            f"/v1/event/{event_id}/sessions",
            headers={"x-access-token": token.json()["token"]},
        )
        assert exported.status_code == 200, exported.text
        accepted = next(
            item for item in exported.json()["results"] if item["title"] == "Accepted talk"
        )
        assert [speaker["full_name"] for speaker in accepted["speakers"]] == [
            "Priya Raman",
            "Aaron Co-speaker",
        ]


async def test_speaker_without_accepted_submission_falls_back_to_latest(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        # Remove the acceptance: no accepted session exists any more.
        connection.execute("DELETE FROM accepted_sessions WHERE id='accepted-1'")
        connection.commit()

        targets = await client.get(
            f"/api/v1/admin/events/{event_id}/speaker-targets"
        )
        assert targets.status_code == 200
        target = next(
            row for row in targets.json()["data"]
            if row["event_speaker_id"] == "speaker-1"
        )
        # With nothing accepted, the newest submission is the best signal.
        assert target["proposal_title"] == "Rejected talk"


async def test_registered_speaker_can_receive_custom_onboarding_tasks(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        speaker_id = _seed_speaker_with_two_submissions(
            connection, organization_id, event_id
        )
        connection.execute(
            "UPDATE event_speakers SET selection_status='submitted' WHERE id=?",
            (speaker_id,),
        )
        connection.commit()

        created = await client.post(
            f"/api/v1/admin/events/{event_id}/speaker-tasks",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "registered-speaker-task",
            },
            json={
                "event_speaker_id": speaker_id,
                "submission_id": None,
                "title": "Confirm availability",
                "help_text": "Share your arrival time.",
                "due_at_ms": 1_900_000_000_000,
                "fields": [],
            },
        )

        assert created.status_code == 201, created.text
        assert created.json()["owner_type"] == "event_speaker"
        assert created.json()["event_speaker_id"] == speaker_id
        assert created.json()["invitation_id"] is None


async def test_organizer_can_create_enforceable_file_request_task(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        speaker_id = _seed_speaker_with_two_submissions(
            connection, organization_id, event_id
        )
        created = await client.post(
            f"/api/v1/admin/events/{event_id}/speaker-tasks",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "speaker-slides-request",
            },
            json={
                "event_speaker_id": speaker_id,
                "submission_id": None,
                "title": "Upload final slides",
                "help_text": "PDF only.",
                "due_at_ms": 1_900_000_000_000,
                "task_type": "slides",
                "upload_enabled": True,
                "allowed_content_types": ["application/pdf"],
                "max_file_bytes": 50 * 1024 * 1024,
                "fields": [],
            },
        )

        assert created.status_code == 201, created.text
        task_type, destination_type, schema_json = connection.execute(
            "SELECT task_type,destination_type,form_schema_json FROM speaker_tasks WHERE id=?",
            (created.json()["id"],),
        ).fetchone()
        assert (task_type, destination_type) == ("slides", "slides")
        assert json.loads(schema_json)["upload"] == {
            "enabled": True,
            "allowed_content_types": ["application/pdf"],
            "max_file_bytes": 50 * 1024 * 1024,
        }


async def test_pending_invitation_can_receive_task_before_registration(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        inviter_id = connection.execute("SELECT id FROM users LIMIT 1").fetchone()[0]
        invitation_id = "77777777-7777-4777-8777-777777777777"
        now = 1_800_000_000_000
        connection.execute(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms,display_name)
               VALUES(?,?,?,?,?,'speaker','pending',?,?,?,?,?)""",
            (
                invitation_id, organization_id, event_id, "marcus@example.test",
                "marcus@example.test", inviter_id, now + 86_400_000, now, now,
                "Marcus Okafor",
            ),
        )
        connection.commit()

        targets = await client.get(f"/api/v1/admin/events/{event_id}/speaker-targets")
        assert any(
            target["event_speaker_id"] == invitation_id
            and target["selection_status"] == "invited"
            for target in targets.json()["data"]
        )
        created = await client.post(
            f"/api/v1/admin/events/{event_id}/speaker-tasks",
            headers={
                "origin": "https://test", "x-csrf-token": csrf,
                "idempotency-key": "pending-speaker-task",
            },
            json={
                "event_speaker_id": invitation_id, "submission_id": None,
                "title": "Confirm availability", "help_text": "", "fields": [],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["owner_type"] == "invitation"
        assert created.json()["event_speaker_id"] is None
        assert created.json()["invitation_id"] == invitation_id
        task = connection.execute(
            "SELECT event_speaker_id,pending_invitation_id FROM speaker_tasks WHERE id=?",
            (created.json()["id"],),
        ).fetchone()
        assert tuple(task) == (None, invitation_id)

        dashboard = await client.get(
            f"/api/v1/admin/events/{event_id}/onboarding", params={"limit": 50}
        )
        assert dashboard.status_code == 200, dashboard.text
        invitation_row = next(
            row for row in dashboard.json()["data"] if row["task_id"] == created.json()["id"]
        )
        assert invitation_row["event_speaker_id"] is None
        assert invitation_row["invitation_id"] == invitation_id
        assert invitation_row["recipient_state"] == "invited"
        assert invitation_row["state"] == "awaiting_acceptance"
        assert dashboard.json()["summary"]["awaiting_acceptance"] == 1
        assert dashboard.json()["summary"]["incomplete"] == 0


SPEAKER_UUID = "11111111-1111-4111-8111-111111111111"


async def test_email_directory_and_profile_attribute_the_accepted_submission(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The three surfaces the original attribution sweep missed.

    The welcome email is the important one: queue_speaker_message renders what
    it sends from preview_speaker_message, so a newest-wins title here is not a
    preview artefact — it is the wrong session name mailed to a real speaker.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        speaker_id = _seed_speaker_with_two_submissions(
            connection,
            organization_id,
            event_id,
            speaker_id=SPEAKER_UUID,
            person_id="person-priya",
            link_user=True,
        )

        # 1. Welcome-email preview — and therefore the send path.
        preview = await client.post(
            f"/api/v1/admin/events/{event_id}/communications/speakers/preview",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={
                "event_speaker_ids": [speaker_id],
                "subject": "Welcome to {{event.name}}",
                "body_text": "We are excited to have you present {{submission.title}}.",
            },
        )
        assert preview.status_code == 200, preview.text
        recipient = preview.json()["recipients"][0]
        assert "Accepted talk" in recipient["html_body"], (
            "the welcome email names the rejected proposal as the speaker's session"
        )
        assert "Rejected talk" not in recipient["html_body"]

        # 2. Organization-wide speaker directory.
        directory = await client.get(
            f"/api/v1/admin/organizations/{organization_id}/people"
        )
        assert directory.status_code == 200, directory.text
        person = next(
            row for row in directory.json()["data"]
            if row["person_id"] == "person-priya"
        )
        listed = next(
            row for row in person["participations"]
            if row["event_speaker_id"] == speaker_id
        )
        assert listed["proposal_title"] == "Accepted talk"

        # 3. Speaker profile page participation list.
        profile = await client.get("/api/v1/speaker-profiles/person-priya")
        assert profile.status_code == 200, profile.text
        participations = profile.json()["participations"]
        assert participations, profile.text
        assert all(row["proposal_title"] == "Accepted talk" for row in participations)

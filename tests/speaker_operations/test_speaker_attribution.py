"""Speaker ↔ submission attribution, driven over HTTP.

Pins the eval-run P0: every surface that shows "the speaker's proposal"
(speaker targets, the single speaker record, the onboarding dashboard, the
home recent-speakers cards) must show the ACCEPTED submission when one
exists — not whichever submission happens to be newest. In the eval run a
later-submitted rejected proposal displaced the accepted session on all of
these surfaces at once.
"""

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
        f"/auth/verify?token={_token(connection, 'admin@example.com')}",
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
    connection: sqlite3.Connection, organization_id: str, event_id: str
) -> str:
    """One speaker, two proposals: the ACCEPTED one submitted FIRST, the
    rejected one submitted LATER (the eval-run shape that broke every
    attribution surface)."""
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
    connection.execute(
        """INSERT INTO people
           (id,organization_id,display_name,created_at_ms,updated_at_ms)
           VALUES ('person-1',?,'Priya Raman',1000,1000)""",
        (organization_id,),
    )
    connection.execute(
        """INSERT INTO event_speakers
           (id,organization_id,event_id,person_id,status,selection_status,
            accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
           VALUES ('speaker-1',?,?,'person-1','onboarding','accepted',
                   3000,3000,1000,1000)""",
        (organization_id, event_id),
    )
    for ssid, sid in (
        ("link-accepted", "submission-accepted"),
        ("link-rejected", "submission-rejected"),
    ):
        connection.execute(
            """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,
                snapshot_name,created_at_ms)
               VALUES (?,?,?,?,'speaker-1','primary','Priya Raman',1000)""",
            (ssid, organization_id, event_id, sid),
        )
    connection.execute(
        """INSERT INTO speaker_tasks
           (id,organization_id,event_id,event_speaker_id,task_type,title,
            destination_type,state,due_at_ms,created_at_ms,updated_at_ms)
           VALUES ('task-1',?,?,'speaker-1','profile','Complete bio and profile',
                   'profile','open',1900000000000,3000,3000)""",
        (organization_id, event_id),
    )
    connection.commit()
    return "speaker-1"


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
            },
        )
        assert record.status_code == 200, record.text
        assert record.json()["proposal_title"] == "Accepted talk"

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

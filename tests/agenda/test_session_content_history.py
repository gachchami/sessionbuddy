"""Session content-history contract, driven over HTTP.

Pins the eval-run P0: the row labelled vN in Content history must restore
EXACTLY the content recorded at version N, the history may never contain
duplicate version rows (the missing UNIQUE constraint made the
INSERT OR IGNORE backfill a silent duplicator), and a save that changes
nothing must not mint a new version.
"""

import sqlite3

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)

EVENT_PAYLOAD = {
    "name": "Content Summit",
    "starts_at_ms": 1_900_000_000_000,
    "ends_at_ms": 1_900_086_400_000,
    "time_zone": "Asia/Kolkata",
    "delivery_mode": "hybrid",
    "location": "Pune",
    "description": "Content history checks",
    "email_sender_name": "Program Team",
    "email_reply_to": "program@example.com",
}


async def _admin(client, connection: sqlite3.Connection) -> tuple[str, str, str]:
    bootstrap = await client.post(
        "/api/v1/bootstrap",
        headers={"x-bootstrap-token": _deployment_key(connection)},
        json={
            "organization_name": "Content Events",
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


def _seed_accepted_session(
    connection: sqlite3.Connection, organization_id: str, event_id: str
) -> str:
    """Direct-SQL seed of the acceptance chain feeding accepted_sessions."""
    user_id = connection.execute(
        "SELECT id FROM users WHERE normalized_email='admin@example.com'"
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,
            schema_json,status,published_at_ms,created_at_ms,updated_at_ms)
           VALUES ('form-1',?,?,1,'content-summit','Welcome',
                   '{"fields":[]}','published',1000,1000,1000)""",
        (organization_id, event_id),
    )
    connection.execute(
        """INSERT INTO submissions
           (id,organization_id,event_id,form_id,public_session_id,
            proposal_title,proposal_abstract,speaker_name,status,
            submitted_at_ms,created_at_ms,updated_at_ms)
           VALUES ('submission-1',?,?,'form-1','public-1',
                   'Original title','Original abstract','Priya Raman',
                   'submitted',1000,1000,1000)""",
        (organization_id, event_id),
    )
    connection.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,name,rubric_json,status,
            created_at_ms,updated_at_ms,closed_at_ms)
           VALUES ('round-1',?,?,'Final','{}','closed',1000,1000,1000)""",
        (organization_id, event_id),
    )
    connection.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,round_id,submission_id,decision,
            internal_reason,decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES ('decision-1',?,?,'round-1','submission-1','accepted','',
                   ?,1000,1000)""",
        (organization_id, event_id, user_id),
    )
    connection.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
           VALUES ('accepted-1',?,?,'submission-1','decision-1',1000)""",
        (organization_id, event_id),
    )
    connection.commit()
    return "accepted-1"


def _content_url(event_id: str, session_id: str) -> str:
    return f"/api/v1/admin/events/{event_id}/sessions/{session_id}/content"


async def test_every_history_row_restores_its_own_content(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        session_id = _seed_accepted_session(connection, organization_id, event_id)
        headers = {"origin": "https://test", "x-csrf-token": csrf}

        current = (await client.get(_content_url(event_id, session_id))).json()
        assert current["version"] == 1
        assert current["title"] == "Original title"

        # Two ordinary edits: v1 -> v2 -> v3.
        for expected_version, title in ((2, "Second title"), (3, "Third title")):
            saved = await client.patch(
                _content_url(event_id, session_id),
                headers=headers,
                json={
                    "title": title,
                    "abstract": f"Abstract for {title}",
                    "content_status": "draft",
                    "version": expected_version - 1,
                },
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["version"] == expected_version

        view = (await client.get(_content_url(event_id, session_id))).json()
        by_version = {row["version"]: row for row in view["history"]}
        assert by_version[1]["title"] == "Original title"
        assert by_version[2]["title"] == "Second title"
        assert by_version[3]["title"] == "Third title"

        # THE CONTRACT: restoring the row labelled vN yields vN's content.
        restored = await client.post(
            f"{_content_url(event_id, session_id)}/restore",
            headers=headers,
            json={"history_version": 2, "current_version": 3},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["title"] == "Second title", (
            "restore of v2 must yield v2's content, not a neighbour's"
        )

        after = (await client.get(_content_url(event_id, session_id))).json()
        assert after["title"] == "Second title"
        assert after["abstract"] == "Abstract for Second title"


async def test_history_never_contains_duplicate_versions(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        session_id = _seed_accepted_session(connection, organization_id, event_id)
        headers = {"origin": "https://test", "x-csrf-token": csrf}

        for version, title in ((1, "Second title"), (2, "Third title"), (3, "Fourth title")):
            saved = await client.patch(
                _content_url(event_id, session_id),
                headers=headers,
                json={
                    "title": title,
                    "abstract": "Abstract",
                    "content_status": "draft",
                    "version": version,
                },
            )
            assert saved.status_code == 200, saved.text

        rows = connection.execute(
            """SELECT version, COUNT(*) AS copies FROM session_content_versions
               WHERE accepted_session_id=? GROUP BY version HAVING COUNT(*) > 1""",
            (session_id,),
        ).fetchall()
        assert rows == [], f"duplicate history rows: {[tuple(r) for r in rows]}"

        view = (await client.get(_content_url(event_id, session_id))).json()
        versions = [row["version"] for row in view["history"]]
        assert versions == sorted(set(versions), reverse=True)
        assert versions == [4, 3, 2, 1]


async def test_saving_identical_content_does_not_mint_a_version(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        session_id = _seed_accepted_session(connection, organization_id, event_id)
        headers = {"origin": "https://test", "x-csrf-token": csrf}

        first = await client.patch(
            _content_url(event_id, session_id),
            headers=headers,
            json={
                "title": "Edited title",
                "abstract": "Edited abstract",
                "content_status": "draft",
                "version": 1,
            },
        )
        assert first.status_code == 200
        assert first.json()["version"] == 2

        # A no-op save (same content, e.g. the editor dialog saved without
        # changes) must be idempotent: same version, no new history rows.
        noop = await client.patch(
            _content_url(event_id, session_id),
            headers=headers,
            json={
                "title": "Edited title",
                "abstract": "Edited abstract",
                "content_status": "draft",
                "version": 2,
            },
        )
        assert noop.status_code == 200, noop.text
        assert noop.json()["version"] == 2
        assert [row["version"] for row in noop.json()["history"]] == [2, 1]

        # Restoring the version that is already current is likewise a no-op.
        restore_current = await client.post(
            f"{_content_url(event_id, session_id)}/restore",
            headers=headers,
            json={"history_version": 2, "current_version": 2},
        )
        assert restore_current.status_code == 200
        assert restore_current.json()["version"] == 2
        assert [row["version"] for row in restore_current.json()["history"]] == [2, 1]

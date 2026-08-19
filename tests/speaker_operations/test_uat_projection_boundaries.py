"""Stress UAT fixes against publication and explicit-association boundaries."""

import pytest

from tests.scheduling.test_public_track_filters import _published_tracks
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)
from tests.speaker_operations.test_admin_asset_workflows import _asset
from tests.speaker_operations.test_speaker_attribution import (
    _admin,
    _seed_speaker_with_two_submissions,
)


@pytest.mark.parametrize("suffix", ["schedule", "schedule.xml", "schedule.ics", "speakers"])
async def test_track_filter_never_reintroduces_unpublished_content(
    production_environment, suffix,  # noqa: F811
):
    connection, _, environment = production_environment
    async with _client(environment) as client:
        event_id, tracks = await _published_tracks(client, connection)
        # A speaker legitimately participates in both tracks. Selecting one
        # must filter their nested sessions, not only the top-level people list.
        session = connection.execute(
            "SELECT id,organization_id FROM accepted_sessions WHERE organizer_title='Talk 1'"
        ).fetchone()
        connection.execute(
            """INSERT INTO accepted_session_participants
               (id,organization_id,event_id,accepted_session_id,event_speaker_id,
                display_name_snapshot,created_at_ms,updated_at_ms)
               VALUES ('shared-participant',?,?,?,'speaker-0','Speaker 0',1,1)""",
            (session["organization_id"], event_id, session["id"]),
        )
        connection.commit()
        url = f"/api/v1/public/events/{event_id}/{suffix}"
        params = {"track_id": tracks["Platform"], "track": "Other"}
        selected = await client.get(url, params=params)
        assert selected.status_code == 200, selected.text
        assert "Talk 0" in selected.text and "Talk 1" not in selected.text
        if suffix == "speakers":
            assert [item["display_name"] for item in selected.json()["data"]] == ["Speaker 0"]
            assert [item["title"] for item in selected.json()["data"][0]["sessions"]] == ["Talk 0"]
        # Use a valid persisted draft-content state, not a fabricated response.
        connection.execute(
            "UPDATE accepted_sessions SET content_status='draft' WHERE organizer_title='Talk 0'"
        )
        connection.commit()
        hidden = await client.get(url, params=params)
        assert hidden.status_code == 200, hidden.text
        assert "Talk 0" not in hidden.text and "Talk 1" not in hidden.text
        connection.execute(
            "UPDATE schedule_revisions SET status='superseded' WHERE status='published'"
        )
        connection.commit()
        unpublished = await client.get(url, params={"track_id": tracks["Other"]})
        assert unpublished.status_code == 200, unpublished.text
        assert "Talk 0" not in unpublished.text and "Talk 1" not in unpublished.text


async def test_asset_explicit_proposal_wins_over_task_proposal_and_anonymous_is_denied(
    production_environment,  # noqa: F811
):
    connection, _, environment = production_environment
    async with _client(environment) as client:
        _, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        _asset(connection, organization_id, event_id, "linked", "version-linked", "slides")
        connection.execute(
            "UPDATE speaker_tasks SET submission_id='submission-rejected' WHERE id='task-speaker-1'"
        )
        connection.execute("UPDATE speaker_assets SET task_id='task-speaker-1' WHERE id='linked'")
        connection.commit()
        url = f"/api/v1/admin/events/{event_id}/assets"
        for path in [url, f"{url}/linked"]:
            response = await client.get(path)
            assert response.status_code == 200, response.text
            item = response.json()["data"][0] if path == url else response.json()
            assert item["submission_id"] == "submission-accepted"
            assert item["proposal_title"] == "Accepted talk"
            assert "Rejected talk" not in response.text
        client.cookies.clear()
        for path in [url, f"{url}/linked"]:
            denied = await client.get(path)
            assert denied.status_code == 401
            assert "Accepted talk" not in denied.text

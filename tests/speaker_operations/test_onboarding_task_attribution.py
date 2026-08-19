"""Onboarding task titles come only from explicit, scoped submission links."""

import pytest

from sessionbuddy.platform.upload_contracts import task_form_schema_json
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)
from tests.speaker_operations.test_speaker_attribution import (
    _admin,
    _seed_speaker_with_two_submissions,
)


async def test_onboarding_general_and_explicit_tasks_do_not_follow_latest_session(
    production_environment,  # noqa: F811
):
    connection, _, environment = production_environment
    async with _client(environment) as client:
        _, org_id, event_id = await _admin(client, connection)
        speaker_id = _seed_speaker_with_two_submissions(connection, org_id, event_id)
        for task_id, submission_id in (("explicit-accepted", "submission-accepted"),
                                       ("explicit-other", "submission-rejected")):
            connection.execute(
                """INSERT INTO speaker_tasks
                   (id,organization_id,event_id,event_speaker_id,submission_id,task_type,
                    title,state,created_at_ms,updated_at_ms,form_schema_json)
                   VALUES (?,?,?,?,?,'slides','Explicit slides','open',3000,3000,?)""",
                (task_id, org_id, event_id, speaker_id, submission_id,
                 task_form_schema_json("slides")),
            )
        connection.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,source_type,organizer_title,organizer_abstract,
                created_at_ms) VALUES ('newest-session',?,?,'organizer_created',
                'Newest unrelated session','Abstract',9000)""", (org_id, event_id),
        )
        connection.execute(
            """INSERT INTO accepted_session_participants
               (id,organization_id,event_id,accepted_session_id,event_speaker_id,
                display_name_snapshot,created_at_ms,updated_at_ms)
               VALUES ('newest-participant',?,?,'newest-session',?,'Speaker',9000,9000)""",
            (org_id, event_id, speaker_id),
        )
        connection.commit()
        response = await client.get(f"/api/v1/admin/events/{event_id}/onboarding")
        assert response.status_code == 200, response.text
        titles = {r["task_id"]: r["proposal_title"] for r in response.json()["data"]}
        assert titles[f"task-{speaker_id}"] == ""
        assert titles["explicit-accepted"] == "Accepted talk"
        assert titles["explicit-other"] == "Rejected talk"


@pytest.mark.parametrize("broken_scope", ["event", "organization", "missing", "unlinked"])
async def test_onboarding_explicit_link_fails_closed_for_invalid_or_cross_scope_rows(
    production_environment, broken_scope,  # noqa: F811
):
    connection, _, environment = production_environment
    async with _client(environment) as client:
        _, org_id, event_id = await _admin(client, connection)
        speaker_id = _seed_speaker_with_two_submissions(connection, org_id, event_id)
        connection.execute(
            "UPDATE speaker_tasks SET submission_id='submission-accepted' WHERE id=?",
            (f"task-{speaker_id}",),
        )
        connection.commit()
        # Offline corruption fixture only: normal FK/owner guards reject these
        # writes. The reader must still refuse a dangling or cross-tenant link.
        connection.execute("PRAGMA foreign_keys=OFF")
        if broken_scope == "event":
            connection.execute(
                "UPDATE call_for_speaker_forms SET event_id='foreign-event' WHERE id='form-1'"
            )
            connection.execute(
                "UPDATE submissions SET event_id='foreign-event' WHERE id='submission-accepted'"
            )
        elif broken_scope == "organization":
            connection.execute(
                "UPDATE call_for_speaker_forms SET organization_id='foreign-org' WHERE id='form-1'"
            )
            connection.execute(
                "UPDATE submissions SET organization_id='foreign-org' "
                "WHERE id='submission-accepted'"
            )
        elif broken_scope == "missing":
            connection.execute(
                "UPDATE submissions SET id='different-id' WHERE id='submission-accepted'"
            )
        else:
            connection.execute(
                "DELETE FROM submission_speakers WHERE submission_id='submission-accepted'"
            )
        connection.commit()
        response = await client.get(f"/api/v1/admin/events/{event_id}/onboarding")
        assert response.status_code == 200, response.text
        task = next(r for r in response.json()["data"] if r["task_id"] == f"task-{speaker_id}")
        assert task["proposal_title"] == ""

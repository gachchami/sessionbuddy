"""Organizer-facing recipient labelling for event messages.

CFP-S4 read the messages page as "no organizer notification surface": every
recipient row said "Active speaker", so a page reached after a decision round
could not tell an accepted speaker from someone whose proposal had just been
rejected. The recipient list already includes both — the preview now says which
is which, and the send path renders from this same query.
"""

from tests.agenda.test_session_content_history import _admin
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401 - pytest fixture
)
from tests.speaker_operations.test_speaker_attribution import (
    SPEAKER_UUID,
    _seed_speaker_with_two_submissions,
)


async def _preview(client, csrf, event_id, speaker_id):
    return await client.post(
        f"/api/v1/admin/events/{event_id}/communications/speakers/preview",
        headers={"origin": "https://test", "x-csrf-token": csrf},
        json={
            "event_speaker_ids": [speaker_id],
            "subject": "About {{event.name}}",
            "body_text": "Hello {{speaker.name}}, about {{submission.title}}.",
        },
    )


async def test_preview_reports_where_each_recipient_stands(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
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

        accepted = await _preview(client, csrf, event_id, speaker_id)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["recipients"][0]["selection_status"] == "accepted"

        # The same person after a rejection round. They stay reachable — an
        # organizer must be able to write to a rejected submitter — but the
        # preview has to say so before the mail is queued.
        connection.execute(
            "UPDATE event_speakers SET selection_status='rejected' WHERE id=?",
            (speaker_id,),
        )
        connection.commit()
        rejected = await _preview(client, csrf, event_id, speaker_id)
        assert rejected.status_code == 200, rejected.text
        recipient = rejected.json()["recipients"][0]
        assert recipient["selection_status"] == "rejected"
        assert recipient["recipient_state"] == "active"


async def test_undecided_submitters_are_reachable_and_labelled(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
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
        connection.execute(
            "UPDATE event_speakers SET selection_status='submitted' WHERE id=?",
            (speaker_id,),
        )
        connection.commit()

        preview = await _preview(client, csrf, event_id, speaker_id)
        assert preview.status_code == 200, preview.text
        assert preview.json()["recipients"][0]["selection_status"] == "submitted"


async def test_unresolvable_recipient_is_named_in_the_error(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _admin(client, connection)
        missing = "22222222-2222-4222-8222-222222222222"
        response = await _preview(client, csrf, event_id, missing)
        assert response.status_code == 422, response.text
        # A stale recipient list must not fail the batch anonymously.
        assert missing in response.text

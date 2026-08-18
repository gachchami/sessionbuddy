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


async def test_preview_aggregates_only_pending_invitees_and_caps_names(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        active_speaker_id = _seed_speaker_with_two_submissions(
            connection,
            organization_id,
            event_id,
            speaker_id=SPEAKER_UUID,
            person_id="person-priya",
            link_user=True,
        )
        inviter_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        invitations = [
            (
                f"30000000-0000-4000-8000-{index:012d}",
                organization_id,
                event_id,
                f"invitee-{index}@example.test",
                f"Invitee {index}",
                inviter_id,
            )
            for index in range(1, 12)
        ]
        connection.executemany(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms,display_name)
               VALUES(?,?,?, ?,?,'speaker','pending',?,9999999999999,1000,1000,?)""",
            [
                (invitation_id, org_id, current_event_id, email, email, inviter, name)
                for invitation_id, org_id, current_event_id, email, name, inviter in invitations
            ],
        )
        connection.commit()

        response = await client.post(
            f"/api/v1/admin/events/{event_id}/communications/speakers/preview",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={
                "event_speaker_ids": [
                    active_speaker_id,
                    *(invitation[0] for invitation in invitations),
                ],
                "subject": "Welcome to {{event.name}}",
                "body_text": "Present {{submission.title}} at {{portal.link}}.",
            },
        )

        assert response.status_code == 422, response.text
        detail = response.json()["error"]["message"]
        assert detail.startswith("11 invited recipients cannot receive this template")
        assert "portal.link and submission.title" in detail
        assert all(f"Invitee {index}" in detail for index in range(1, 11))
        assert "Invitee 11" not in detail
        assert "and 1 other" in detail
        assert "Priya" not in detail


async def test_preview_rejects_variables_unavailable_to_speaker_messages_before_recipients(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _admin(client, connection)
        response = await client.post(
            f"/api/v1/admin/events/{event_id}/communications/speakers/preview",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={
                "event_speaker_ids": ["22222222-2222-4222-8222-222222222222"],
                "subject": "Schedule for {{event.name}}",
                "body_text": "Meet us in {{schedule.room}} at {{schedule.start}}.",
            },
        )

        assert response.status_code == 422, response.text
        detail = response.json()["error"]["message"]
        assert "unavailable here template variable(s): schedule.room, schedule.start" in detail
        assert "Available variables:" in detail
        assert "portal.link" in detail
        assert "task.title" not in detail
        assert "schedule.room, schedule.start" in detail
        assert "22222222-2222-4222-8222-222222222222" not in detail


async def test_preview_returns_a_structured_context_safe_template_suggestion(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _admin(client, connection)
        response = await client.post(
            f"/api/v1/admin/events/{event_id}/communications/speakers/preview",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={
                "event_speaker_ids": ["22222222-2222-4222-8222-222222222222"],
                "subject": "Your portal",
                "body_text": "Open {{portal_link}}.",
            },
        )

        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert "x-template-suggestion" not in response.headers
        assert "portal_link" in error["message"]
        assert error["metadata"]["template_suggestion"] == {
            "source": "portal_link",
            "replacement": "portal.link",
        }


async def test_preview_rejects_a_blank_submission_title(
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
        # The schema rejects the empty string but permits whitespace-only
        # historical content, which is equally blank once rendered.
        connection.execute(
            "UPDATE submissions SET proposal_title='   ' WHERE event_id=?",
            (event_id,),
        )
        connection.commit()

        response = await _preview(client, csrf, event_id, speaker_id)

        assert response.status_code == 422, response.text
        assert "submission.title" in response.json()["error"]["message"]


async def test_preview_rejects_the_no_submission_title_fallback(
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
            "DELETE FROM submission_speakers WHERE event_speaker_id=?",
            (speaker_id,),
        )
        connection.commit()

        response = await _preview(client, csrf, event_id, speaker_id)

        assert response.status_code == 422, response.text
        assert "submission.title" in response.json()["error"]["message"]


async def test_preview_rejects_a_blank_display_name_without_crashing(
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
            "UPDATE people SET display_name='   ' WHERE id='person-priya'"
        )
        connection.commit()

        response = await _preview(client, csrf, event_id, speaker_id)

        assert response.status_code == 422, response.text
        assert "speaker.name" in response.json()["error"]["message"]

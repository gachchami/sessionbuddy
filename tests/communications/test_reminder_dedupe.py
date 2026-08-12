from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)
from tests.speaker_operations.test_speaker_attribution import (
    _admin,
    _seed_speaker_with_two_submissions,
)


async def test_task_reminders_are_deduplicated_server_side_per_day(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(
            connection, organization_id, event_id, link_user=True
        )
        url = f"/api/v1/admin/events/{event_id}/speaker-tasks/task-speaker-1/reminders"
        common = {"origin": "https://test", "x-csrf-token": csrf}
        first = await client.post(
            url, headers={**common, "idempotency-key": "reminder-attempt-one"}, json={}
        )
        retry = await client.post(
            url, headers={**common, "idempotency-key": "reminder-attempt-two"}, json={}
        )
        assert first.status_code == retry.status_code == 202
        assert retry.json() == first.json()
        assert connection.execute(
            """SELECT COUNT(*) FROM communication_messages
               WHERE deterministic_key LIKE 'task-reminder:%'"""
        ).fetchone()[0] == 1

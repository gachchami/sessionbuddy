"""Round dates survive every response used by the organizer's round cards."""

import pytest

from tests.evaluation.test_draft_round_notification_timing import _create_round, _round_body
from tests.evaluation.test_round_version_contract import _setup_event
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)


@pytest.mark.parametrize("status", ["draft", "open"])
async def test_created_and_current_round_return_persisted_dates(
    production_environment, status  # noqa: F811
):
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup_event(client, connection)
        body = _round_body(status=status)
        dates = {"review_opens_at_ms": 1787961600000, "review_closes_at_ms": 1789257540000}
        body.update(dates)
        created = await _create_round(client, csrf, event_id, body, label=f"dates-{status}")
        assert created.status_code == 201, created.text
        for key, value in dates.items():
            assert created.json()[key] == value
        listed = await client.get(f"/api/v1/admin/events/{event_id}/evaluation-rounds")
        assert listed.status_code == 200, listed.text
        saved = next(row for row in listed.json()["data"] if row["id"] == created.json()["id"])
        for key, value in dates.items():
            assert saved[key] == value
        if status == "open":
            current = await client.get(f"/api/v1/admin/events/{event_id}/evaluation-rounds/current")
            assert current.status_code == 200, current.text
            for key, value in dates.items():
                assert current.json()[key] == value

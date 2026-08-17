"""Retired event-level authority cannot be recreated through HTTP."""

from tests.agenda.test_session_content_history import _admin
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401 - pytest fixture
)


def _headers(csrf_token: str) -> dict[str, str]:
    return {"origin": "https://test", "x-csrf-token": csrf_token}


async def test_event_access_grant_routes_are_not_exposed(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, _organization_id, event_id = await _admin(root, connection)
        collection = f"/api/v1/admin/events/{event_id}/access-grants"
        member = f"{collection}/retired-user"

        responses = [
            await root.get(collection),
            await root.post(
                collection,
                headers=_headers(csrf),
                json={"email": "retired@example.com", "permission": "manage"},
            ),
            await root.patch(
                member,
                headers=_headers(csrf),
                json={"permission": "manage"},
            ),
            await root.delete(member, headers=_headers(csrf)),
        ]

    assert [response.status_code for response in responses] == [404, 404, 404, 404]

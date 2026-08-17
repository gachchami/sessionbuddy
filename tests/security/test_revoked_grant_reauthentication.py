"""Revoked organization authority stays revoked after fresh authentication."""

from tests.security.test_organizer_workflow import (
    EVENT_PAYLOAD,
    _bootstrap_admin,
    _mutation,
)
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401 - pytest fixture
)
from tests.security.test_resource_control_plane import _insert_user, _sign_in


async def test_revoked_organization_grant_fails_after_reauthentication(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        owner_csrf, organization_id = await _bootstrap_admin(owner, connection)
        created = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(owner_csrf),
            json=EVENT_PAYLOAD,
        )
        assert created.status_code == 201, created.text
        event_id = created.json()["id"]
        _insert_user(
            connection,
            user_id="revoked-manager",
            email="revoked-manager@example.com",
        )
        organization_grant = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/access-grants",
            headers=_mutation(owner_csrf),
            json={"email": "revoked-manager@example.com"},
        )
        assert organization_grant.status_code == 201

        async with _client(environment) as delegate:
            initial = await _sign_in(
                delegate, connection, "revoked-manager@example.com"
            )
            assert initial["organization_access"][0]["permissions"] == ["manage"]

            revoked_organization = await owner.delete(
                f"/api/v1/admin/organizations/{organization_id}/access-grants/"
                "revoked-manager",
                headers={**_mutation(owner_csrf), "content-type": "application/json"},
            )
            assert revoked_organization.status_code == 204
            assert (await delegate.get("/api/v1/auth/session")).status_code == 401

            fresh = await _sign_in(
                delegate, connection, "revoked-manager@example.com"
            )
            assert fresh["organization_access"] == []
            assert fresh["event_access"] == []
            denied_organization = await delegate.get(
                f"/api/v1/admin/organizations/{organization_id}/access-grants"
            )
            denied_event = await delegate.get(f"/api/v1/admin/events/{event_id}")
            assert (denied_organization.status_code, denied_event.status_code) == (
                404,
                404,
            )

    assert tuple(connection.execute(
        """SELECT resource_id,status FROM resource_access_grants
           WHERE user_id='revoked-manager'"""
    ).fetchone()) == (organization_id, "revoked")

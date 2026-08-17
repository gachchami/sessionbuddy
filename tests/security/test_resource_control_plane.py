import sqlite3
import time

import pytest

from tests.security.test_organizer_workflow import EVENT_PAYLOAD, _bootstrap_admin, _mutation
from tests.security.test_production_identity_flow import (
    _client,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)


def _insert_user(
    connection: sqlite3.Connection,
    *,
    user_id: str,
    email: str,
) -> None:
    now = int(time.time() * 1000)
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES(?,?,?,'active',?,?)""",
        (user_id, email, email, now, now),
    )
    connection.commit()


async def _sign_in(client, connection: sqlite3.Connection, email: str) -> dict[str, object]:
    requested = await client.post(
        "/api/v1/auth/magic-links",
        json={"email": email, "redirect_path": "/account"},
    )
    assert requested.status_code == 202
    verified = await client.post(
        "/auth/verify",
        data={"token": _token(connection, email)},
        follow_redirects=False,
    )
    assert verified.status_code == 303, verified.text
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    return session.json()


@pytest.mark.parametrize("permission", ["view", "edit"])
async def test_retired_organization_grant_levels_are_rejected(
    production_environment,  # noqa: F811 - pytest fixture
    permission: str,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        owner_csrf, organization_id = await _bootstrap_admin(owner, connection)
        created = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(owner_csrf),
            json=EVENT_PAYLOAD,
        )
        assert created.status_code == 201
        email = f"organization-{permission}@example.com"
        _insert_user(
            connection,
            user_id=f"organization-{permission}",
            email=email,
        )
        granted = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/access-grants",
            headers=_mutation(owner_csrf),
            json={"email": email, "permission": permission},
        )
        assert granted.status_code == 422
        assert connection.execute(
            "SELECT COUNT(*) FROM resource_access_grants WHERE user_id=?",
            (f"organization-{permission}",),
        ).fetchone()[0] == 0


async def test_organization_grant_lifecycle_is_exact_revocable_and_owner_immutable(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        owner_csrf, organization_id = await _bootstrap_admin(owner, connection)
        owner_user_id = connection.execute(
            "SELECT owner_user_id FROM owned_resources WHERE id=?",
            (organization_id,),
        ).fetchone()[0]
        _insert_user(
            connection,
            user_id="organization-manager",
            email="organization-manager@example.com",
        )
        _insert_user(
            connection,
            user_id="organization-delegate",
            email="organization-delegate@example.com",
        )

        manager_grant = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/access-grants",
            headers=_mutation(owner_csrf),
            json={"email": "organization-manager@example.com"},
        )
        assert manager_grant.status_code == 201, manager_grant.text
        assert manager_grant.json()["permission"] == "manage"
        assert tuple(connection.execute(
            """SELECT role,status FROM organization_memberships
               WHERE organization_id=? AND user_id='organization-manager'""",
            (organization_id,),
        ).fetchone()) == ("member", "active")
        assert tuple(connection.execute(
            """SELECT role,status,is_default FROM user_roles
               WHERE user_id='organization-manager'"""
        ).fetchone()) == ("organizer", "active", 1)

        listed = await owner.get(
            f"/api/v1/admin/organizations/{organization_id}/access-grants"
        )
        assert [(item["user_id"], item["permission"]) for item in listed.json()["data"]] == [
            (owner_user_id, "owner"),
            ("organization-manager", "manage"),
        ]
        immutable_patch = await owner.patch(
            f"/api/v1/admin/organizations/{organization_id}/access-grants/{owner_user_id}",
            headers=_mutation(owner_csrf),
            json={"permission": "view"},
        )
        immutable_delete = await owner.delete(
            f"/api/v1/admin/organizations/{organization_id}/access-grants/{owner_user_id}",
            headers={**_mutation(owner_csrf), "content-type": "application/json"},
        )
        assert (immutable_patch.status_code, immutable_delete.status_code) == (405, 409)

        async with _client(environment) as manager:
            manager_session = await _sign_in(
                manager, connection, "organization-manager@example.com"
            )
            manager_headers = _mutation(str(manager_session["csrf_token"]))
            delegated = await manager.post(
                f"/api/v1/admin/organizations/{organization_id}/access-grants",
                headers=manager_headers,
                json={"email": "organization-delegate@example.com"},
            )
            assert delegated.status_code == 201, delegated.text
            changed = await manager.patch(
                f"/api/v1/admin/organizations/{organization_id}/access-grants/"
                "organization-delegate",
                headers=manager_headers,
                json={"permission": "edit"},
            )
            assert changed.status_code == 405, changed.text
            revoked_delegate = await manager.delete(
                f"/api/v1/admin/organizations/{organization_id}/access-grants/"
                "organization-delegate",
                headers={**manager_headers, "content-type": "application/json"},
            )
            assert revoked_delegate.status_code == 204

            revoked_manager = await owner.delete(
                f"/api/v1/admin/organizations/{organization_id}/access-grants/"
                "organization-manager",
                headers={**_mutation(owner_csrf), "content-type": "application/json"},
            )
            assert revoked_manager.status_code == 204
            # The authorization-version bump invalidates an already-open
            # manager session immediately.
            assert (
                await manager.get(
                    f"/api/v1/admin/organizations/{organization_id}/access-grants"
                )
            ).status_code == 401

            manager_session = await _sign_in(
                manager, connection, "organization-manager@example.com"
            )
            denied_after_reauthentication = await manager.get(
                f"/api/v1/admin/organizations/{organization_id}/access-grants"
            )
            assert denied_after_reauthentication.status_code == 404
            assert manager_session["organization_access"] == []

    assert connection.execute(
        """SELECT COUNT(*) FROM resource_access_grants
           WHERE resource_id=? AND status='active'""",
        (organization_id,),
    ).fetchone()[0] == 0
    assert connection.execute(
        """SELECT COUNT(*) FROM audit_events
           WHERE organization_id=? AND target_type='organization'
             AND action LIKE 'resource_access_grant.%'""",
        (organization_id,),
    ).fetchone()[0] == 4


async def test_only_exact_owner_can_transfer_organization_to_an_existing_admin(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        owner_csrf, organization_id = await _bootstrap_admin(owner, connection)
        previous_owner = connection.execute(
            "SELECT owner_user_id FROM owned_resources WHERE id=?", (organization_id,)
        ).fetchone()[0]
        _insert_user(connection, user_id="next-owner", email="next-owner@example.com")
        granted = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/access-grants",
            headers=_mutation(owner_csrf),
            json={"email": "next-owner@example.com"},
        )
        assert granted.status_code == 201, granted.text

        async with _client(environment) as admin:
            admin_session = await _sign_in(admin, connection, "next-owner@example.com")
            denied = await admin.post(
                f"/api/v1/admin/organizations/{organization_id}/ownership-transfers",
                headers=_mutation(str(admin_session["csrf_token"])),
                json={"email": "next-owner@example.com"},
            )
            assert denied.status_code == 404

        transferred = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/ownership-transfers",
            headers=_mutation(owner_csrf),
            json={"email": "next-owner@example.com"},
        )
        assert transferred.status_code == 201, transferred.text
        assert transferred.json()["previous_owner_user_id"] == previous_owner
        assert transferred.json()["new_owner_user_id"] == "next-owner"

    assert connection.execute(
        "SELECT owner_user_id FROM owned_resources WHERE id=?", (organization_id,)
    ).fetchone()[0] == "next-owner"
    assert connection.execute(
        """SELECT permission FROM resource_access_grants
           WHERE resource_id=? AND user_id=? AND status='active'""",
        (organization_id, previous_owner),
    ).fetchone()[0] == "manage"
    assert connection.execute(
        """SELECT COUNT(*) FROM resource_access_grants
           WHERE resource_id=? AND user_id='next-owner' AND status='active'""",
        (organization_id,),
    ).fetchone()[0] == 0
    assert connection.execute(
        """SELECT COUNT(*) FROM audit_events WHERE organization_id=?
           AND action='resource_ownership.transfer' AND target_type='organization'""",
        (organization_id,),
    ).fetchone()[0] == 1



async def test_event_ownership_control_plane_is_not_available(
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

        transfer = await owner.post(
            f"/api/v1/admin/events/{event_id}/ownership-transfers",
            headers=_mutation(owner_csrf),
            json={"email": "nobody@example.test"},
        )
        recovery = await owner.get(
            f"/api/v1/admin/organizations/{organization_id}/ownership-recovery/events"
        )

    assert transfer.status_code == 404
    assert recovery.status_code == 404
    assert connection.execute(
        "SELECT COUNT(*) FROM owned_resources WHERE resource_type='event'"
    ).fetchone()[0] == 0

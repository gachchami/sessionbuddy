import sqlite3
import time

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
            json={"email": "organization-manager@example.com", "permission": "manage"},
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
        assert (immutable_patch.status_code, immutable_delete.status_code) == (409, 409)

        async with _client(environment) as manager:
            manager_session = await _sign_in(
                manager, connection, "organization-manager@example.com"
            )
            manager_headers = _mutation(str(manager_session["csrf_token"]))
            delegated = await manager.post(
                f"/api/v1/admin/organizations/{organization_id}/access-grants",
                headers=manager_headers,
                json={"email": "organization-delegate@example.com", "permission": "view"},
            )
            assert delegated.status_code == 201, delegated.text
            changed = await manager.patch(
                f"/api/v1/admin/organizations/{organization_id}/access-grants/"
                "organization-delegate",
                headers=manager_headers,
                json={"permission": "edit"},
            )
            assert changed.status_code == 200, changed.text
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
    ).fetchone()[0] == 5


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
            json={"email": "next-owner@example.com", "permission": "manage"},
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


async def test_event_ownership_recovery_transfer_and_optional_previous_owner_access(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as organization_owner:
        owner_csrf, organization_id = await _bootstrap_admin(
            organization_owner, connection
        )
        organization_owner_user_id = connection.execute(
            "SELECT owner_user_id FROM owned_resources WHERE id=?",
            (organization_id,),
        ).fetchone()[0]
        _insert_user(connection, user_id="event-maker", email="event-maker@example.com")
        _insert_user(connection, user_id="recovery-target", email="target@example.com")
        _insert_user(connection, user_id="final-owner", email="final-owner@example.com")

        maker_grant = await organization_owner.post(
            f"/api/v1/admin/organizations/{organization_id}/access-grants",
            headers=_mutation(owner_csrf),
            json={"email": "event-maker@example.com", "permission": "manage"},
        )
        assert maker_grant.status_code == 201, maker_grant.text

        async with _client(environment) as event_maker:
            maker_session = await _sign_in(event_maker, connection, "event-maker@example.com")
            maker_headers = _mutation(str(maker_session["csrf_token"]))
            created = await event_maker.post(
                f"/api/v1/admin/organizations/{organization_id}/events",
                headers=maker_headers,
                json=EVENT_PAYLOAD,
            )
            assert created.status_code == 201, created.text
            event_id = created.json()["id"]
            archived = await event_maker.patch(
                f"/api/v1/admin/events/{event_id}",
                headers=maker_headers,
                json={**EVENT_PAYLOAD, "status": "archived", "version": 1},
            )
            assert archived.status_code == 200, archived.text

            # Organization ownership cascades to every event in the organization.
            assert (
                await organization_owner.get(f"/api/v1/admin/events/{event_id}")
            ).status_code == 200

            recovered = await organization_owner.post(
                f"/api/v1/admin/events/{event_id}/ownership-transfers",
                headers=_mutation(owner_csrf),
                json={"email": "target@example.com", "reason": "Owner recovery"},
            )
            assert recovered.status_code == 201, recovered.text
            assert recovered.json()["previous_owner_user_id"] == "event-maker"
            assert recovered.json()["new_owner_user_id"] == "recovery-target"
            assert recovered.json()["previous_owner_permission"] is None

            # The former event owner retains organization-level management,
            # which intentionally cascades to every event after reauthentication.
            assert (await event_maker.get("/api/v1/auth/session")).status_code == 401
            await _sign_in(event_maker, connection, "event-maker@example.com")
            assert (
                await event_maker.get(f"/api/v1/admin/events/{event_id}")
            ).status_code == 200

        assert connection.execute(
            "SELECT owner_user_id FROM owned_resources WHERE id=?", (event_id,)
        ).fetchone()[0] == "recovery-target"
        assert tuple(connection.execute(
            """SELECT role,status FROM organization_memberships
               WHERE organization_id=? AND user_id='recovery-target'""",
            (organization_id,),
        ).fetchone()) == ("member", "active")
        assert tuple(connection.execute(
            """SELECT role,status FROM user_roles WHERE user_id='recovery-target'"""
        ).fetchone()) == ("organizer", "active")

        async with _client(environment) as target_owner:
            target_session = await _sign_in(target_owner, connection, "target@example.com")
            assert (
                await target_owner.get(f"/api/v1/admin/events/{event_id}")
            ).json()["status"] == "archived"
            transferred_by_exact_owner = await target_owner.post(
                f"/api/v1/admin/events/{event_id}/ownership-transfers",
                headers=_mutation(str(target_session["csrf_token"])),
                json={
                    "email": "final-owner@example.com",
                    "grant_previous_owner_manage": True,
                },
            )
            assert transferred_by_exact_owner.status_code == 201, (
                transferred_by_exact_owner.text
            )
            assert transferred_by_exact_owner.json()["previous_owner_permission"] == "manage"
            assert (await target_owner.get("/api/v1/auth/session")).status_code == 401
            await _sign_in(target_owner, connection, "target@example.com")
            assert (
                await target_owner.get(f"/api/v1/admin/events/{event_id}")
            ).status_code == 200

        async with _client(environment) as final_owner:
            await _sign_in(final_owner, connection, "final-owner@example.com")
            final_event = await final_owner.get(f"/api/v1/admin/events/{event_id}")
            assert final_event.status_code == 200
            assert final_event.json()["status"] == "archived"

    transfers = connection.execute(
        """SELECT from_user_id,to_user_id,transferred_by_user_id
           FROM resource_ownership_transfers WHERE resource_id=?
           ORDER BY transferred_at_ms,id""",
        (event_id,),
    ).fetchall()
    assert [tuple(row) for row in transfers] == [
        ("event-maker", "recovery-target", organization_owner_user_id),
        ("recovery-target", "final-owner", "recovery-target"),
    ]
    assert connection.execute(
        """SELECT permission FROM resource_access_grants
           WHERE resource_id=? AND user_id='recovery-target' AND status='active'""",
        (event_id,),
    ).fetchone()[0] == "manage"
    assert connection.execute(
        """SELECT COUNT(*) FROM audit_events
           WHERE event_id=? AND action='resource_ownership.transfer'""",
        (event_id,),
    ).fetchone()[0] == 2


async def test_foreign_organization_owner_cannot_use_event_recovery_control_plane(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        owner_csrf, organization_id = await _bootstrap_admin(owner, connection)
        event = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(owner_csrf),
            json=EVENT_PAYLOAD,
        )
        event_id = event.json()["id"]
        _insert_user(connection, user_id="foreign-owner", email="foreign@example.com")
        _insert_user(connection, user_id="foreign-target", email="foreign-target@example.com")
        now = int(time.time() * 1000)
        connection.execute(
            """INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms)
               VALUES('foreign-org','Foreign Events','active',?,?)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES('foreign-member','foreign-org','foreign-owner','member','active',?,?)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES('foreign-owner','organizer','active',?,?,1)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,created_at_ms,updated_at_ms)
               VALUES('foreign-org','organization','foreign-owner','foreign-owner','active',?,?)""",
            (now, now),
        )
        connection.commit()

        async with _client(environment) as foreign_owner:
            foreign_session = await _sign_in(
                foreign_owner, connection, "foreign@example.com"
            )
            denied = await foreign_owner.post(
                f"/api/v1/admin/events/{event_id}/ownership-transfers",
                headers=_mutation(str(foreign_session["csrf_token"])),
                json={"email": "foreign-target@example.com"},
            )
            assert denied.status_code == 404

    assert connection.execute(
        "SELECT owner_user_id FROM owned_resources WHERE id=?", (event_id,)
    ).fetchone()[0] != "foreign-target"
    assert connection.execute(
        "SELECT COUNT(*) FROM resource_ownership_transfers WHERE resource_id=?",
        (event_id,),
    ).fetchone()[0] == 0


async def test_only_exact_organization_owner_can_discover_minimal_recovery_events(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as organization_owner:
        owner_csrf, organization_id = await _bootstrap_admin(
            organization_owner, connection
        )
        _insert_user(connection, user_id="recovery-maker", email="maker@example.com")
        _insert_user(connection, user_id="event-grantee", email="grantee@example.com")
        _insert_user(connection, user_id="other-org-owner", email="other-owner@example.com")
        granted = await organization_owner.post(
            f"/api/v1/admin/organizations/{organization_id}/access-grants",
            headers=_mutation(owner_csrf),
            json={"email": "maker@example.com", "permission": "manage"},
        )
        assert granted.status_code == 201, granted.text

        async with _client(environment) as maker:
            maker_session = await _sign_in(maker, connection, "maker@example.com")
            maker_headers = _mutation(str(maker_session["csrf_token"]))
            created_event_ids: list[str] = []
            for name in ("Private active recovery", "Private archived recovery"):
                created = await maker.post(
                    f"/api/v1/admin/organizations/{organization_id}/events",
                    headers=maker_headers,
                    json={
                        **EVENT_PAYLOAD,
                        "name": name,
                        "description": "Must not appear in recovery discovery",
                    },
                )
                assert created.status_code == 201, created.text
                created_event_ids.append(created.json()["id"])
            archived = await maker.patch(
                f"/api/v1/admin/events/{created_event_ids[1]}",
                headers=maker_headers,
                json={
                    **EVENT_PAYLOAD,
                    "name": "Private archived recovery",
                    "description": "Must not appear in recovery discovery",
                    "status": "archived",
                    "version": 1,
                },
            )
            assert archived.status_code == 200, archived.text

            event_grant = await maker.post(
                f"/api/v1/admin/events/{created_event_ids[0]}/access-grants",
                headers=maker_headers,
                json={"email": "grantee@example.com", "permission": "manage"},
            )
            assert event_grant.status_code == 201, event_grant.text
            now = int(time.time() * 1000)
            connection.execute(
                """INSERT INTO organization_memberships
                   (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES('event-grantee-membership',?,'event-grantee','member',
                          'active',?,?)""",
                (organization_id, now, now),
            )
            connection.commit()

            # Exact organization management and event ownership do not expose
            # the owner-only recovery inventory.
            manager_denied = await maker.get(
                f"/api/v1/admin/organizations/{organization_id}/"
                "ownership-recovery/events"
            )
            assert manager_denied.status_code == 404

        for event_id in created_event_ids:
            # Organization ownership cascades to the ordinary event read path.
            assert (
                await organization_owner.get(f"/api/v1/admin/events/{event_id}")
            ).status_code == 200

        first_page = await organization_owner.get(
            f"/api/v1/admin/organizations/{organization_id}/"
            "ownership-recovery/events?limit=1"
        )
        assert first_page.status_code == 200, first_page.text
        assert len(first_page.json()["data"]) == 1
        assert first_page.json()["next_cursor"]
        assert set(first_page.json()["data"][0]) == {
            "event_id",
            "name",
            "status",
            "current_owner_user_id",
            "current_owner_email",
        }
        cursor = first_page.json()["next_cursor"]
        second_page = await organization_owner.get(
            f"/api/v1/admin/organizations/{organization_id}/"
            f"ownership-recovery/events?limit=1&cursor={cursor}"
        )
        assert second_page.status_code == 200, second_page.text
        discovered = first_page.json()["data"] + second_page.json()["data"]
        assert {row["event_id"] for row in discovered} == set(created_event_ids)
        assert {row["status"] for row in discovered} == {"active", "archived"}
        assert {row["current_owner_user_id"] for row in discovered} == {
            "recovery-maker"
        }
        assert {row["current_owner_email"] for row in discovered} == {
            "maker@example.com"
        }
        assert "Must not appear" not in first_page.text + second_page.text
        tampered = await organization_owner.get(
            f"/api/v1/admin/organizations/{organization_id}/"
            f"ownership-recovery/events?cursor={cursor}x"
        )
        assert tampered.status_code == 400

        async with _client(environment) as grantee:
            await _sign_in(grantee, connection, "grantee@example.com")
            event_grantee_denied = await grantee.get(
                f"/api/v1/admin/organizations/{organization_id}/"
                "ownership-recovery/events"
            )
            assert event_grantee_denied.status_code == 404

        now = int(time.time() * 1000)
        connection.execute(
            """INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms)
               VALUES('other-org','Other Organization','active',?,?)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES('other-org-member','other-org','other-org-owner','member','active',?,?)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES('other-org-owner','organizer','active',?,?,1)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,
                created_at_ms,updated_at_ms)
               VALUES('other-org','organization','other-org-owner',
                      'other-org-owner','active',?,?)""",
            (now, now),
        )
        connection.commit()
        async with _client(environment) as other_owner:
            await _sign_in(other_owner, connection, "other-owner@example.com")
            foreign_owner_denied = await other_owner.get(
                f"/api/v1/admin/organizations/{organization_id}/"
                "ownership-recovery/events"
            )
            assert foreign_owner_denied.status_code == 404

"""Multi-organizer workflow: a second event_admin and an invitable
organization_admin, driven end to end over HTTP.

Also covers the single-event read endpoint and the archived-status
preservation contract on event updates.
"""

import re
import sqlite3
import time
from pathlib import Path

from tests.schema import MIGRATIONS
from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)

PROJECT_ROOT = Path(__file__).parents[2]

EVENT_PAYLOAD = {
    "name": "Speaker Summit",
    "starts_at_ms": 1_900_000_000_000,
    "ends_at_ms": 1_900_086_400_000,
    "time_zone": "Asia/Kolkata",
    "delivery_mode": "hybrid",
    "location": "Mumbai",
    "description": "Speaker conference",
}




async def _bootstrap_admin(client, connection: sqlite3.Connection):
    bootstrap = await client.post(
        "/api/v1/bootstrap",
        headers={"x-bootstrap-token": _deployment_key(connection)},
        json={
            "organization_name": "Summit Events",
            "admin_name": "Root Admin",
            "admin_email": "root@example.com",
        },
    )
    assert bootstrap.status_code == 200
    requested = await client.post(
        "/api/v1/auth/magic-links",
        json={"email": "root@example.com", "redirect_path": "/admin"},
    )
    assert requested.status_code == 202
    row = connection.execute(
        """SELECT html_body FROM communication_messages
           WHERE recipient_email='root@example.com'
           ORDER BY queued_at_ms DESC,id DESC LIMIT 1"""
    ).fetchone()
    token = re.search(r"/auth/verify\?token=([^\"<]+)", row[0]).group(1)
    confirmed = await client.post(f"/auth/verify?token={token}", follow_redirects=False)
    assert confirmed.status_code == 303
    session = (await client.get("/api/v1/auth/session")).json()
    organization_id = session["organization_access"][0]["organization_id"]
    return session["csrf_token"], organization_id


def _mutation(csrf: str) -> dict[str, str]:
    return {"origin": "https://test", "x-csrf-token": csrf}


async def test_event_creation_is_idempotent_and_rejects_key_reuse(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        url = f"/api/v1/admin/organizations/{organization_id}/events"
        headers = {
            **_mutation(csrf),
            "idempotency-key": "event-create-retry-2026-08-14",
        }

        created = await root.post(url, headers=headers, json=EVENT_PAYLOAD)
        replayed = await root.post(url, headers=headers, json=EVENT_PAYLOAD)

        assert created.status_code == 201, created.text
        assert replayed.status_code == 201, replayed.text
        assert replayed.json() == created.json()
        event_id = created.json()["id"]
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE id=?", (event_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM owned_resources WHERE id=? AND owner_user_id="
            "(SELECT id FROM users WHERE normalized_email='root@example.com')",
            (event_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='event.create' AND event_id=?",
            (event_id,),
        ).fetchone()[0] == 1

        changed = await root.post(
            url,
            headers=headers,
            json={**EVENT_PAYLOAD, "name": "Different Summit"},
        )
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "conflict"


async def test_event_creation_rejects_malformed_idempotency_key(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        response = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers={**_mutation(csrf), "idempotency-key": "too-short"},
            json=EVENT_PAYLOAD,
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"


async def test_exact_event_grants_can_be_created_updated_listed_and_revoked(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf), json=EVENT_PAYLOAD,
        )
        event_id = event.json()["id"]
        now = int(time.time() * 1000)
        connection.execute(
            """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES('grant-user','grantee@example.com','grantee@example.com','active',?,?)""",
            (now, now),
        )
        connection.commit()

        created = await root.post(
            f"/api/v1/admin/events/{event_id}/access-grants",
            headers=_mutation(csrf),
            json={"email": "grantee@example.com", "permission": "view"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["permission"] == "view"
        listed = await root.get(f"/api/v1/admin/events/{event_id}/access-grants")
        assert [(row["email"], row["permission"]) for row in listed.json()["data"]] == [
            ("root@example.com", "owner"), ("grantee@example.com", "view")
        ]

        updated = await root.patch(
            f"/api/v1/admin/events/{event_id}/access-grants/grant-user",
            headers=_mutation(csrf), json={"permission": "manage"},
        )
        assert updated.status_code == 200
        assert updated.json()["permission"] == "manage"
        assert connection.execute(
            """SELECT permission FROM resource_access_grants
               WHERE resource_id=? AND user_id='grant-user' AND status='active'""",
            (event_id,),
        ).fetchone()[0] == "manage"

        owner_id = connection.execute(
            "SELECT owner_user_id FROM owned_resources WHERE id=?", (event_id,)
        ).fetchone()[0]
        denied = await root.patch(
            f"/api/v1/admin/events/{event_id}/access-grants/{owner_id}",
            headers=_mutation(csrf), json={"permission": "view"},
        )
        assert denied.status_code == 409

        revoked = await root.delete(
            f"/api/v1/admin/events/{event_id}/access-grants/grant-user",
            headers={**_mutation(csrf), "content-type": "application/json"},
        )
        assert revoked.status_code == 204, revoked.text
        assert connection.execute(
            """SELECT status FROM resource_access_grants
               WHERE resource_id=? AND user_id='grant-user' AND permission='manage'""",
            (event_id,),
        ).fetchone()[0] == "revoked"
        assert connection.execute(
            """SELECT COUNT(*) FROM audit_events
               WHERE event_id=? AND action LIKE 'resource_access_grant.%'""",
            (event_id,),
        ).fetchone()[0] == 3


async def test_event_editor_cannot_promote_own_access_over_http(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        event_id = created.json()["id"]
        owner_id = connection.execute(
            "SELECT owner_user_id FROM owned_resources WHERE id=?", (event_id,)
        ).fetchone()[0]
        now = int(time.time() * 1000)
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES('editor-user','editor@example.com','editor@example.com','active',?,?)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES('editor-member',?,'editor-user','member','active',?,?)""",
            (organization_id, now, now),
        )
        connection.execute(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES('editor-user','organizer','active',?,?,1)""",
            (now, now),
        )
        connection.commit()
        granted = await root.post(
            f"/api/v1/admin/events/{event_id}/access-grants",
            headers=_mutation(csrf),
            json={"email": "editor@example.com", "permission": "edit"},
        )
        assert granted.status_code == 201, granted.text

        async with _client(environment) as editor:
            requested = await editor.post(
                "/api/v1/auth/magic-links",
                json={"email": "editor@example.com", "redirect_path": "/account"},
            )
            assert requested.status_code == 202
            signed_in = await editor.post(
                f"/auth/verify?token={_token(connection, 'editor@example.com')}",
                follow_redirects=False,
            )
            assert signed_in.status_code == 303
            editor_session = (await editor.get("/api/v1/auth/session")).json()

            denied_list = await editor.get(
                f"/api/v1/admin/events/{event_id}/access-grants"
            )
            denied_patch = await editor.patch(
                f"/api/v1/admin/events/{event_id}/access-grants/editor-user",
                headers=_mutation(editor_session["csrf_token"]),
                json={"permission": "manage"},
            )
            denied_invite = await editor.post(
                f"/api/v1/admin/events/{event_id}/invitations",
                headers=_mutation(editor_session["csrf_token"]),
                json={"email": "editor@example.com", "role": "event_admin"},
            )
            # Exact-resource authorization failures are deliberately 404 so
            # an editor cannot use access administration to enumerate scope.
            assert (denied_list.status_code, denied_patch.status_code) == (404, 404)
            assert denied_invite.status_code == 404
            assert connection.execute(
                """SELECT permission FROM resource_access_grants
                   WHERE resource_id=? AND user_id='editor-user' AND status='active'""",
                (event_id,),
            ).fetchone()[0] == "edit"

            invited = await root.post(
                f"/api/v1/admin/events/{event_id}/invitations",
                headers=_mutation(csrf),
                json={"email": "editor@example.com", "role": "event_admin"},
            )
            assert invited.status_code == 201, invited.text
            accepted = await editor.post(
                f"/auth/verify?token={_token(connection, 'editor@example.com')}",
                follow_redirects=False,
            )
            assert accepted.status_code == 303, accepted.text

            active_permissions = connection.execute(
                """SELECT permission FROM resource_access_grants
                   WHERE resource_id=? AND user_id='editor-user' AND status='active'
                   ORDER BY permission""",
                (event_id,),
            ).fetchall()
            assert [row[0] for row in active_permissions] == ["manage"]
            assert connection.execute(
                """SELECT granted_by_user_id FROM resource_access_grants
                   WHERE resource_id=? AND user_id='editor-user'
                     AND permission='manage' AND status='active'""",
                (event_id,),
            ).fetchone()[0] == owner_id


async def test_draft_can_store_past_dates_but_active_creation_and_activation_cannot(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    now = int(time.time() * 1000)
    past_event = {
        **EVENT_PAYLOAD,
        "name": "Imported conference archive",
        "starts_at_ms": now - 172_800_000,
        "ends_at_ms": now - 86_400_000,
    }
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        url = f"/api/v1/admin/organizations/{organization_id}/events"

        active = await root.post(url, headers=_mutation(csrf), json=past_event)
        assert active.status_code == 422, active.text
        assert active.json()["error"]["code"] == "request_failed"

        draft = await root.post(
            url,
            headers=_mutation(csrf),
            json={**past_event, "status": "draft"},
        )
        assert draft.status_code == 201, draft.text
        assert draft.json()["status"] == "draft"

        activation = await root.patch(
            f"/api/v1/admin/events/{draft.json()['id']}",
            headers=_mutation(csrf),
            json={**past_event, "status": "active", "version": 1},
        )
        assert activation.status_code == 422, activation.text
        assert activation.json()["error"]["code"] == "request_failed"

        stored = connection.execute(
            "SELECT status,version FROM events WHERE id=?", (draft.json()["id"],)
        ).fetchone()
        assert tuple(stored) == ("draft", 1)


async def _accept_invitation(client, connection, email: str) -> dict[str, object]:
    """Accept the latest emailed invitation link in a fresh browser.

    The acceptance link is intentionally NOT part of the invitation API
    response (it would let the inviter sign in as the invitee), so tests
    read it from the queued invitation email like a real invitee would.
    """
    confirmed = await client.post(
        f"/auth/verify?token={_token(connection, email)}", follow_redirects=False
    )
    assert confirmed.status_code == 303, confirmed.text
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    return session.json()


async def test_second_event_admin_can_manage_but_not_escalate(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        assert created.status_code == 201, created.text
        event = created.json()
        invited = await root.post(
            f"/api/v1/admin/events/{event['id']}/invitations",
            headers=_mutation(csrf),
            json={"email": "helper@example.com", "role": "event_admin"},
        )
        assert invited.status_code == 201, invited.text
        assert "accept_url" not in invited.json()

        async with _client(environment) as helper:
            helper_session = await _accept_invitation(helper, connection, "helper@example.com")
            assert helper_session["profile_complete"] is False
            assert helper_session["event_access"] == [
                {
                    "organization_id": organization_id,
                    "event_id": event["id"],
                    "event_name": event["name"],
                    "permissions": ["manage"],
                    "assignments": [],
                }
            ]
            assert helper_session["organization_access"] == []
            account_page = await helper.get("/account", follow_redirects=False)
            assert account_page.status_code == 200
            assert 'data-auth-shell' in account_page.text
            helper_csrf = helper_session["csrf_token"]

            # The second organizer can read and manage the event directly.
            single = await helper.get(f"/api/v1/admin/events/{event['id']}")
            assert single.status_code == 200
            assert single.json()["time_zone"] == "Asia/Kolkata"
            updated = await helper.patch(
                f"/api/v1/admin/events/{event['id']}",
                headers=_mutation(helper_csrf),
                json={**EVENT_PAYLOAD, "name": "Speaker Summit 2027", "version": 1},
            )
            assert updated.status_code == 200, updated.text
            assert updated.json()["name"] == "Speaker Summit 2027"
            assert updated.json()["status"] == "active"
            speaker_invite = await helper.post(
                f"/api/v1/admin/events/{event['id']}/invitations",
                headers=_mutation(helper_csrf),
                json={"email": "talent@example.com", "role": "speaker", "display_name": "Talent"},
            )
            assert speaker_invite.status_code == 201, speaker_invite.text

            # ...but cannot create events or escalate anyone to org admin.
            denied_create = await helper.post(
                f"/api/v1/admin/organizations/{organization_id}/events",
                headers=_mutation(helper_csrf),
                json=EVENT_PAYLOAD,
            )
            assert denied_create.status_code == 404
            denied_escalation = await helper.post(
                f"/api/v1/admin/events/{event['id']}/invitations",
                headers=_mutation(helper_csrf),
                json={"email": "helper@example.com", "role": "organization_admin"},
            )
            assert denied_escalation.status_code == 404
        members = await root.get(f"/api/v1/admin/events/{event['id']}/members")
        assert all(member["role"] != "event_admin" for member in members.json()["data"])
        assert connection.execute(
            """SELECT permission FROM resource_access_grants
               WHERE resource_id=? AND user_id=(
                 SELECT id FROM users WHERE normalized_email='helper@example.com'
               ) AND status='active'""",
            (event["id"],),
        ).fetchone()["permission"] == "manage"
    assert connection.execute(
        "SELECT COUNT(*) FROM organization_memberships WHERE role='organization_admin'"
    ).fetchone()[0] == 1


async def test_organization_admin_is_invitable_and_shares_org_control(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        assert created.status_code == 201
        event_id = created.json()["id"]
        invited = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "co-owner@example.com", "role": "organization_admin"},
        )
        assert invited.status_code == 201, invited.text

        async with _client(environment) as co_owner:
            session = await _accept_invitation(co_owner, connection, "co-owner@example.com")
            assert session["organization_access"] == [{
                "organization_id": organization_id,
                "organization_name": "Summit Events",
                "permissions": ["manage"],
            }]
            # Organization-wide control: the co-owner can create further events.
            second_event = await co_owner.post(
                f"/api/v1/admin/organizations/{organization_id}/events",
                headers=_mutation(session["csrf_token"]),
                json={**EVENT_PAYLOAD, "name": "Winter Summit"},
            )
            assert second_event.status_code == 201, second_event.text
    assert connection.execute(
        "SELECT COUNT(*) FROM organization_memberships WHERE role='organization_admin' "
        "AND status='active'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM resource_access_grants WHERE resource_id=? "
        "AND permission='manage' AND status='active'",
        (organization_id,),
    ).fetchone()[0] == 1


async def test_event_update_without_status_preserves_archived(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        event = created.json()
        archived = await root.patch(
            f"/api/v1/admin/events/{event['id']}",
            headers=_mutation(csrf),
            json={**EVENT_PAYLOAD, "status": "archived", "version": 1},
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"

        archived_at_ms = connection.execute(
            "SELECT archived_at_ms FROM events WHERE id=?", (event["id"],)
        ).fetchone()[0]
        assert archived_at_ms is not None

        now = int(time.time() * 1000)
        past_archive = {
            **EVENT_PAYLOAD,
            "name": "Historical event with branding",
            "starts_at_ms": now - 172_800_000,
            "ends_at_ms": now - 86_400_000,
            "status": "archived",
            "version": 2,
        }
        historical = await root.patch(
            f"/api/v1/admin/events/{event['id']}",
            headers=_mutation(csrf),
            json=past_archive,
        )
        assert historical.status_code == 200, historical.text
        assert historical.json()["status"] == "archived"
        assert historical.json()["ends_at_ms"] < now

        # A partial-intent update that omits status must not resurrect it —
        # and must not restamp the original archive timestamp either.
        renamed = await root.patch(
            f"/api/v1/admin/events/{event['id']}",
            headers=_mutation(csrf),
            json={**past_archive, "name": "Renamed while archived", "version": 3},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["status"] == "archived"
        assert connection.execute(
            "SELECT archived_at_ms FROM events WHERE id=?", (event["id"],)
        ).fetchone()[0] == archived_at_ms
        # The single-event read keeps archived events visible to admins.
        single = await root.get(f"/api/v1/admin/events/{event['id']}")
        assert single.status_code == 200
        assert single.json()["status"] == "archived"
    assert connection.execute(
        "SELECT status,archived_at_ms FROM events WHERE id=?", (event["id"],)
    ).fetchone()[0] == "archived"


async def test_single_event_read_denies_outsiders(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        event_id = created.json()["id"]
    async with _client(environment) as anonymous:
        denied = await anonymous.get(f"/api/v1/admin/events/{event_id}")
        assert denied.status_code == 401


def test_console_gates_privileged_entry_points_by_real_permission() -> None:
    static = PROJECT_ROOT / "src" / "sessionbuddy" / "static"
    shell = (static / "app_shell.js").read_text(encoding="utf-8")
    events = (static / "events_admin.js").read_text(encoding="utf-8")
    access = (static / "access_admin.js").read_text(encoding="utf-8")

    # People directory requires organization management; nav renders only then.
    assert "function canManageOrganization(session)" in shell
    assert shell.count('navLink("People", "/admin/speakers"') == 2
    # Event sub-nav uses exact event authority, or an API permission probe for
    # the selected event — never authority over some unrelated resource.
    assert "administersEventDirectly(session, currentEventId)" in shell
    assert "/api/v1/admin/events/${encodeURIComponent(currentEventId)}" in shell
    # Create event tracks the selected organization's exact manage permission.
    assert "state.adminOrganizationIds" in events
    assert "updateCreateAccess(event.currentTarget.value)" in events
    # Organization management invitations are presented as exact resource
    # permissions and offered only for this event's own organization.
    assert "manageableOrganizationIds.has(selectedEvent.organization_id)" in access
    assert '["owner", "manage"].includes(permission)' in access
    assert 'event_admin: "Can manage this event"' in access
    assert 'organization_admin: "Can manage this organization"' in access
    # Destructive access changes require an explicit second click and surface
    # failures.
    assert "destructiveButton(" in access
    assert "Select again to revoke" in access
    # The single-event endpoint replaced the all-orgs scans.
    for name in (
        "event_overview.js",
        "event_workspace.js",
        "admin_submissions.js",
        "admin_programs.js",
    ):
        script = (static / name).read_text(encoding="utf-8")
        assert "organizations/${encodeURIComponent(organization.id)}/events" not in script, name
        assert "/api/v1/admin/events/${encodeURIComponent(" in script, name


async def test_event_invitation_cannot_restore_revoked_org_admin(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Revoked org admin + later speaker invitation => plain member, not admin."""
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        event_id = created.json()["id"]
        co_owner_invite = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "former@example.com", "role": "organization_admin"},
        )
        assert co_owner_invite.status_code == 201, co_owner_invite.text
        async with _client(environment) as former:
            await _accept_invitation(former, connection, "former@example.com")
        # Offboard the delegated manager (no dedicated endpoint yet: direct revocation).
        connection.execute(
            """UPDATE organization_memberships SET status='revoked',revoked_at_ms=1
               WHERE user_id=(SELECT id FROM users WHERE normalized_email='former@example.com')""",
        )
        connection.execute(
            """UPDATE resource_access_grants
               SET status='revoked',revoked_at_ms=1,
                   revoked_by_user_id=(SELECT owner_user_id FROM owned_resources
                                       WHERE id=resource_access_grants.resource_id)
               WHERE user_id=(SELECT id FROM users
                              WHERE normalized_email='former@example.com')"""
        )
        connection.execute(
            """UPDATE users SET authorization_version=authorization_version+1
               WHERE normalized_email='former@example.com'"""
        )
        connection.commit()

        # An event admin later invites the same address as a speaker.
        helper_invite = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "helper@example.com", "role": "event_admin"},
        )
        assert helper_invite.status_code == 201, helper_invite.text
        async with _client(environment) as helper:
            helper_session = await _accept_invitation(helper, connection, "helper@example.com")
            speaker_invite = await helper.post(
                f"/api/v1/admin/events/{event_id}/invitations",
                headers=_mutation(helper_session["csrf_token"]),
                json={
                    "email": "former@example.com",
                    "role": "speaker",
                    "display_name": "Former Admin",
                },
            )
            assert speaker_invite.status_code == 201, speaker_invite.text
        async with _client(environment) as former:
            session = await _accept_invitation(former, connection, "former@example.com")
            # Reactivated as a speaker only — the admin role did not return.
            assert session["organization_access"] == []
            assert session["event_access"] == [
                {
                    "organization_id": organization_id,
                    "event_id": event_id,
                    "event_name": "Speaker Summit",
                    "permissions": [],
                    "assignments": ["speaker"],
                }
            ]
    membership = connection.execute(
        """SELECT role,status FROM organization_memberships
           WHERE user_id=(SELECT id FROM users WHERE normalized_email='former@example.com')"""
    ).fetchone()
    assert tuple(membership) == ("member", "active")


async def test_event_admin_cannot_manage_org_admin_invitations(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        event_id = created.json()["id"]
        co_owner_invite = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "co-owner@example.com", "role": "organization_admin"},
        )
        assert co_owner_invite.status_code == 201
        invitation_id = co_owner_invite.json()["id"]
        helper_invite = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "helper@example.com", "role": "event_admin"},
        )
        assert helper_invite.status_code == 201, helper_invite.text
        async with _client(environment) as helper:
            helper_session = await _accept_invitation(helper, connection, "helper@example.com")
            helper_headers = _mutation(helper_session["csrf_token"])
            resent = await helper.post(
                f"/api/v1/admin/events/{event_id}/invitations/{invitation_id}/resend",
                headers={**helper_headers, "content-type": "application/json"},
                json={},
            )
            assert resent.status_code == 404, resent.text
            revoked = await helper.delete(
                f"/api/v1/admin/events/{event_id}/invitations/{invitation_id}",
                headers={**helper_headers, "content-type": "application/json"},
            )
            assert revoked.status_code == 404, revoked.text
        # The organization admin still can.
        resent = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations/{invitation_id}/resend",
            headers={**_mutation(csrf), "content-type": "application/json"},
            json={},
        )
        assert resent.status_code == 200, resent.text
        revoked = await root.delete(
            f"/api/v1/admin/events/{event_id}/invitations/{invitation_id}",
            headers={**_mutation(csrf), "content-type": "application/json"},
        )
        assert revoked.status_code == 204, revoked.text
    assert connection.execute(
        "SELECT status FROM identity_invitations WHERE id=?", (invitation_id,)
    ).fetchone()[0] == "revoked"


def test_reactivation_never_restores_revoked_roles_in_sql() -> None:
    """Both membership upserts demote a revoked row to member on reactivation."""
    access = (
        PROJECT_ROOT / "src" / "sessionbuddy" / "platform" / "auth" / "access.py"
    ).read_text(encoding="utf-8")
    cfp = (PROJECT_ROOT / "src" / "sessionbuddy" / "cfp" / "router.py").read_text(
        encoding="utf-8"
    )
    guard = "role=CASE WHEN organization_memberships.status='revoked'"
    assert guard in access
    assert guard in cfp


def _seed_events(connection, count: int, *, start: int = 1_900_000_000_000) -> None:
    owner_user_id = connection.execute(
        "SELECT owner_user_id FROM owned_resources WHERE resource_type='organization'"
    ).fetchone()[0]
    for index in range(count):
        connection.execute(
            "INSERT INTO events(id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,"
            "location,delivery_mode,description,status,created_at_ms,updated_at_ms) "
            "VALUES(?,?,?,?,?,'UTC','Online','virtual','Seeded','active',1,1)",
            (
                f"seed-event-{index:03d}",
                connection.execute("SELECT id FROM organizations").fetchone()[0],
                f"Seeded event {index:03d}",
                start + index * 60_000,
                start + index * 60_000 + 1,
            ),
        )
        connection.execute(
            "INSERT INTO owned_resources"
            "(id,resource_type,created_by_user_id,owner_user_id,status,"
            "created_at_ms,updated_at_ms) "
            "VALUES(?,'event',?,?,'active',1,1)",
            (f"seed-event-{index:03d}", owner_user_id, owner_user_id),
        )
    connection.commit()


async def test_events_list_paginates_with_signed_cursors(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        del csrf
        _seed_events(connection, 55)

        first = await root.get(f"/api/v1/admin/organizations/{organization_id}/events")
        assert first.status_code == 200
        page_one = first.json()
        assert len(page_one["data"]) == 50
        assert page_one["next_cursor"]

        second = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"cursor": page_one["next_cursor"]},
        )
        assert second.status_code == 200
        page_two = second.json()
        assert len(page_two["data"]) == 5
        assert page_two["next_cursor"] is None

        ids = [event["id"] for event in page_one["data"] + page_two["data"]]
        assert len(ids) == len(set(ids)) == 55
        starts = [event["starts_at_ms"] for event in page_one["data"] + page_two["data"]]
        assert starts == sorted(starts, reverse=True)

        tampered = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"cursor": page_one["next_cursor"][:-4] + "AAAA"},
        )
        assert tampered.status_code == 400


async def test_active_events_can_be_ordered_nearest_upcoming_first(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        _csrf, organization_id = await _bootstrap_admin(root, connection)
        _seed_events(connection, 15)
        # Exercise the deterministic id tie-break at the nearest start time.
        connection.execute(
            "UPDATE events SET starts_at_ms=1900000000000,ends_at_ms=1900000000001 "
            "WHERE id IN ('seed-event-000','seed-event-001')"
        )
        connection.execute(
            "UPDATE events SET status='draft' WHERE id='seed-event-002'"
        )
        connection.execute(
            "UPDATE events SET status='archived',archived_at_ms=2 "
            "WHERE id='seed-event-003'"
        )
        connection.commit()

        first = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"view": "active", "order": "upcoming", "limit": 3},
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert [event["id"] for event in body["data"]] == [
            "seed-event-000",
            "seed-event-001",
            "seed-event-004",
        ]
        assert body["next_cursor"]

        second = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={
                "view": "active",
                "order": "upcoming",
                "limit": 3,
                "cursor": body["next_cursor"],
            },
        )
        assert second.status_code == 200, second.text
        assert [event["id"] for event in second.json()["data"]] == [
            "seed-event-005",
            "seed-event-006",
            "seed-event-007",
        ]

        crossed = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"view": "active", "cursor": body["next_cursor"]},
        )
        assert crossed.status_code == 400


async def test_events_list_filters_event_admins_in_sql(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        event_id = created.json()["id"]
        _seed_events(connection, 10)
        invited = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "helper@example.com", "role": "event_admin"},
        )
        assert invited.status_code == 201, invited.text
        async with _client(environment) as helper:
            await _accept_invitation(helper, connection, "helper@example.com")
            listed = await helper.get(
                f"/api/v1/admin/organizations/{organization_id}/events"
            )
            assert listed.status_code == 200
            body = listed.json()
            # Only the administered event comes back — the seeded events are
            # excluded by the SQL join, not by post-fetch filtering.
            assert [event["id"] for event in body["data"]] == [event_id]
            assert body["next_cursor"] is None


def test_clients_respect_events_pagination() -> None:
    """No client may auto-follow every page: Events paginates interactively,
    Home reads one overview page with bounded fan-out and honest counts, and
    the directory resolves its single event directly."""
    static = PROJECT_ROOT / "src" / "sessionbuddy" / "static"
    home = (static / "admin_home.js").read_text(encoding="utf-8")
    events = (static / "events_admin.js").read_text(encoding="utf-8")
    events_page = (static / "events_admin.html").read_text(encoding="utf-8")
    directory = (static / "speaker_directory.js").read_text(encoding="utf-8")

    scripts = ((home, "admin_home"), (events, "events_admin"), (directory, "speaker_directory"))
    for script, name in scripts:
        assert "listAllEvents" not in script, name
        assert "page < 40" not in script, name

    # Events page: explicit user-driven pagination.
    assert "const cursor = state.nextCursor" in events
    assert "fetchEventsPage(state.organizationId, cursor)" in events
    assert 'byId("load-more-events").hidden = !state.nextCursor' in events
    assert 'id="load-more-events"' in events_page
    assert '${events.length}${state.nextCursor ? "+" : ""}' in events

    # Home: the nearest three active events, aggregate metrics endpoint, and
    # NO per-event speaker fan-out at all.
    assert "/events?view=active&order=upcoming&limit=3" in home
    assert "/metrics" in home
    assert "metrics.event_count" in home and "metrics.speaker_count" in home
    assert "recent_speakers" in home
    assert "speaker-targets" not in home
    assert "listAllEvents" not in home

    # Directory: resolves its event with the single-event endpoint.
    assert "/api/v1/admin/events/${encodeURIComponent(selectedEventId)}" in directory
    scoped_loader = directory.split("loadEventScopedDirectory", 1)[1].split("function ", 1)[0]
    assert "organizations/${encodeURIComponent" not in scoped_loader


async def test_organization_metrics_are_aggregated_and_role_scoped(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        created = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        event_id = created.json()["id"]
        _seed_events(connection, 55)

        metrics = await root.get(f"/api/v1/admin/organizations/{organization_id}/metrics")
        assert metrics.status_code == 200
        assert metrics.json() == {
            "organization_id": organization_id,
            "event_count": 56,
            "speaker_count": 0,
            "session_count": 0,
            "proposal_count": 0,
            "pending_review_count": 0,
            "recent_speakers": [],
        }

        # The events list itself honours a smaller page for dashboards.
        overview = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events", params={"limit": 12}
        )
        assert len(overview.json()["data"]) == 12
        assert overview.json()["next_cursor"]

        invited = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "helper@example.com", "role": "event_admin"},
        )
        assert invited.status_code == 201, invited.text
        async with _client(environment) as helper:
            await _accept_invitation(helper, connection, "helper@example.com")
            scoped = await helper.get(
                f"/api/v1/admin/organizations/{organization_id}/metrics"
            )
            assert scoped.status_code == 200
            # Event admins see counts over the events they administer only.
            assert scoped.json()["event_count"] == 1
            assert scoped.json()["speaker_count"] == 0
            assert scoped.json()["session_count"] == 0
            assert scoped.json()["proposal_count"] == 0
            assert scoped.json()["pending_review_count"] == 0
    async with _client(environment) as anonymous:
        denied = await anonymous.get(
            f"/api/v1/admin/organizations/{organization_id}/metrics"
        )
        assert denied.status_code == 401


def _seed_speaker_graph(connection) -> None:
    """One person speaking (with proposals) at two events, one person without
    any proposal: unique-people count must be exactly 1."""
    organization_id = connection.execute("SELECT id FROM organizations").fetchone()[0]
    owner_user_id = connection.execute(
        "SELECT owner_user_id FROM owned_resources WHERE id=?", (organization_id,)
    ).fetchone()[0]
    now = 1_500_000
    for suffix in ("a", "b"):
        connection.execute(
            "INSERT INTO events(id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,"
            "location,delivery_mode,description,status,created_at_ms,updated_at_ms) "
            "VALUES(?,?,?,?,?,'UTC','Online','virtual','D','active',?,?)",
            (f"spk-event-{suffix}", organization_id, f"Speaker event {suffix}",
             1_910_000_000_000, 1_910_000_000_001, now, now),
        )
        connection.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,
                created_at_ms,updated_at_ms)
               VALUES(?,'event',?,?,'active',?,?)""",
            (f"spk-event-{suffix}", owner_user_id, owner_user_id, now, now),
        )
    connection.execute(
        "INSERT INTO people(id,organization_id,display_name,created_at_ms,updated_at_ms) "
        "VALUES('person-1',?,'Repeat Speaker',?,?)",
        (organization_id, now, now),
    )
    connection.execute(
        "INSERT INTO people(id,organization_id,display_name,created_at_ms,updated_at_ms) "
        "VALUES('person-2',?,'No Proposal Person',?,?)",
        (organization_id, now, now),
    )
    for index, suffix in enumerate(("a", "b")):
        connection.execute(
            "INSERT INTO event_speakers(id,organization_id,event_id,person_id,status,"
            "accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms,selection_status) "
            "VALUES(?,?,?,'person-1','onboarding',?,?,?,?,'submitted')",
            (f"es-{suffix}", organization_id, f"spk-event-{suffix}",
             now, now + index, now, now),
        )
        connection.execute(
            "INSERT INTO call_for_speaker_forms(id,organization_id,event_id,version,slug,"
            "welcome_text,schema_json,status,published_at_ms,created_at_ms,updated_at_ms) "
            "VALUES(?,?,?,1,?,'Welcome','{}','published',?,?,?)",
            (f"form-{suffix}", organization_id, f"spk-event-{suffix}",
             f"spk-form-{suffix}", now, now, now),
        )
        connection.execute(
            "INSERT INTO submissions(id,organization_id,event_id,form_id,public_session_id,"
            "proposal_title,proposal_abstract,speaker_name,speaker_email,status,"
            "submitted_at_ms,created_at_ms,updated_at_ms) "
            "VALUES(?,?,?,?,'seed-session-0123456789',?,'Abstract','Repeat Speaker',"
            "'r@x.t','submitted',?,?,?)",
            (f"sub-{suffix}", organization_id, f"spk-event-{suffix}", f"form-{suffix}",
             f"Talk {suffix.upper()}", now, now, now),
        )
        connection.execute(
            "INSERT INTO submission_speakers(id,organization_id,event_id,submission_id,"
            "event_speaker_id,role,snapshot_name,created_at_ms) "
            "VALUES(?,?,?,?,?,'primary','Repeat Speaker',?)",
            (f"ss-{suffix}", organization_id, f"spk-event-{suffix}", f"sub-{suffix}",
             f"es-{suffix}", now),
        )
    connection.execute(
        "INSERT INTO event_speakers(id,organization_id,event_id,person_id,status,"
        "accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms,selection_status) "
        "VALUES('es-empty',?,'spk-event-a','person-2','onboarding',?,?,?,?,'submitted')",
        (organization_id, now, now, now, now),
    )
    connection.commit()


async def test_speaker_metric_counts_unique_people_and_ships_recent_speakers(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        _csrf, organization_id = await _bootstrap_admin(root, connection)
        _seed_speaker_graph(connection)
        metrics = await root.get(f"/api/v1/admin/organizations/{organization_id}/metrics")
        assert metrics.status_code == 200
        body = metrics.json()
        assert body["event_count"] == 2
        # One person at two events counts once; the proposal-less person not at all.
        assert body["speaker_count"] == 1
        assert body["session_count"] == 0
        assert body["proposal_count"] == 2
        assert body["pending_review_count"] == 0
        recent = body["recent_speakers"]
        assert [entry["person_id"] for entry in recent] == ["person-1", "person-1"]
        assert {entry["proposal_title"] for entry in recent} == {"Talk A", "Talk B"}
        assert all(entry["display_name"] == "Repeat Speaker" for entry in recent)


def test_metrics_speaker_queries_use_the_speaker_index() -> None:
    """The proposal-attachment probe must ride idx_submission_speakers_speaker,
    never a full scan of submission_speakers."""
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    plan = " ".join(
        str(value)
        for row in connection.execute(
            """EXPLAIN QUERY PLAN
               SELECT COUNT(DISTINCT es.person_id) FROM event_speakers es
               WHERE es.organization_id=? AND EXISTS (
                 SELECT 1 FROM submission_speakers ss
                 WHERE ss.organization_id=es.organization_id
                   AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id)""",
            ("org",),
        )
        for value in row
    )
    assert "idx_submission_speakers_speaker" in plan
    assert "SCAN ss" not in plan
    access = (
        PROJECT_ROOT / "src" / "sessionbuddy" / "platform" / "auth" / "access.py"
    ).read_text(encoding="utf-8")
    assert "COUNT(DISTINCT es.person_id)" in access
    assert access.count("ss.organization_id=es.organization_id") >= 3


async def test_events_view_filter_and_search_run_server_side(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        _seed_events(connection, 55)
        # Age one old seeded event into 'archived' and rename another.
        connection.execute(
            "UPDATE events SET status='archived',archived_at_ms=2 WHERE id='seed-event-000'"
        )
        connection.execute(
            "UPDATE events SET name='Solstice Retreat' WHERE id='seed-event-001'"
        )
        connection.commit()

        # The archived (oldest, beyond page one of 'all') event is still found
        # by the server-side 'past' view without loading any other page.
        past = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"view": "past"},
        )
        assert past.status_code == 200
        assert [event["id"] for event in past.json()["data"]] == ["seed-event-000"]

        found = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"q": "solstice"},
        )
        assert [event["name"] for event in found.json()["data"]] == ["Solstice Retreat"]

        # LIKE wildcards in the query are literals, not patterns.
        wildcard = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"q": "%"},
        )
        assert wildcard.json()["data"] == []

        unknown_view = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"view": "everything"},
        )
        assert unknown_view.status_code == 422

        # A cursor minted under one view/query cannot page a different one.
        first = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"view": "active", "limit": 10},
        )
        cursor = first.json()["next_cursor"]
        assert cursor
        crossed = await root.get(
            f"/api/v1/admin/organizations/{organization_id}/events",
            params={"view": "past", "cursor": cursor},
        )
        assert crossed.status_code == 400
        del csrf


async def test_malformed_cursors_return_structured_400(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        _csrf, organization_id = await _bootstrap_admin(root, connection)
        for malformed in ("a.a", "!!!.???", "a", "\u05d0.\u05d1", "YQ.YQ", "..", "a."):
            response = await root.get(
                f"/api/v1/admin/organizations/{organization_id}/events",
                params={"cursor": malformed},
            )
            assert response.status_code == 400, (malformed, response.status_code)
            assert response.json()["error"]["code"] == "invalid_request", malformed

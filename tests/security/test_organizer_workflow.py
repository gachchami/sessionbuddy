"""Organization-admin workflows driven end to end over HTTP.

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
    token = re.search(r"/auth/verify#token=([^\"<]+)", row[0]).group(1)
    confirmed = await client.post(
        "/auth/verify", data={"token": token}, follow_redirects=False
    )
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
            "SELECT COUNT(*) FROM events WHERE id=? AND created_by_user_id="
            "(SELECT id FROM users WHERE normalized_email='root@example.com')",
            (event_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM owned_resources WHERE id=?", (event_id,)
        ).fetchone()[0] == 0
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


async def test_exact_event_grant_api_is_retired(
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
        create = await root.post(
            f"/api/v1/admin/events/{event_id}/access-grants",
            headers=_mutation(csrf),
            json={"email": "grantee@example.com", "permission": "manage"},
        )
        listed = await root.get(f"/api/v1/admin/events/{event_id}/access-grants")
        update = await root.patch(
            f"/api/v1/admin/events/{event_id}/access-grants/grant-user",
            headers=_mutation(csrf), json={"permission": "manage"},
        )
        revoke = await root.delete(
            f"/api/v1/admin/events/{event_id}/access-grants/grant-user",
            headers={**_mutation(csrf), "content-type": "application/json"},
        )
        assert [create.status_code, listed.status_code, update.status_code, revoke.status_code] == [
            404,
            404,
            404,
            404,
        ]


async def test_organization_grants_only_create_manage_authority(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        now = int(time.time() * 1000)
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES('editor-user','editor@example.com','editor@example.com','active',?,?)""",
            (now, now),
        )
        connection.commit()
        rejected = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/access-grants",
            headers=_mutation(csrf),
            json={"email": "editor@example.com", "permission": "edit"},
        )
        assert rejected.status_code == 422
        granted = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/access-grants",
            headers=_mutation(csrf),
            json={"email": "editor@example.com"},
        )
        assert granted.status_code == 201, granted.text
        assert granted.json()["permission"] == "manage"
        update = await root.patch(
            f"/api/v1/admin/organizations/{organization_id}/access-grants/editor-user",
            headers=_mutation(csrf),
            json={"permission": "manage"},
        )
        assert update.status_code == 405


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
        assert active.json()["error"]["code"] == "validation_failed"

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
        assert activation.json()["error"]["code"] == "validation_failed"

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
        "/auth/verify",
        data={"token": _token(connection, email)},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303, confirmed.text
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    return session.json()


async def test_event_admin_invitation_role_is_rejected(
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
        invited = await root.post(
            f"/api/v1/admin/events/{created.json()['id']}/invitations",
            headers=_mutation(csrf),
            json={"email": "helper@example.com", "role": "event_admin"},
        )
        assert invited.status_code == 422
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_invitations WHERE role='event_admin'"
        ).fetchone()[0] == 0


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
            listed = await co_owner.get(
                f"/api/v1/admin/organizations/{organization_id}/events",
                params={"limit": 1},
            )
            assert listed.status_code == 200, listed.text
            first_page = listed.json()
            assert len(first_page["data"]) == 1
            assert first_page["next_cursor"]
            second_page = await co_owner.get(
                f"/api/v1/admin/organizations/{organization_id}/events",
                params={"limit": 1, "cursor": first_page["next_cursor"]},
            )
            assert second_page.status_code == 200, second_page.text
            assert {
                event["id"] for event in first_page["data"] + second_page.json()["data"]
            } == {event_id, second_event.json()["id"]}
            foreign = await co_owner.get(
                "/api/v1/admin/organizations/foreign-organization/events"
            )
            assert foreign.status_code == 404
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


def test_source_wiring_console_gates_privileged_entry_points_by_real_permission() -> None:
    static = PROJECT_ROOT / "src" / "sessionbuddy" / "static"
    shell = (static / "app_shell.js").read_text(encoding="utf-8")
    home = (static / "admin_home.js").read_text(encoding="utf-8")
    editor = (static / "event_editor.js").read_text(encoding="utf-8")
    access = (static / "access_admin.js").read_text(encoding="utf-8")

    # People navigation consumes the server-resolved organizer workspace;
    # exact organization operations still check the selected resource grant.
    assert 'choice.role === "organizer"' in shell
    assert "managesAnyOrganization" not in shell
    assert (
        "const organizationNavigation = !onboardingLocked && hasOrganizerWorkspace;"
        in shell
    )
    assert "function canManageOrganization(session, organizationId)" in shell
    assert shell.count('navLink("People", "/admin/people"') == 2
    # Event sub-nav comes only from organization authority; event_access is
    # assignment context for speakers and reviewers.
    assert "administersEventDirectly" not in shell
    assert "organizerWorkspace && currentEventId && organizationNavigation" in shell
    # Create event tracks the selected organization's exact manage permission.
    assert "canManageOrganization(state.organizationId)" in home
    assert 'byId("new-event").hidden = !manager' in home
    assert "canManageOrganization(state.session, org.id)" in editor
    # Event participation invitations never manufacture an event administrator
    # or generic event grant; organizers are managed at organization scope.
    assert 'invitation.role === "evaluator"' in access
    assert "/evaluators?email=" in access
    assert "event_admin" not in access
    assert "organization_admin" not in access
    assert "access-grants" not in access
    assert "ownership-transfers" not in access
    assert 'name="display_name"' in (static / "access_admin.html").read_text(
        encoding="utf-8"
    )
    assert "showAccessLink(issued.access_url" in access
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

        speaker_invite = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
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
                    "assignments": ["speaker"],
                }
            ]
    membership = connection.execute(
        """SELECT role,status FROM organization_memberships
           WHERE user_id=(SELECT id FROM users WHERE normalized_email='former@example.com')"""
    ).fetchone()
    assert tuple(membership) == ("member", "active")


async def test_organization_admin_can_resend_and_revoke_org_admin_invitations(
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


async def test_accepted_reviewer_is_not_reinvited_and_can_be_revoked_safely(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, _organization_id = await _bootstrap_admin(root, connection)
        event = await root.post(
            f"/api/v1/admin/organizations/{_organization_id}/events",
            headers=_mutation(csrf), json=EVENT_PAYLOAD,
        )
        event_id = event.json()["id"]
        invited = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "reviewer@example.com", "role": "evaluator"},
        )
        async with _client(environment) as reviewer:
            await _accept_invitation(reviewer, connection, "reviewer@example.com")

        reinvite = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "reviewer@example.com", "role": "evaluator"},
        )
        assert reinvite.status_code == 409
        stored = connection.execute(
            "SELECT status,accepted_at_ms FROM identity_invitations WHERE id=?",
            (invited.json()["id"],),
        ).fetchone()
        assert stored[0] == "accepted" and stored[1] is not None

        await root.post(
            "/api/v1/auth/magic-links",
            json={"email": "root@example.com", "redirect_path": "/admin"},
        )
        await root.post(
            "/auth/verify", data={"token": _token(connection, "root@example.com")},
            follow_redirects=False,
        )
        csrf = (await root.get("/api/v1/auth/session")).json()["csrf_token"]

        revoked = await root.delete(
            f"/api/v1/admin/events/{event_id}/invitations/{invited.json()['id']}",
            headers={**_mutation(csrf), "content-type": "application/json"},
        )
        assert revoked.status_code == 204, revoked.text
        assert connection.execute(
            "SELECT status FROM identity_invitations WHERE id=?", (invited.json()["id"],)
        ).fetchone()[0] == "revoked"
        assert connection.execute(
            """SELECT COUNT(*) FROM audit_events
               WHERE action='evaluator.eligibility.revoke' AND event_id=?""", (event_id,)
        ).fetchone()[0] == 1


async def test_active_event_speaker_cannot_be_reinvited_but_pending_and_other_events_can(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        first_event = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        second_event = await root.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json={**EVENT_PAYLOAD, "name": "Another Speaker Summit"},
        )
        first_event_id = first_event.json()["id"]
        second_event_id = second_event.json()["id"]

        pending = await root.post(
            f"/api/v1/admin/events/{first_event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "speaker@example.com", "role": "speaker"},
        )
        assert pending.status_code == 201, pending.text
        pending_retry = await root.post(
            f"/api/v1/admin/events/{first_event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "SPEAKER@example.com", "role": "speaker"},
        )
        assert pending_retry.status_code == 201, pending_retry.text
        assert pending_retry.json()["id"] == pending.json()["id"]

        now = int(time.time() * 1000)
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,email_verified_at_ms,
                created_at_ms,updated_at_ms)
               VALUES('active-speaker-user','speaker@example.com','speaker@example.com',
                      'active',?,?,?)""",
            (now, now, now),
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES('active-speaker-membership',?,'active-speaker-user','member',
                      'active',?,?)""",
            (organization_id, now, now),
        )
        connection.execute(
            """INSERT INTO people
               (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
               VALUES('active-speaker-person',?,'active-speaker-user','Active Speaker',?,?)""",
            (organization_id, now, now),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,accepted_at_ms,
                last_activity_at_ms,created_at_ms,updated_at_ms,selection_status)
               VALUES('active-event-speaker',?,?,'active-speaker-person','onboarding',
                      ?,?,?,?,'accepted')""",
            (organization_id, first_event_id, now, now, now, now),
        )
        connection.commit()

        duplicate = await root.post(
            f"/api/v1/admin/events/{first_event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "speaker@example.com", "role": "speaker"},
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["error"]["message"] == (
            "This person is already an active speaker for this event"
        )

        other_event = await root.post(
            f"/api/v1/admin/events/{second_event_id}/invitations",
            headers=_mutation(csrf),
            json={"email": "speaker@example.com", "role": "speaker"},
        )
        assert other_event.status_code == 201, other_event.text


def test_source_wiring_reviewer_eligibility_revoke_fails_closed_with_active_assignments() -> None:
    source = (PROJECT_ROOT / "src/sessionbuddy/platform/auth/access.py").read_text()
    assert "Remove this reviewer from active evaluation assignments first" in source
    assert "a.evaluator_user_id=?3 AND a.status!='revoked'" in source


def test_source_wiring_reactivation_never_restores_revoked_roles_in_sql() -> None:
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
            "location,delivery_mode,description,status,created_at_ms,updated_at_ms,"
            "created_by_user_id) "
            "VALUES(?,?,?,?,?,'UTC','Online','virtual','Seeded','active',1,1,?)",
            (
                f"seed-event-{index:03d}",
                connection.execute("SELECT id FROM organizations").fetchone()[0],
                f"Seeded event {index:03d}",
                start + index * 60_000,
                start + index * 60_000 + 1,
                owner_user_id,
            ),
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
        assert tampered.json()["error"]["code"] == "stale_cursor"
        assert tampered.json()["error"]["metadata"] == {"reason": "invalid"}


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
            """UPDATE events SET status='draft',draft_starts_at_ms=starts_at_ms,
                      draft_ends_at_ms=ends_at_ms,draft_delivery_mode=delivery_mode
               WHERE id='seed-event-002'"""
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


async def test_events_list_includes_all_events_for_organization_admins(
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
            json={"email": "helper@example.com", "role": "organization_admin"},
        )
        assert invited.status_code == 201, invited.text
        async with _client(environment) as helper:
            await _accept_invitation(helper, connection, "helper@example.com")
            listed = await helper.get(
                f"/api/v1/admin/organizations/{organization_id}/events"
            )
            assert listed.status_code == 200
            body = listed.json()
            assert event_id in {event["id"] for event in body["data"]}
            assert len(body["data"]) == 11
            assert body["next_cursor"] is None


def test_clients_respect_events_pagination() -> None:
    """No client may auto-follow every page: Events paginates interactively,
    Home stays organization-level, and the directory resolves its event directly."""
    static = PROJECT_ROOT / "src" / "sessionbuddy" / "static"
    home = (static / "admin_home.js").read_text(encoding="utf-8")
    directory = (static / "speaker_directory.js").read_text(encoding="utf-8")

    scripts = ((home, "admin_home"), (directory, "speaker_directory"))
    for script, name in scripts:
        assert "listAllEvents" not in script, name
        assert "page < 40" not in script, name

    # Home is the consolidated event ledger with explicit user-driven pagination.
    assert "await loadEventPage(state.nextCursor)" in home
    assert 'byId("load-more-events").hidden = !state.nextCursor' in home
    assert 'id="load-more-events"' in (
        static / "admin_home.html"
    ).read_text(encoding="utf-8")
    assert '${state.events.length}${state.nextCursor ? "+" : ""}' in home

    # Home avoids legacy aggregate scans and metrics.
    assert "/events?view=" not in home
    assert "/metrics" not in home
    assert "recent_speakers" not in home
    assert "speaker-targets" not in home
    assert "listAllEvents" not in home

    # Directory: resolves its event with the single-event endpoint.
    assert "/api/v1/admin/events/${encodeURIComponent(selectedEventId)}" in directory
    scoped_loader = directory.split("loadEventScopedDirectory", 1)[1].split("function ", 1)[0]
    assert "organizations/${encodeURIComponent" not in scoped_loader


async def test_organization_metrics_are_aggregated_and_admin_scoped(
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
            json={"email": "helper@example.com", "role": "organization_admin"},
        )
        assert invited.status_code == 201, invited.text
        async with _client(environment) as helper:
            await _accept_invitation(helper, connection, "helper@example.com")
            scoped = await helper.get(
                f"/api/v1/admin/organizations/{organization_id}/metrics"
            )
            assert scoped.status_code == 200
            assert scoped.json()["event_count"] == 56
            assert scoped.json()["speaker_count"] == 0
            assert scoped.json()["session_count"] == 0
            assert scoped.json()["proposal_count"] == 0
            assert scoped.json()["pending_review_count"] == 0
    async with _client(environment) as anonymous:
        denied = await anonymous.get(
            f"/api/v1/admin/organizations/{organization_id}/metrics"
        )
        assert denied.status_code == 401


async def test_organization_pending_reviews_equal_the_sum_of_event_rows(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        csrf, organization_id = await _bootstrap_admin(owner, connection)
        created = await owner.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=_mutation(csrf),
            json=EVENT_PAYLOAD,
        )
        assert created.status_code == 201, created.text
        event_id = created.json()["id"]
        evaluator_user_id = connection.execute(
            "SELECT owner_user_id FROM owned_resources WHERE id=?", (organization_id,)
        ).fetchone()[0]
        evaluator_email = connection.execute(
            "SELECT normalized_email FROM users WHERE id=?", (evaluator_user_id,)
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES(?,'reviewer','active',1000,1000,0)""",
            (evaluator_user_id,),
        )
        connection.execute(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,accepted_at_ms,created_at_ms,updated_at_ms)
               VALUES('count-invitation',?,?,?,?, 'evaluator','accepted',?,9999999999999,
                      1000,1000,1000)""",
            (organization_id, event_id, evaluator_email, evaluator_email, evaluator_user_id),
        )
        connection.execute(
            """INSERT INTO call_for_speaker_forms
               (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
                published_at_ms,created_at_ms,updated_at_ms)
               VALUES('count-form',?,?,1,'count-form','Welcome','{"fields":[]}',
                      'published',1000,1000,1000)""",
            (organization_id, event_id),
        )
        cases = (
            ("valid", "submitted", "open", None),
            ("closed", "submitted", "closed", 1001),
            ("withdrawn", "withdrawn", "open", None),
        )
        for suffix, submission_status, round_status, closed_at_ms in cases:
            submission_id = f"count-sub-{suffix}"
            round_id = "count-round-closed" if round_status == "closed" else "count-round-open"
            connection.execute(
                """INSERT INTO submissions
                   (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                    proposal_abstract,speaker_name,speaker_email,status,submitted_at_ms,
                    created_at_ms,updated_at_ms,answers_json,version)
                   VALUES(?,?,?,'count-form',?,?,'Abstract','Speaker','speaker@example.test',
                          ?,1000,1000,1000,'{}',1)""",
                (
                    submission_id,
                    organization_id,
                    event_id,
                    f"public-{suffix}",
                    suffix,
                    submission_status,
                ),
            )
            if connection.execute(
                "SELECT 1 FROM evaluation_rounds WHERE id=?", (round_id,)
            ).fetchone() is None:
                connection.execute(
                    """INSERT INTO evaluation_rounds
                       (id,organization_id,event_id,name,rubric_json,status,created_at_ms,
                        updated_at_ms,closed_at_ms)
                       VALUES(?,?,?,?,'{}',?,1000,1000,?)""",
                    (round_id, organization_id, event_id, suffix, round_status, closed_at_ms),
                )
                connection.execute(
                    """INSERT INTO evaluation_round_evaluators
                       (round_id,evaluator_user_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
                       VALUES(?,?,?,?,'active',1000,1000)""",
                    (round_id, evaluator_user_id, organization_id, event_id),
                )
            connection.execute(
                """INSERT INTO evaluation_round_submissions
                   (round_id,submission_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
                   VALUES(?,?,?,?,'active',1000,1000)""",
                (round_id, submission_id, organization_id, event_id),
            )
            connection.execute(
                """INSERT INTO evaluation_assignments
                   (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                    status,created_at_ms,updated_at_ms)
                   VALUES(?,?,?,?,?,?,'assigned',1000,1000)""",
                (f"count-assignment-{suffix}", organization_id, event_id, round_id,
                 submission_id, evaluator_user_id),
            )
        connection.commit()

        organizations = (await owner.get("/api/v1/admin/organizations")).json()["data"]
        events = (await owner.get(
            f"/api/v1/admin/organizations/{organization_id}/events"
        )).json()["data"]
        assert organizations[0]["pending_review_count"] == 1
        assert sum(event["pending_review_count"] for event in events) == 1

        connection.execute(
            "UPDATE submissions SET status='withdrawn' WHERE id='count-sub-valid'"
        )
        connection.commit()
        organizations = (await owner.get("/api/v1/admin/organizations")).json()["data"]
        events = (await owner.get(
            f"/api/v1/admin/organizations/{organization_id}/events"
        )).json()["data"]
        assert organizations[0]["pending_review_count"] == 0
        assert sum(event["pending_review_count"] for event in events) == 0


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
            "location,delivery_mode,description,status,created_at_ms,updated_at_ms,"
            "created_by_user_id) "
            "VALUES(?,?,?,?,?,'UTC','Online','virtual','D','active',?,?,?)",
            (f"spk-event-{suffix}", organization_id, f"Speaker event {suffix}",
             1_910_000_000_000, 1_910_000_000_001, now, now, owner_user_id),
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
            payload = response.json()["error"]
            assert payload["code"] == "stale_cursor", malformed
            assert payload["metadata"]["reason"] == "invalid", malformed

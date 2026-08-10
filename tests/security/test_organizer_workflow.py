"""Multi-organizer workflow: a second event_admin and an invitable
organization_admin, driven end to end over HTTP.

Also covers the single-event read endpoint and the archived-status
preservation contract on event updates.
"""

import re
import sqlite3
from pathlib import Path

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
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


def _accept_token(accept_url: str) -> str:
    match = re.search(r"token=([^&]+)", accept_url)
    assert match is not None, accept_url
    return match.group(1)


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


async def _accept_invitation(client, accept_url: str) -> dict[str, object]:
    """Accept an invitation in the given fresh browser; return its session."""
    confirmed = await client.post(
        f"/auth/verify?token={_accept_token(accept_url)}", follow_redirects=False
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
        assert invited.json()["accept_url"]

        async with _client(environment) as helper:
            helper_session = await _accept_invitation(helper, invited.json()["accept_url"])
            assert helper_session["event_access"] == [
                {
                    "organization_id": organization_id,
                    "event_id": event["id"],
                    "roles": ["event_admin"],
                }
            ]
            assert helper_session["organization_access"] == []
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
        assert {
            (member["email"], member["role"]) for member in members.json()["data"]
        } >= {("root@example.com", "event_admin"), ("helper@example.com", "event_admin")}
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
            session = await _accept_invitation(co_owner, invited.json()["accept_url"])
            assert session["organization_access"] == [
                {"organization_id": organization_id, "roles": ["organization_admin"]}
            ]
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
    ).fetchone()[0] == 2


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

        # A partial-intent update that omits status must not resurrect it —
        # and must not restamp the original archive timestamp either.
        renamed = await root.patch(
            f"/api/v1/admin/events/{event['id']}",
            headers=_mutation(csrf),
            json={**EVENT_PAYLOAD, "name": "Renamed while archived", "version": 2},
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
    # Event sub-nav: exact event_admin membership, or an API permission probe
    # for organization admins — never "admin of any organization".
    assert "administersEventDirectly(session, currentEventId)" in shell
    assert "/api/v1/admin/events/${encodeURIComponent(currentEventId)}" in shell
    # Create event tracks the SELECTED organization's admin role.
    assert "state.adminOrganizationIds" in events
    assert "updateCreateAccess(event.currentTarget.value)" in events
    # Organization-admin invitations offered only for this event's own org.
    assert "adminOrganizationIds.has(event.organization_id)" in access
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
        async with _client(environment) as former:
            await _accept_invitation(former, co_owner_invite.json()["accept_url"])
        # Offboard the co-owner (no dedicated endpoint yet: direct revocation).
        connection.execute(
            """UPDATE organization_memberships SET status='revoked',revoked_at_ms=1
               WHERE user_id=(SELECT id FROM users WHERE normalized_email='former@example.com')""",
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
        async with _client(environment) as helper:
            helper_session = await _accept_invitation(helper, helper_invite.json()["accept_url"])
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
            session = await _accept_invitation(former, speaker_invite.json()["accept_url"])
            # Reactivated as a speaker only — the admin role did not return.
            assert session["organization_access"] == []
            assert session["event_access"] == [
                {"organization_id": organization_id, "event_id": event_id, "roles": ["speaker"]}
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
        async with _client(environment) as helper:
            helper_session = await _accept_invitation(helper, helper_invite.json()["accept_url"])
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


def test_invitation_rebuild_migration_survives_live_challenges() -> None:
    """Deploying 0045 with pending invitation sign-in challenges must succeed
    and preserve every row, with foreign keys enabled throughout."""
    import sqlite3

    migrations = sorted((PROJECT_ROOT / "migrations").glob("*.sql"))
    target = PROJECT_ROOT / "migrations" / "0045_org_admin_invitations.sql"
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in migrations:
        if migration.name >= target.name:
            break
        connection.executescript(migration.read_text(encoding="utf-8"))
    now = 1_000_000
    connection.execute(
        "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
        "VALUES('org','O','active',?,?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms) "
        "VALUES('root','r@x.t','r@x.t','active',?,?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO organization_memberships"
        "(id,organization_id,user_id,role,status,created_at_ms,updated_at_ms) "
        "VALUES('m1','org','root','organization_admin','active',?,?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO events(id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,"
        "location,delivery_mode,description,status,created_at_ms,updated_at_ms) "
        "VALUES('ev','org','E',?,?,'UTC','X','virtual','D','active',?,?)",
        (now + 10, now + 20, now, now),
    )
    connection.execute(
        "INSERT INTO identity_invitations(id,organization_id,event_id,normalized_email,"
        "email,role,status,invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms) "
        "VALUES('inv','org','ev','h@x.t','h@x.t','event_admin','pending','root',?,?,?)",
        (now + 99_999, now, now),
    )
    connection.execute(
        "INSERT INTO authentication_challenges(id,normalized_email,token_hash,purpose,"
        "provisioning_context,redirect_path,expires_at_ms,created_at_ms,organization_id,"
        "event_id,invitation_id) "
        "VALUES('ch','h@x.t',?,'sign_in','invitation','/admin',?,?,'org','ev','inv')",
        (b"t" * 32, now + 99_999, now),
    )
    connection.commit()

    connection.executescript(target.read_text(encoding="utf-8"))

    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert tuple(
        connection.execute(
            "SELECT role,status FROM identity_invitations WHERE id='inv'"
        ).fetchone()
    ) == ("event_admin", "pending")
    assert connection.execute(
        "SELECT invitation_id FROM authentication_challenges WHERE id='ch'"
    ).fetchone()[0] == "inv"
    # The widened CHECK now admits organization_admin invitations.
    connection.execute(
        "INSERT INTO identity_invitations(id,organization_id,event_id,normalized_email,"
        "email,role,status,invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms) "
        "VALUES('inv2','org','ev','o@x.t','o@x.t','organization_admin','pending','root',?,?,?)",
        (2_000_000, 1_100_000, 1_100_000),
    )
    connection.close()


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
        async with _client(environment) as helper:
            await _accept_invitation(helper, invited.json()["accept_url"])
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
    assert "fetchEventsPage(state.organizationId, state.nextCursor)" in events
    assert 'byId("load-more-events").hidden = !state.nextCursor' in events
    assert 'id="load-more-events"' in events_page
    assert '${events.length}${state.nextCursor ? "+" : ""}' in events

    # Home: a few recent events per organization, aggregate metrics endpoint,
    # and NO per-event speaker fan-out at all.
    assert "/events?limit=12" in home
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
        async with _client(environment) as helper:
            await _accept_invitation(helper, invited.json()["accept_url"])
            scoped = await helper.get(
                f"/api/v1/admin/organizations/{organization_id}/metrics"
            )
            assert scoped.status_code == 200
            # Event admins see counts over the events they administer only.
            assert scoped.json()["event_count"] == 1
            assert scoped.json()["speaker_count"] == 0
    async with _client(environment) as anonymous:
        denied = await anonymous.get(
            f"/api/v1/admin/organizations/{organization_id}/metrics"
        )
        assert denied.status_code == 401


def _seed_speaker_graph(connection) -> None:
    """One person speaking (with proposals) at two events, one person without
    any proposal: unique-people count must be exactly 1."""
    organization_id = connection.execute("SELECT id FROM organizations").fetchone()[0]
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
    for migration in sorted((PROJECT_ROOT / "migrations").glob("*.sql")):
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

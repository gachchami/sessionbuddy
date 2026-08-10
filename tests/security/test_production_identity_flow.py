import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app

MIGRATIONS = sorted((Path(__file__).parents[2] / "migrations").glob("*.sql"))


class SQLiteStatement:
    def __init__(self, connection: sqlite3.Connection, sql: str) -> None:
        self.connection = connection
        self.sql = sql
        self.parameters: tuple[object, ...] = ()

    def bind(self, *values: object) -> "SQLiteStatement":
        self.parameters = values
        return self

    async def first(self, column: str | None = None):
        cursor = self.connection.execute(self.sql, self.parameters)
        row = cursor.fetchone()
        self.connection.commit()
        if row is None:
            return None
        result = dict(row)
        return result[column] if column is not None else result

    async def run(self):
        cursor = self.connection.execute(self.sql, self.parameters)
        self.connection.commit()
        return {"meta": {"changes": max(0, cursor.rowcount)}}

    async def all(self):
        rows = [dict(row) for row in self.connection.execute(self.sql, self.parameters).fetchall()]
        return {"results": rows}


class SQLiteD1:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.prepare_count = 0

    def prepare(self, sql: str) -> SQLiteStatement:
        self.prepare_count += 1
        return SQLiteStatement(self.connection, sql)

    async def batch(self, statements: list[SQLiteStatement]):
        results = []
        try:
            self.connection.execute("BEGIN")
            for statement in statements:
                cursor = self.connection.execute(statement.sql, statement.parameters)
                result: dict[str, object] = {"meta": {"changes": max(0, cursor.rowcount)}}
                if cursor.description is not None:
                    result["results"] = [dict(row) for row in cursor.fetchall()]
                results.append(result)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return results


class AllowingRateLimiter:
    async def limit(self, options: dict[str, str]) -> dict[str, bool]:
        assert options["key"]
        return {"success": True}


class CapturingQueue:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, message: dict[str, object]) -> None:
        self.messages.append(message)


def _token(connection: sqlite3.Connection, email: str) -> str:
    row = connection.execute(
        """SELECT html_body FROM communication_messages
           WHERE recipient_email=? ORDER BY queued_at_ms DESC,id DESC LIMIT 1""",
        (email,),
    ).fetchone()
    assert row is not None
    match = re.search(r"/auth/verify\?token=([^\"<]+)", row[0])
    assert match is not None
    return match.group(1)


def _deployment_key(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        """SELECT deployment_key FROM instance_setup_credentials
           WHERE singleton_key='primary'"""
    ).fetchone()
    assert row is not None
    return str(row[0])


@pytest.fixture
def production_environment():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    queue = CapturingQueue()
    environment = SimpleNamespace(
        APP_ENV="production",
        DB=SQLiteD1(connection),
        SESSION_HMAC_KEY="s" * 32,
        CSRF_HMAC_KEY="c" * 32,
        PASSWORD_PEPPER="p" * 32,
        RATE_LIMIT_HMAC_KEY="r" * 32,
        AUTH_RATE_LIMITER=AllowingRateLimiter(),
        PUBLIC_RATE_LIMITER=AllowingRateLimiter(),
        PUBLIC_BASE_URL="https://test",
        ALLOWED_ORIGINS="https://test",
        COMMUNICATION_QUEUE=queue,
    )
    yield connection, queue, environment
    connection.close()


def _client(environment) -> AsyncClient:
    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    return AsyncClient(transport=ASGITransport(app=inject_environment), base_url="https://test")


async def test_expired_browser_magic_link_has_html_recovery_without_changing_api_contract(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        bootstrap = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Expired Link Events",
                "admin_name": "Admin",
                "admin_email": "admin@example.com",
            },
        )
        assert bootstrap.status_code == 200
        requested = await client.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        assert requested.status_code == 202
        token = _token(connection, "admin@example.com")
        connection.execute(
            "UPDATE authentication_challenges SET created_at_ms=0,expires_at_ms=1"
        )
        connection.commit()

        browser = await client.post(f"/auth/verify?token={token}")
        assert browser.status_code == 404
        assert browser.headers["content-type"].startswith("text/html")
        assert browser.headers["cache-control"] == "no-store"
        assert "This link has expired" in browser.text
        assert 'href="/sign-in"' in browser.text
        assert token not in browser.text

        api = await client.get(f"/api/v1/auth/verify?token={token}")
        assert api.status_code == 404
        assert api.headers["content-type"].startswith("application/json")
        assert api.json()["error"] == {
            "code": "resource_not_found",
            "message": "Resource not found",
        }


async def test_missing_browser_magic_link_token_has_same_recovery_page(
    production_environment,
) -> None:
    _connection, _queue, environment = production_environment
    async with _client(environment) as client:
        response = await client.get("/auth/verify")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert ">Sign in</a>" in response.text


async def test_first_run_setup_creates_named_admin_and_profile_is_editable(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        initial_home = await client.get("/", follow_redirects=False)
        assert initial_home.status_code == 303
        assert initial_home.headers["location"] == "/setup"
        page = await client.get("/setup")
        initial = await client.get("/api/v1/setup/status")
        assert page.status_code == 200
        assert "Set up SessionBuddy" in page.text
        assert initial.json() == {"configured": False}

        created = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Example Events",
                "admin_name": "Asha Rao",
                "admin_email": "asha@example.com",
                "admin_job_title": "Event director",
                "admin_company": "Example Events",
                "admin_time_zone": "Asia/Kolkata",
            },
        )
        assert created.status_code == 200
        assert (await client.get("/api/v1/setup/status")).json() == {"configured": True}
        closed_setup = await client.get("/setup", follow_redirects=False)
        assert closed_setup.status_code == 303
        assert closed_setup.headers["location"] == "/"
        assert "Set up SessionBuddy" not in closed_setup.text
        configured_home = await client.get("/", follow_redirects=False)
        assert configured_home.status_code == 200
        assert "Plan your conference program in one place" in configured_home.text
        assert connection.execute(
            "SELECT COUNT(*) FROM instance_setup WHERE singleton_key='primary'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM instance_setup_credentials"
        ).fetchone()[0] == 0
        user = connection.execute(
            """SELECT display_name,job_title,company,time_zone FROM users
               WHERE normalized_email='asha@example.com'"""
        ).fetchone()
        assert tuple(user) == ("Asha Rao", "Event director", "Example Events", "Asia/Kolkata")

        assert (
            await client.post(
                "/api/v1/auth/magic-links",
                json={"email": "asha@example.com", "redirect_path": "/account"},
            )
        ).status_code == 202
        assert (
            await client.post(
                f"/auth/verify?token={_token(connection, 'asha@example.com')}",
                follow_redirects=False,
            )
        ).status_code == 303
        session = (await client.get("/api/v1/auth/session")).json()
        assert session["display_name"] == "Asha Rao"
        assert session["profile_complete"] is False
        profile = (await client.get("/api/v1/account/profile")).json()
        assert profile["email"] == "asha@example.com"
        assert profile["version"] == 1

        body = {
            "first_name": "Asha R.",
            "last_name": "Rao",
            "job_title": "Program director",
            "company": "Example Events",
            "time_zone": "Asia/Kolkata",
            "password": None,
            "password_confirmation": None,
            "version": profile["version"],
        }
        denied = await client.patch("/api/v1/account/profile", json=body)
        assert denied.status_code == 403
        updated = await client.patch(
            "/api/v1/account/profile",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json=body,
        )
        assert updated.status_code == 200
        assert updated.json()["display_name"] == "Asha R. Rao"
        assert updated.json()["profile_complete"] is True
        assert updated.json()["version"] == 2
        assert (await client.get("/api/v1/auth/session")).json()["profile_complete"] is True
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='account.profile.update'"
        ).fetchone()[0] == 1


async def test_profile_can_create_password_and_password_sign_in_keeps_magic_links(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        bootstrap = await client.post(
            "/api/v1/bootstrap",
            headers={"X-Bootstrap-Token": _deployment_key(connection)},
            json={
                "organization_name": "Password Events",
                "admin_name": "Password Owner",
                "admin_email": "password@example.com",
            },
        )
        assert bootstrap.status_code == 200
        await client.post(
            "/api/v1/auth/magic-links",
            json={"email": "password@example.com", "redirect_path": "/account"},
        )
        assert (
            await client.post(
                f"/auth/verify?token={_token(connection, 'password@example.com')}",
                follow_redirects=False,
            )
        ).status_code == 303
        session = (await client.get("/api/v1/auth/session")).json()
        profile = (await client.get("/api/v1/account/profile")).json()
        updated = await client.patch(
            "/api/v1/account/profile",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json={
                "first_name": "Password",
                "last_name": "Owner",
                "job_title": None,
                "company": None,
                "time_zone": "UTC",
                "password": "a private local passphrase",
                "password_confirmation": "a private local passphrase",
                "version": profile["version"],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["has_password"] is True
        assert "password" not in updated.json()
        assert (await client.get("/api/v1/auth/session")).status_code == 401

    async with _client(environment) as password_client:
        signed_in = await password_client.post(
            "/api/v1/auth/password/sign-in",
            json={
                "email": "password@example.com",
                "password": "a private local passphrase",
                "redirect_path": "/account",
            },
        )
        assert signed_in.status_code == 200
        assert signed_in.json()["redirect_path"] == "/account"
        assert (await password_client.get("/api/v1/auth/session")).status_code == 200
        # Password support is additive; requesting another email link still works.
        assert (
            await password_client.post(
                "/api/v1/auth/magic-links",
                json={"email": "password@example.com", "redirect_path": "/account"},
            )
        ).status_code == 202


async def test_setup_completion_cannot_be_reopened_by_deleting_business_data(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    deployment_key = _deployment_key(connection)
    async with _client(environment) as client:
        created = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": deployment_key},
            json={
                "organization_name": "One Time Events",
                "admin_name": "Owner",
                "admin_email": "owner@example.com",
            },
        )
        assert created.status_code == 200

        connection.execute("DELETE FROM audit_events")
        connection.execute("DELETE FROM organization_memberships")
        connection.execute("DELETE FROM users")
        connection.execute("DELETE FROM organizations")
        connection.commit()

        assert (await client.get("/api/v1/setup/status")).json() == {"configured": True}
        repeated = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": deployment_key},
            json={
                "organization_name": "Second Setup",
                "admin_name": "Other",
                "admin_email": "other@example.com",
            },
        )
        assert repeated.status_code == 409
        assert connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM instance_setup").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM instance_setup_credentials"
        ).fetchone()[0] == 0


async def test_setup_uses_the_current_migration_generated_key(production_environment) -> None:
    connection, _queue, environment = production_environment
    old_key = _deployment_key(connection)
    replacement = "a" * 64 if old_key != "a" * 64 else "b" * 64
    connection.execute(
        """UPDATE instance_setup_credentials SET deployment_key=?
           WHERE singleton_key='primary'""",
        (replacement,),
    )
    connection.commit()

    async with _client(environment) as client:
        rejected = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": old_key},
            json={
                "organization_name": "Old Key",
                "admin_name": "Old",
                "admin_email": "old@example.com",
            },
        )
        accepted = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": replacement},
            json={
                "organization_name": "New Key",
                "admin_name": "New",
                "admin_email": "new@example.com",
            },
        )

    assert rejected.status_code == 404
    assert accepted.status_code == 200
    assert connection.execute(
        "SELECT COUNT(*) FROM instance_setup_credentials"
    ).fetchone()[0] == 0


async def test_bootstrap_magic_link_invitation_draft_and_owned_submission(
    production_environment,
) -> None:
    connection, queue, environment = production_environment
    deployment_key = _deployment_key(connection)
    async with _client(environment) as admin:
        bootstrap = await admin.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": deployment_key},
            json={
                "organization_name": "Integration Events",
                "admin_name": "Admin",
                "admin_email": "admin@example.com",
            },
        )
        assert bootstrap.status_code == 200
        organization_id = bootstrap.json()["organization_id"]
        assert bootstrap.json()["event_id"] is None
        assert (
            await admin.post(
                "/api/v1/bootstrap",
                headers={"x-bootstrap-token": deployment_key},
                json={
                    "organization_name": "Second",
                    "admin_name": "Other",
                    "admin_email": "other@example.com",
                },
            )
        ).status_code == 409

        requested = await admin.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        assert requested.status_code == 202
        verified = await admin.post(
            f"/auth/verify?token={_token(connection, 'admin@example.com')}",
            follow_redirects=False,
        )
        assert verified.status_code == 303
        assert verified.headers["location"] == "/admin/events"
        session = (await admin.get("/api/v1/auth/session")).json()
        assert session["email"] == "admin@example.com"
        assert session["organization_access"] == [
            {"organization_id": organization_id, "roles": ["organization_admin"]}
        ]
        assert session["event_access"] == []
        csrf = session["csrf_token"]
        mutation_headers = {"origin": "https://test", "x-csrf-token": csrf}

        organizations = await admin.get("/api/v1/admin/organizations")
        assert organizations.json()["data"][0]["id"] == organization_id
        empty_events = await admin.get(
            f"/api/v1/admin/organizations/{organization_id}/events"
        )
        assert empty_events.status_code == 200
        assert empty_events.json()["data"] == []
        event = await admin.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=mutation_headers,
            json={
                "name": "Speaker Summit",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_086_400_000,
                "time_zone": "Asia/Kolkata",
                "delivery_mode": "hybrid",
                "location": "Mumbai",
                "description": "Speaker conference",
                "email_sender_name": "Speaker Summit",
                "email_reply_to": "program@example.com",
            },
        )
        assert event.status_code == 201
        assert event.json()["email_sender_name"] == "Speaker Summit"
        assert event.json()["email_reply_to"] == "program@example.com"
        event_id = event.json()["id"]
        refreshed_session = (await admin.get("/api/v1/auth/session")).json()
        assert refreshed_session["event_access"] == [
            {
                "organization_id": organization_id,
                "event_id": event_id,
                "roles": ["event_admin"],
            }
        ]
        assert (await admin.get(f"/api/v1/admin/events/{event_id}/agenda")).status_code == 404
        agenda_headers = {**mutation_headers, "idempotency-key": "agenda-setup-2026"}
        agenda = await admin.post(
            f"/api/v1/admin/events/{event_id}/agenda/setup",
            headers=agenda_headers,
            json={"room_names": ["Main stage", "Workshop room"], "track_names": ["General"]},
        )
        assert agenda.status_code == 201
        assert [room["name"] for room in agenda.json()["rooms"]] == [
            "Main stage",
            "Workshop room",
        ]
        assert [track["name"] for track in agenda.json()["tracks"]] == ["General"]
        empty_publish = await admin.post(
            f"/api/v1/admin/events/{event_id}/agenda/publish",
            headers=mutation_headers,
            json={
                "revision_id": agenda.json()["revision"]["id"],
                "version": agenda.json()["revision"]["version"],
            },
        )
        assert empty_publish.status_code == 409
        added_room = await admin.post(
            f"/api/v1/admin/events/{event_id}/agenda/rooms",
            headers=mutation_headers,
            json={"name": "Auditorium"},
        )
        assert added_room.status_code == 200
        assert [room["name"] for room in added_room.json()["rooms"]] == [
            "Auditorium",
            "Main stage",
            "Workshop room",
        ]
        general_track = added_room.json()["tracks"][0]
        archived_track = await admin.patch(
            f"/api/v1/admin/events/{event_id}/agenda/tracks/{general_track['id']}",
            headers=mutation_headers,
            json={"status": "archived", "version": general_track["version"]},
        )
        assert archived_track.status_code == 200
        assert archived_track.json()["tracks"] == []
        empty_auto_schedule = await admin.post(
            f"/api/v1/admin/events/{event_id}/agenda/auto-schedule",
            headers={**mutation_headers, "idempotency-key": "agenda-auto-empty-2026"},
            json={"session_minutes": 45, "gap_minutes": 15, "room_ids": []},
        )
        assert empty_auto_schedule.status_code == 200
        assert empty_auto_schedule.json()["auto_schedule"] == {
            "scheduled_count": 0,
            "remaining_count": 0,
        }
        replayed_agenda = await admin.post(
            f"/api/v1/admin/events/{event_id}/agenda/setup",
            headers=agenda_headers,
            json={"room_names": ["Main stage", "Workshop room"], "track_names": ["General"]},
        )
        assert replayed_agenda.status_code == 201
        assert (
            await admin.post(
                f"/api/v1/admin/events/{event_id}/agenda/setup",
                headers={**mutation_headers, "idempotency-key": "agenda-setup-again-2026"},
                json={"room_names": ["Another room"], "track_names": []},
            )
        ).status_code == 409
        invitation = await admin.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=mutation_headers,
            json={
                "email": "speaker@example.com",
                "role": "speaker",
                "display_name": "Invited Speaker",
                "job_title": "Engineer",
                "company": "Example Co",
            },
        )
        assert invitation.status_code == 201
        invited_roster = await admin.get(
            f"/api/v1/admin/events/{event_id}/speaker-targets"
        )
        assert invited_roster.status_code == 200
        assert any(
            target["email"] == "speaker@example.com"
            and target["display_name"] == "Invited Speaker"
            and target["selection_status"] == "invited"
            for target in invited_roster.json()["data"]
        )

        draft_workspace = await admin.get(f"/api/v1/admin/events/{event_id}/cfp")
        assert draft_workspace.status_code == 200
        assert draft_workspace.json()["event_name"] == "Speaker Summit"
        assert draft_workspace.json()["published_form"] is None
        published = await admin.post(
            f"/api/v1/admin/events/{event_id}/cfp/publish",
            headers={**mutation_headers, "idempotency-key": "publish-integration-2026"},
            json={
                "slug": "speaker-summit",
                "welcome_text": "Share your session.",
                "closes_at_ms": 1_899_913_600_000,
            },
        )
        assert published.status_code == 201
        live_workspace = await admin.get(f"/api/v1/admin/events/{event_id}/cfp")
        assert live_workspace.status_code == 200
        assert live_workspace.json()["organization_id"] == organization_id
        assert live_workspace.json()["event_id"] == event_id
        assert live_workspace.json()["published_form"]["slug"] == "speaker-summit"
        published_form = live_workspace.json()["published_form"]
        update_payload = {
            "version": published_form["version"],
            "slug": published_form["slug"],
            "welcome_text": "Share your revised session proposal.",
            "fields": published_form["fields"],
            "conditions": published_form["conditions"],
            "routing_rules": published_form["routing_rules"],
            "opens_at_ms": published_form["opens_at_ms"],
            "closes_at_ms": published_form["closes_at_ms"],
            "submission_limit": published_form["submission_limit"],
            "success_title": published_form["success_title"],
            "success_message": published_form["success_message"],
            "redirect_to_portal": published_form["redirect_to_portal"],
        }
        updated_form = await admin.patch(
            f"/api/v1/admin/events/{event_id}/cfp",
            headers=mutation_headers,
            json=update_payload,
        )
        assert updated_form.status_code == 200
        assert updated_form.json()["version"] == published_form["version"] + 1
        assert updated_form.json()["welcome_text"] == "Share your revised session proposal."
        stale_form = await admin.patch(
            f"/api/v1/admin/events/{event_id}/cfp",
            headers=mutation_headers,
            json=update_payload,
        )
        assert stale_form.status_code == 409
        assert stale_form.headers["x-conflict-type"] == "stale"

    async with _client(environment) as speaker:
        requested = await speaker.post(
            "/api/v1/auth/magic-links",
            json={"email": "speaker@example.com", "redirect_path": "/speaker"},
        )
        assert requested.status_code == 202
        verified = await speaker.post(
            f"/auth/verify?token={_token(connection, 'speaker@example.com')}",
            follow_redirects=False,
        )
        assert verified.status_code == 303
        speaker_session = (await speaker.get("/api/v1/auth/session")).json()
        speaker_headers = {
            "origin": "https://test",
            "x-csrf-token": speaker_session["csrf_token"],
        }
        portal = await speaker.get("/api/v1/speaker/portal")
        assert portal.status_code == 200
        assert portal.json()["event"]["id"] == event_id

        draft = await speaker.put(
            "/api/v1/forms/speaker-summit/draft",
            headers=speaker_headers,
            json={
                "version": 0,
                "answers": {
                    "speaker_name": "Integration Speaker",
                    "speaker_email": "speaker@example.com",
                    "proposal_title": "Production identity",
                    "proposal_abstract": "An end-to-end verification.",
                },
            },
        )
        assert draft.status_code == 200
        assert draft.json()["version"] == 1
        submission = await speaker.post(
            "/api/v1/forms/speaker-summit/submissions",
            headers={
                **speaker_headers,
                "idempotency-key": "submission-integration-2026",
                "x-public-session-id": "public-session-integration-2026",
            },
            json={
                "speaker_name": "Integration Speaker",
                "speaker_email": "speaker@example.com",
                "proposal_title": "Production identity",
                "proposal_abstract": "An end-to-end verification.",
            },
        )
        assert submission.status_code == 201
        assert (await speaker.get("/api/v1/forms/speaker-summit/draft")).json() is None
        portal = await speaker.get("/api/v1/speaker/portal")
        assert [item["id"] for item in portal.json()["submissions"]] == [submission.json()["id"]]
        person_id = connection.execute(
            "SELECT id FROM people WHERE user_id=?",
            (speaker_session["user_id"],),
        ).fetchone()["id"]
        own_profile = await speaker.get(f"/api/v1/speaker-profiles/{person_id}")
        assert own_profile.status_code == 200
        assert own_profile.json()["can_edit"] is True
        assert own_profile.json()["email"] == "speaker@example.com"

    async with _client(environment) as admin_again:
        await admin_again.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        await admin_again.post(
            f"/auth/verify?token={_token(connection, 'admin@example.com')}",
            follow_redirects=False,
        )
        session = (await admin_again.get("/api/v1/auth/session")).json()
        headers = {
            "content-type": "application/json",
            "origin": "https://test",
            "x-csrf-token": session["csrf_token"],
        }
        managed_profile = await admin_again.get(
            f"/api/v1/speaker-profiles/{person_id}"
        )
        assert managed_profile.status_code == 200
        assert managed_profile.json()["can_edit"] is False
        assert managed_profile.json()["email"] == ""
        connection.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,
                created_at_ms,updated_at_ms,closed_at_ms)
               VALUES ('round-content',?,?,'Final','{}','closed',1000,1000,1000)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,round_id,submission_id,decision,
                internal_reason,decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES ('decision-content',?,?,'round-content',?,'accepted','',?,1000,1000)""",
            (organization_id, event_id, submission.json()["id"], session["user_id"]),
        )
        connection.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
               VALUES ('session-content',?,?,?,'decision-content',1000)""",
            (organization_id, event_id, submission.json()["id"]),
        )
        connection.commit()

        submissions_after_decision = await admin_again.get(
            f"/api/v1/admin/events/{event_id}/submissions"
        )
        assert submissions_after_decision.status_code == 200
        decided_submission = next(
            item
            for item in submissions_after_decision.json()["data"]
            if item["id"] == submission.json()["id"]
        )
        assert decided_submission["status"] == "accepted"

        agenda_item = await admin_again.post(
            f"/api/v1/admin/events/{event_id}/agenda/items",
            headers={**headers, "idempotency-key": "agenda-item-integration-2026"},
            json={
                "session_id": "session-content",
                "start_at_ms": 1_900_000_000_000,
                "end_at_ms": 1_900_003_600_000,
                "room_id": agenda.json()["rooms"][0]["id"],
                "version": 0,
            },
        )
        assert agenda_item.status_code == 201
        unscheduled = await admin_again.delete(
            f"/api/v1/admin/events/{event_id}/agenda/items/{agenda_item.json()['id']}",
            headers={**headers, "idempotency-key": "agenda-unschedule-integration-2026"},
            params={"version": agenda_item.json()["version"]},
        )
        assert unscheduled.status_code == 204
        agenda_after_unschedule = await admin_again.get(
            f"/api/v1/admin/events/{event_id}/agenda"
        )
        assert [item["session_id"] for item in agenda_after_unschedule.json()["items"]] == []
        assert "session-content" in {
            item["session_id"]
            for item in agenda_after_unschedule.json()["unscheduled_sessions"]
        }

        content_url = f"/api/v1/admin/events/{event_id}/sessions/session-content/content"
        initial_content = await admin_again.get(content_url)
        assert initial_content.status_code == 200
        assert initial_content.json()["version"] == 1
        assert initial_content.json()["content_status"] == "draft"
        approved_content = await admin_again.patch(
            content_url,
            headers=headers,
            json={
                "title": "Production identity: revised",
                "abstract": "A publication-ready session abstract.",
                "content_status": "approved",
                "version": 1,
            },
        )
        assert approved_content.status_code == 200
        assert approved_content.json()["version"] == 2
        assert approved_content.json()["content_status"] == "approved"
        assert [item["version"] for item in approved_content.json()["history"]] == [2, 1]
        stale_content = await admin_again.patch(
            content_url,
            headers=headers,
            json={
                "title": "Stale update",
                "abstract": "This update must not overwrite the approved copy.",
                "content_status": "draft",
                "version": 1,
            },
        )
        assert stale_content.status_code == 409
        restored_content = await admin_again.post(
            f"{content_url}/restore",
            headers=headers,
            json={"history_version": 1, "current_version": 2},
        )
        assert restored_content.status_code == 200
        assert restored_content.json()["version"] == 3
        assert restored_content.json()["title"] == "Production identity"
        assert restored_content.json()["content_status"] == "draft"

        second_event = await admin_again.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=headers,
            json={
                "name": "Next Speaker Summit",
                "starts_at_ms": 1_901_000_000_000,
                "ends_at_ms": 1_901_086_400_000,
                "time_zone": "Asia/Kolkata",
                "delivery_mode": "hybrid",
                "location": "Mumbai",
                "description": "Next speaker conference",
            },
        )
        assert second_event.status_code == 201
        second_event_id = second_event.json()["id"]
        assert (
            await admin_again.post(
                f"/api/v1/admin/events/{second_event_id}/cfp/publish",
                headers={**headers, "idempotency-key": "second-form-integration-2026"},
                json={
                    "slug": "next-speaker-summit",
                    "welcome_text": "Join the next event.",
                    "closes_at_ms": 1_900_913_600_000,
                },
            )
        ).status_code == 201

        invitations = await admin_again.get(f"/api/v1/admin/events/{event_id}/invitations")
        assert [(item["role"], item["status"]) for item in invitations.json()["data"]] == [
            ("speaker", "accepted")
        ]
        members = await admin_again.get(f"/api/v1/admin/events/{event_id}/members")
        speaker_member = next(
            item for item in members.json()["data"] if item["email"] == "speaker@example.com"
        )
        revoked = await admin_again.delete(
            f"/api/v1/admin/events/{event_id}/members/{speaker_member['user_id']}/roles/speaker",
            headers=headers,
        )
        assert revoked.status_code == 204
        assert (
            await admin_again.delete(
                f"/api/v1/admin/events/{event_id}/members/{session['user_id']}/roles/event_admin",
                headers=headers,
            )
        ).status_code == 409

    assert len(queue.messages) >= 3
    members = connection.execute(
        "SELECT role,status FROM event_memberships WHERE event_id=? ORDER BY role", (event_id,)
    ).fetchall()
    assert [(row["role"], row["status"]) for row in members] == [
        ("event_admin", "active"),
        ("speaker", "revoked"),
    ]
    assert connection.execute(
        """SELECT status FROM organization_memberships
           WHERE organization_id=? AND user_id=?""",
        (organization_id, speaker_member["user_id"]),
    ).fetchone()["status"] == "revoked"
    assert connection.execute(
        "SELECT 1 FROM audit_events WHERE action='event_membership.revoke'"
    ).fetchone()

    async with _client(environment) as returning_speaker:
        requested = await returning_speaker.post(
            "/api/v1/auth/magic-links",
            json={
                "email": "speaker@example.com",
                "redirect_path": "/cfp/next-speaker-summit",
                "form_slug": "next-speaker-summit",
            },
        )
        assert requested.status_code == 202
        assert (
            await returning_speaker.post(
                f"/auth/verify?token={_token(connection, 'speaker@example.com')}",
                follow_redirects=False,
            )
        ).status_code == 303
        returning_session = (await returning_speaker.get("/api/v1/auth/session")).json()
        assert returning_session["event_access"] == []
        assert connection.execute(
            """SELECT status FROM organization_memberships
               WHERE organization_id=? AND user_id=?""",
            (organization_id, speaker_member["user_id"]),
        ).fetchone()["status"] == "revoked"


async def test_existing_user_accepts_a_new_role_invitation(production_environment) -> None:
    connection, _, environment = production_environment
    async with _client(environment) as client:
        bootstrap = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Existing User Events",
                "admin_name": "Admin",
                "admin_email": "admin@example.com",
            },
        )
        organization_id = bootstrap.json()["organization_id"]
        await client.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        verified = await client.post(
            f"/auth/verify?token={_token(connection, 'admin@example.com')}",
            follow_redirects=False,
        )
        assert verified.status_code == 303
        session = (await client.get("/api/v1/auth/session")).json()
        headers = {
            "content-type": "application/json",
            "origin": "https://test",
            "x-csrf-token": session["csrf_token"],
        }
        organization = await client.patch(
            f"/api/v1/admin/organizations/{organization_id}",
            headers=headers,
            json={"name": "Existing User Events Updated", "version": 1},
        )
        assert organization.status_code == 200
        assert organization.json()["version"] == 2
        event = await client.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=headers,
            json={
                "name": "Multi-role Summit",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_086_400_000,
                "time_zone": "UTC",
                "delivery_mode": "in_person",
                "location": "Mumbai",
                "description": "Multi-role conference",
            },
        )
        event_id = event.json()["id"]
        updated_event = await client.patch(
            f"/api/v1/admin/events/{event_id}",
            headers=headers,
            json={
                "name": "Multi-role Summit Updated",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_086_400_000,
                "time_zone": "Asia/Kolkata",
                "delivery_mode": "hybrid",
                "location": "Development",
                "description": "Disposable staging rehearsal.",
                "email_sender_name": "Program Team",
                "email_reply_to": "program-team@example.com",
                "status": "active",
                "version": 1,
            },
        )
        assert updated_event.status_code == 200
        assert updated_event.json()["version"] == 2
        assert updated_event.json()["email_sender_name"] == "Program Team"
        assert updated_event.json()["email_reply_to"] == "program-team@example.com"
        invalid_email = await client.patch(
            f"/api/v1/admin/events/{event_id}",
            headers=headers,
            json={
                "name": "Multi-role Summit Updated",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_086_400_000,
                "time_zone": "Asia/Kolkata",
                "delivery_mode": "hybrid",
                "email_reply_to": "not-an-email",
                "status": "active",
                "version": 2,
            },
        )
        assert invalid_email.status_code == 422
        temporary = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=headers,
            json={"email": "reviewer@example.com", "role": "evaluator"},
        )
        assert temporary.status_code == 201
        # The one-time acceptance link is delivered only by email — never in
        # the API response, where the inviter could use it to sign in as the
        # invitee.
        assert "accept_url" not in temporary.json()
        assert _token(connection, "reviewer@example.com")
        listed = await client.get(f"/api/v1/admin/events/{event_id}/invitations")
        assert listed.json()["data"][0]["id"] == temporary.json()["id"]
        assert "accept_url" not in listed.json()["data"][0]
        resent = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations/{temporary.json()['id']}/resend",
            headers=headers,
            json={},
        )
        assert resent.status_code == 200
        assert "accept_url" not in resent.json()
        resend_rows = connection.execute(
            """SELECT html_body FROM communication_messages
               WHERE recipient_email='reviewer@example.com'"""
        ).fetchall()
        resend_tokens = {
            re.search(r"token=([^\"<]+)", str(row[0])).group(1) for row in resend_rows
        }
        assert len(resend_tokens) == 2  # resend minted a fresh link
        revoked = await client.delete(
            f"/api/v1/admin/events/{event_id}/invitations/{temporary.json()['id']}",
            headers=headers,
        )
        assert revoked.status_code == 204
        invitation = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=headers,
            json={"email": "admin@example.com", "role": "speaker"},
        )
        assert invitation.status_code == 201
        accepted = await client.post(
            f"/auth/verify?token={_token(connection, 'admin@example.com')}",
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        assert accepted.headers["location"] == "/speaker"
        portal = await client.get("/api/v1/speaker/portal")
        assert portal.status_code == 200
        assert portal.json()["event"]["id"] == event_id

    roles = connection.execute(
        "SELECT role FROM event_memberships WHERE event_id=? AND status='active' ORDER BY role",
        (event_id,),
    ).fetchall()
    assert [row["role"] for row in roles] == ["event_admin", "speaker"]
    audit = connection.execute(
        "SELECT action,target_id FROM audit_events WHERE action='identity.invitation.accept'"
    ).fetchone()
    assert audit is not None
    assert audit["target_id"] == invitation.json()["id"]
    actions = {
        row["action"]
        for row in connection.execute(
            "SELECT action FROM audit_events WHERE action LIKE 'identity.invitation.%'"
        ).fetchall()
    }
    assert actions == {
        "identity.invitation.accept",
        "identity.invitation.create",
        "identity.invitation.resend",
        "identity.invitation.revoke",
    }


async def test_existing_admin_becomes_speaker_only_after_submitting_cfp(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        bootstrap = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Admin Speaker Events",
                "admin_name": "Admin Speaker",
                "admin_email": "admin@example.com",
                "event_name": "Admin Speaker Summit",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_086_400_000,
                "time_zone": "UTC",
                "event_location": "Online",
                "event_description": "Administrator speaker conference",
                "event_delivery_mode": "virtual",
            },
        )
        event_id = bootstrap.json()["event_id"]
        await client.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        await client.post(
            f"/auth/verify?token={_token(connection, 'admin@example.com')}",
            follow_redirects=False,
        )
        session = (await client.get("/api/v1/auth/session")).json()
        headers = {"origin": "https://test", "x-csrf-token": session["csrf_token"]}
        published = await client.post(
            f"/api/v1/admin/events/{event_id}/cfp/publish",
            headers={**headers, "idempotency-key": "admin-speaker-form"},
            json={
                "slug": "admin-speaker",
                "welcome_text": "Share your proposal.",
                "closes_at_ms": 1_899_913_600_000,
            },
        )
        assert published.status_code == 201
        assert (await client.get("/api/v1/speaker/portal")).status_code == 404

        assert (await client.get("/cfp/admin-speaker")).status_code == 200
        cfp_session = (await client.get("/api/v1/auth/session")).json()
        event_access = next(
            access for access in cfp_session["event_access"] if access["event_id"] == event_id
        )
        assert event_access["roles"] == ["event_admin"]
        cfp_headers = {"origin": "https://test", "x-csrf-token": cfp_session["csrf_token"]}
        draft = await client.put(
            "/api/v1/forms/admin-speaker/draft",
            headers=cfp_headers,
            json={"answers": {"proposal_title": "Admin on stage"}, "version": 0},
        )
        assert draft.status_code == 403
        submitted = await client.post(
            "/api/v1/forms/admin-speaker/submissions",
            headers={
                **cfp_headers,
                "idempotency-key": "admin-speaker-submission",
                "x-public-session-id": "admin-speaker-browser-session",
            },
            json={
                "speaker_name": "Admin Speaker",
                "speaker_email": "admin@example.com",
                "proposal_title": "Admin on stage",
                "proposal_abstract": "A real proposal creates the speaker identity.",
            },
        )
        assert submitted.status_code == 201
        cfp_session = (await client.get("/api/v1/auth/session")).json()
        event_access = next(
            access for access in cfp_session["event_access"] if access["event_id"] == event_id
        )
        assert event_access["roles"] == ["event_admin", "speaker"]
        assert (await client.get("/api/v1/speaker/portal")).status_code == 200

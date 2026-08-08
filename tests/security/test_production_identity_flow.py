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

    def prepare(self, sql: str) -> SQLiteStatement:
        return SQLiteStatement(self.connection, sql)

    async def batch(self, statements: list[SQLiteStatement]):
        results = []
        try:
            self.connection.execute("BEGIN")
            for statement in statements:
                cursor = self.connection.execute(statement.sql, statement.parameters)
                results.append({"meta": {"changes": max(0, cursor.rowcount)}})
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
            json={"organization_name": "Expired Link Events", "admin_email": "admin@example.com"},
        )
        assert bootstrap.status_code == 200
        requested = await client.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        assert requested.status_code == 202
        token = _token(connection, "admin@example.com")
        connection.execute("UPDATE authentication_challenges SET expires_at_ms=0")
        connection.commit()

        browser = await client.get(f"/auth/verify?token={token}")
        assert browser.status_code == 404
        assert browser.headers["content-type"].startswith("text/html")
        assert browser.headers["cache-control"] == "no-store"
        assert "This sign-in link can’t be used." in browser.text
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
        page = await client.get("/setup")
        initial = await client.get("/api/v1/setup/status")
        assert page.status_code == 200
        assert "Create your workspace" in page.text
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
        assert closed_setup.headers["location"] == "/sign-in?redirect=%2Fadmin"
        assert "Create your workspace" not in closed_setup.text
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
            await client.get(
                f"/auth/verify?token={_token(connection, 'asha@example.com')}",
                follow_redirects=False,
            )
        ).status_code == 303
        session = (await client.get("/api/v1/auth/session")).json()
        assert session["display_name"] == "Asha Rao"
        profile = (await client.get("/api/v1/account/profile")).json()
        assert profile["email"] == "asha@example.com"
        assert profile["version"] == 1

        body = {
            "display_name": "Asha R. Rao",
            "job_title": "Program director",
            "company": "Example Events",
            "time_zone": "Asia/Kolkata",
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
        assert updated.json()["version"] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='account.profile.update'"
        ).fetchone()[0] == 1


async def test_setup_completion_cannot_be_reopened_by_deleting_business_data(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    deployment_key = _deployment_key(connection)
    async with _client(environment) as client:
        created = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": deployment_key},
            json={"organization_name": "One Time Events", "admin_email": "owner@example.com"},
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
            json={"organization_name": "Second Setup", "admin_email": "other@example.com"},
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
            json={"organization_name": "Old Key", "admin_email": "old@example.com"},
        )
        accepted = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": replacement},
            json={"organization_name": "New Key", "admin_email": "new@example.com"},
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
                    "admin_email": "other@example.com",
                },
            )
        ).status_code == 409

        requested = await admin.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        assert requested.status_code == 202
        verified = await admin.get(
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
        invitation = await admin.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=mutation_headers,
            json={"email": "speaker@example.com", "role": "speaker"},
        )
        assert invitation.status_code == 201

        program = await admin.post(
            "/api/v1/admin/programs",
            headers={**mutation_headers, "idempotency-key": "program-integration-2026"},
            json={
                "organization_id": organization_id,
                "event_id": event_id,
                "name": "Speaker Summit CFP",
            },
        )
        assert program.status_code == 201
        published = await admin.post(
            f"/api/v1/admin/programs/{program.json()['id']}/forms/publish",
            headers={**mutation_headers, "idempotency-key": "publish-integration-2026"},
            json={"slug": "speaker-summit", "welcome_text": "Share your session."},
        )
        assert published.status_code == 201

    async with _client(environment) as speaker:
        requested = await speaker.post(
            "/api/v1/auth/magic-links",
            json={"email": "speaker@example.com", "redirect_path": "/speaker"},
        )
        assert requested.status_code == 202
        verified = await speaker.get(
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

    async with _client(environment) as admin_again:
        await admin_again.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        await admin_again.get(
            f"/auth/verify?token={_token(connection, 'admin@example.com')}",
            follow_redirects=False,
        )
        session = (await admin_again.get("/api/v1/auth/session")).json()
        headers = {
            "content-type": "application/json",
            "origin": "https://test",
            "x-csrf-token": session["csrf_token"],
        }
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
        "SELECT 1 FROM audit_events WHERE action='event_membership.revoke'"
    ).fetchone()


async def test_existing_user_accepts_a_new_role_invitation(production_environment) -> None:
    connection, _, environment = production_environment
    async with _client(environment) as client:
        bootstrap = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Existing User Events",
                "admin_email": "admin@example.com",
            },
        )
        organization_id = bootstrap.json()["organization_id"]
        await client.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        verified = await client.get(
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
        listed = await client.get(f"/api/v1/admin/events/{event_id}/invitations")
        assert listed.json()["data"][0]["id"] == temporary.json()["id"]
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

        requested = await client.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/speaker"},
        )
        assert requested.status_code == 202
        accepted = await client.get(
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
        "identity.invitation.revoke",
    }

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
        BOOTSTRAP_TOKEN="b" * 40,
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


async def test_bootstrap_magic_link_invitation_draft_and_owned_submission(
    production_environment,
) -> None:
    connection, queue, environment = production_environment
    async with _client(environment) as admin:
        bootstrap = await admin.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": environment.BOOTSTRAP_TOKEN},
            json={
                "organization_name": "Integration Events",
                "event_name": "Primary Conference",
                "admin_email": "admin@example.com",
                "starts_at_ms": 1_800_000_000_000,
                "ends_at_ms": 1_800_086_400_000,
                "time_zone": "Asia/Kolkata",
            },
        )
        assert bootstrap.status_code == 200
        organization_id = bootstrap.json()["organization_id"]
        assert (
            await admin.post(
                "/api/v1/bootstrap",
                headers={"x-bootstrap-token": environment.BOOTSTRAP_TOKEN},
                json={
                    "organization_name": "Second",
                    "event_name": "Rejected",
                    "admin_email": "other@example.com",
                    "starts_at_ms": 1,
                    "ends_at_ms": 2,
                    "time_zone": "UTC",
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
        csrf = session["csrf_token"]
        mutation_headers = {"origin": "https://test", "x-csrf-token": csrf}

        organizations = await admin.get("/api/v1/admin/organizations")
        assert organizations.json()["data"][0]["id"] == organization_id
        event = await admin.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=mutation_headers,
            json={
                "name": "Speaker Summit",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_086_400_000,
                "time_zone": "Asia/Kolkata",
                "delivery_mode": "hybrid",
            },
        )
        assert event.status_code == 201
        event_id = event.json()["id"]
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

    assert len(queue.messages) >= 3
    members = connection.execute(
        "SELECT role,status FROM event_memberships WHERE event_id=? ORDER BY role", (event_id,)
    ).fetchall()
    assert [(row["role"], row["status"]) for row in members] == [
        ("event_admin", "active"),
        ("speaker", "active"),
    ]

import sqlite3
from types import SimpleNamespace

from fastapi import Response
from starlette.requests import Request

from sessionbuddy.platform.auth import router
from sessionbuddy.platform.auth.d1 import D1AuthorizationFacts
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Persona
from tests.security.test_production_identity_flow import SQLiteD1

NOW_MS = 1_800_000_000_000
ABSOLUTE_EXPIRY_MS = NOW_MS + 86_400_000


def refresh_database(*, active_role: str | None) -> tuple[sqlite3.Connection, SQLiteD1]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE users (id TEXT PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          token_hash BLOB NOT NULL,
          csrf_secret_hash BLOB NOT NULL,
          authorization_version INTEGER NOT NULL,
          created_at_ms INTEGER NOT NULL,
          last_seen_at_ms INTEGER NOT NULL,
          idle_expires_at_ms INTEGER NOT NULL,
          absolute_expires_at_ms INTEGER NOT NULL,
          revoked_at_ms INTEGER,
          revoke_reason TEXT,
          rotated_from_session_id TEXT
        );
        CREATE TABLE user_roles (
          user_id TEXT, role TEXT, status TEXT, is_default INTEGER,
          PRIMARY KEY(user_id,role)
        );
        CREATE TABLE session_active_roles (
          session_id TEXT PRIMARY KEY, user_id TEXT, role TEXT, selected_at_ms INTEGER
        );
        CREATE TABLE organization_memberships (
          user_id TEXT, organization_id TEXT, role TEXT, status TEXT
        );
        CREATE TABLE event_memberships (
          user_id TEXT, organization_id TEXT, event_id TEXT, role TEXT, status TEXT
        );
        CREATE TABLE owned_resources (
          id TEXT PRIMARY KEY, owner_user_id TEXT, status TEXT
        );
        CREATE TABLE resource_access_grants (
          resource_id TEXT, user_id TEXT, permission TEXT, status TEXT
        );
        CREATE TABLE audit_events (
          id TEXT, organization_id TEXT, event_id TEXT, actor_user_id TEXT,
          actor_type TEXT, action TEXT, target_type TEXT, target_id TEXT,
          result TEXT, reason_code TEXT, correlation_id TEXT,
          metadata_json TEXT, occurred_at_ms INTEGER
        );
        INSERT INTO users VALUES('user-a','active');
        INSERT INTO user_roles VALUES('user-a','organizer','active',1);
        INSERT INTO user_roles VALUES('user-a','speaker','active',0);
        """
    )
    connection.execute(
        """INSERT INTO sessions
           (id,user_id,token_hash,csrf_secret_hash,authorization_version,
            created_at_ms,last_seen_at_ms,idle_expires_at_ms,absolute_expires_at_ms)
           VALUES('old-session','user-a',X'01',X'02',1,?,?,?,?)""",
        (NOW_MS - 1000, NOW_MS - 1000, NOW_MS + 1000, ABSOLUTE_EXPIRY_MS),
    )
    if active_role is not None:
        connection.execute(
            "INSERT INTO session_active_roles VALUES('old-session','user-a',?,1234)",
            (active_role,),
        )
    connection.commit()
    return connection, SQLiteD1(connection)


def refresh_request(database: SQLiteD1) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/session/refresh",
            "headers": [],
            "env": SimpleNamespace(
                DB=database,
                APP_ENV="local",
                SESSION_HMAC_KEY="s" * 32,
                CSRF_HMAC_KEY="c" * 32,
            ),
        }
    )
    request.state.request_id = "refresh-test"
    return request


async def authenticated_context(*_args, **_kwargs) -> AuthenticatedContext:
    return AuthenticatedContext(
        Actor(user_id="user-a", active_persona=Persona.SPEAKER),
        "old-session",
    )


async def run_refresh(monkeypatch, database: SQLiteD1):
    monkeypatch.setattr(router, "authenticate_request", authenticated_context)
    monkeypatch.setattr(router, "guard_mutation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router, "utc_now_ms", lambda: NOW_MS)
    monkeypatch.setattr(router, "new_id", lambda: "new-session")
    monkeypatch.setattr(router, "generate_token", lambda: "new-session-token")
    response = Response()
    view = await router.refresh_session(refresh_request(database), response)
    return view, response


async def test_refresh_copies_the_narrowed_active_role_in_the_rotation_batch(monkeypatch) -> None:
    connection, database = refresh_database(active_role="speaker")
    try:
        view, response = await run_refresh(monkeypatch, database)

        assert view.session_id == "new-session"
        assert "sessionbuddy-local=" in response.headers["set-cookie"]
        assert tuple(
            connection.execute(
                "SELECT revoked_at_ms,revoke_reason FROM sessions WHERE id='old-session'"
            ).fetchone()
        ) == (NOW_MS, "rotated")
        assert tuple(
            connection.execute(
                """SELECT user_id,role,selected_at_ms FROM session_active_roles
                   WHERE session_id='new-session'"""
            ).fetchone()
        ) == ("user-a", "speaker", 1234)
        assert database.prepare_count == 5
    finally:
        connection.close()


async def test_refresh_does_not_restore_default_after_active_role_revocation(monkeypatch) -> None:
    connection, database = refresh_database(active_role=None)
    try:
        await run_refresh(monkeypatch, database)

        assert connection.execute(
            "SELECT role FROM session_active_roles WHERE session_id='new-session'"
        ).fetchone() is None
        actor = await D1AuthorizationFacts(database).actor_for_session("new-session")
        assert actor is not None
        assert actor.active_persona is None
    finally:
        connection.close()

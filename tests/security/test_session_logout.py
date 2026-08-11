"""Sign-out contract for POST /api/v1/session/logout, driven over HTTP.

The endpoint predates these tests but had no behavioral coverage: only its
presence in the OpenAPI document was asserted. This file pins the contract
the console's Sign out button depends on: the session is revoked in D1 (not
merely the cookie dropped), the browser cookie is cleared, the action is
audited, and the CSRF mutation guard applies.
"""

import sqlite3

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)


async def _signed_in_admin(client, connection: sqlite3.Connection) -> str:
    bootstrap = await client.post(
        "/api/v1/bootstrap",
        headers={"x-bootstrap-token": _deployment_key(connection)},
        json={
            "organization_name": "Logout Events",
            "admin_name": "Admin",
            "admin_email": "admin@example.com",
        },
    )
    assert bootstrap.status_code == 200
    requested = await client.post(
        "/api/v1/auth/magic-links",
        json={"email": "admin@example.com", "redirect_path": "/admin"},
    )
    assert requested.status_code == 202
    verified = await client.post(
        "/auth/verify",
        data={"token": _token(connection, "admin@example.com")},
        follow_redirects=False,
    )
    assert verified.status_code == 303
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200
    return session.json()["csrf_token"]


async def test_logout_revokes_the_session_clears_the_cookie_and_audits(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf = await _signed_in_admin(client, connection)
        cookie = client.cookies.get("__Host-session")
        assert cookie

        response = await client.post(
            "/api/v1/session/logout",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={},
        )
        assert response.status_code == 204

        # The browser cookie is actively cleared, not left to idle out.
        cleared = response.headers.get("set-cookie", "")
        assert "__Host-session=" in cleared
        assert "Max-Age=0" in cleared or "expires" in cleared.lower()

        # Revocation is server-side: replaying the pre-logout cookie (a stolen
        # or cached copy) is dead, not just the cleared browser jar.
        client.cookies.set("__Host-session", cookie, domain="test")
        followup = await client.get("/api/v1/auth/session")
        assert followup.status_code == 401

        row = connection.execute(
            "SELECT revoked_at_ms, revoke_reason FROM sessions"
        ).fetchone()
        assert row is not None
        assert row["revoked_at_ms"] is not None
        assert row["revoke_reason"] == "logout"

        audited = connection.execute(
            """SELECT result FROM audit_events
               WHERE action='session.logout' AND actor_type='user'"""
        ).fetchone()
        assert audited is not None
        assert audited["result"] == "succeeded"


async def test_logout_is_a_guarded_mutation(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        await _signed_in_admin(client, connection)

        # Missing CSRF token: rejected, and the session survives.
        missing_csrf = await client.post(
            "/api/v1/session/logout",
            headers={"origin": "https://test"},
            json={},
        )
        assert missing_csrf.status_code == 403

        # Cross-origin request: rejected, and the session survives.
        cross_origin = await client.post(
            "/api/v1/session/logout",
            headers={"origin": "https://evil.example", "x-csrf-token": "x" * 32},
            json={},
        )
        assert cross_origin.status_code == 403

        still_signed_in = await client.get("/api/v1/auth/session")
        assert still_signed_in.status_code == 200
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sessions WHERE revoked_at_ms IS NOT NULL"
            ).fetchone()[0]
            == 0
        )


async def test_logout_without_a_session_is_unauthorized(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    _connection, _queue, environment = production_environment
    async with _client(environment) as client:
        response = await client.post(
            "/api/v1/session/logout",
            headers={"origin": "https://test", "x-csrf-token": "x" * 32},
            json={},
        )
        assert response.status_code == 401


async def test_logout_is_terminal_for_the_revoked_session(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf = await _signed_in_admin(client, connection)
        cookie = client.cookies.get("__Host-session")
        assert cookie
        first = await client.post(
            "/api/v1/session/logout",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={},
        )
        assert first.status_code == 204

        # Replaying the old cookie + CSRF pair cannot resurrect anything.
        client.cookies.set("__Host-session", cookie, domain="test")
        second = await client.post(
            "/api/v1/session/logout",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={},
        )
        assert second.status_code == 401
        row = connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE revoke_reason='logout'"
        ).fetchone()
        assert row[0] == 1

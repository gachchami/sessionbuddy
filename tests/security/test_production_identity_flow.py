import hashlib
import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app
from sessionbuddy.platform.auth.passwords import hash_password
from tests.schema import MIGRATIONS


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


class DenyingRateLimiter:
    async def limit(self, options: dict[str, str]) -> dict[str, bool]:
        assert options["key"].startswith("account.headshot.upload:")
        return {"success": False}


class RecordingRateLimiter:
    def __init__(self, *, allowed: bool = True) -> None:
        self.keys: list[str] = []
        self.allowed = allowed

    async def limit(self, options: dict[str, str]) -> dict[str, bool]:
        self.keys.append(options["key"])
        return {"success": self.allowed}


class CapturingQueue:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, message: dict[str, object]) -> None:
        self.messages.append(message)


def _token(connection: sqlite3.Connection, email: str) -> str:
    row = connection.execute(
        """SELECT html_body FROM communication_messages
           WHERE recipient_email=? AND html_body LIKE '%/auth/verify#token=%'
           ORDER BY queued_at_ms DESC,id DESC LIMIT 1""",
        (email,),
    ).fetchone()
    assert row is not None
    match = re.search(r"/auth/verify#token=([^\"<]+)", row[0])
    assert match is not None
    return match.group(1)


def _deployment_key(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        """SELECT deployment_key FROM instance_setup_credentials
           WHERE singleton_key='primary'"""
    ).fetchone()
    assert row is not None
    return str(row[0])


def _insert_password_speaker(
    connection: sqlite3.Connection, *, user_id: str, email: str, password: str
) -> None:
    verifier = hash_password(password, b"p" * 32)
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,display_name,
            profile_completed_at_ms,created_at_ms,updated_at_ms)
           VALUES(?,?,?,'active',1,?,1,1,1)""",
        (user_id, email, email.casefold(), email.split("@", 1)[0]),
    )
    connection.execute(
        """INSERT INTO user_roles
           (user_id,role,status,created_at_ms,updated_at_ms,is_default)
           VALUES(?,'speaker','active',1,1,1)""",
        (user_id,),
    )
    connection.execute(
        """INSERT INTO password_credentials
           (user_id,verifier_phc,pepper_version,status,created_at_ms,updated_at_ms)
           VALUES(?,?,1,'active',1,1)""",
        (user_id, verifier),
    )
    connection.commit()


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
        SPEAKER_UPLOAD_AUTH_RATE_LIMITER=AllowingRateLimiter(),
        HEADSHOT_UPLOAD_RATE_LIMITER=AllowingRateLimiter(),
        MAGIC_LINK_RECIPIENT_RATE_LIMITER=AllowingRateLimiter(),
        MAGIC_LINK_SOURCE_RATE_LIMITER=AllowingRateLimiter(),
        PUBLIC_BASE_URL="https://test",
        ALLOWED_ORIGINS="https://test",
        COMMUNICATION_QUEUE=queue,
    )
    yield connection, queue, environment
    connection.close()


def _client(environment, *, origin: str | None = "https://test") -> AsyncClient:
    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    headers = {} if origin is None else {"origin": origin}
    return AsyncClient(
        transport=ASGITransport(app=inject_environment),
        base_url="https://test",
        headers=headers,
    )


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
        assert requested.json() == {"accepted": True}
        token = _token(connection, "admin@example.com")
        connection.execute(
            "UPDATE authentication_challenges SET created_at_ms=0,expires_at_ms=1"
        )
        connection.commit()

        browser = await client.post("/auth/verify", data={"token": token})
        assert browser.status_code == 404
        assert browser.headers["content-type"].startswith("text/html")
        assert browser.headers["cache-control"] == "no-store"
        assert "This link has expired" in browser.text
        assert 'href="/sign-in"' in browser.text
        assert token not in browser.text

        api = await client.get(f"/api/v1/auth/verify?token={token}")
        assert api.status_code == 405
        assert api.headers["content-type"].startswith("application/json")
        assert api.headers["allow"] == "POST"
        assert connection.execute(
            "SELECT consumed_at_ms FROM authentication_challenges"
        ).fetchone()[0] is None


async def test_magic_link_redemption_rejects_cross_site_and_missing_origins(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        bootstrap = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Origin Guard Events",
                "admin_name": "Admin",
                "admin_email": "admin@example.com",
            },
        )
        assert bootstrap.status_code == 200
        assert (
            await client.post(
                "/api/v1/auth/magic-links",
                json={"email": "admin@example.com", "redirect_path": "/admin"},
            )
        ).status_code == 202
        token = _token(connection, "admin@example.com")

        api_get = await client.get(f"/api/v1/auth/verify?token={token}")
        assert api_get.status_code == 405
        assert api_get.headers["allow"] == "POST"

        query_only = await client.post(
            f"/auth/verify?token={token}",
            data={"unused": "value"},
            follow_redirects=False,
        )
        assert query_only.status_code == 404
        assert "set-cookie" not in query_only.headers

        cross_site = await client.post(
            "/auth/verify",
            data={"token": token},
            headers={"origin": "https://attacker.example"},
            follow_redirects=False,
        )
        assert cross_site.status_code == 403
        assert "set-cookie" not in cross_site.headers

        async with _client(environment, origin=None) as no_origin_client:
            missing_origin = await no_origin_client.post(
                "/auth/verify", data={"token": token}, follow_redirects=False
            )
        assert missing_origin.status_code == 403
        assert "set-cookie" not in missing_origin.headers
        assert connection.execute(
            "SELECT consumed_at_ms FROM authentication_challenges WHERE token_hash=?",
            (hashlib.sha256(token.encode()).digest(),),
        ).fetchone()[0] is None

        async with _client(environment, origin=None) as metadata_client:
            metadata_confirmed = await metadata_client.post(
                "/auth/verify",
                data={"token": token},
                headers={"sec-fetch-site": "same-origin"},
                follow_redirects=False,
            )
        assert metadata_confirmed.status_code == 303
        assert "set-cookie" in metadata_confirmed.headers

        assert (
            await client.post(
                "/api/v1/auth/magic-links",
                json={"email": "admin@example.com", "redirect_path": "/admin"},
            )
        ).status_code == 202
        token = _token(connection, "admin@example.com")

        async with _client(environment, origin=None) as referer_client:
            referer_confirmed = await referer_client.post(
                "/auth/verify",
                data={"token": token},
                headers={"referer": "https://test/auth/verify"},
                follow_redirects=False,
            )
        assert referer_confirmed.status_code == 303
        assert "set-cookie" in referer_confirmed.headers

        # Issue a fresh challenge because the Referer-backed confirmation above
        # consumed the original one.
        assert (
            await client.post(
                "/api/v1/auth/magic-links",
                json={"email": "admin@example.com", "redirect_path": "/admin"},
            )
        ).status_code == 202
        token = _token(connection, "admin@example.com")

        confirmed = await client.post(
            "/auth/verify", data={"token": token}, follow_redirects=False
        )
        assert confirmed.status_code == 303
        assert "set-cookie" in confirmed.headers


async def test_account_headshot_scan_is_rate_limited_before_body_or_scanner_work(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        assert (
            await client.post(
                "/api/v1/bootstrap",
                headers={"x-bootstrap-token": _deployment_key(connection)},
                json={
                    "organization_name": "Headshot Events",
                    "admin_name": "Admin",
                    "admin_email": "admin@example.com",
                },
            )
        ).status_code == 200
        assert (
            await client.post(
                "/api/v1/auth/magic-links",
                json={"email": "admin@example.com", "redirect_path": "/account"},
            )
        ).status_code == 202
        assert (
            await client.post(
                "/auth/verify",
                data={"token": _token(connection, "admin@example.com")},
                follow_redirects=False,
            )
        ).status_code == 303
        session = (await client.get("/api/v1/auth/session")).json()
        environment.HEADSHOT_UPLOAD_RATE_LIMITER = DenyingRateLimiter()

        refused = await client.put(
            "/api/v1/account/headshot",
            content=b"not read because the limiter denies first",
            headers={
                "content-type": "image/png",
                "x-csrf-token": session["csrf_token"],
            },
        )

    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "60"


async def test_magic_link_recipient_and_source_limits_use_independent_keys(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    recipient = RecordingRateLimiter()
    source = RecordingRateLimiter()
    environment.MAGIC_LINK_RECIPIENT_RATE_LIMITER = recipient
    environment.MAGIC_LINK_SOURCE_RATE_LIMITER = source
    async with _client(environment) as client:
        assert (
            await client.post(
                "/api/v1/bootstrap",
                headers={"x-bootstrap-token": _deployment_key(connection)},
                json={
                    "organization_name": "Rate Limit Events",
                    "admin_name": "Admin",
                    "admin_email": "admin@example.com",
                },
            )
        ).status_code == 200
        for ip_address in ("192.0.2.10", "192.0.2.11"):
            response = await client.post(
                "/api/v1/auth/magic-links",
                headers={"cf-connecting-ip": ip_address},
                json={"email": "admin@example.com", "redirect_path": "/admin"},
            )
            assert response.status_code == 202

    assert len(recipient.keys) == 2
    assert recipient.keys[0] == recipient.keys[1]
    assert recipient.keys[0].startswith("auth.magic_link.recipient:")
    assert len(source.keys) == 2
    assert source.keys[0] != source.keys[1]
    assert all(key.startswith("auth.magic_link.source:") for key in source.keys)
    assert all("admin@example.com" not in key for key in recipient.keys)


async def test_either_magic_link_limit_can_refuse_delivery(production_environment) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        assert (
            await client.post(
                "/api/v1/bootstrap",
                headers={"x-bootstrap-token": _deployment_key(connection)},
                json={
                    "organization_name": "Rate Limit Events",
                    "admin_name": "Admin",
                    "admin_email": "admin@example.com",
                },
            )
        ).status_code == 200

        denied_source = RecordingRateLimiter(allowed=False)
        untouched_recipient = RecordingRateLimiter()
        environment.MAGIC_LINK_SOURCE_RATE_LIMITER = denied_source
        environment.MAGIC_LINK_RECIPIENT_RATE_LIMITER = untouched_recipient
        source_refused = await client.post(
            "/api/v1/auth/magic-links",
            headers={"cf-connecting-ip": "192.0.2.20"},
            json={"email": "admin@example.com", "redirect_path": "/admin"},
        )
        assert source_refused.status_code == 429
        assert source_refused.headers["retry-after"] == "60"
        assert len(denied_source.keys) == 1
        assert untouched_recipient.keys == []

        allowed_source = RecordingRateLimiter()
        denied_recipient = RecordingRateLimiter(allowed=False)
        environment.MAGIC_LINK_SOURCE_RATE_LIMITER = allowed_source
        environment.MAGIC_LINK_RECIPIENT_RATE_LIMITER = denied_recipient
        recipient_refused = await client.post(
            "/api/v1/auth/magic-links",
            headers={"cf-connecting-ip": "192.0.2.21"},
            json={"email": "admin@example.com", "redirect_path": "/admin"},
        )
        assert recipient_refused.status_code == 429
        assert recipient_refused.headers["retry-after"] == "60"
        assert len(allowed_source.keys) == 1
        assert len(denied_recipient.keys) == 1


async def test_magic_link_get_renders_fragment_transfer_without_a_server_token(
    production_environment,
) -> None:
    _connection, _queue, environment = production_environment
    async with _client(environment) as client:
        response = await client.get("/auth/verify")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<form method="post" action="/auth/verify"' in response.text
    assert 'name="token" autocomplete="off" value=""' in response.text
    assert 'auth-link-confirm.js?v=3' in response.text


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
                "admin_first_name": "Asha",
                "admin_last_name": "Rao",
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
        assert "From open call to published agenda." in configured_home.text
        assert connection.execute(
            "SELECT COUNT(*) FROM instance_setup WHERE singleton_key='primary'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM instance_setup_credentials"
        ).fetchone()[0] == 0
        user = connection.execute(
            """SELECT first_name,last_name,display_name,job_title,company,time_zone FROM users
               WHERE normalized_email='asha@example.com'"""
        ).fetchone()
        assert tuple(user) == (
            "Asha",
            "Rao",
            "Asha Rao",
            "Event director",
            "Example Events",
            "Asia/Kolkata",
        )

        assert (
            await client.post(
                "/api/v1/auth/magic-links",
                json={"email": "asha@example.com", "redirect_path": "/account"},
            )
        ).status_code == 202
        assert (
            await client.post(
                "/auth/verify",
                data={"token": _token(connection, "asha@example.com")},
                follow_redirects=False,
            )
        ).status_code == 303
        session = (await client.get("/api/v1/auth/session")).json()
        assert session["display_name"] == "Asha Rao"
        assert session["profile_complete"] is False
        assert session["default_email_sender_name"] == "SessionBuddy"
        assert session["default_email_address"] == "events@example.test"
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
        assert updated.json()["csrf_token"] is None
        assert "set-cookie" not in updated.headers
        assert (await client.get("/api/v1/auth/session")).json()["profile_complete"] is True
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='account.profile.update'"
        ).fetchone()[0] == 1


async def test_profile_password_change_rotates_the_caller_and_keeps_magic_links(
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
                "/auth/verify",
                data={"token": _token(connection, "password@example.com")},
                follow_redirects=False,
            )
        ).status_code == 303
        session = (await client.get("/api/v1/auth/session")).json()
        profile = (await client.get("/api/v1/account/profile")).json()
        async with _client(environment) as old_session_client:
            old_session_client.cookies.update(client.cookies)
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
            assert "set-cookie" in updated.headers
            result = updated.json()
            assert result["has_password"] is True
            assert result["csrf_token"]
            assert "password" not in result

            # The caller continues with the replacement cookie and CSRF token;
            # another browser holding the retired cookie cannot authenticate.
            replacement = await client.get("/api/v1/auth/session")
            assert replacement.status_code == 200
            assert replacement.json()["csrf_token"] == result["csrf_token"]
            assert (await old_session_client.get("/api/v1/auth/session")).status_code == 401
            saved_again = await client.patch(
                "/api/v1/account/profile",
                headers={"origin": "https://test", "x-csrf-token": result["csrf_token"]},
                json={
                    "first_name": "Password",
                    "last_name": "Owner",
                    "job_title": None,
                    "company": None,
                    "time_zone": "UTC",
                    "password": None,
                    "password_confirmation": None,
                    "version": result["version"],
                },
            )
            assert saved_again.status_code == 200
            assert saved_again.json()["csrf_token"] is None
            assert "set-cookie" not in saved_again.headers

        session_versions = connection.execute(
            """SELECT s.authorization_version,u.authorization_version
               FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.revoked_at_ms IS NULL AND s.user_id=?""",
            (bootstrap.json()["admin_user_id"],),
        ).fetchall()
        assert len(session_versions) == 1
        assert session_versions[0][0] == session_versions[0][1]
        audit = connection.execute(
            """SELECT metadata_json FROM audit_events
               WHERE action='account.profile_and_password.update'"""
        ).fetchone()
        assert json.loads(audit[0]) == {
            "other_sessions_revoked": True,
            "session_rotated": True,
        }

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


async def test_sign_in_uses_default_role_and_switching_is_server_authoritative(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        created = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Role Defaults",
                "admin_name": "Role Owner",
                "admin_email": "roles@example.com",
            },
        )
        user_id = created.json()["admin_user_id"]
        connection.execute(
            "UPDATE user_roles SET is_default=0 WHERE user_id=? AND role='organizer'",
            (user_id,),
        )
        connection.execute(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES(?,'speaker','active',1,1,1)""",
            (user_id,),
        )
        connection.commit()

        assert (
            await client.post(
                "/api/v1/auth/magic-links",
                json={"email": "roles@example.com", "redirect_path": "/"},
            )
        ).status_code == 202
        verified = await client.post(
            "/auth/verify",
            data={"token": _token(connection, "roles@example.com")},
            follow_redirects=False,
        )
        assert verified.status_code == 303
        assert verified.headers["location"] == "/speaker"

        session = (await client.get("/api/v1/auth/session")).json()
        assert session["default_role"] == "speaker"
        assert session["active_role"] == "speaker"
        switched = await client.put(
            "/api/v1/session/active-role",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json={"role": "organizer"},
        )
        assert switched.status_code == 200
        assert switched.json()["active_role"] == "organizer"
        assert switched.json()["default_role"] == "speaker"
        organization_id = created.json()["organization_id"]
        event = await client.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json={
                "name": "Persona Boundary",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_000_001_000,
                    "time_zone": "UTC",
                    "delivery_mode": "virtual",
                    "location": "Online",
                    "description": "Active-persona authorization test.",
            },
        )
        assert event.status_code == 201
        speaker_session = await client.put(
            "/api/v1/session/active-role",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json={"role": "speaker"},
        )
        assert speaker_session.status_code == 200
        denied_while_speaker = await client.patch(
            f"/api/v1/admin/events/{event.json()['id']}",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json={
                "name": "Must not change",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_000_001_000,
                "time_zone": "UTC",
                "delivery_mode": "virtual",
                "location": "Online",
                "description": "Active-persona authorization test.",
                "status": "active",
                "version": 1,
            },
        )
        assert denied_while_speaker.status_code == 403
        assert connection.execute(
            "SELECT name FROM events WHERE id=?", (event.json()["id"],)
        ).fetchone()["name"] == "Persona Boundary"
        await client.put(
            "/api/v1/session/active-role",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json={"role": "organizer"},
        )
        changed_default = await client.put(
            "/api/v1/account/default-role",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json={"role": "organizer"},
        )
        assert changed_default.status_code == 200
        assert changed_default.json()["active_role"] == "organizer"
        assert changed_default.json()["default_role"] == "organizer"
        assert connection.execute(
            "SELECT role FROM user_roles WHERE user_id=? AND is_default=1", (user_id,)
        ).fetchone()["role"] == "organizer"
        denied = await client.put(
            "/api/v1/session/active-role",
            headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
            json={"role": "reviewer"},
        )
        assert denied.status_code == 403


async def test_password_sign_in_replaces_an_incompatible_role_workspace_redirect(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    password = "speaker password for redirect regression"  # noqa: S105 - test fixture
    _insert_password_speaker(
        connection,
        user_id="speaker-stale-redirect",
        email="speaker-stale-redirect@example.com",
        password=password,
    )

    async with _client(environment) as client:
        signed_in = await client.post(
            "/api/v1/auth/password/sign-in",
            json={
                "email": "speaker-stale-redirect@example.com",
                "password": password,
                "redirect_path": "/reviews",
            },
        )
        assert signed_in.status_code == 200
        assert signed_in.json()["redirect_path"] == "/speaker"
        # An unlinked speaker sees the UI onboarding state; the domain API
        # remains opaque so no event or tenant data crosses the boundary.
        assert (await client.get("/api/v1/speaker/portal")).status_code == 404


async def test_sign_in_and_session_fail_closed_without_an_explicit_active_role(
    production_environment,
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        created = await client.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Strict Roles",
                "admin_name": "Strict Owner",
                "admin_email": "strict@example.com",
            },
        )
        assert created.status_code == 200
        await client.post(
            "/api/v1/auth/magic-links",
            json={"email": "strict@example.com", "redirect_path": "/"},
        )
        token = _token(connection, "strict@example.com")
        verified = await client.post(
            "/auth/verify",
            data={"token": token},
            follow_redirects=False,
        )
        assert verified.status_code == 303
        assert verified.headers["location"] == "/admin"

        connection.execute("DELETE FROM session_active_roles")
        connection.commit()
        session = await client.get("/api/v1/auth/session")
        home = await client.get("/", follow_redirects=False)
        assert session.status_code == 403
        assert home.status_code == 403
        assert "location" not in home.headers

    async with _client(environment) as fresh_client:
        await fresh_client.post(
            "/api/v1/auth/magic-links",
            json={"email": "strict@example.com", "redirect_path": "/"},
        )
        token = _token(connection, "strict@example.com")
        connection.execute(
            "UPDATE user_roles SET is_default=0 WHERE user_id=?",
            (created.json()["admin_user_id"],),
        )
        connection.commit()
        session_count = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        refused = await fresh_client.post(
            "/auth/verify",
            data={"token": token},
            follow_redirects=False,
        )
        assert refused.status_code == 403
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == session_count


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
        for table in (
            "organization_activity",
            "event_activity",
            "organizer_activity",
            "reviewer_activity",
            "speaker_activity",
            "activity_distribution_guards",
            "activity_status",
            "activity_routing",
            "activities",
            "activity_entities",
        ):
            connection.execute(f'DELETE FROM "{table}"')  # noqa: S608
        connection.execute("DELETE FROM resource_access_grants")
        connection.execute("DELETE FROM resource_ownership_transfers")
        connection.execute("DELETE FROM owned_resources")
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
            "/auth/verify",
            data={"token": _token(connection, "admin@example.com")},
            follow_redirects=False,
        )
        assert verified.status_code == 303
        assert verified.headers["location"] == "/admin/events"
        session = (await admin.get("/api/v1/auth/session")).json()
        assert session["email"] == "admin@example.com"
        assert session["organization_access"] == [{
            "organization_id": organization_id,
            "organization_name": "Integration Events",
            "permissions": ["owner"],
        }]
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
                "event_name": "Speaker Summit",
                "permissions": ["owner"],
                "assignments": [],
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
            headers={**mutation_headers, "idempotency-key": "empty-agenda-publish-2026"},
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
                "biography": "Builds dependable systems for event teams.",
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

        drafted = await admin.patch(
            f"/api/v1/admin/events/{event_id}",
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
                "status": "draft",
                "version": 1,
            },
        )
        assert drafted.status_code == 200

        draft_workspace = await admin.get(f"/api/v1/admin/events/{event_id}/cfp")
        assert draft_workspace.status_code == 200
        assert draft_workspace.json()["event_name"] == "Speaker Summit"
        assert draft_workspace.json()["published_form"] is None
        draft_publish = await admin.post(
            f"/api/v1/admin/events/{event_id}/cfp/publish",
            headers={**mutation_headers, "idempotency-key": "draft-publish-integration-2026"},
            json={
                "slug": "speaker-summit",
                "welcome_text": "Share your session.",
                "closes_at_ms": 1_899_913_600_000,
                "confirmation_subject": "Speaker Summit received your proposal",
                "confirmation_body": "Thank you for proposing a session to Speaker Summit.",
            },
        )
        assert draft_publish.status_code in {409, 422}
        assert draft_publish.json()["error"]["code"] == "conflict"
        assert connection.execute(
                "SELECT COUNT(*) FROM call_for_speaker_forms WHERE event_id=?", (event_id,)
        ).fetchone()[0] == 0

        activated = await admin.patch(
            f"/api/v1/admin/events/{event_id}",
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
                "status": "active",
                "version": 2,
            },
        )
        assert activated.status_code == 200

        published = await admin.post(
            f"/api/v1/admin/events/{event_id}/cfp/publish",
            headers={**mutation_headers, "idempotency-key": "publish-integration-2026"},
            json={
                "slug": "speaker-summit",
                "welcome_text": "Share your session.",
                "closes_at_ms": 1_899_913_600_000,
                "confirmation_subject": "Speaker Summit received your proposal",
                "confirmation_body": "Thank you for proposing a session to Speaker Summit.",
            },
        )
        assert published.status_code == 201
        live_workspace = await admin.get(f"/api/v1/admin/events/{event_id}/cfp")
        assert live_workspace.status_code == 200
        assert live_workspace.json()["organization_id"] == organization_id
        assert live_workspace.json()["event_id"] == event_id
        assert live_workspace.json()["published_form"]["slug"] == "speaker-summit"
        assert live_workspace.json()["published_form"]["confirmation_subject"] == (
            "Speaker Summit received your proposal"
        )
        assert live_workspace.json()["published_form"]["confirmation_body"] == (
            "Thank you for proposing a session to Speaker Summit."
        )
        public_form = await admin.get("/api/v1/forms/speaker-summit")
        assert public_form.status_code == 200
        assert "confirmation_subject" not in public_form.json()
        assert "confirmation_body" not in public_form.json()
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
            "confirmation_subject": "Your revised proposal was received",
            "confirmation_body": "Thank you. The revised program team message is saved.",
        }
        updated_form = await admin.patch(
            f"/api/v1/admin/events/{event_id}/cfp",
            headers=mutation_headers,
            json=update_payload,
        )
        assert updated_form.status_code == 200
        assert updated_form.json()["version"] == published_form["version"] + 1
        assert updated_form.json()["welcome_text"] == "Share your revised session proposal."
        assert updated_form.json()["confirmation_subject"] == "Your revised proposal was received"
        assert updated_form.json()["confirmation_body"] == (
            "Thank you. The revised program team message is saved."
        )
        reloaded_workspace = await admin.get(f"/api/v1/admin/events/{event_id}/cfp")
        assert reloaded_workspace.json()["published_form"]["confirmation_subject"] == (
            "Your revised proposal was received"
        )
        reloaded_public_form = await admin.get("/api/v1/forms/speaker-summit")
        assert "confirmation_subject" not in reloaded_public_form.json()
        assert "confirmation_body" not in reloaded_public_form.json()
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
            "/auth/verify",
            data={"token": _token(connection, "speaker@example.com")},
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
        assert portal.json()["profile"]["biography"] == (
            "Builds dependable systems for event teams."
        )
        open_call = portal.json()["open_call"]
        assert open_call["slug"] == "speaker-summit"
        assert open_call["accepting_submissions"] is True
        assert open_call["submitted_count"] == 0
        if open_call["submission_limit"] is not None:
            assert open_call["remaining_submissions"] == open_call["submission_limit"]

        # Public CFP speakers are external participants, not tenant members.
        # Their authenticated user identity owns the draft; requiring broad
        # organization membership here would make public draft saving fail.
        speaker_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email=?",
            ("speaker@example.com",),
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM speaker_tasks WHERE event_speaker_id IN "
            "(SELECT es.id FROM event_speakers es JOIN people p ON p.id=es.person_id "
            "WHERE p.user_id=?)",
            (speaker_user_id,),
        )
        connection.execute(
            "DELETE FROM event_speakers WHERE person_id IN "
            "(SELECT id FROM people WHERE user_id=?)",
            (speaker_user_id,),
        )
        connection.execute("DELETE FROM people WHERE user_id=?", (speaker_user_id,))
        connection.execute(
            "DELETE FROM event_memberships WHERE user_id=?",
            (speaker_user_id,),
        )
        connection.execute(
            "DELETE FROM organization_memberships WHERE user_id=?",
            (speaker_user_id,),
        )
        connection.commit()

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
        listed_drafts = await speaker.get("/api/v1/speaker/proposal-drafts")
        assert listed_drafts.status_code == 200
        assert listed_drafts.json() == {
            "data": [
                {
                    "id": draft.json()["id"],
                    "form_id": draft.json()["form_id"],
                    "event_id": event_id,
                    "event_name": "Speaker Summit",
                    "form_slug": "speaker-summit",
                    "proposal_title": "Production identity",
                    "updated_at_ms": draft.json()["updated_at_ms"],
                    "edit_path": f"/cfp/{event_id.replace('-', '')[:6]}/speaker-summit",
                }
            ]
        }
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
        workspace_path = f"/speaker/proposals/speaker-summit/{submission.json()['id']}"
        owner_workspace = await speaker.get(workspace_path)
        assert owner_workspace.status_code == 200
        assert "Proposal" in owner_workspace.text

        outsider_password = "outsider private passphrase"  # noqa: S105 - synthetic test credential
        contributor_password = "contributor private passphrase"  # noqa: S105 - synthetic test credential
        _insert_password_speaker(
            connection,
            user_id="workspace-outsider",
            email="workspace-outsider@example.com",
            password=outsider_password,
        )
        _insert_password_speaker(
            connection,
            user_id="workspace-contributor",
            email="workspace-contributor@example.com",
            password=contributor_password,
        )
        connection.execute(
            """INSERT INTO submission_contributors
               (id,organization_id,event_id,submission_id,display_name,email,
                normalized_email,created_at_ms,updated_at_ms,invitation_status,
                accepted_at_ms,user_id)
               VALUES('workspace-contributor-link',?,?,?,?,?,?,1,1,'accepted',1,?)""",
            (
                organization_id,
                event_id,
                submission.json()["id"],
                "Workspace Contributor",
                "workspace-contributor@example.com",
                "workspace-contributor@example.com",
                "workspace-contributor",
            ),
        )
        connection.commit()

        async with _client(environment) as outsider:
            signed_in = await outsider.post(
                "/api/v1/auth/password/sign-in",
                json={
                    "email": "workspace-outsider@example.com",
                    "password": outsider_password,
                    "redirect_path": "/speaker",
                },
            )
            assert signed_in.status_code == 200
            assert (await outsider.get(workspace_path)).status_code == 404

        async with _client(environment) as contributor:
            signed_in = await contributor.post(
                "/api/v1/auth/password/sign-in",
                json={
                    "email": "workspace-contributor@example.com",
                    "password": contributor_password,
                    "redirect_path": "/speaker",
                },
            )
            assert signed_in.status_code == 200
            assert (await contributor.get(workspace_path)).status_code == 200

        assert (await speaker.get("/api/v1/forms/speaker-summit/draft")).json() is None
        withdrawal_candidate = await speaker.post(
            "/api/v1/forms/speaker-summit/submissions",
            headers={
                **speaker_headers,
                "idempotency-key": "withdrawal-candidate-integration-2026",
                "x-public-session-id": "public-session-withdrawal-2026",
            },
            json={
                "speaker_name": "Integration Speaker",
                "speaker_email": "speaker@example.com",
                "proposal_title": "Proposal to withdraw",
                "proposal_abstract": "A proposal used to verify safe withdrawal.",
            },
        )
        assert withdrawal_candidate.status_code == 201
        withdrawal_path = (
            "/api/v1/forms/speaker-summit/submissions/"
            f"{withdrawal_candidate.json()['id']}/withdraw"
        )
        withdrawn = await speaker.post(withdrawal_path, headers=speaker_headers, json={})
        assert withdrawn.status_code == 200
        assert withdrawn.json()["status"] == "withdrawn"
        assert withdrawn.json()["editable"] is False
        repeated_withdrawal = await speaker.post(
            withdrawal_path, headers=speaker_headers, json={}
        )
        assert repeated_withdrawal.status_code == 200
        assert repeated_withdrawal.json()["status"] == "withdrawn"
        assert connection.execute(
            """SELECT COUNT(*) FROM audit_events
               WHERE action='submission.withdraw' AND target_id=?""",
            (withdrawal_candidate.json()["id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            """SELECT COUNT(*) FROM evaluation_assignments
               WHERE submission_id=?""",
            (withdrawal_candidate.json()["id"],),
        ).fetchone()[0] == 0
        portal = await speaker.get("/api/v1/speaker/portal")
        portal_submissions = {item["id"]: item for item in portal.json()["submissions"]}
        stored_submitted_at = connection.execute(
            "SELECT submitted_at_ms FROM submissions WHERE id=?",
            (submission.json()["id"],),
        ).fetchone()[0]
        # The portal call allowance is computed from this signed-in speaker's
        # active proposals. A withdrawn proposal does not consume the limit.
        refreshed_call = portal.json()["open_call"]
        assert refreshed_call["submitted_count"] == 1
        if refreshed_call["submission_limit"] is not None:
            assert refreshed_call["remaining_submissions"] == (
                refreshed_call["submission_limit"] - 1
            )
        assert portal_submissions[submission.json()["id"]]["status"] == "submitted"
        assert (
            portal_submissions[submission.json()["id"]]["submitted_at_ms"]
            == stored_submitted_at
        )
        assert portal_submissions[withdrawal_candidate.json()["id"]]["status"] == "withdrawn"
        assert portal_submissions[withdrawal_candidate.json()["id"]]["editable"] is False
        person_id = connection.execute(
            "SELECT id FROM people WHERE user_id=?",
            (speaker_session["user_id"],),
        ).fetchone()["id"]
        own_profile = await speaker.get(f"/api/v1/speaker-profiles/{person_id}")
        assert own_profile.status_code == 200
        assert own_profile.json()["can_edit"] is True
        assert own_profile.json()["email"] == "speaker@example.com"

        async with _client(environment, origin=None) as anonymous:
            default_public_profile = await anonymous.get(
                f"/api/v1/public/people/{speaker_session['user_id']}"
            )
            assert default_public_profile.status_code == 200

        account = (await speaker.get("/api/v1/account/profile")).json()
        assert account["public_profile_enabled"] is True
        enabled = await speaker.patch(
            "/api/v1/account/profile",
            headers=speaker_headers,
            json={
                "first_name": account["first_name"] or "Integration",
                "last_name": account["last_name"] or "Speaker",
                "job_title": account["job_title"],
                "company": account["company"],
                "time_zone": account["time_zone"],
                "description": account["description"],
                "website_url": account["website_url"],
                "linkedin_url": account["linkedin_url"],
                "x_url": account["x_url"],
                "public_profile_enabled": True,
                "version": account["version"],
            },
        )
        assert enabled.status_code == 200
        assert enabled.json()["public_profile_enabled"] is True

        async with _client(environment, origin=None) as anonymous:
            public_profile = await anonymous.get(
                f"/api/v1/public/people/{speaker_session['user_id']}"
            )
            assert public_profile.status_code == 200
            public_payload = public_profile.json()
            assert public_payload["user_id"] == speaker_session["user_id"]
            assert public_payload["display_name"]
            assert "email" not in public_payload
            assert "roles" not in public_payload
            assert "organization_id" not in public_payload
            assert "event_id" not in public_payload
            public_page = await anonymous.get(f"/people/{speaker_session['user_id']}")
            assert public_page.status_code == 200

    async with _client(environment) as admin_again:
        await admin_again.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        await admin_again.post(
            "/auth/verify",
            data={"token": _token(connection, "admin@example.com")},
            follow_redirects=False,
        )
        session = (await admin_again.get("/api/v1/auth/session")).json()
        headers = {
            "content-type": "application/json",
            "origin": "https://test",
            "x-csrf-token": session["csrf_token"],
        }
        forbidden_withdrawal = await admin_again.post(
            withdrawal_path, headers=headers, json={}
        )
        assert forbidden_withdrawal.status_code == 404
        managed_profile = await admin_again.get(
            f"/api/v1/speaker-profiles/{person_id}"
        )
        assert managed_profile.status_code == 200
        assert managed_profile.json()["can_edit"] is False
        assert managed_profile.json()["email"] == ""
        reviewer_user_id = "77777777-7777-4777-8777-777777777777"
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,email_verified_at_ms,version,
                authorization_version,created_at_ms,updated_at_ms,display_name,
                first_name,last_name,profile_completed_at_ms)
               VALUES (?,?,?,?,?,1,1,?,?,?, ?,?,?)""",
            (
                reviewer_user_id,
                "reviewer@example.com",
                "reviewer@example.com",
                "active",
                800,
                800,
                800,
                "Review Person",
                "Review",
                "Person",
                800,
            ),
        )
        connection.execute(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES (?,'reviewer','active',800,800,1)""",
            (reviewer_user_id,),
        )
        connection.commit()

        # Exact-email lookup cannot enumerate or expose unrelated accounts.
        assert (
            await admin_again.get(f"/api/v1/admin/events/{event_id}/evaluators")
        ).json() == {"data": [], "pending": []}
        assert (
            await admin_again.get(
                f"/api/v1/admin/events/{event_id}/evaluators",
                params={"email": "review"},
            )
        ).json() == {"data": [], "pending": []}
        assert (
            await admin_again.get(
                f"/api/v1/admin/events/{event_id}/evaluators",
                params={"email": "speaker@example.com"},
            )
        ).json() == {"data": [], "pending": []}
        # A seeded global reviewer has neither sign-in nor assignment authority
        # until this exact event's evaluator invitation has been accepted.
        assert (
            await admin_again.get(
                f"/api/v1/admin/events/{event_id}/evaluators",
                params={"email": "REVIEWER@example.com"},
            )
        ).json() == {"data": [], "pending": []}
        delivered_without_invitation = connection.execute(
            "SELECT COUNT(*) FROM communication_messages WHERE recipient_email=?",
            ("reviewer@example.com",),
        ).fetchone()[0]
        uninvited_request = await admin_again.post(
            "/api/v1/auth/magic-links",
            json={"email": "reviewer@example.com", "redirect_path": "/reviews"},
        )
        assert uninvited_request.status_code == 202
        assert connection.execute(
            "SELECT COUNT(*) FROM communication_messages WHERE recipient_email=?",
            ("reviewer@example.com",),
        ).fetchone()[0] == delivered_without_invitation
        admin_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,accepted_at_ms,created_at_ms,updated_at_ms)
               VALUES('accepted-reviewer-fixture',?,?,?,?,'evaluator','accepted',?,
                      2000000000000,801,800,801)""",
            (
                organization_id,
                event_id,
                "reviewer@example.com",
                "reviewer@example.com",
                admin_user_id,
            ),
        )
        connection.commit()
        reviewer_lookup = await admin_again.get(
            f"/api/v1/admin/events/{event_id}/evaluators",
            params={"email": "REVIEWER@example.com"},
        )
        assert reviewer_lookup.json() == {
            "data": [{"user_id": reviewer_user_id, "display_name": "Review Person"}],
            "pending": [],
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM event_memberships WHERE user_id=?",
            (reviewer_user_id,),
        ).fetchone()[0] == 0

        created_round = await admin_again.post(
            f"/api/v1/admin/events/{event_id}/evaluation-rounds",
            headers={**headers, "idempotency-key": "external-reviewer-round"},
            json={
                "name": "External reviewer round",
                "rating_min": 1,
                "rating_max": 5,
                "recommendations": ["accept", "reject"],
                "assignment_strategy": "all",
                "submission_ids": [submission.json()["id"]],
                "evaluator_user_ids": [reviewer_user_id],
            },
        )
        assert created_round.status_code == 201, created_round.text
        assignment_id = connection.execute(
            "SELECT id FROM evaluation_assignments WHERE round_id=?",
            (created_round.json()["id"],),
        ).fetchone()[0]
        async with _client(environment) as reviewer:
            await reviewer.post(
                "/api/v1/auth/magic-links",
                json={"email": "reviewer@example.com", "redirect_path": "/admin"},
            )
            verified = await reviewer.post(
                "/auth/verify",
                data={"token": _token(connection, "reviewer@example.com")},
                follow_redirects=False,
            )
            assert verified.status_code == 303
            assert verified.headers["location"] == "/reviews"
            reviewer_session = (await reviewer.get("/api/v1/auth/session")).json()
            assert reviewer_session["organization_access"] == []
            assert reviewer_session["event_access"] == [
                {
                    "organization_id": organization_id,
                    "event_id": event_id,
                    "event_name": "Speaker Summit",
                    "permissions": [],
                    "assignments": ["reviewer"],
                }
            ]
            assert (
                await reviewer.get(f"/api/v1/admin/events/{event_id}/submissions")
            ).status_code == 403
            assert (await reviewer.get("/api/v1/speaker/portal")).status_code == 404
            reviews = await reviewer.get("/api/v1/evaluator/assignments")
            assert reviews.status_code == 200
            assert [item["id"] for item in reviews.json()["data"]] == [assignment_id]
            connection.execute(
                "UPDATE evaluation_assignments SET status='revoked' WHERE id=?",
                (assignment_id,),
            )
            connection.commit()
            assert (await reviewer.get("/api/v1/evaluator/assignments")).json()["data"] == []
        connection.execute(
            """UPDATE identity_invitations
               SET status='revoked',accepted_at_ms=NULL,revoked_at_ms=902,updated_at_ms=902
               WHERE id='accepted-reviewer-fixture'"""
        )
        connection.commit()
        delivered_before = connection.execute(
            "SELECT COUNT(*) FROM communication_messages WHERE recipient_email=?",
            ("reviewer@example.com",),
        ).fetchone()[0]
        async with _client(environment) as signed_out_reviewer:
            revoked_request = await signed_out_reviewer.post(
                "/api/v1/auth/magic-links",
                json={"email": "reviewer@example.com", "redirect_path": "/reviews"},
            )
            unknown_request = await signed_out_reviewer.post(
                "/api/v1/auth/magic-links",
                json={"email": "unknown@example.com", "redirect_path": "/reviews"},
            )
        assert revoked_request.status_code == unknown_request.status_code == 202
        assert revoked_request.json() == unknown_request.json()
        assert connection.execute(
            "SELECT COUNT(*) FROM communication_messages WHERE recipient_email=?",
            ("reviewer@example.com",),
        ).fetchone()[0] == delivered_before
        connection.execute(
            "DELETE FROM evaluation_assignments WHERE round_id=?",
            (created_round.json()["id"],),
        )
        connection.execute(
            "DELETE FROM evaluation_round_evaluators WHERE round_id=?",
            (created_round.json()["id"],),
        )
        connection.execute(
            "DELETE FROM evaluation_round_submissions WHERE round_id=?",
            (created_round.json()["id"],),
        )
        connection.execute(
            "DELETE FROM evaluation_rounds WHERE id=?",
            (created_round.json()["id"],),
        )
        connection.execute(
            """UPDATE identity_invitations
               SET status='accepted',accepted_at_ms=903,revoked_at_ms=NULL,updated_at_ms=903
               WHERE id='accepted-reviewer-fixture'"""
        )
        connection.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,
                created_at_ms,updated_at_ms,closed_at_ms)
               VALUES ('review-block-round',?,?,'Initial review','{}','open',900,900,NULL)""",
            (organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO evaluation_round_submissions
               (round_id,submission_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES ('review-block-round',?,?,?,'active',900,900)""",
            (submission.json()["id"], organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO evaluation_round_evaluators
               (round_id,evaluator_user_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES ('review-block-round',?,?,?,'active',900,900)""",
            (reviewer_user_id, organization_id, event_id),
        )
        connection.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,
                evaluator_user_id,status,created_at_ms,updated_at_ms)
               VALUES
                 ('review-block-assignment',?,?,'review-block-round',?,?,'assigned',900,900)""",
            (organization_id, event_id, submission.json()["id"], reviewer_user_id),
        )
        connection.commit()
        async with _client(environment) as speaker_after_review:
            await speaker_after_review.post(
                "/api/v1/auth/magic-links",
                json={"email": "speaker@example.com", "redirect_path": "/speaker"},
            )
            await speaker_after_review.post(
                "/auth/verify",
                data={"token": _token(connection, "speaker@example.com")},
                follow_redirects=False,
            )
            reviewed_session = (
                await speaker_after_review.get("/api/v1/auth/session")
            ).json()
            blocked_withdrawal = await speaker_after_review.post(
                "/api/v1/forms/speaker-summit/submissions/"
                f"{submission.json()['id']}/withdraw",
                headers={
                    "origin": "https://test",
                    "x-csrf-token": reviewed_session["csrf_token"],
                },
                json={},
            )
            assert blocked_withdrawal.status_code == 409
            assert blocked_withdrawal.json()["error"]["code"] == "conflict"
        connection.execute(
            "DELETE FROM evaluation_assignments WHERE id='review-block-assignment'"
        )
        connection.execute(
            "DELETE FROM evaluation_round_evaluators WHERE round_id='review-block-round'"
        )
        connection.execute(
            "DELETE FROM evaluation_round_submissions WHERE round_id='review-block-round'"
        )
        connection.execute("DELETE FROM evaluation_rounds WHERE id='review-block-round'")
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

        archived_event = await admin_again.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=headers,
            json={
                "name": "Archived Summit",
                "starts_at_ms": 1_902_000_000_000,
                "ends_at_ms": 1_902_086_400_000,
                "time_zone": "Asia/Kolkata",
                "delivery_mode": "hybrid",
                "location": "Mumbai",
                "description": "Archived conference",
            },
        )
        assert archived_event.status_code == 201
        archived_event_id = archived_event.json()["id"]
        archived = await admin_again.patch(
            f"/api/v1/admin/events/{archived_event_id}",
            headers=headers,
            json={
                "name": "Archived Summit",
                "starts_at_ms": 1_902_000_000_000,
                "ends_at_ms": 1_902_086_400_000,
                "time_zone": "Asia/Kolkata",
                "delivery_mode": "hybrid",
                "location": "Mumbai",
                "description": "Archived conference",
                "status": "archived",
                "version": 1,
            },
        )
        assert archived.status_code == 200
        assert (
            await admin_again.get(f"/api/v1/admin/events/{archived_event_id}/cfp")
        ).status_code == 404

        invitations = await admin_again.get(f"/api/v1/admin/events/{event_id}/invitations")
        assert {
            (item["role"], item["status"]) for item in invitations.json()["data"]
        } == {("speaker", "accepted"), ("evaluator", "accepted")}
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
                "/auth/verify",
                data={
                    "token": _token(connection, "speaker@example.com"),
                    "first_name": "Returning",
                    "last_name": "Speaker",
                    "password": "a private returning speaker passphrase",
                    "password_confirmation": "a private returning speaker passphrase",
                },
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
            "/auth/verify",
            data={"token": _token(connection, "admin@example.com")},
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
            json={
                "email": "reviewer@example.com",
                "role": "evaluator",
                "display_name": "Sam Whitfield",
            },
        )
        assert temporary.status_code == 201
        # Authorized invitation managers receive the newly minted bearer URL
        # once so they can copy it into an approved delivery channel. Lists
        # never disclose it later.
        assert temporary.json()["access_url"].startswith(
            "https://test/auth/verify#token="
        )
        assert _token(connection, "reviewer@example.com")
        listed = await client.get(f"/api/v1/admin/events/{event_id}/invitations")
        assert listed.json()["data"][0]["id"] == temporary.json()["id"]
        assert "access_url" not in listed.json()["data"][0]
        resent = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations/{temporary.json()['id']}/resend",
            headers=headers,
            json={},
        )
        assert resent.status_code == 200
        assert resent.json()["access_url"].startswith("https://test/auth/verify#token=")
        assert resent.json()["access_url"] != temporary.json()["access_url"]
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
            "/auth/verify",
            data={"token": _token(connection, "admin@example.com")},
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
    assert [row["role"] for row in roles] == ["speaker"]
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


async def test_accepted_reviewer_can_sign_in_before_a_round_without_event_access(
    production_environment,
) -> None:
    connection, _, environment = production_environment
    async with _client(environment) as admin:
        bootstrap = await admin.post(
            "/api/v1/bootstrap",
            headers={"x-bootstrap-token": _deployment_key(connection)},
            json={
                "organization_name": "Pre-round Review Events",
                "admin_name": "Admin",
                "admin_email": "admin@example.com",
            },
        )
        organization_id = bootstrap.json()["organization_id"]
        await admin.post(
            "/api/v1/auth/magic-links",
            json={"email": "admin@example.com", "redirect_path": "/admin/events"},
        )
        await admin.post(
            "/auth/verify",
            data={"token": _token(connection, "admin@example.com")},
            follow_redirects=False,
        )
        admin_session = (await admin.get("/api/v1/auth/session")).json()
        headers = {
            "origin": "https://test",
            "x-csrf-token": admin_session["csrf_token"],
        }
        event = await admin.post(
            f"/api/v1/admin/organizations/{organization_id}/events",
            headers=headers,
            json={
                "name": "Review Summit",
                "starts_at_ms": 1_900_000_000_000,
                "ends_at_ms": 1_900_086_400_000,
                "time_zone": "UTC",
                "location": "Online",
                "delivery_mode": "virtual",
                "description": "A reviewer authentication boundary test.",
            },
        )
        assert event.status_code == 201
        event_id = event.json()["id"]
        invitation = await admin.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=headers,
            json={
                "email": "reviewer@example.com",
                "role": "evaluator",
                "display_name": "Sam Whitfield",
            },
        )
        assert invitation.status_code == 201

    async with _client(environment) as accepting_reviewer:
        accepted = await accepting_reviewer.post(
            "/auth/verify",
            data={"token": _token(connection, "reviewer@example.com")},
            follow_redirects=False,
        )
        assert accepted.status_code == 303
        assert accepted.headers["location"] == "/account?onboarding=1&next=/reviews"

    reviewer_user_id = connection.execute(
        "SELECT id FROM users WHERE normalized_email='reviewer@example.com'"
    ).fetchone()[0]
    assert connection.execute(
        "SELECT display_name FROM users WHERE id=?", (reviewer_user_id,)
    ).fetchone()[0] == "Sam Whitfield"
    assert connection.execute(
        "SELECT COUNT(*) FROM organization_memberships WHERE user_id=?",
        (reviewer_user_id,),
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM evaluation_assignments WHERE evaluator_user_id=?",
        (reviewer_user_id,),
    ).fetchone()[0] == 0

    async with _client(environment) as returning_reviewer:
        requested = await returning_reviewer.post(
            "/api/v1/auth/magic-links",
            json={"email": "reviewer@example.com", "redirect_path": "/reviews"},
        )
        assert requested.status_code == 202
        redeemed = await returning_reviewer.post(
            "/auth/verify",
            data={"token": _token(connection, "reviewer@example.com")},
            follow_redirects=False,
        )
        assert redeemed.status_code == 303
        session = (await returning_reviewer.get("/api/v1/auth/session")).json()
        assert session["organization_access"] == []
        assert session["event_access"] == []
        assignments = await returning_reviewer.get("/api/v1/evaluator/assignments")
        assert assignments.status_code == 200
        assert assignments.json()["data"] == []
        assert (
            await returning_reviewer.get(f"/api/v1/admin/events/{event_id}/submissions")
        ).status_code == 403
        assert (await returning_reviewer.get("/api/v1/admin/organizations")).status_code == 403
        assert (
            await returning_reviewer.get(
                f"/api/v1/admin/organizations/{organization_id}/people"
            )
        ).status_code == 403
        assert (await returning_reviewer.get("/api/v1/speaker/portal")).status_code == 404

    delivered_before_revoke = connection.execute(
        "SELECT COUNT(*) FROM communication_messages WHERE recipient_email=?",
        ("reviewer@example.com",),
    ).fetchone()[0]
    connection.execute(
        """UPDATE identity_invitations
           SET status='revoked',accepted_at_ms=NULL,revoked_at_ms=updated_at_ms+1,
               updated_at_ms=updated_at_ms+1
           WHERE id=?""",
        (invitation.json()["id"],),
    )
    connection.commit()
    async with _client(environment) as revoked_reviewer:
        rejected = await revoked_reviewer.post(
            "/api/v1/auth/magic-links",
            json={"email": "reviewer@example.com", "redirect_path": "/reviews"},
        )
        unknown = await revoked_reviewer.post(
            "/api/v1/auth/magic-links",
            json={"email": "unknown@example.com", "redirect_path": "/reviews"},
        )
    assert rejected.status_code == unknown.status_code == 202
    assert rejected.json() == unknown.json()
    assert connection.execute(
        "SELECT COUNT(*) FROM communication_messages WHERE recipient_email=?",
        ("reviewer@example.com",),
    ).fetchone()[0] == delivered_before_revoke


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
            "/auth/verify",
            data={"token": _token(connection, "admin@example.com")},
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

        legacy_cfp = await client.get("/cfp/admin-speaker", follow_redirects=False)
        assert legacy_cfp.status_code == 308
        event_key = event_id.replace("-", "")[:6]
        assert legacy_cfp.headers["location"] == f"/cfp/{event_key}/admin-speaker"
        assert (await client.get(legacy_cfp.headers["location"])).status_code == 200
        cfp_session = (await client.get("/api/v1/auth/session")).json()
        event_access = next(
            access for access in cfp_session["event_access"] if access["event_id"] == event_id
        )
        assert event_access["permissions"] == ["owner"]
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
        assert event_access["permissions"] == ["owner"]
        assert event_access["assignments"] == ["speaker"]
        switched = await client.put(
            "/api/v1/session/active-role",
            headers={"origin": "https://test", "x-csrf-token": cfp_session["csrf_token"]},
            json={"role": "speaker"},
        )
        assert switched.status_code == 200
        assert (await client.get("/api/v1/speaker/portal")).status_code == 200

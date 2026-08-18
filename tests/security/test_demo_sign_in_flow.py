"""End-to-end demo sign-in against the real routes and the real schema.

These exercise the HTTP endpoints rather than helpers: cookie issuance, the
persisted session row, the active-role row, the audit record, replacement of a
prior session, refusal whenever DEMO_LOGIN_ENABLED is not exactly ``true``, and
the fact that a caller cannot choose an identity.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from sessionbuddy.api.app import app
from sessionbuddy.platform.auth.cookies import verify_session_cookie
from sessionbuddy.platform.auth.passwords import hash_password
from sessionbuddy.platform.auth.tokens import hash_token
from tests.schema import MIGRATIONS

ORGANIZER_ID = "10000000-0000-4000-8000-000000000001"
REVIEWER_ID = "10000000-0000-4000-8000-000000000002"
SPEAKER_ID = "10000000-0000-4000-8000-000000000003"
DEMO_PASSWORD = "demo-password-that-is-long"  # noqa: S105 - synthetic fixture credential

PERSONAS = (
    ("organizer", ORGANIZER_ID, "/admin"),
    ("reviewer", REVIEWER_ID, "/reviews"),
    ("speaker", SPEAKER_ID, "/speaker"),
)


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
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def limit(self, options: dict[str, str]) -> dict[str, bool]:
        self.keys.append(options["key"])
        return {"success": True}


class DenyingRateLimiter:
    async def limit(self, options: dict[str, str]) -> dict[str, bool]:
        return {"success": False}


def _seed_identity(connection: sqlite3.Connection, user_id: str, role: str, email: str) -> None:
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,display_name,
            profile_completed_at_ms,created_at_ms,updated_at_ms)
           VALUES(?,?,?,'active',1,?,1,1,1)""",
        (user_id, email, email.casefold(), f"Demo {role.title()}"),
    )
    connection.execute(
        """INSERT INTO user_roles
           (user_id,role,status,created_at_ms,updated_at_ms,is_default)
           VALUES(?,?,'active',1,1,1)""",
        (user_id, role),
    )
    connection.execute(
        """INSERT INTO password_credentials
           (user_id,verifier_phc,pepper_version,status,created_at_ms,updated_at_ms)
           VALUES(?,?,1,'active',1,1)""",
        (user_id, hash_password(DEMO_PASSWORD, b"p" * 32)),
    )
    if role == "organizer":
        organization_id = "20000000-0000-4000-8000-000000000001"
        connection.execute(
            """INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms)
               VALUES(?,'Demo Organization','active',1,1)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,created_at_ms,updated_at_ms)
               VALUES(?,'organization',?,?,'active',1,1)""",
            (organization_id, user_id, user_id),
        )
    connection.commit()


def _environment(connection: sqlite3.Connection, **overrides) -> SimpleNamespace:
    values = {
        "APP_ENV": "local",
        "DEMO_LOGIN_ENABLED": "true",
        "DEMO_ORGANIZER_USER_ID": ORGANIZER_ID,
        "DEMO_REVIEWER_USER_ID": REVIEWER_ID,
        "DEMO_SPEAKER_USER_ID": SPEAKER_ID,
        "DB": SQLiteD1(connection),
        "SESSION_HMAC_KEY": "s" * 32,
        "CSRF_HMAC_KEY": "c" * 32,
        "PASSWORD_PEPPER": "p" * 32,
        "RATE_LIMIT_HMAC_KEY": "r" * 32,
        "AUTH_RATE_LIMITER": AllowingRateLimiter(),
        "DEMO_AUTH_RATE_LIMITER": AllowingRateLimiter(),
        "PUBLIC_BASE_URL": "https://test",
        "ALLOWED_ORIGINS": "https://test",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def demo_environment():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    for role, user_id, _ in PERSONAS:
        _seed_identity(connection, user_id, role, f"demo-{role}@sessionbuddy.demo")
    yield connection, _environment(connection)
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


def _session_cookie(response) -> str:
    # APP_ENV=local uses the non-__Host cookie name.
    return response.cookies.get("sessionbuddy-local") or ""


# --- the happy path, per persona ------------------------------------------


@pytest.mark.parametrize(("role", "user_id", "destination"), PERSONAS)
async def test_demo_sign_in_creates_a_real_session_for_each_persona(
    demo_environment, role: str, user_id: str, destination: str
) -> None:
    connection, environment = demo_environment
    async with _client(environment) as client:
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": role})

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == user_id
    assert body["redirect_path"] == destination
    assert body["csrf_token"]

    cookie = _session_cookie(response)
    assert cookie, "a session cookie must be issued"
    token = verify_session_cookie(cookie, b"s" * 32)
    assert token is not None, "the cookie must carry a validly signed token"

    stored = connection.execute(
        "SELECT id,user_id,revoked_at_ms FROM sessions WHERE token_hash=?",
        (hash_token(token),),
    ).fetchone()
    assert stored is not None, "the session must be persisted"
    assert stored["user_id"] == user_id
    assert stored["revoked_at_ms"] is None

    active = connection.execute(
        "SELECT role FROM session_active_roles WHERE session_id=?", (stored["id"],)
    ).fetchone()
    assert active is not None, "the session must carry an active role"
    assert active["role"] == role


@pytest.mark.parametrize(("role", "user_id", "destination"), PERSONAS)
async def test_the_session_is_immediately_usable(
    demo_environment, role: str, user_id: str, destination: str
) -> None:
    _connection, environment = demo_environment
    async with _client(environment) as client:
        await client.post("/api/v1/auth/demo-sign-in", json={"role": role})
        session = await client.get("/api/v1/auth/session")

    assert session.status_code == 200
    assert session.json()["active_role"] == role
    assert session.json()["user_id"] == user_id


async def test_demo_sign_in_records_an_audit_event(demo_environment) -> None:
    connection, environment = demo_environment
    async with _client(environment) as client:
        await client.post("/api/v1/auth/demo-sign-in", json={"role": "reviewer"})

    record = connection.execute(
        """SELECT actor_user_id,result,metadata_json FROM audit_events
           WHERE action='session.demo_sign_in' ORDER BY occurred_at_ms DESC LIMIT 1"""
    ).fetchone()
    assert record is not None, "demo sign-in must be audited"
    assert record["actor_user_id"] == REVIEWER_ID
    assert record["result"] == "succeeded"
    assert json.loads(record["metadata_json"])["role"] == "reviewer"


async def test_ordinary_password_sign_in_still_works_for_a_demo_account(
    demo_environment,
) -> None:
    """The demo control is an addition, not a replacement."""
    _connection, environment = demo_environment
    async with _client(environment) as client:
        response = await client.post(
            "/api/v1/auth/password/sign-in",
            json={"email": "demo-speaker@sessionbuddy.demo", "password": DEMO_PASSWORD},
        )

    assert response.status_code == 200
    assert response.json()["user_id"] == SPEAKER_ID
    assert response.json()["redirect_path"] == "/speaker"
    assert _session_cookie(response)


# --- replacing an existing session ----------------------------------------


async def test_signing_into_a_demo_persona_revokes_the_previous_session(
    demo_environment,
) -> None:
    connection, environment = demo_environment
    async with _client(environment) as client:
        first = await client.post("/api/v1/auth/demo-sign-in", json={"role": "organizer"})
        first_token = verify_session_cookie(_session_cookie(first), b"s" * 32)
        second = await client.post("/api/v1/auth/demo-sign-in", json={"role": "speaker"})

    assert second.status_code == 200
    previous = connection.execute(
        "SELECT revoked_at_ms,revoke_reason FROM sessions WHERE token_hash=?",
        (hash_token(first_token or ""),),
    ).fetchone()
    assert previous is not None
    assert previous["revoked_at_ms"] is not None, "the replaced session must not stay valid"
    assert previous["revoke_reason"] == "demo_switch"


async def test_switching_personas_does_not_alter_the_previous_account(
    demo_environment,
) -> None:
    connection, environment = demo_environment
    async with _client(environment) as client:
        await client.post("/api/v1/auth/demo-sign-in", json={"role": "organizer"})
        await client.post("/api/v1/auth/demo-sign-in", json={"role": "reviewer"})

    roles = connection.execute(
        "SELECT role,status,is_default FROM user_roles WHERE user_id=?", (ORGANIZER_ID,)
    ).fetchall()
    assert [(row["role"], row["status"], row["is_default"]) for row in roles] == [
        ("organizer", "active", 1)
    ]


# --- refusals --------------------------------------------------------------


@pytest.mark.parametrize("flag", ["false", "", "1", "yes", "off"])
async def test_both_endpoints_are_absent_unless_the_flag_says_true(
    demo_environment, flag: str
) -> None:
    connection, _ = demo_environment
    environment = _environment(connection, DEMO_LOGIN_ENABLED=flag)
    async with _client(environment) as client:
        personas = await client.get("/api/v1/auth/demo-personas")
        sign_in = await client.post("/api/v1/auth/demo-sign-in", json={"role": "organizer"})

    assert personas.status_code == 404
    assert sign_in.status_code == 404
    assert not _session_cookie(sign_in)
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()


async def test_an_absent_flag_leaves_both_endpoints_absent(demo_environment) -> None:
    connection, _ = demo_environment
    environment = _environment(connection)
    del environment.DEMO_LOGIN_ENABLED
    async with _client(environment) as client:
        assert (await client.get("/api/v1/auth/demo-personas")).status_code == 404
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": "organizer"})
    assert response.status_code == 404
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()


@pytest.mark.parametrize("app_env", ["local", "development"])
async def test_supported_app_env_allows_demo_sign_in(demo_environment, app_env: str) -> None:
    """The explicit demo flag works only in supported non-production environments."""
    connection, _ = demo_environment
    environment = _environment(connection, APP_ENV=app_env)
    async with _client(environment) as client:
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": "organizer"})

    assert response.status_code == 200
    issued = response.cookies.get("sessionbuddy-local") or response.cookies.get("__Host-session")
    assert issued, "a session cookie must be issued under the environment's cookie policy"
    assert connection.execute("SELECT 1 FROM sessions").fetchall()


@pytest.mark.parametrize("app_env", ["production", "preview", "staging", "unknown", ""])
async def test_unsupported_app_env_refuses_demo_sign_in(demo_environment, app_env: str) -> None:
    connection, _ = demo_environment
    environment = _environment(connection, APP_ENV=app_env)
    async with _client(environment) as client:
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": "organizer"})

    assert response.status_code == 404
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()


async def test_a_persona_with_no_configured_id_cannot_be_signed_into(
    demo_environment,
) -> None:
    connection, _ = demo_environment
    environment = _environment(connection, DEMO_REVIEWER_USER_ID="")
    async with _client(environment) as client:
        listed = await client.get("/api/v1/auth/demo-personas")
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": "reviewer"})

    assert [entry["role"] for entry in listed.json()["data"]] == ["organizer", "speaker"]
    assert response.status_code == 404
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()


async def test_an_account_that_lost_its_role_cannot_be_signed_into(
    demo_environment,
) -> None:
    """A revoked role must deny sign-in, not create a session without a persona."""
    connection, environment = demo_environment
    connection.execute(
        "UPDATE user_roles SET status='revoked',revoked_at_ms=2 WHERE user_id=?",
        (SPEAKER_ID,),
    )
    connection.commit()
    async with _client(environment) as client:
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": "speaker"})

    assert response.status_code == 404
    assert not _session_cookie(response)
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()


async def test_a_suspended_account_cannot_be_signed_into(demo_environment) -> None:
    connection, environment = demo_environment
    connection.execute("UPDATE users SET status='suspended' WHERE id=?", (REVIEWER_ID,))
    connection.commit()
    async with _client(environment) as client:
        listed = await client.get("/api/v1/auth/demo-personas")
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": "reviewer"})

    assert "reviewer" not in [entry["role"] for entry in listed.json()["data"]]
    assert response.status_code == 404
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()


@pytest.mark.parametrize(
    "payload",
    [
        {"role": "organizer", "email": "someone@example.test"},
        {"role": "organizer", "user_id": SPEAKER_ID},
        {"role": "admin"},
        {"role": "organization_admin"},
        {},
    ],
)
async def test_a_caller_cannot_choose_the_identity_it_signs_in_as(
    demo_environment, payload: dict
) -> None:
    connection, environment = demo_environment
    async with _client(environment) as client:
        response = await client.post("/api/v1/auth/demo-sign-in", json=payload)

    assert response.status_code == 422
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()


async def test_a_cross_origin_request_is_refused(demo_environment) -> None:
    connection, environment = demo_environment
    async with _client(environment, origin="https://evil.example") as client:
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": "organizer"})

    assert response.status_code == 403
    assert not _session_cookie(response)
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()


async def test_an_offsite_redirect_is_refused(demo_environment) -> None:
    _connection, environment = demo_environment
    async with _client(environment) as client:
        response = await client.post(
            "/api/v1/auth/demo-sign-in",
            json={"role": "organizer", "redirect_path": "//evil.example"},
        )
    assert response.status_code == 422


async def test_an_incompatible_redirect_is_corrected_to_the_role_workspace(
    demo_environment,
) -> None:
    _connection, environment = demo_environment
    async with _client(environment) as client:
        response = await client.post(
            "/api/v1/auth/demo-sign-in",
            json={"role": "reviewer", "redirect_path": "/admin/events"},
        )
    assert response.status_code == 200
    assert response.json()["redirect_path"] == "/reviews"


async def test_demo_sign_in_is_rate_limited_on_its_own_binding(demo_environment) -> None:
    connection, _ = demo_environment
    credential_limiter = AllowingRateLimiter()
    environment = _environment(
        connection,
        AUTH_RATE_LIMITER=credential_limiter,
        DEMO_AUTH_RATE_LIMITER=DenyingRateLimiter(),
    )
    async with _client(environment) as client:
        response = await client.post("/api/v1/auth/demo-sign-in", json={"role": "organizer"})

    assert response.status_code == 429
    assert not connection.execute("SELECT 1 FROM sessions").fetchall()
    # Demo traffic must not consume the credential limiter's budget.
    assert credential_limiter.keys == []


async def test_the_persona_list_never_exposes_an_identity(demo_environment) -> None:
    _connection, environment = demo_environment
    async with _client(environment) as client:
        listed = await client.get("/api/v1/auth/demo-personas")

    assert listed.status_code == 200
    body = listed.text
    for user_id in (ORGANIZER_ID, REVIEWER_ID, SPEAKER_ID):
        assert user_id not in body
    assert "sessionbuddy.demo" not in body
    for entry in listed.json()["data"]:
        assert set(entry) == {"role", "label", "description", "destination"}
        assert entry["label"].startswith("Sign in as demo ")

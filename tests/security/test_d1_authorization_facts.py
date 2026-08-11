import sqlite3

from sessionbuddy.platform.auth.d1 import D1AuthorizationFacts
from sessionbuddy.platform.authorization.types import Persona, ResourceGrant, Role


class Statement:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.parameters: tuple[object, ...] = ()

    def bind(self, *values: object) -> "Statement":
        self.parameters = values
        return self

    async def all(self) -> dict[str, object]:
        return {"results": self.rows}


class CountingDatabase:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def prepare(self, query: str) -> Statement:
        self.queries.append(query)
        return Statement(self.rows)


async def test_actor_facts_use_one_database_round_trip_and_ignore_affiliation_role() -> None:
    database = CountingDatabase(
        [
            {
                "fact_type": "principal",
                "user_id": "user-a",
                "user_status": "active",
                "organization_id": None,
                "event_id": None,
                "role": None,
                "active_persona": "organizer",
                "resource_id": None,
                "permission": None,
            },
            {
                "fact_type": "organization",
                "user_id": "user-a",
                "user_status": "active",
                "organization_id": "org-a",
                "event_id": None,
                "role": "member",
            },
            {
                "fact_type": "event",
                "user_id": "user-a",
                "user_status": "active",
                "organization_id": "org-a",
                "event_id": "event-a",
                "role": "speaker",
            },
        ]
    )

    actor = await D1AuthorizationFacts(database).actor_for_session("session-a")

    assert actor is not None
    assert actor.organization_roles == {}
    assert actor.event_roles == {("org-a", "event-a"): frozenset({Role.SPEAKER})}
    assert len(database.queries) == 1


async def test_actor_facts_load_active_persona_ownership_and_exact_grants() -> None:
    database = CountingDatabase(
        [
            {
                "fact_type": "principal",
                "user_id": "user-a",
                "user_status": "active",
                "active_persona": "speaker",
                "organization_id": None,
                "event_id": None,
                "role": None,
                "resource_id": None,
                "permission": None,
            },
            {
                "fact_type": "owner",
                "user_id": "user-a",
                "user_status": "active",
                "active_persona": "speaker",
                "organization_id": None,
                "event_id": None,
                "role": None,
                "resource_id": "event-owned",
                "permission": None,
            },
            {
                "fact_type": "grant",
                "user_id": "user-a",
                "user_status": "active",
                "active_persona": "speaker",
                "organization_id": None,
                "event_id": None,
                "role": None,
                "resource_id": "event-delegated",
                "permission": "manage",
            },
        ]
    )

    actor = await D1AuthorizationFacts(database).actor_for_session("session-a")

    assert actor is not None
    assert actor.active_persona is Persona.SPEAKER
    assert actor.owned_resource_ids == frozenset({"event-owned"})
    assert actor.resource_grants == {
        "event-delegated": frozenset({ResourceGrant.MANAGE})
    }


async def test_actor_facts_do_not_restore_default_role_when_active_role_is_missing() -> None:
    connection = authorization_connection()
    try:
        actor = await D1AuthorizationFacts(SQLiteDatabase(connection)).actor_for_session(
            "session-a"
        )

        assert actor is not None
        assert actor.active_persona is None
    finally:
        connection.close()


class SQLiteStatement:
    def __init__(self, connection: sqlite3.Connection, query: str) -> None:
        self.connection = connection
        self.query = query
        self.parameters: tuple[object, ...] = ()

    def bind(self, *values: object) -> "SQLiteStatement":
        self.parameters = values
        return self

    async def all(self) -> dict[str, object]:
        rows = self.connection.execute(self.query, self.parameters).fetchall()
        return {"results": [dict(row) for row in rows]}


class SQLiteDatabase:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def prepare(self, query: str) -> SQLiteStatement:
        return SQLiteStatement(self.connection, query)


def authorization_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE users (id TEXT PRIMARY KEY, status TEXT NOT NULL);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
        CREATE TABLE session_active_roles (session_id TEXT, role TEXT);
        CREATE TABLE user_roles (
          user_id TEXT, role TEXT, status TEXT, is_default INTEGER
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
        INSERT INTO users VALUES('user-a','active');
        INSERT INTO sessions VALUES('session-a','user-a');
        INSERT INTO user_roles VALUES('user-a','organizer','active',1);
        """
    )
    return connection


async def test_actor_facts_exclude_revoked_grants_and_archived_ownership() -> None:
    connection = authorization_connection()
    try:
        connection.executemany(
            "INSERT INTO owned_resources VALUES(?,?,?)",
            [
                ("owned-active", "user-a", "active"),
                ("owned-archived", "user-a", "archived"),
            ],
        )
        connection.executemany(
            "INSERT INTO resource_access_grants VALUES(?,?,?,?)",
            [
                ("grant-active", "user-a", "edit", "active"),
                ("grant-revoked", "user-a", "manage", "revoked"),
            ],
        )

        actor = await D1AuthorizationFacts(SQLiteDatabase(connection)).actor_for_session(
            "session-a"
        )

        assert actor is not None
        assert actor.owned_resource_ids == frozenset({"owned-active"})
        assert actor.resource_grants == {
            "grant-active": frozenset({ResourceGrant.EDIT})
        }
    finally:
        connection.close()


async def test_actor_facts_do_not_silently_drop_resources_after_500() -> None:
    connection = authorization_connection()
    try:
        connection.executemany(
            "INSERT INTO owned_resources VALUES(?,?, 'active')",
            [(f"owned-{index:03}", "user-a") for index in range(501)],
        )
        connection.executemany(
            "INSERT INTO resource_access_grants VALUES(?,?, 'view','active')",
            [(f"grant-{index:03}", "user-a") for index in range(501)],
        )

        actor = await D1AuthorizationFacts(SQLiteDatabase(connection)).actor_for_session(
            "session-a"
        )

        assert actor is not None
        assert len(actor.owned_resource_ids) == 501
        assert len(actor.resource_grants) == 501
        assert "owned-500" in actor.owned_resource_ids
        assert actor.resource_grants["grant-500"] == frozenset({ResourceGrant.VIEW})
    finally:
        connection.close()

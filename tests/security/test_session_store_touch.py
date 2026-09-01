"""D1SessionStore sliding-idle touch semantics.

Every authenticated request resolves its session. Only a session whose last
touch is older than the touch interval may issue a write; the common path is a
single read so concurrent page loads never contend for a write lock.
"""

import sqlite3

import pytest

from sessionbuddy.platform.auth.d1 import (
    SESSION_IDLE_EXTENSION_MS,
    SESSION_TOUCH_INTERVAL_MS,
    D1SessionStore,
)
from tests.security.test_production_identity_flow import SQLiteD1

NOW_MS = 1_800_000_000_000
TOKEN_HASH = b"token-hash"


class RecordingD1(SQLiteD1):
    """Records prepared SQL so a test can prove which statements ran."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        super().__init__(connection)
        self.prepared: list[str] = []

    def prepare(self, sql: str):
        self.prepared.append(" ".join(sql.split()))
        return super().prepare(sql)

    def writes(self) -> list[str]:
        return [sql for sql in self.prepared if sql.upper().startswith("UPDATE")]


def session_database(
    *,
    last_seen_at_ms: int,
    revoked_at_ms: int | None = None,
    user_status: str = "active",
    idle_expires_at_ms: int = NOW_MS + 60_000,
) -> tuple[sqlite3.Connection, RecordingD1]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE users (
          id TEXT PRIMARY KEY, status TEXT NOT NULL, authorization_version INTEGER NOT NULL
        );
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY,
          user_id TEXT NOT NULL,
          token_hash BLOB NOT NULL UNIQUE,
          authorization_version INTEGER NOT NULL,
          last_seen_at_ms INTEGER NOT NULL,
          idle_expires_at_ms INTEGER NOT NULL,
          absolute_expires_at_ms INTEGER NOT NULL,
          revoked_at_ms INTEGER
        );
        """
    )
    connection.execute(
        "INSERT INTO users (id,status,authorization_version) VALUES ('user-1',?,3)",
        (user_status,),
    )
    connection.execute(
        """INSERT INTO sessions
           (id,user_id,token_hash,authorization_version,last_seen_at_ms,
            idle_expires_at_ms,absolute_expires_at_ms,revoked_at_ms)
           VALUES ('session-1','user-1',?,3,?,?,?,?)""",
        (TOKEN_HASH, last_seen_at_ms, idle_expires_at_ms, NOW_MS + 86_400_000, revoked_at_ms),
    )
    connection.commit()
    return connection, RecordingD1(connection)


def session_row(connection: sqlite3.Connection) -> sqlite3.Row:
    return connection.execute(
        "SELECT last_seen_at_ms,idle_expires_at_ms FROM sessions WHERE id='session-1'"
    ).fetchone()


@pytest.mark.asyncio
async def test_recently_seen_session_resolves_with_a_single_read() -> None:
    connection, db = session_database(last_seen_at_ms=NOW_MS - SESSION_TOUCH_INTERVAL_MS + 1)

    record = await D1SessionStore(db).resolve(TOKEN_HASH, NOW_MS)

    assert record is not None and record.id == "session-1" and record.user_id == "user-1"
    assert db.writes() == [], "a fresh session must not take a write lock"
    assert len(db.prepared) == 1
    row = session_row(connection)
    assert row["last_seen_at_ms"] == NOW_MS - SESSION_TOUCH_INTERVAL_MS + 1
    assert row["idle_expires_at_ms"] == NOW_MS + 60_000


@pytest.mark.asyncio
async def test_stale_active_session_is_touched_and_idle_window_extended() -> None:
    connection, db = session_database(last_seen_at_ms=NOW_MS - SESSION_TOUCH_INTERVAL_MS)

    record = await D1SessionStore(db).resolve(TOKEN_HASH, NOW_MS)

    assert record is not None and record.id == "session-1"
    assert len(db.writes()) == 1
    row = session_row(connection)
    assert row["last_seen_at_ms"] == NOW_MS
    assert row["idle_expires_at_ms"] == NOW_MS + SESSION_IDLE_EXTENSION_MS


@pytest.mark.asyncio
async def test_touch_never_extends_a_session_that_is_not_active() -> None:
    for kwargs in (
        {"revoked_at_ms": NOW_MS - 1},
        {"user_status": "suspended"},
        {"idle_expires_at_ms": NOW_MS - 1},
    ):
        connection, db = session_database(
            last_seen_at_ms=NOW_MS - 2 * SESSION_TOUCH_INTERVAL_MS, **kwargs
        )

        record = await D1SessionStore(db).resolve(TOKEN_HASH, NOW_MS)

        assert record is not None, kwargs
        assert db.writes() == [], kwargs
        assert session_row(connection)["last_seen_at_ms"] == NOW_MS - 2 * SESSION_TOUCH_INTERVAL_MS


@pytest.mark.asyncio
async def test_unknown_token_resolves_to_none_without_writes() -> None:
    _, db = session_database(last_seen_at_ms=NOW_MS - 2 * SESSION_TOUCH_INTERVAL_MS)

    assert await D1SessionStore(db).resolve(b"other", NOW_MS) is None
    assert db.writes() == []

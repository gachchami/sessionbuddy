import sqlite3
from pathlib import Path

import pytest

MIGRATIONS = sorted((Path(__file__).parents[2] / "migrations").glob("*.sql"))


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    return connection


def test_identity_invitation_schema_supports_guarded_provisioning(db: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in db.execute("PRAGMA table_info(authentication_challenges)").fetchall()
    }
    assert {"user_id", "organization_id", "event_id", "invitation_id"} <= columns
    invitation_columns = {
        row[1] for row in db.execute("PRAGMA table_info(identity_invitations)").fetchall()
    }
    assert {"normalized_email", "role", "status", "expires_at_ms"} <= invitation_columns

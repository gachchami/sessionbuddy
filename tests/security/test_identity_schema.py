import sqlite3
from pathlib import Path

import pytest

from scripts.setup_key import READ_KEY_SQL, REGENERATE_KEY_SQL

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


def test_setup_migration_backfills_existing_installations() -> None:
    connection = sqlite3.connect(":memory:")
    setup_migration = next(
        migration
        for migration in MIGRATIONS
        if migration.name == "0021_one_time_instance_setup.sql"
    )
    lock_migration = next(
        migration
        for migration in MIGRATIONS
        if migration.name == "0022_lock_completed_instance_setup.sql"
    )
    for migration in MIGRATIONS:
        if migration == setup_migration:
            break
        connection.executescript(migration.read_text(encoding="utf-8"))
    connection.execute(
        """INSERT INTO organizations (id,name,status,created_at_ms,updated_at_ms)
           VALUES ('existing','Existing Events','active',100,100)"""
    )

    connection.executescript(setup_migration.read_text(encoding="utf-8"))
    connection.executescript(lock_migration.read_text(encoding="utf-8"))

    assert connection.execute("SELECT COUNT(*) FROM instance_setup_credentials").fetchone()[0] == 0
    assert connection.execute(
        "SELECT singleton_key,completed_at_ms FROM instance_setup"
    ).fetchone() == ("primary", 100)
    connection.close()


def test_migration_key_can_rotate_only_before_setup() -> None:
    connection = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))

    initial = connection.execute(READ_KEY_SQL).fetchone()[0]
    replacement = connection.execute(REGENERATE_KEY_SQL).fetchone()[0]
    assert replacement != initial
    assert len(replacement) == 64

    connection.execute(
        """INSERT INTO instance_setup (singleton_key,completed_at_ms)
           VALUES ('primary',100)"""
    )
    assert connection.execute(READ_KEY_SQL).fetchone() is None
    assert connection.execute(REGENERATE_KEY_SQL).fetchone() is None
    with pytest.raises(sqlite3.IntegrityError, match="completion is permanent"):
        connection.execute("DELETE FROM instance_setup WHERE singleton_key='primary'")
    with pytest.raises(sqlite3.IntegrityError, match="already completed"):
        connection.execute(
            """INSERT INTO instance_setup_credentials
               (singleton_key,deployment_key,generated_at_ms)
               VALUES ('primary',?,200)""",
            ("a" * 64,),
        )
    connection.close()

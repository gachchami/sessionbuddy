import sqlite3

import pytest

from tests.schema import MIGRATIONS


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    for user_id in ("creator", "delegate"):
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES(?,?,?,'active',1,1)""",
            (user_id, f"{user_id}@example.test", f"{user_id}@example.test"),
        )
    yield connection
    connection.close()


def test_creator_is_immutable_and_owner_does_not_need_a_grant(db: sqlite3.Connection) -> None:
    db.execute(
        "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
        "VALUES('organization','Organization','active',1,1)"
    )
    db.execute(
        """INSERT INTO owned_resources
           (id,resource_type,created_by_user_id,owner_user_id,status,created_at_ms,updated_at_ms)
           VALUES('organization','organization','creator','creator','active',1,1)"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="resource creator is immutable"):
        db.execute(
            "UPDATE owned_resources SET created_by_user_id='delegate' WHERE id='organization'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="owner does not need"):
        db.execute(
            """INSERT INTO resource_access_grants
               (id,resource_id,user_id,permission,status,granted_by_user_id,
                created_at_ms,updated_at_ms)
               VALUES('redundant','organization','creator','manage','active','creator',1,1)"""
        )


def test_delegation_is_exact_to_one_organization(db: sqlite3.Connection) -> None:
    for resource_id in ("organization-a", "organization-b"):
        db.execute(
            "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
            "VALUES(?,?, 'active',1,1)",
            (resource_id, resource_id),
        )
        db.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,
                created_at_ms,updated_at_ms)
               VALUES(?,'organization','creator','creator','active',1,1)""",
            (resource_id,),
        )
    db.execute(
        """INSERT INTO resource_access_grants
           (id,resource_id,user_id,permission,status,granted_by_user_id,
            created_at_ms,updated_at_ms)
           VALUES('grant','organization-a','delegate','manage','active','creator',1,1)"""
    )

    accessible = db.execute(
        """SELECT resource_id FROM resource_access_grants
           WHERE user_id='delegate' AND status='active'"""
    ).fetchall()
    assert accessible == [("organization-a",)]

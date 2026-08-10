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


def test_password_authentication_is_optional_and_separated_from_users(
    db: sqlite3.Connection,
) -> None:
    user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)").fetchall()}
    assert not ({"password", "password_hash", "verifier_phc"} & user_columns)
    credential_columns = {
        row[1] for row in db.execute("PRAGMA table_info(password_credentials)").fetchall()
    }
    assert {
        "user_id",
        "verifier_phc",
        "pepper_version",
        "status",
        "last_verified_at_ms",
    } <= credential_columns
    assert db.execute("SELECT COUNT(*) FROM password_credentials").fetchone()[0] == 0


def test_password_schema_rejects_unsafe_verifiers_and_inconsistent_throttle_state(
    db: sqlite3.Connection,
) -> None:
    db.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES('user','User@example.test','user@example.test','active',1,1,1)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO password_credentials
               (user_id,verifier_phc,pepper_version,created_at_ms,updated_at_ms)
               VALUES('user','plain-text-password',1,1,1)"""
        )
    db.execute(
        """INSERT INTO password_credentials
           (user_id,verifier_phc,pepper_version,created_at_ms,updated_at_ms)
           VALUES('user',?,1,1,1)""",
        ("$argon2id$v=19$m=19456,t=2,p=1$" + "a" * 22 + "$" + "b" * 43,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO password_authentication_state
               (user_id,consecutive_failures,updated_at_ms) VALUES('user',1,2)"""
        )


def test_password_recovery_requires_a_verified_matching_active_identity(
    db: sqlite3.Connection,
) -> None:
    db.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES('verified','v@example.test','v@example.test','active',1,1,1),
                 ('unverified','u@example.test','u@example.test','active',NULL,1,1)"""
    )
    db.execute(
        """INSERT INTO password_recovery_challenges
           (id,user_id,normalized_email,token_hash,purpose,expires_at_ms,created_at_ms)
           VALUES('ok','verified','v@example.test',?,'password_setup',100,1)""",
        (bytes(32),),
    )
    with pytest.raises(sqlite3.IntegrityError, match="identity mismatch"):
        db.execute(
            """INSERT INTO password_recovery_challenges
               (id,user_id,normalized_email,token_hash,purpose,expires_at_ms,created_at_ms)
               VALUES('bad','unverified','u@example.test',?,'password_reset',100,1)""",
            (bytes([1]) * 32,),
        )


def test_password_rotation_with_live_sessions_requires_session_invalidation(
    db: sqlite3.Connection,
) -> None:
    db.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES('user','u@example.test','u@example.test','active',1,1,1)"""
    )
    original = "$argon2id$v=19$m=19456,t=2,p=1$" + "a" * 22 + "$" + "b" * 43
    rotated = "$argon2id$v=19$m=19456,t=2,p=1$" + "c" * 22 + "$" + "d" * 43
    db.execute(
        """INSERT INTO password_credentials
           (user_id,verifier_phc,pepper_version,created_at_ms,updated_at_ms)
           VALUES('user',?,1,1,1)""",
        (original,),
    )
    db.execute(
        """INSERT INTO sessions
           (id,user_id,token_hash,csrf_secret_hash,authorization_version,created_at_ms,
            last_seen_at_ms,idle_expires_at_ms,absolute_expires_at_ms)
           VALUES('session','user',?, ?,1,1,1,100,200)""",
        (bytes([1]) * 32, bytes([2]) * 32),
    )
    with pytest.raises(sqlite3.IntegrityError, match="authorization bump"):
        db.execute(
            """UPDATE password_credentials
               SET verifier_phc=?,updated_at_ms=2 WHERE user_id='user'""",
            (rotated,),
        )
    db.execute("UPDATE users SET authorization_version=2 WHERE id='user'")
    db.execute(
        """UPDATE password_credentials
           SET verifier_phc=?,updated_at_ms=2 WHERE user_id='user'""",
        (rotated,),
    )


def test_account_role_does_not_require_a_resource_membership(db: sqlite3.Connection) -> None:
    db.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES('organizer','organizer@example.test','organizer@example.test','active',1,1,1)"""
    )
    db.execute(
        """INSERT INTO user_roles(user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('organizer','organizer','active',1,1)"""
    )
    assert db.execute(
        "SELECT role FROM user_roles WHERE user_id='organizer'"
    ).fetchone() == ("organizer",)
    assert db.execute(
        "SELECT COUNT(*) FROM organization_memberships WHERE user_id='organizer'"
    ).fetchone() == (0,)


def test_session_accepts_exactly_one_active_assigned_role(db: sqlite3.Connection) -> None:
    db.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES('multi','multi@example.test','multi@example.test','active',1,1,1)"""
    )
    db.executemany(
        """INSERT INTO user_roles(user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('multi',?,'active',1,1)""",
        [("organizer",), ("speaker",)],
    )
    db.execute(
        """INSERT INTO sessions
           (id,user_id,token_hash,csrf_secret_hash,authorization_version,created_at_ms,
            last_seen_at_ms,idle_expires_at_ms,absolute_expires_at_ms)
           VALUES('session','multi',?, ?,1,1,1,100,200)""",
        (bytes([3]) * 32, bytes([4]) * 32),
    )
    db.execute(
        """INSERT INTO session_active_roles(session_id,user_id,role,selected_at_ms)
           VALUES('session','multi','organizer',1)"""
    )
    db.execute(
        """UPDATE session_active_roles SET role='speaker',selected_at_ms=2
           WHERE session_id='session'"""
    )
    assert db.execute(
        "SELECT role FROM session_active_roles WHERE session_id='session'"
    ).fetchone() == ("speaker",)
    with pytest.raises(sqlite3.IntegrityError, match="not available"):
        db.execute(
            "UPDATE session_active_roles SET role='reviewer' WHERE session_id='session'"
        )


def test_revoking_a_role_removes_it_from_live_session_context(db: sqlite3.Connection) -> None:
    db.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES('user','user@example.test','user@example.test','active',1,1,1)"""
    )
    db.execute(
        """INSERT INTO user_roles(user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('user','organizer','active',1,1)"""
    )
    db.execute(
        """INSERT INTO sessions
           (id,user_id,token_hash,csrf_secret_hash,authorization_version,created_at_ms,
            last_seen_at_ms,idle_expires_at_ms,absolute_expires_at_ms)
           VALUES('session','user',?, ?,1,1,1,100,200)""",
        (bytes([5]) * 32, bytes([6]) * 32),
    )
    db.execute(
        """INSERT INTO session_active_roles(session_id,user_id,role,selected_at_ms)
           VALUES('session','user','organizer',1)"""
    )
    db.execute(
        """UPDATE user_roles SET status='revoked',revoked_at_ms=2,updated_at_ms=2
           WHERE user_id='user' AND role='organizer'"""
    )
    assert db.execute("SELECT COUNT(*) FROM session_active_roles").fetchone() == (0,)


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

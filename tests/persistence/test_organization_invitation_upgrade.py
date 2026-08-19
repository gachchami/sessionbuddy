"""Fresh/retained upgrade semantics; runner no-op is checked by the release gate."""

import sqlite3

from tests.persistence.test_baseline_migration import execute_migration_in_transaction
from tests.schema import MIGRATIONS
from tests.speaker_operations.test_speaker_onboarding_schema import seed_platform


def schema(db):
    return db.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()


def test_org_invitation_upgrade_preserves_existing_tenants_and_matches_fresh_schema():
    with sqlite3.connect(":memory:") as upgraded, sqlite3.connect(":memory:") as fresh:
        for db in (upgraded, fresh):
            db.execute("PRAGMA foreign_keys=ON")
        for migration in MIGRATIONS[:-1]:
            upgraded.executescript(migration.read_text())
        seed_platform(upgraded)
        upgraded.commit()
        tables = ("users", "organizations", "events", "organization_memberships", "owned_resources")
        before = {
            table: upgraded.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()  # noqa: S608
            for table in tables
        }
        assert MIGRATIONS[-1].name == "0006_organization_admin_invitations.sql"
        execute_migration_in_transaction(upgraded, MIGRATIONS[-1])
        for table, rows in before.items():
            assert (
                upgraded.execute(
                    f"SELECT * FROM {table} ORDER BY id"  # noqa: S608
                ).fetchall()
                == rows
            )
        for migration in MIGRATIONS:
            fresh.executescript(migration.read_text())
        assert schema(upgraded) == schema(fresh)
        assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
        assert fresh.execute("PRAGMA foreign_key_check").fetchall() == []
        assert upgraded.execute(
            "SELECT COUNT(*) FROM organization_admin_invitations"
        ).fetchone() == (0,)

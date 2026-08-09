import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request

from sessionbuddy.competition import router
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Permission, Role
from tests.speaker_operations.test_asset_boundary import AsyncSqlite

MIGRATIONS = sorted((Path(__file__).parents[2] / "migrations").glob("*.sql"))


def request_for(database: AsyncSqlite) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/organizations/org/speakers",
            "headers": [],
            "env": SimpleNamespace(DB=database),
        }
    )


@pytest.fixture
def directory_database() -> tuple[sqlite3.Connection, AsyncSqlite]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
        "VALUES('org','Organization','active',1,1)"
    )
    connection.execute(
        """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES('speaker-user','speaker@example.test','speaker@example.test','active',1,1)"""
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('membership','org','speaker-user','member','active',1,1)"""
    )
    connection.execute(
        """INSERT INTO people
           (id,organization_id,user_id,display_name,company,links_json,created_at_ms,updated_at_ms)
           VALUES('person','org','speaker-user','Speaker','Company','[\"https://example.test\"]',1,1)"""
    )
    for suffix, name, starts, selection in (
        ("a", "Event A", 100, "submitted"),
        ("b", "Event B", 200, "accepted"),
    ):
        connection.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms)
               VALUES(?, 'org', ?, ?, ?, 'UTC','Online','virtual','Description',
                      'active',1,1)""",
            (f"event-{suffix}", name, starts, starts + 10),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,accepted_at_ms,
                last_activity_at_ms,created_at_ms,updated_at_ms,selection_status)
               VALUES(?, 'org', ?, 'person','onboarding',1,1,1,1,?)""",
            (f"event-speaker-{suffix}", f"event-{suffix}", selection),
        )
    yield connection, AsyncSqlite(connection)
    connection.close()


@pytest.fixture
def allow_organization_admin(monkeypatch):
    async def allowed(request, permission, context, **kwargs):
        assert permission is Permission.ORGANIZATION_MANAGE
        assert context.organization_id == "org"
        return AuthenticatedContext(
            Actor("admin", organization_roles={"org": frozenset({Role.ORGANIZATION_ADMIN})}),
            "session",
        )

    monkeypatch.setattr(router, "require_permission", allowed)


async def test_organization_directory_deduplicates_people_and_nests_events(
    directory_database, allow_organization_admin
) -> None:
    _connection, database = directory_database

    result = await router.list_organization_speakers("org", request_for(database))

    assert result.organization_id == "org"
    assert len(result.data) == 1
    speaker = result.data[0]
    assert speaker.person_id == "person"
    assert speaker.email == "speaker@example.test"
    assert speaker.links == ["https://example.test"]
    assert [item.event_id for item in speaker.participations] == ["event-b", "event-a"]
    assert [item.event_speaker_id for item in speaker.participations] == [
        "event-speaker-b",
        "event-speaker-a",
    ]
    assert [item.selection_status for item in speaker.participations] == [
        "accepted",
        "submitted",
    ]
    assert all(item.proposal_title == "No proposal" for item in speaker.participations)

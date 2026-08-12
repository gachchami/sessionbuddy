import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from sessionbuddy.competition import router
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Permission, Persona, Role
from tests.schema import MIGRATIONS
from tests.speaker_operations.test_asset_boundary import AsyncSqlite


def request_for(database: AsyncSqlite) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/organizations/org/people",
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
    for user_id in ("admin", "other-admin"):
        connection.execute(
            """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES(?,?,?,'active',1,1)""",
            (user_id, f"{user_id}@example.test", f"{user_id}@example.test"),
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
    for resource_id, resource_type in (
        ("org", "organization"),
        ("event-a", "event"),
        ("event-b", "event"),
    ):
        connection.execute(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,created_at_ms,updated_at_ms)
               VALUES(?,?, 'admin','admin','active',1,1)""",
            (resource_id, resource_type),
        )
    yield connection, AsyncSqlite(connection)
    connection.close()


@pytest.fixture
def allow_organization_admin(monkeypatch):
    async def allowed(request, permission, context, **kwargs):
        assert permission is Permission.ORGANIZATION_MANAGE
        assert context.organization_id == "org"
        return AuthenticatedContext(
            Actor(
                "admin",
                active_persona=Persona.ORGANIZER,
                owned_resource_ids=frozenset({"org", "event-a", "event-b"}),
                organization_roles={"org": frozenset({Role.ORGANIZATION_ADMIN})},
            ),
            "session",
        )

    monkeypatch.setattr(router, "require_permission", allowed)


async def test_organization_directory_deduplicates_people_and_nests_events(
    directory_database, allow_organization_admin
) -> None:
    _connection, database = directory_database

    result = await router.list_organization_speakers("org", request_for(database))

    assert result.organization_id == "org"
    assert len(result.data) == 2
    speaker = next(item for item in result.data if item.person_id == "person")
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
    owner = next(item for item in result.data if item.user_id == "admin")
    assert owner.organization_roles == ["Organizer"]


async def test_organization_directory_http_filters_people_and_participations_to_exact_events(
    directory_database, allow_organization_admin
) -> None:
    connection, database = directory_database
    connection.execute(
        """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES('private-user','private-person@example.test','private-person@example.test',
                  'active',1,1)"""
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('private-membership','org','private-user','member','active',1,1)"""
    )
    connection.execute(
        """INSERT INTO people
           (id,organization_id,user_id,display_name,biography,links_json,created_at_ms,updated_at_ms)
           VALUES('private-person','org','private-user','Private Event B Speaker',
                  'Event B private biography','["https://private.example.test"]',1,1)"""
    )
    connection.execute(
        """INSERT INTO event_speakers
           (id,organization_id,event_id,person_id,status,accepted_at_ms,last_activity_at_ms,
            created_at_ms,updated_at_ms,selection_status)
           VALUES('private-event-speaker','org','event-b','private-person','onboarding',
                  1,1,1,1,'accepted')"""
    )
    connection.execute(
        "UPDATE owned_resources SET owner_user_id='other-admin' WHERE resource_type='event'"
    )
    connection.commit()

    application = FastAPI()
    application.include_router(router.competition_router)

    async def inject_environment(scope, receive, send):
        scope["env"] = SimpleNamespace(DB=database)
        await application(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="https://test"
    ) as client:
        organization_only = await client.get("/api/v1/admin/organizations/org/people")
        assert organization_only.status_code == 200
        assert [item["email"] for item in organization_only.json()["data"]] == [
            "admin@example.test"
        ]

        connection.execute(
            "UPDATE owned_resources SET owner_user_id='admin' WHERE id='event-a'"
        )
        connection.commit()
        event_a_only = await client.get("/api/v1/admin/organizations/org/people")

    assert event_a_only.status_code == 200
    payload = event_a_only.json()
    speaker = next(item for item in payload["data"] if item["person_id"] == "person")
    assert [item["event_id"] for item in speaker["participations"]] == [
        "event-a"
    ]
    assert "event-b" not in event_a_only.text
    assert "private-person@example.test" not in event_a_only.text
    assert "Event B private biography" not in event_a_only.text


async def test_people_adds_only_exact_org_invited_reviewers(
    directory_database, allow_organization_admin
) -> None:
    connection, database = directory_database
    connection.execute(
        """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES('reviewer','reviewer@example.test','reviewer@example.test','active',1,1),
                 ('global','global@example.test','global@example.test','active',1,1)"""
    )
    connection.execute(
        """INSERT INTO user_roles
           (user_id,role,status,is_default,created_at_ms,updated_at_ms)
           VALUES('global','reviewer','active',1,1,1)"""
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('admin-membership','org','admin','organization_admin','active',1,1)"""
    )
    connection.execute(
        """INSERT INTO identity_invitations
           (id,organization_id,event_id,normalized_email,email,role,status,
            invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms)
           VALUES('review-invite','org','event-a','reviewer@example.test',
                  'reviewer@example.test','evaluator','pending','admin',999999,1,1)"""
    )
    connection.commit()

    result = await router.list_organization_speakers("org", request_for(database))
    by_email = {item.email: item for item in result.data}
    assert by_email["reviewer@example.test"].organization_roles == ["Reviewer"]
    assert by_email["reviewer@example.test"].event_associations[0].event_id == "event-a"
    assert "global@example.test" not in by_email


async def test_people_includes_pending_speaker_invitations(
    directory_database, allow_organization_admin
) -> None:
    connection, database = directory_database
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('pending-admin-membership','org','admin','organization_admin',
                  'active',1,1)"""
    )
    connection.execute(
        """INSERT INTO identity_invitations
           (id,organization_id,event_id,normalized_email,email,role,status,
            invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms,display_name)
           VALUES('speaker-invite','org','event-a','pending@example.test',
                  'pending@example.test','speaker','pending','admin',999999,1,1,
                  'Pending Speaker')"""
    )
    connection.commit()

    result = await router.list_organization_speakers("org", request_for(database))

    pending = next(item for item in result.data if item.email == "pending@example.test")
    assert pending.display_name == "Pending Speaker"
    assert pending.organization_roles == ["Speaker"]
    assert pending.event_associations[0].status == "pending"

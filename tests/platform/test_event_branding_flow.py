import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from sessionbuddy.platform.auth import access
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Permission, Role
from sessionbuddy.platform.db.types import utc_now_ms
from tests.speaker_operations.test_asset_boundary import AsyncSqlite

MIGRATIONS = sorted((Path(__file__).parents[2] / "migrations").glob("*.sql"))
PNG = b"\x89PNG\r\n\x1a\n" + b"event-branding"


@dataclass
class Stored:
    body: bytes


class Bucket:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, key: str, body: bytes) -> None:
        self.objects[key] = body

    async def get(self, key: str):
        body = self.objects.get(key)
        return None if body is None else Stored(body)


@pytest.fixture
def branding_database() -> tuple[sqlite3.Connection, AsyncSqlite]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    for suffix in ("a", "b"):
        connection.execute(
            """INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms)
               VALUES(?,?, 'active',1,1)""",
            (f"org-{suffix}", f"Organization {suffix.upper()}"),
        )
        connection.execute(
            """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES(?,?,?,'active',1,1)""",
            (f"user-{suffix}", f"user-{suffix}@example.test", f"user-{suffix}@example.test"),
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?,?,?,'organization_admin','active',1,1)""",
            (f"member-{suffix}", f"org-{suffix}", f"user-{suffix}"),
        )
    yield connection, AsyncSqlite(connection)
    connection.close()


def branding_request(database: AsyncSqlite, bucket: Bucket, body: bytes = PNG) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/organizations/org-a/event-assets/logo",
            "headers": [
                (b"content-type", b"image/png"),
                (b"content-length", str(len(body)).encode()),
            ],
            "env": SimpleNamespace(
                DB=database,
                ASSETS=bucket,
                APP_ENV="development",
                MALWARE_SCAN_MODE="disabled",
            ),
        },
        receive,
    )
    request.state.request_id = "branding-request"
    return request


@pytest.fixture
def allow_organization_admin(monkeypatch):
    async def allowed(request, permission, context, **kwargs):
        assert permission in {Permission.ORGANIZATION_MANAGE, Permission.EVENT_MANAGE}
        assert context.organization_id == "org-a"
        return AuthenticatedContext(
            Actor(
                "user-a",
                organization_roles={"org-a": frozenset({Role.ORGANIZATION_ADMIN})},
            ),
            "session-a",
        )

    monkeypatch.setattr(access, "require_permission", allowed)


async def test_staged_logo_is_scoped_stored_and_attached_atomically(
    branding_database, allow_organization_admin
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    request = branding_request(database, bucket)

    uploaded = await access.upload_organization_event_asset("org-a", "logo", request)
    assert uploaded.kind == "logo"
    assert uploaded.asset_url.startswith("/api/v1/public/event-assets/")
    staged = connection.execute(
        """SELECT organization_id,event_id,kind,object_key,status
           FROM event_branding_assets WHERE asset_url=?""",
        (uploaded.asset_url,),
    ).fetchone()
    assert tuple(staged) == (
        "org-a",
        None,
        "logo",
        next(iter(bucket.objects)),
        "pending",
    )
    assert staged["object_key"].startswith("public/event-branding/org-a/")
    assert bucket.objects[staged["object_key"]] == PNG

    now = utc_now_ms()
    created = await access.create_event(
        "org-a",
        access.EventCreate(
            name="Future Event",
            starts_at_ms=now + 86_400_000,
            ends_at_ms=now + 172_800_000,
            time_zone="UTC",
            location="Online",
            delivery_mode="virtual",
            description="A future event.",
            logo_url=uploaded.asset_url,
        ),
        branding_request(database, bucket, b""),
    )
    attached = connection.execute(
        """SELECT event_id,status,attached_at_ms FROM event_branding_assets
           WHERE asset_url=?""",
        (uploaded.asset_url,),
    ).fetchone()
    assert attached["event_id"] == created.id
    assert attached["status"] == "attached"
    assert attached["attached_at_ms"] is not None
    assert connection.execute(
        "SELECT logo_url FROM events WHERE id=?", (created.id,)
    ).fetchone()[0] == uploaded.asset_url
    await access._require_event_branding_reference(
        database,
        organization_id="org-a",
        kind="logo",
        asset_url=uploaded.asset_url,
        event_id=created.id,
    )
    with pytest.raises(HTTPException) as already_attached_elsewhere:
        await access._require_event_branding_reference(
            database,
            organization_id="org-a",
            kind="logo",
            asset_url=uploaded.asset_url,
            event_id="another-event",
        )
    assert already_attached_elsewhere.value.status_code == 422


async def test_branding_reference_cannot_cross_tenant_or_kind(
    branding_database, allow_organization_admin
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    uploaded = await access.upload_organization_event_asset(
        "org-a", "logo", branding_request(database, bucket)
    )

    with pytest.raises(HTTPException) as wrong_tenant:
        await access._require_event_branding_reference(
            database,
            organization_id="org-b",
            kind="logo",
            asset_url=uploaded.asset_url,
        )
    assert wrong_tenant.value.status_code == 422
    with pytest.raises(HTTPException) as wrong_kind:
        await access._require_event_branding_reference(
            database,
            organization_id="org-a",
            kind="cover",
            asset_url=uploaded.asset_url,
        )
    assert wrong_kind.value.status_code == 422

    with pytest.raises(sqlite3.IntegrityError, match="invalid event logo asset"):
        connection.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,logo_url,status,created_at_ms,updated_at_ms)
               VALUES('foreign-event','org-b','Foreign',10,20,'UTC','Online','virtual',
                      'Description',?,'active',1,1)""",
            (uploaded.asset_url,),
        )


async def test_public_branding_url_streams_only_a_registered_object(
    branding_database, allow_organization_admin
) -> None:
    _connection, database = branding_database
    bucket = Bucket()
    request = branding_request(database, bucket)
    uploaded = await access.upload_organization_event_asset("org-a", "cover", request)
    asset_name = uploaded.asset_url.rsplit("/", 1)[1]

    response = await access.public_event_branding_asset(
        asset_name, branding_request(database, bucket, b"")
    )
    chunks = [chunk async for chunk in response.body_iterator]
    assert b"".join(chunks) == PNG
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"

    with pytest.raises(HTTPException) as missing:
        await access.public_event_branding_asset(
            "not-registered.png", branding_request(database, bucket, b"")
        )
    assert missing.value.status_code == 404

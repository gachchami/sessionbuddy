import json
import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response

from sessionbuddy.cfp.models import DEFAULT_FORM_FIELDS
from sessionbuddy.cfp.router import get_form
from sessionbuddy.competition.router import public_speakers
from sessionbuddy.platform.auth import access
from sessionbuddy.platform.auth.branding_purge import (
    PENDING_BRANDING_RETENTION_MS,
    purge_pending_branding_assets,
)
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Permission, Persona
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.scheduling.router import get_public_schedule
from tests.schema import MIGRATIONS
from tests.speaker_operations.test_asset_boundary import AsyncSqlite

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

    async def delete(self, key: str) -> None:
        del self.objects[key]


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
                APP_ENV="local",
                MALWARE_SCAN_MODE="disabled",
            ),
        },
        receive,
    )
    request.state.request_id = "branding-request"
    return request


@pytest.fixture
def allow_organization_admin(monkeypatch):
    authenticated_context = AuthenticatedContext(
        Actor(
            "user-a",
            active_persona=Persona.ORGANIZER,
            owned_resource_ids=frozenset({"org-a"}),
        ),
        "session-a",
    )

    async def allowed(request, permission, context, **kwargs):
        assert permission in {Permission.ORGANIZATION_MANAGE, Permission.EVENT_MANAGE}
        assert context.organization_id == "org-a"
        return authenticated_context

    async def authenticated(request):
        return authenticated_context

    monkeypatch.setattr(access, "require_permission", allowed)
    monkeypatch.setattr(access, "authenticate_request", authenticated)


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
                delivery_mode,description,logo_url,status,created_at_ms,updated_at_ms,
                created_by_user_id)
               VALUES('foreign-event','org-b','Foreign',10,20,'UTC','Online','virtual',
                      'Description',?,'active',1,1,
                      (SELECT id FROM users ORDER BY id LIMIT 1))""",
            (uploaded.asset_url,),
        )


async def test_expired_pending_branding_reference_requires_a_new_upload(
    branding_database, allow_organization_admin
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    uploaded = await access.upload_organization_event_asset(
        "org-a", "logo", branding_request(database, bucket)
    )
    connection.execute(
        "UPDATE event_branding_assets SET created_at_ms=? WHERE asset_url=?",
        (utc_now_ms() - PENDING_BRANDING_RETENTION_MS - 1, uploaded.asset_url),
    )

    with pytest.raises(HTTPException) as expired:
        await access._require_event_branding_reference(
            database,
            organization_id="org-a",
            kind="logo",
            asset_url=uploaded.asset_url,
        )

    assert expired.value.status_code == 422
    assert expired.value.detail == "invalid event logo asset"


async def test_public_branding_url_streams_only_a_registered_object(
    branding_database, allow_organization_admin
) -> None:
    connection, database = branding_database
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
    assert response.headers["cache-control"] == "private, no-store"

    connection.execute(
        """INSERT INTO events
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
            delivery_mode,description,cover_image_url,status,created_at_ms,updated_at_ms,
            created_by_user_id)
           VALUES('cache-event','org-a','Cache event',10,20,'UTC','Online','virtual',
                  'Cache behavior',?,'active',2,2,
                  (SELECT id FROM users ORDER BY id LIMIT 1))""",
        (uploaded.asset_url,),
    )
    attached_response = await access.public_event_branding_asset(
        asset_name, branding_request(database, bucket, b"")
    )
    assert (
        attached_response.headers["cache-control"]
        == "public, max-age=31536000, immutable"
    )
    connection.execute("UPDATE events SET cover_image_url=NULL WHERE id='cache-event'")
    retired_response = await access.public_event_branding_asset(
        asset_name, branding_request(database, bucket, b"")
    )
    assert (
        retired_response.headers["cache-control"]
        == "public, max-age=31536000, immutable"
    )

    with pytest.raises(HTTPException) as missing:
        await access.public_event_branding_asset(
            "not-registered.png", branding_request(database, bucket, b"")
        )
    assert missing.value.status_code == 404


async def test_pending_branding_purge_is_indexed_restartable_and_idempotent(
    branding_database,
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    now = PENDING_BRANDING_RETENTION_MS + 10_000
    for event_id, organization_id in (
        ("attached-event", "org-a"),
        ("retired-event", "org-a"),
    ):
        connection.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms,created_by_user_id)
               VALUES(?,?,?,10,20,'UTC','Online','virtual','Purge guard','active',1,1,
                      (SELECT id FROM users ORDER BY id LIMIT 1))""",
            (event_id, organization_id, event_id),
        )
    rows = (
        ("expired-a", "org-a", None, "pending", 1, None),
        ("fresh-a", "org-a", None, "pending", 10_001, None),
        ("attached-a", "org-a", "attached-event", "attached", 1, 2),
        ("retired-a", "org-a", "retired-event", "retired", 1, 2),
        ("expired-b", "org-b", None, "pending", 1, None),
    )
    for asset_id, organization_id, event_id, status, created_at_ms, attached_at_ms in rows:
        object_key = f"public/event-branding/{organization_id}/{asset_id}.png"
        asset_url = f"/api/v1/public/event-assets/{asset_id}.png"
        connection.execute(
            """INSERT INTO event_branding_assets
               (id,organization_id,event_id,kind,object_key,asset_url,content_type,
                byte_size,checksum_sha256,status,created_by_user_id,created_at_ms,
                attached_at_ms)
               VALUES(?,?,?,'logo',?,?,'image/png',1,?,?,?,?,?)""",
            (
                asset_id,
                organization_id,
                event_id,
                object_key,
                asset_url,
                bytes(32),
                status,
                f"user-{organization_id[-1]}",
                created_at_ms,
                attached_at_ms,
            ),
        )
        bucket.objects[object_key] = PNG

    plan = connection.execute(
        """EXPLAIN QUERY PLAN
           SELECT id,object_key FROM event_branding_assets
           WHERE organization_id=? AND status='pending'
             AND created_at_ms<=? AND event_id IS NULL
           ORDER BY created_at_ms,id LIMIT ?""",
        ("org-a", now - PENDING_BRANDING_RETENTION_MS, 200),
    ).fetchall()
    detail = " ".join(str(row[3]) for row in plan)
    assert "USING INDEX idx_event_branding_assets_pending" in detail
    assert "SCAN event_branding_assets" not in detail

    first = await purge_pending_branding_assets(database, bucket, now)
    assert first.deleted_rows == 2
    assert first.deleted_objects == 2
    assert first.delete_failures == 0
    assert {
        row[0]
        for row in connection.execute(
            "SELECT id FROM event_branding_assets ORDER BY id"
        ).fetchall()
    } == {"attached-a", "fresh-a", "retired-a"}

    second = await purge_pending_branding_assets(database, bucket, now)
    assert second.deleted_rows == second.deleted_objects == second.delete_failures == 0


async def test_pending_branding_purge_keeps_row_deletion_when_object_delete_fails(
    branding_database,
) -> None:
    connection, database = branding_database
    object_key = "public/event-branding/org-a/retry.png"
    connection.execute(
        """INSERT INTO event_branding_assets
           (id,organization_id,event_id,kind,object_key,asset_url,content_type,
            byte_size,checksum_sha256,status,created_by_user_id,created_at_ms)
           VALUES('retry','org-a',NULL,'logo',?, '/api/v1/public/event-assets/retry.png',
                  'image/png',1,?,'pending','user-a',1)""",
        (object_key, bytes(32)),
    )

    class FailingBucket(Bucket):
        async def delete(self, key: str) -> None:
            raise RuntimeError("R2 unavailable")

    failed = await purge_pending_branding_assets(
        database, FailingBucket(), PENDING_BRANDING_RETENTION_MS + 1
    )
    assert failed.deleted_rows == 1
    assert failed.deleted_objects == 0
    assert failed.delete_failures == 1
    assert connection.execute(
        "SELECT status FROM event_branding_assets WHERE id='retry'"
    ).fetchone() is None

    recovered_bucket = Bucket()
    recovered_bucket.objects[object_key] = PNG
    recovered = await purge_pending_branding_assets(
        database, recovered_bucket, PENDING_BRANDING_RETENTION_MS + 1
    )
    assert recovered.deleted_rows == recovered.deleted_objects == 0
    assert recovered.delete_failures == 0


@pytest.mark.parametrize(
    ("include_logo", "include_cover"),
    ((True, False), (False, True), (True, True), (False, False)),
    ids=("logo-only", "cover-only", "both", "neither"),
)
async def test_event_branding_matrix_is_consistent_across_public_views(
    branding_database,
    allow_organization_admin,
    include_logo: bool,
    include_cover: bool,
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    expected: dict[str, str | None] = {"logo": None, "cover": None}
    for kind, enabled in (("logo", include_logo), ("cover", include_cover)):
        if enabled:
            uploaded = await access.upload_organization_event_asset(
                "org-a", kind, branding_request(database, bucket)
            )
            expected[kind] = uploaded.asset_url

    now = utc_now_ms()
    website_url = "https://events.example.test/conference"
    created = await access.create_event(
        "org-a",
        access.EventCreate(
            name="Branding matrix event",
            starts_at_ms=now + 86_400_000,
            ends_at_ms=now + 172_800_000,
            time_zone="UTC",
            location="Online",
            delivery_mode="virtual",
            description="An event used to verify every branding combination.",
            logo_url=expected["logo"],
            cover_image_url=expected["cover"],
            website_url=website_url,
        ),
        branding_request(database, bucket, b""),
    )
    assert created.logo_url == expected["logo"]
    assert created.cover_image_url == expected["cover"]
    assert created.website_url == website_url

    saved = connection.execute(
        "SELECT logo_url,cover_image_url,website_url FROM events WHERE id=?",
        (created.id,),
    ).fetchone()
    assert tuple(saved) == (expected["logo"], expected["cover"], website_url)

    attached_rows = connection.execute(
        """SELECT kind,asset_url,event_id,status FROM event_branding_assets
           WHERE event_id=? ORDER BY kind""",
        (created.id,),
    ).fetchall()
    assert {
        row["kind"]: (row["asset_url"], row["event_id"], row["status"])
        for row in attached_rows
    } == {
        kind: (asset_url, created.id, "attached")
        for kind, asset_url in expected.items()
        if asset_url is not None
    }

    for asset_url in (value for value in expected.values() if value is not None):
        response = await access.public_event_branding_asset(
            asset_url.rsplit("/", 1)[1], branding_request(database, bucket, b"")
        )
        assert b"".join([chunk async for chunk in response.body_iterator]) == PNG

    event_list = await access.list_events(
        "org-a", branding_request(database, bucket, b"")
    )
    event_view = next(item for item in event_list.data if item.id == created.id)
    assert event_view.logo_url == expected["logo"]
    assert event_view.cover_image_url == expected["cover"]
    assert event_view.website_url == website_url
    assert event_view.cfp_status == "not_started"

    slug = f"branding-{int(include_logo)}-{int(include_cover)}"
    schema_json = json.dumps(
        {"fields": [field.model_dump(mode="json") for field in DEFAULT_FORM_FIELDS]}
    )
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
            published_at_ms,created_at_ms,updated_at_ms)
           VALUES(?,?,?,?,?,?,?,'published',?,?,?)""",
        (
            f"form-{slug}",
            "org-a",
            created.id,
            1,
            slug,
            "Submit your proposal.",
            schema_json,
            now,
            now,
            now,
        ),
    )
    refreshed_events = await access.list_events(
        "org-a", branding_request(database, bucket, b"")
    )
    refreshed_event = next(item for item in refreshed_events.data if item.id == created.id)
    assert refreshed_event.cfp_status == "published"
    published_form = await get_form(slug, branding_request(database, bucket, b""))
    assert published_form.logo_url == expected["logo"]
    assert published_form.cover_image_url == expected["cover"]

    public_schedule = await get_public_schedule(
        created.id,
        branding_request(database, bucket, b""),
        Response(),
    )
    assert public_schedule.event.logo_url == expected["logo"]
    assert public_schedule.event.cover_image_url == expected["cover"]
    assert public_schedule.event.website_url == website_url
    assert public_schedule.event.cfp_url == f"/cfp/{created.id.replace('-', '')[:6]}/{slug}"

    public_gallery = await public_speakers(
        created.id, branding_request(database, bucket, b"")
    )
    assert public_gallery.event["cfp_url"] == f"/cfp/{created.id.replace('-', '')[:6]}/{slug}"
    assert public_gallery.event["logo_url"] == expected["logo"]
    assert public_gallery.event["cover_image_url"] == expected["cover"]
    assert public_gallery.event["website_url"] == website_url

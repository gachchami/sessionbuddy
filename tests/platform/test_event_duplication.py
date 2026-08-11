from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from sessionbuddy.platform.auth import access
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Permission, Persona
from sessionbuddy.platform.db.types import utc_now_ms
from tests.platform.test_event_branding_flow import Bucket, branding_request

pytest_plugins = ("tests.platform.test_event_branding_flow",)


def duplicate_body(event: access.EventView, *, name: str) -> access.EventDuplicateCreate:
    return access.EventDuplicateCreate(
        name=name,
        starts_at_ms=event.starts_at_ms,
        ends_at_ms=event.ends_at_ms,
        time_zone=event.time_zone,
        location=event.location,
        delivery_mode=event.delivery_mode,
        description=event.description,
        accent_color=event.accent_color,
        website_url=event.website_url,
        email_sender_name=event.email_sender_name,
        email_reply_to=event.email_reply_to,
        source_version=event.version,
    )


def create_body(*, name: str, status: str = "active") -> access.EventCreateRequest:
    now = utc_now_ms()
    return access.EventCreateRequest(
        name=name,
        starts_at_ms=now + 86_400_000,
        ends_at_ms=now + 172_800_000,
        time_zone="UTC",
        location="Online",
        delivery_mode="virtual",
        description="An event.",
        status=status,
    )


async def test_create_event_accepts_active_and_draft_with_idempotent_replay(
    branding_database, allow_organization_admin
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    active_body = create_body(name="Active event")
    active = await access.create_event(
        "org-a",
        active_body,
        branding_request(database, bucket, b""),
        "create-active-event-0001",
    )
    assert active.status == "active"
    replayed = await access.create_event(
        "org-a",
        active_body,
        branding_request(database, bucket, b""),
        "create-active-event-0001",
    )
    assert replayed.id == active.id

    draft = await access.create_event(
        "org-a",
        create_body(name="Draft event", status="draft"),
        branding_request(database, bucket, b""),
        "create-draft-event-0001",
    )
    assert draft.status == "draft"
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2


async def test_duplicate_event_creates_only_safe_draft_setup(
    branding_database, allow_organization_admin
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    now = utc_now_ms()
    source_logo = await access.upload_organization_event_asset(
        "org-a", "logo", branding_request(database, bucket)
    )
    source_cover = await access.upload_organization_event_asset(
        "org-a", "cover", branding_request(database, bucket)
    )
    source = await access.create_event(
        "org-a",
        access.EventCreate(
            name="AI Summit",
            starts_at_ms=now + 86_400_000,
            ends_at_ms=now + 172_800_000,
            time_zone="UTC",
            location="Online",
            delivery_mode="hybrid",
            description="A focused conference.",
            accent_color="#6d4aff",
            logo_url=source_logo.asset_url,
            cover_image_url=source_cover.asset_url,
            website_url="https://example.test/summit",
            email_sender_name="AI Summit",
            email_reply_to="events@example.test",
        ),
        branding_request(database, bucket, b""),
    )

    duplicated = await access.duplicate_event(
        source.id,
        access.EventDuplicateCreate(
            **{
                **duplicate_body(source, name="AI Summit copy").model_dump(),
                "location": "Bengaluru",
                "retain_source_logo": True,
                "retain_source_cover": True,
            }
        ),
        branding_request(database, bucket, b""),
        "duplicate-ai-summit-0001",
    )
    assert duplicated.name == "AI Summit copy"
    assert duplicated.status == "draft"
    assert duplicated.organization_id == source.organization_id
    assert duplicated.location == "Bengaluru"
    assert duplicated.logo_url is not None and duplicated.logo_url != source.logo_url
    assert duplicated.cover_image_url is not None
    assert duplicated.cover_image_url != source.cover_image_url
    assert duplicated.website_url == source.website_url
    assert duplicated.proposal_count == 0
    assert duplicated.pending_review_count == 0
    assert duplicated.schedule_status == "not_started"

    # Retrying the same request is a replay, not a second copy.
    replayed = await access.duplicate_event(
        source.id,
        access.EventDuplicateCreate(
            **{
                **duplicate_body(source, name="AI Summit copy").model_dump(),
                "location": "Bengaluru",
                "retain_source_logo": True,
                "retain_source_cover": True,
            }
        ),
        branding_request(database, bucket, b""),
        "duplicate-ai-summit-0001",
    )
    assert replayed.id == duplicated.id
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM owned_resources WHERE id=? AND owner_user_id='user-a'",
        (duplicated.id,),
    ).fetchone()[0] == 1
    copied_assets = connection.execute(
        """SELECT kind,status,event_id,asset_url FROM event_branding_assets
           WHERE event_id=? ORDER BY kind""",
        (duplicated.id,),
    ).fetchall()
    assert [(row["kind"], row["status"]) for row in copied_assets] == [
        ("cover", "attached"),
        ("logo", "attached"),
    ]
    for table in (
        "submissions",
        "evaluation_rounds",
        "event_speakers",
        "schedule_revisions",
        "agenda_items",
    ):
        assert connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE event_id=?",  # noqa: S608
            (duplicated.id,),
        ).fetchone()[0] == 0


async def test_duplicate_event_uses_collision_safe_names(
    branding_database, allow_organization_admin
) -> None:
    _connection, database = branding_database
    bucket = Bucket()
    now = utc_now_ms()
    source = await access.create_event(
        "org-a",
        access.EventCreate(
            name="Workshop",
            starts_at_ms=now + 86_400_000,
            ends_at_ms=now + 172_800_000,
            time_zone="UTC",
            location="Online",
            delivery_mode="virtual",
            description="A workshop.",
        ),
        branding_request(database, bucket, b""),
    )
    first = await access.duplicate_event(
        source.id,
        duplicate_body(source, name="Workshop copy"),
        branding_request(database, bucket, b""),
        "duplicate-workshop-0001",
    )
    second = await access.duplicate_event(
        source.id,
        access.EventDuplicateCreate(
            **{
                **duplicate_body(source, name="Workshop copy").model_dump(),
                "status": "active",
            }
        ),
        branding_request(database, bucket, b""),
        "duplicate-workshop-0002",
    )
    assert first.name == "Workshop copy"
    assert second.name == "Workshop copy 2"
    assert first.status == "draft"
    assert second.status == "active"


async def test_duplicate_event_rejects_stale_source_without_writing_assets(
    branding_database, allow_organization_admin
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    now = utc_now_ms()
    staged_logo = await access.upload_organization_event_asset(
        "org-a", "logo", branding_request(database, bucket)
    )
    source = await access.create_event(
        "org-a",
        access.EventCreate(
            name="Changing event",
            starts_at_ms=now + 86_400_000,
            ends_at_ms=now + 172_800_000,
            time_zone="UTC",
            location="Online",
            delivery_mode="virtual",
            description="A changing event.",
            logo_url=staged_logo.asset_url,
        ),
        branding_request(database, bucket, b""),
    )
    connection.execute(
        "UPDATE events SET version=version+1 WHERE id=?",
        (source.id,),
    )
    object_keys_before = set(bucket.objects)
    event_count_before = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    with pytest.raises(HTTPException) as stale:
        await access.duplicate_event(
            source.id,
            access.EventDuplicateCreate(
                **{
                    **duplicate_body(source, name="Changing event copy").model_dump(),
                    "retain_source_logo": True,
                }
            ),
            branding_request(database, bucket, b""),
            "duplicate-stale-source-0001",
        )
    assert stale.value.status_code == 409

    assert set(bucket.objects) == object_keys_before
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == event_count_before
    assert connection.execute(
        "SELECT COUNT(*) FROM audit_events WHERE action='event.duplicate'"
    ).fetchone()[0] == 0


async def test_duplicate_event_http_denies_org_only_actor_before_private_branding_copy(
    branding_database, allow_organization_admin, monkeypatch
) -> None:
    connection, database = branding_database
    bucket = Bucket()
    staged_logo = await access.upload_organization_event_asset(
        "org-a", "logo", branding_request(database, bucket)
    )
    staged_cover = await access.upload_organization_event_asset(
        "org-a", "cover", branding_request(database, bucket)
    )
    now = utc_now_ms()
    source = await access.create_event(
        "org-a",
        access.EventCreate(
            name="Private brand source",
            starts_at_ms=now + 86_400_000,
            ends_at_ms=now + 172_800_000,
            time_zone="UTC",
            location="Private venue",
            delivery_mode="in_person",
            description="Private source event.",
            logo_url=staged_logo.asset_url,
            cover_image_url=staged_cover.asset_url,
        ),
        branding_request(database, bucket, b""),
    )
    organization_only = AuthenticatedContext(
        Actor(
            "user-a",
            active_persona=Persona.ORGANIZER,
            owned_resource_ids=frozenset({"org-a"}),
        ),
        "session-a",
    )
    prepared_sql: list[str] = []
    original_prepare = database.prepare

    def track_prepare(sql: str):
        prepared_sql.append(sql)
        return original_prepare(sql)

    monkeypatch.setattr(database, "prepare", track_prepare)

    async def deny_source_event(request, permission, context, **kwargs):
        if permission is Permission.ORGANIZATION_MANAGE:
            return organization_only
        assert permission is Permission.EVENT_MANAGE
        assert context.organization_id == "org-a"
        assert context.event_id == source.id
        assert not any("logo_url,cover_image_url" in sql for sql in prepared_sql)
        raise HTTPException(status_code=404)

    monkeypatch.setattr(access, "require_permission", deny_source_event)
    application = FastAPI()

    @application.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "duplicate-security-test"
        return await call_next(request)

    application.include_router(access.access_router)

    async def inject_environment(scope, receive, send):
        scope["env"] = SimpleNamespace(DB=database, ASSETS=bucket)
        await application(scope, receive, send)

    object_keys_before = set(bucket.objects)
    event_count_before = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    body = {
        **duplicate_body(source, name="Unauthorized copy").model_dump(),
        "retain_source_logo": True,
        "retain_source_cover": True,
    }
    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="https://test"
    ) as client:
        response = await client.post(
            f"/api/v1/admin/events/{source.id}/duplicate",
            headers={"Idempotency-Key": "deny-private-copy-0001"},
            json=body,
        )

    assert response.status_code == 404
    assert not any("logo_url,cover_image_url" in sql for sql in prepared_sql)
    assert set(bucket.objects) == object_keys_before
    assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == event_count_before
    assert connection.execute(
        "SELECT COUNT(*) FROM audit_events WHERE action='event.duplicate'"
    ).fetchone()[0] == 0

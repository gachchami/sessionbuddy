"""Production passwordless access and one-time tenant bootstrap."""

import hmac
from html import escape
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from sessionbuddy.console import embedded_assets
from sessionbuddy.platform.authorization import Permission, ResourceContext, Role
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch
from sessionbuddy.platform.db.d1 import result_rows, row_mapping
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit

from .cookies import sign_session_cookie
from .csrf import issue_csrf_token
from .http import authenticate_request, database, require_permission, secret
from .tokens import generate_token, hash_token, normalize_email

access_router = APIRouter()


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


@access_router.get("/sign-in", include_in_schema=False)
async def sign_in_page() -> Response:
    return Response(
        _asset("sign_in.html"), media_type="text/html", headers={"Cache-Control": "no-store"}
    )


@access_router.get("/auth/assets/sign-in.js", include_in_schema=False)
async def sign_in_javascript() -> Response:
    return Response(_asset("sign_in.js"), media_type="text/javascript")


@access_router.get("/admin/events/{event_id}/access", include_in_schema=False)
async def event_access_page(event_id: str) -> Response:
    return Response(
        _asset("access_admin.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/access/assets/access.js", include_in_schema=False)
async def event_access_javascript() -> Response:
    return Response(_asset("access_admin.js"), media_type="text/javascript")


@access_router.get("/admin/events", include_in_schema=False)
async def events_page() -> Response:
    return Response(
        _asset("events_admin.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/events/assets/events.js", include_in_schema=False)
async def events_javascript() -> Response:
    return Response(_asset("events_admin.js"), media_type="text/javascript")


class BootstrapCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization_name: str = Field(min_length=1, max_length=200)
    admin_email: str = Field(min_length=3, max_length=320)
    event_name: str | None = Field(default=None, min_length=1, max_length=200)
    starts_at_ms: int | None = None
    ends_at_ms: int | None = None
    time_zone: str | None = Field(default=None, min_length=1, max_length=100)


class BootstrapView(BaseModel):
    organization_id: str
    event_id: str | None
    admin_user_id: str


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=320)
    redirect_path: str = Field(default="/", max_length=500)
    form_slug: str | None = Field(default=None, min_length=3, max_length=80)


class GenericAccepted(BaseModel):
    accepted: bool = True


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=320)
    role: Literal["event_admin", "evaluator", "speaker"]
    expires_in_days: int = Field(default=14, ge=1, le=30)


class InvitationView(BaseModel):
    id: str
    event_id: str
    email: str
    role: Literal["event_admin", "evaluator", "speaker"]
    status: Literal["pending", "accepted", "revoked", "expired"] = "pending"


class InvitationList(BaseModel):
    data: list[InvitationView]


class EventMemberView(BaseModel):
    user_id: str
    email: str
    role: Literal["event_admin", "evaluator", "speaker"]
    status: Literal["active", "revoked"]


class EventMemberList(BaseModel):
    data: list[EventMemberView]


class OrganizationView(BaseModel):
    id: str
    name: str
    status: Literal["active", "archived"]
    version: int


class OrganizationList(BaseModel):
    data: list[OrganizationView]


class OrganizationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    version: int = Field(ge=1)


class EventView(BaseModel):
    id: str
    organization_id: str
    name: str
    starts_at_ms: int
    ends_at_ms: int
    time_zone: str
    location: str | None = None
    delivery_mode: Literal["in_person", "virtual", "hybrid"]
    description: str | None = None
    status: Literal["draft", "active", "archived"]
    version: int


class EventList(BaseModel):
    data: list[EventView]


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    starts_at_ms: int
    ends_at_ms: int
    time_zone: str = Field(min_length=1, max_length=100)
    location: str | None = Field(default=None, max_length=500)
    delivery_mode: Literal["in_person", "virtual", "hybrid"] = "hybrid"
    description: str | None = Field(default=None, max_length=5000)


class EventUpdate(EventCreate):
    version: int = Field(ge=1)
    status: Literal["draft", "active", "archived"] = "active"


class SessionCreated(BaseModel):
    authenticated: bool = True
    user_id: str
    csrf_token: str
    redirect_path: str


class CurrentSession(BaseModel):
    authenticated: bool = True
    user_id: str
    csrf_token: str
    organization_id: str | None = None
    event_id: str | None = None


def _valid_redirect(value: str) -> bool:
    return value.startswith("/") and not value.startswith("//") and "\\" not in value


def _email(value: str) -> tuple[str, str]:
    display = value.strip()
    normalized = normalize_email(display)
    local, separator, domain = normalized.partition("@")
    if not separator or not local or "." not in domain or domain.startswith("."):
        raise HTTPException(status_code=422)
    return display, normalized


@access_router.post("/api/v1/bootstrap", response_model=BootstrapView, tags=["administration"])
async def bootstrap_tenant(
    body: BootstrapCreate,
    request: Request,
    bootstrap_token: str | None = Header(default=None, alias="X-Bootstrap-Token"),
) -> BootstrapView:
    expected = str(getattr(request.scope.get("env"), "BOOTSTRAP_TOKEN", "")).encode()
    supplied = (bootstrap_token or "").encode()
    if len(expected) < 32 or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=404)
    event_values = (body.event_name, body.starts_at_ms, body.ends_at_ms, body.time_zone)
    has_event = any(value is not None for value in event_values)
    if has_event:
        if (
            body.event_name is None
            or body.starts_at_ms is None
            or body.ends_at_ms is None
            or body.time_zone is None
        ):
            raise HTTPException(status_code=422)
        if body.ends_at_ms <= body.starts_at_ms:
            raise HTTPException(status_code=422)
    db = database(request)
    existing = row_mapping(await db.prepare("SELECT id FROM organizations LIMIT 1").first())
    if existing is not None:
        raise HTTPException(status_code=409)
    now = utc_now_ms()
    organization_id, user_id = new_id(), new_id()
    event_id = new_id() if has_event else None
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO organizations
               (id,name,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,'active',?3,?3)"""
        ).bind(organization_id, body.organization_name, now)
    )
    admin_email, normalized = _email(body.admin_email)
    batch.add_statement(
        db.prepare(
            """INSERT INTO users
           (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES(?1,?2,?3,'active',?4,?4,?4)"""
        ).bind(user_id, admin_email, normalized, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES(?1,?2,?3,'organization_admin','active',?4,?4)"""
        ).bind(new_id(), organization_id, user_id, now)
    )
    if has_event:
        batch.add_statement(
            db.prepare(
                """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,delivery_mode,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,'hybrid','active',?7,?7)"""
            ).bind(
                event_id,
                organization_id,
                body.event_name,
                body.starts_at_ms,
                body.ends_at_ms,
                body.time_zone,
                now,
            )
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'event_admin','active',?5,?5)"""
            ).bind(new_id(), organization_id, event_id, user_id, now)
        )
    batch.audit(
        AuditEvent(
            actor_type="system",
            action="tenant.bootstrap",
            target_type="organization",
            target_id=organization_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={"initial_admin": 1, "initial_event": int(has_event)},
        )
    )
    await batch.execute()
    return BootstrapView(organization_id=organization_id, event_id=event_id, admin_user_id=user_id)


@access_router.get(
    "/api/v1/admin/organizations",
    response_model=OrganizationList,
    tags=["administration"],
)
async def list_organizations(request: Request) -> OrganizationList:
    authenticated = await authenticate_request(request)
    db = database(request)
    organizations: list[OrganizationView] = []
    for organization_id, roles in authenticated.actor.organization_roles.items():
        if Role.ORGANIZATION_ADMIN not in roles:
            continue
        row = row_mapping(
            await db.prepare("SELECT id,name,status,version FROM organizations WHERE id=?1 LIMIT 1")
            .bind(organization_id)
            .first()
        )
        if row is not None:
            organizations.append(OrganizationView(**row))
    organizations.sort(key=lambda item: (item.name.casefold(), item.id))
    return OrganizationList(data=organizations)


@access_router.patch(
    "/api/v1/admin/organizations/{organization_id}",
    response_model=OrganizationView,
    tags=["administration"],
)
async def update_organization(
    organization_id: str, body: OrganizationUpdate, request: Request
) -> OrganizationView:
    authenticated = await require_permission(
        request,
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(organization_id),
        mutation=True,
    )
    db, now = database(request), utc_now_ms()
    row = row_mapping(
        await db.prepare(
            """UPDATE organizations SET name=?1,version=version+1,updated_at_ms=?2
               WHERE id=?3 AND status='active' AND version=?4
               RETURNING id,name,status,version"""
        )
        .bind(body.name, now, organization_id, body.version)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=409)
    audit = CommandBatch(db)
    audit.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="organization.update",
            target_type="organization",
            target_id=organization_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
        )
    )
    await audit.execute()
    return OrganizationView(**row)


@access_router.get(
    "/api/v1/admin/organizations/{organization_id}/events",
    response_model=EventList,
    tags=["administration"],
)
async def list_events(organization_id: str, request: Request) -> EventList:
    authenticated = await authenticate_request(request)
    organization_roles = authenticated.actor.organization_roles.get(organization_id, frozenset())
    manageable_event_ids = {
        event_id
        for (scope_organization_id, event_id), roles in authenticated.actor.event_roles.items()
        if scope_organization_id == organization_id and Role.EVENT_ADMIN in roles
    }
    if Role.ORGANIZATION_ADMIN not in organization_roles and not manageable_event_ids:
        raise HTTPException(status_code=404)
    result = await (
        database(request)
        .prepare(
            """SELECT id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                      delivery_mode,description,status,version
               FROM events WHERE organization_id=?1 ORDER BY starts_at_ms DESC,id DESC"""
        )
        .bind(organization_id)
        .all()
    )
    events = [EventView(**row) for row in result_rows(result)]
    if Role.ORGANIZATION_ADMIN not in organization_roles:
        events = [event for event in events if event.id in manageable_event_ids]
    return EventList(data=events)


def _validate_event_times(starts_at_ms: int, ends_at_ms: int) -> None:
    if ends_at_ms <= starts_at_ms:
        raise HTTPException(status_code=422)


@access_router.post(
    "/api/v1/admin/organizations/{organization_id}/events",
    response_model=EventView,
    status_code=201,
    tags=["administration"],
)
async def create_event(organization_id: str, body: EventCreate, request: Request) -> EventView:
    _validate_event_times(body.starts_at_ms, body.ends_at_ms)
    authenticated = await require_permission(
        request,
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(organization_id),
        mutation=True,
    )
    db, now, event_id = database(request), utc_now_ms(), new_id()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,'active',?10,?10)"""
        ).bind(
            event_id,
            organization_id,
            body.name,
            body.starts_at_ms,
            body.ends_at_ms,
            body.time_zone,
            body.location,
            body.delivery_mode,
            body.description,
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'event_admin','active',?5,?5)"""
        ).bind(new_id(), organization_id, event_id, authenticated.actor.user_id, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="event.create",
            target_type="event",
            target_id=event_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
        )
    )
    await batch.execute()
    return EventView(
        id=event_id,
        organization_id=organization_id,
        status="active",
        version=1,
        **body.model_dump(),
    )


@access_router.patch(
    "/api/v1/admin/events/{event_id}",
    response_model=EventView,
    tags=["administration"],
)
async def update_event(event_id: str, body: EventUpdate, request: Request) -> EventView:
    _validate_event_times(body.starts_at_ms, body.ends_at_ms)
    db = database(request)
    event = row_mapping(
        await db.prepare("SELECT organization_id FROM events WHERE id=?1 LIMIT 1")
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request,
        Permission.EVENT_MANAGE,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
    )
    now = utc_now_ms()
    archived_at_ms = now if body.status == "archived" else None
    row = row_mapping(
        await db.prepare(
            """UPDATE events SET name=?1,starts_at_ms=?2,ends_at_ms=?3,time_zone=?4,
               location=?5,delivery_mode=?6,description=?7,status=?8,archived_at_ms=?9,
               version=version+1,updated_at_ms=?10 WHERE id=?11 AND version=?12
               RETURNING id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                         delivery_mode,description,status,version"""
        )
        .bind(
            body.name,
            body.starts_at_ms,
            body.ends_at_ms,
            body.time_zone,
            body.location,
            body.delivery_mode,
            body.description,
            body.status,
            archived_at_ms,
            now,
            event_id,
            body.version,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=409)
    audit = CommandBatch(db)
    audit.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="event.update",
            target_type="event",
            target_id=event_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            metadata={"status": body.status},
        )
    )
    await audit.execute()
    return EventView(**row)


@access_router.post(
    "/api/v1/admin/events/{event_id}/invitations",
    response_model=InvitationView,
    status_code=201,
    tags=["administration"],
)
async def create_invitation(
    event_id: str, body: InvitationCreate, request: Request
) -> InvitationView:
    db = database(request)
    event = row_mapping(
        await db.prepare(
            "SELECT organization_id FROM events WHERE id=?1 AND status!='archived' LIMIT 1"
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request,
        Permission.EVENT_MANAGE,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
    )
    email, normalized = _email(body.email)
    now, invitation_id = utc_now_ms(), new_id()
    await (
        db.prepare(
            """INSERT INTO identity_invitations
         (id,organization_id,event_id,normalized_email,email,role,status,invited_by_user_id,
          expires_at_ms,created_at_ms,updated_at_ms)
         VALUES(?1,?2,?3,?4,?5,?6,'pending',?7,?8,?9,?9)
         ON CONFLICT(organization_id,event_id,normalized_email,role) DO UPDATE SET
           email=excluded.email,status='pending',invited_by_user_id=excluded.invited_by_user_id,
           expires_at_ms=excluded.expires_at_ms,accepted_at_ms=NULL,revoked_at_ms=NULL,
           updated_at_ms=excluded.updated_at_ms"""
        )
        .bind(
            invitation_id,
            event["organization_id"],
            event_id,
            normalized,
            email,
            body.role,
            authenticated.actor.user_id,
            now + body.expires_in_days * 86_400_000,
            now,
        )
        .run()
    )
    row = row_mapping(
        await db.prepare(
            """SELECT id,event_id,email,role,status FROM identity_invitations
           WHERE organization_id=?1 AND event_id=?2 AND normalized_email=?3 AND role=?4"""
        )
        .bind(event["organization_id"], event_id, normalized, body.role)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=409)
    audit = CommandBatch(db)
    audit.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="identity.invitation.create",
            target_type="identity_invitation",
            target_id=str(row["id"]),
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            metadata={"role": body.role},
        )
    )
    await audit.execute()
    base = str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "")).rstrip("/")
    if base.startswith("https://"):
        message_id = new_id()
        destination = {
            "speaker": "/speaker",
            "evaluator": "/reviews",
            "event_admin": "/admin/programs",
        }[body.role]
        sign_in_link = f"{base}/sign-in?redirect={destination}"
        await (
            db.prepare(
                """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'You are invited to SessionBuddy',?5,?6,'queued',?7,?7)"""
            )
            .bind(
                message_id,
                event["organization_id"],
                event_id,
                email,
                f'<p><a href="{escape(sign_in_link)}">Accept your SessionBuddy invitation</a></p>',
                f"identity-invitation:{row['id']}:{now}",
                now,
            )
            .run()
        )
        queue = getattr(request.scope.get("env"), "COMMUNICATION_QUEUE", None)
        if queue is not None:
            await queue.send({"schema_version": 1, "message_id": message_id})
    return InvitationView(**row)


async def _managed_event(request: Request, event_id: str, *, mutation: bool):
    db = database(request)
    event = row_mapping(
        await db.prepare(
            "SELECT organization_id FROM events WHERE id=?1 AND status!='archived' LIMIT 1"
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request,
        Permission.EVENT_MANAGE,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=mutation,
    )
    return db, str(event["organization_id"]), authenticated


@access_router.get(
    "/api/v1/admin/events/{event_id}/invitations",
    response_model=InvitationList,
    tags=["administration"],
)
async def list_invitations(event_id: str, request: Request) -> InvitationList:
    db, organization_id, _ = await _managed_event(request, event_id, mutation=False)
    result = await (
        db.prepare(
            """SELECT id,event_id,email,role,
                      CASE WHEN status='pending' AND expires_at_ms<=?3
                           THEN 'expired' ELSE status END AS status
               FROM identity_invitations WHERE organization_id=?1 AND event_id=?2
               ORDER BY created_at_ms DESC,id DESC"""
        )
        .bind(organization_id, event_id, utc_now_ms())
        .all()
    )
    return InvitationList(data=[InvitationView(**row) for row in result_rows(result)])


@access_router.delete(
    "/api/v1/admin/events/{event_id}/invitations/{invitation_id}",
    status_code=204,
    tags=["administration"],
)
async def revoke_invitation(event_id: str, invitation_id: str, request: Request) -> Response:
    db, organization_id, authenticated = await _managed_event(request, event_id, mutation=True)
    now = utc_now_ms()
    changed = row_mapping(
        await db.prepare(
            """UPDATE identity_invitations SET status='revoked',revoked_at_ms=?1,updated_at_ms=?1
               WHERE id=?2 AND organization_id=?3 AND event_id=?4 AND status='pending'
               RETURNING id"""
        )
        .bind(now, invitation_id, organization_id, event_id)
        .first()
    )
    if changed is None:
        raise HTTPException(status_code=404)
    audit = CommandBatch(db)
    audit.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="identity.invitation.revoke",
            target_type="identity_invitation",
            target_id=invitation_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
        )
    )
    await audit.execute()
    return Response(status_code=204)


@access_router.get(
    "/api/v1/admin/events/{event_id}/members",
    response_model=EventMemberList,
    tags=["administration"],
)
async def list_event_members(event_id: str, request: Request) -> EventMemberList:
    db, organization_id, _ = await _managed_event(request, event_id, mutation=False)
    result = await (
        db.prepare(
            """SELECT em.user_id,u.email,em.role,em.status FROM event_memberships em
               JOIN users u ON u.id=em.user_id
               WHERE em.organization_id=?1 AND em.event_id=?2
               ORDER BY u.normalized_email,em.role"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    return EventMemberList(data=[EventMemberView(**row) for row in result_rows(result)])


@access_router.delete(
    "/api/v1/admin/events/{event_id}/members/{user_id}/roles/{role}",
    status_code=204,
    tags=["administration"],
)
async def revoke_event_member(
    event_id: str,
    user_id: str,
    role: Literal["event_admin", "evaluator", "speaker"],
    request: Request,
) -> Response:
    db, organization_id, authenticated = await _managed_event(request, event_id, mutation=True)
    if user_id == authenticated.actor.user_id:
        raise HTTPException(status_code=409)
    now = utc_now_ms()
    changed = row_mapping(
        await db.prepare(
            """UPDATE event_memberships SET status='revoked',revoked_at_ms=?1,
               version=version+1,updated_at_ms=?1
               WHERE organization_id=?2 AND event_id=?3 AND user_id=?4 AND role=?5
                 AND status='active' RETURNING id"""
        )
        .bind(now, organization_id, event_id, user_id, role)
        .first()
    )
    if changed is None:
        raise HTTPException(status_code=404)
    await (
        db.prepare(
            """UPDATE users SET authorization_version=authorization_version+1,
               updated_at_ms=?1 WHERE id=?2"""
        )
        .bind(now, user_id)
        .run()
    )
    audit = CommandBatch(db)
    audit.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="event_membership.revoke",
            target_type="event_membership",
            target_id=f"{user_id}:{role}",
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={"role": role},
        )
    )
    await audit.execute()
    return Response(status_code=204)


@access_router.post(
    "/api/v1/auth/magic-links",
    response_model=GenericAccepted,
    status_code=202,
    tags=["authentication"],
)
async def request_magic_link(body: MagicLinkRequest, request: Request) -> GenericAccepted:
    if not _valid_redirect(body.redirect_path):
        raise HTTPException(status_code=422)
    db = database(request)
    email, normalized = _email(body.email)
    source = request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client is not None else "unknown"
    )
    await enforce_rate_limit(
        request,
        binding_name="AUTH_RATE_LIMITER",
        policy=RateLimitPolicy("auth.magic_link", limit=10, window_seconds=60),
        subject=f"{normalized}:{source}",
    )
    user = row_mapping(
        await db.prepare(
            """SELECT u.id,om.organization_id,e.id AS event_id FROM users u
           JOIN organization_memberships om ON om.user_id=u.id AND om.status='active'
           LEFT JOIN events e ON e.organization_id=om.organization_id AND e.status!='archived'
           WHERE u.normalized_email=?1 AND u.status='active' LIMIT 1"""
        )
        .bind(normalized)
        .first()
    )
    invitation = row_mapping(
        await db.prepare(
            """SELECT id,organization_id,event_id FROM identity_invitations
               WHERE normalized_email=?1 AND status='pending' AND expires_at_ms>?2
               ORDER BY created_at_ms DESC LIMIT 1"""
        )
        .bind(normalized, utc_now_ms())
        .first()
    )
    submission_context = None
    if user is None and invitation is None and body.form_slug is not None:
        submission_context = row_mapping(
            await db.prepare(
                """SELECT organization_id,event_id FROM call_for_speaker_forms
                   WHERE slug=?1 AND status='published' LIMIT 1"""
            )
            .bind(body.form_slug)
            .first()
        )
    if user is None and invitation is None and submission_context is None:
        return GenericAccepted()
    context = invitation or user or submission_context
    now, raw_token, challenge_id = utc_now_ms(), generate_token(), new_id()
    await (
        db.prepare(
            """INSERT INTO authentication_challenges
           (id,normalized_email,token_hash,purpose,provisioning_context,redirect_path,
            expires_at_ms,created_at_ms,user_id,organization_id,event_id,invitation_id)
           VALUES(?1,?2,?3,'sign_in',?4,?5,?6,?7,?8,?9,?10,?11)"""
        )
        .bind(
            challenge_id,
            normalized,
            hash_token(raw_token),
            (
                "invitation"
                if invitation is not None
                else "existing_user"
                if user is not None
                else "submission"
            ),
            body.redirect_path,
            now + 15 * 60 * 1000,
            now,
            user["id"] if user is not None else None,
            context["organization_id"],
            context["event_id"],
            invitation["id"] if invitation is not None else None,
        )
        .run()
    )
    base = str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "")).rstrip("/")
    if base.startswith("https://"):
        message_id = new_id()
        link = f"{base}/auth/verify?token={raw_token}"
        await (
            db.prepare(
                """INSERT INTO communication_messages
             (id,organization_id,event_id,recipient_user_id,recipient_email,subject,html_body,
              deterministic_key,status,queued_at_ms,updated_at_ms)
             VALUES(?1,?2,?3,?4,?5,'Your SessionBuddy sign-in link',?6,?7,'queued',?8,?8)"""
            )
            .bind(
                message_id,
                context["organization_id"],
                context["event_id"],
                user["id"] if user is not None else None,
                email,
                f'<p><a href="{escape(link)}">Sign in to SessionBuddy</a></p>',
                f"auth:{challenge_id}",
                now,
            )
            .run()
        )
        queue = getattr(request.scope.get("env"), "COMMUNICATION_QUEUE", None)
        if queue is not None:
            await queue.send({"schema_version": 1, "message_id": message_id})
    return GenericAccepted()


@access_router.get("/api/v1/auth/verify", response_model=SessionCreated, tags=["authentication"])
async def verify_magic_link(token: str, request: Request, response: Response) -> SessionCreated:
    if len(token) < 32:
        raise HTTPException(status_code=404)
    db, now = database(request), utc_now_ms()
    challenge = row_mapping(
        await db.prepare(
            """UPDATE authentication_challenges SET consumed_at_ms=?1
           WHERE token_hash=?2 AND consumed_at_ms IS NULL AND expires_at_ms>?1
           RETURNING user_id,normalized_email,organization_id,event_id,provisioning_context,
                     invitation_id,redirect_path"""
        )
        .bind(now, hash_token(token))
        .first()
    )
    if challenge is None:
        raise HTTPException(status_code=404)
    user_id = challenge["user_id"]
    if challenge["invitation_id"] is not None:
        invitation = row_mapping(
            await db.prepare(
                """SELECT id,organization_id,event_id,email,normalized_email,role
                   FROM identity_invitations WHERE id=?1 AND status='pending'
                     AND expires_at_ms>?2 LIMIT 1"""
            )
            .bind(challenge["invitation_id"], now)
            .first()
        )
        if invitation is None:
            raise HTTPException(status_code=404)
        existing_user = row_mapping(
            await db.prepare("SELECT id FROM users WHERE normalized_email=?1 LIMIT 1")
            .bind(invitation["normalized_email"])
            .first()
        )
        if user_id is not None and (
            existing_user is None or str(existing_user["id"]) != str(user_id)
        ):
            raise HTTPException(status_code=404)
        user_id = str(existing_user["id"]) if existing_user is not None else new_id()
        batch = CommandBatch(db)
        if existing_user is None:
            batch.add_statement(
                db.prepare(
                    """INSERT INTO users
                   (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,'active',?4,?4,?4)"""
                ).bind(user_id, invitation["email"], invitation["normalized_email"], now)
            )
        batch.add_statement(
            db.prepare(
                """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,'member','active',?4,?4)
               ON CONFLICT(organization_id,user_id) DO UPDATE SET status='active',
                 revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
            ).bind(new_id(), invitation["organization_id"], user_id, now)
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,'active',?6,?6)
               ON CONFLICT(organization_id,event_id,user_id,role) DO UPDATE SET status='active',
                 revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
            ).bind(
                new_id(),
                invitation["organization_id"],
                invitation["event_id"],
                user_id,
                invitation["role"],
                now,
            )
        )
        if invitation["role"] == "speaker":
            await _add_speaker_profile(
                batch,
                db,
                organization_id=str(invitation["organization_id"]),
                event_id=str(invitation["event_id"]),
                user_id=str(user_id),
                email=str(invitation["email"]),
                now=now,
            )
        batch.add_statement(
            db.prepare(
                """UPDATE identity_invitations
                   SET status='accepted',accepted_at_ms=?1,updated_at_ms=?1
               WHERE id=?2 AND status='pending'"""
            ).bind(now, invitation["id"])
        )
        batch.audit(
            AuditEvent(
                actor_type="user",
                actor_user_id=str(user_id),
                action="identity.invitation.accept",
                target_type="identity_invitation",
                target_id=str(invitation["id"]),
                result="succeeded",
                correlation_id=request.state.request_id,
                occurred_at_ms=now,
                organization_id=str(invitation["organization_id"]),
                event_id=str(invitation["event_id"]),
                metadata={"role": str(invitation["role"])},
            )
        )
        await batch.execute()
    elif user_id is None and challenge["provisioning_context"] == "submission":
        user_id = new_id()
        batch = CommandBatch(db)
        batch.add_statement(
            db.prepare(
                """INSERT INTO users
                   (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?2,'active',?3,?3,?3)"""
            ).bind(user_id, challenge["normalized_email"], now)
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO organization_memberships
                   (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,'member','active',?4,?4)"""
            ).bind(new_id(), challenge["organization_id"], user_id, now)
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_memberships
                   (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,'speaker','active',?5,?5)"""
            ).bind(new_id(), challenge["organization_id"], challenge["event_id"], user_id, now)
        )
        await _add_speaker_profile(
            batch,
            db,
            organization_id=str(challenge["organization_id"]),
            event_id=str(challenge["event_id"]),
            user_id=str(user_id),
            email=str(challenge["normalized_email"]),
            now=now,
        )
        await batch.execute()
    if user_id is None:
        raise HTTPException(status_code=404)
    session_id, session_token = new_id(), generate_token()
    csrf = issue_csrf_token(session_id, secret(request, "CSRF_HMAC_KEY"))
    await (
        db.prepare(
            """INSERT INTO sessions
         (id,user_id,token_hash,csrf_secret_hash,authorization_version,created_at_ms,last_seen_at_ms,
          idle_expires_at_ms,absolute_expires_at_ms)
         SELECT ?1,id,?2,?3,authorization_version,?4,?4,?5,?6 FROM users WHERE id=?7"""
        )
        .bind(
            session_id,
            hash_token(session_token),
            hash_token(csrf),
            now,
            now + 12 * 60 * 60 * 1000,
            now + 30 * 24 * 60 * 60 * 1000,
            user_id,
        )
        .run()
    )
    deployed = getattr(request.scope.get("env"), "APP_ENV", "production") != "local"
    response.set_cookie(
        "__Host-session" if deployed else "sessionbuddy-local",
        sign_session_cookie(session_token, secret(request, "SESSION_HMAC_KEY")),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=deployed,
        samesite="lax",
        path="/",
    )
    return SessionCreated(
        user_id=str(user_id),
        csrf_token=csrf,
        redirect_path=str(challenge["redirect_path"]),
    )


async def _add_speaker_profile(
    batch: CommandBatch,
    db,
    *,
    organization_id: str,
    event_id: str,
    user_id: str,
    email: str,
    now: int,
) -> None:
    person = row_mapping(
        await db.prepare("SELECT id FROM people WHERE organization_id=?1 AND user_id=?2 LIMIT 1")
        .bind(organization_id, user_id)
        .first()
    )
    person_id = str(person["id"]) if person is not None else new_id()
    if person is None:
        display_name = email.partition("@")[0].strip()[:200] or "Speaker"
        batch.add_statement(
            db.prepare(
                """INSERT INTO people
                   (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?5)"""
            ).bind(person_id, organization_id, user_id, display_name, now)
        )
    event_speaker = row_mapping(
        await db.prepare(
            """SELECT id FROM event_speakers
               WHERE organization_id=?1 AND event_id=?2 AND person_id=?3 LIMIT 1"""
        )
        .bind(organization_id, event_id, person_id)
        .first()
    )
    if event_speaker is None:
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_speakers
                   (id,organization_id,event_id,person_id,status,accepted_at_ms,
                    last_activity_at_ms,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,'onboarding',?5,?5,?5,?5)"""
            ).bind(new_id(), organization_id, event_id, person_id, now)
        )


@access_router.get("/auth/verify", include_in_schema=False)
async def verify_magic_link_in_browser(token: str, request: Request) -> RedirectResponse:
    cookie_response = Response()
    session = await verify_magic_link(token, request, cookie_response)
    response = RedirectResponse(session.redirect_path, status_code=303)
    for key, value in cookie_response.raw_headers:
        if key.lower() == b"set-cookie":
            response.raw_headers.append((key, value))
    return response


@access_router.get("/api/v1/auth/session", response_model=CurrentSession, tags=["authentication"])
async def current_session(request: Request) -> CurrentSession:
    authenticated = await authenticate_request(request)
    organization_id = next(iter(authenticated.actor.organization_roles), None)
    event_scope = next(iter(authenticated.actor.event_roles), None)
    if organization_id is None and event_scope is not None:
        organization_id = event_scope[0]
    return CurrentSession(
        user_id=authenticated.actor.user_id,
        csrf_token=issue_csrf_token(authenticated.session_id, secret(request, "CSRF_HMAC_KEY")),
        organization_id=organization_id,
        event_id=event_scope[1] if event_scope is not None else None,
    )

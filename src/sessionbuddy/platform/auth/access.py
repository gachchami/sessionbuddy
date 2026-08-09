"""Production passwordless access and one-time tenant bootstrap."""

import hashlib
import hmac
from email.headerregistry import Address
from html import escape
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sessionbuddy.console import embedded_assets
from sessionbuddy.platform.authorization import Permission, ResourceContext, Role
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch
from sessionbuddy.platform.db.d1 import D1Database, PersistenceError, result_rows, row_mapping
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit
from sessionbuddy.platform.storage import malware_scan_disabled
from sessionbuddy.speaker_operations.asset_boundary import ScanJob
from sessionbuddy.speaker_operations.scanner_adapter import SignedScannerAdapter

from .cookies import sign_session_cookie
from .csrf import issue_csrf_token
from .http import authenticate_request, database, guard_mutation, require_permission, secret
from .tokens import generate_token, hash_token, normalize_email

access_router = APIRouter()

_EVENT_LOGO_RULES = {
    "image/jpeg": ("jpg", lambda body: body.startswith(b"\xff\xd8\xff")),
    "image/png": ("png", lambda body: body.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (
        "webp",
        lambda body: len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP",
    ),
}
_EVENT_LOGO_MAX_BYTES = 2 * 1024 * 1024
_EVENT_IMAGE_MEDIA_TYPES = frozenset(_EVENT_LOGO_RULES)

_SETUP_COMPLETED_SQL = """SELECT singleton_key
FROM instance_setup
WHERE singleton_key='primary'
UNION ALL
SELECT 'legacy' FROM organizations
LIMIT 1"""


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


@access_router.get("/sign-in", include_in_schema=False)
async def sign_in_page() -> Response:
    return Response(
        _asset("sign_in.html"), media_type="text/html", headers={"Cache-Control": "no-store"}
    )


async def setup_is_configured(db: D1Database) -> bool:
    return row_mapping(await db.prepare(_SETUP_COMPLETED_SQL).first()) is not None


@access_router.get("/setup", include_in_schema=False)
async def setup_page(request: Request) -> Response:
    environment = request.scope.get("env")
    db = getattr(environment, "DB", None) if environment is not None else None
    if db is not None and await setup_is_configured(db):
        return RedirectResponse(
            "/",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )
    return Response(
        _asset("setup.html"), media_type="text/html", headers={"Cache-Control": "no-store"}
    )


@access_router.get("/setup/assets/setup.css", include_in_schema=False)
async def setup_stylesheet() -> Response:
    return Response(_asset("setup.css"), media_type="text/css")


@access_router.get("/setup/assets/setup.js", include_in_schema=False)
async def setup_javascript() -> Response:
    return Response(_asset("setup.js"), media_type="text/javascript")


@access_router.get("/auth/assets/sign-in.js", include_in_schema=False)
async def sign_in_javascript() -> Response:
    return Response(_asset("sign_in.js"), media_type="text/javascript")


@access_router.get("/app-shell/assets/app-shell.css", include_in_schema=False)
async def app_shell_stylesheet() -> Response:
    return Response(_asset("app_shell.css"), media_type="text/css")


@access_router.get("/app-shell/assets/app-shell.js", include_in_schema=False)
async def app_shell_javascript() -> Response:
    return Response(_asset("app_shell.js"), media_type="text/javascript")


@access_router.get("/app-shell/assets/api-client.js", include_in_schema=False)
async def api_client_javascript() -> Response:
    return Response(
        _asset("api_client.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@access_router.get("/app-shell/assets/error-page.css", include_in_schema=False)
async def error_page_stylesheet() -> Response:
    return Response(
        _asset("error_page.css"),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=300"},
    )


@access_router.get("/co-speaker-invitations/{token}", include_in_schema=False)
async def co_speaker_invitation_page(token: str) -> Response:
    return Response(
        _asset("co_speaker_invitation.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get(
    "/co-speaker-invitations/assets/invitation.js", include_in_schema=False
)
async def co_speaker_invitation_javascript() -> Response:
    return Response(_asset("co_speaker_invitation.js"), media_type="text/javascript")


@access_router.get("/admin", include_in_schema=False)
async def admin_home_page() -> Response:
    return Response(
        _asset("admin_home.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/home/assets/home.js", include_in_schema=False)
async def admin_home_javascript() -> Response:
    return Response(_asset("admin_home.js"), media_type="text/javascript")


@access_router.get("/admin/events/{event_id}", include_in_schema=False)
async def event_overview_page(event_id: str) -> Response:
    return Response(
        _asset("event_overview.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/event-overview/assets/event-overview.js", include_in_schema=False)
async def event_overview_javascript() -> Response:
    return Response(_asset("event_overview.js"), media_type="text/javascript")


@access_router.get("/admin/speakers", include_in_schema=False)
@access_router.get("/admin/events/{event_id}/speakers", include_in_schema=False)
@access_router.get(
    "/admin/events/{event_id}/speakers/{event_speaker_id}", include_in_schema=False
)
async def speaker_directory_page(
    event_id: str | None = None, event_speaker_id: str | None = None
) -> Response:
    return Response(
        _asset("speaker_directory.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/events/{event_id}/messages", include_in_schema=False)
async def speaker_messages_page(event_id: str) -> Response:
    return Response(
        _asset("speaker_messages.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/speakers/assets/speakers.js", include_in_schema=False)
async def speaker_directory_javascript() -> Response:
    return Response(_asset("speaker_directory.js"), media_type="text/javascript")


@access_router.get("/admin/speakers/assets/messages.js", include_in_schema=False)
async def speaker_messages_javascript() -> Response:
    return Response(_asset("speaker_messages.js"), media_type="text/javascript")


@access_router.get("/account", include_in_schema=False)
async def account_page() -> Response:
    return Response(
        _asset("account.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/account/assets/account.js", include_in_schema=False)
async def account_javascript() -> Response:
    return Response(_asset("account.js"), media_type="text/javascript")


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
    admin_name: str = Field(min_length=1, max_length=200)
    admin_email: str = Field(min_length=3, max_length=320)
    admin_job_title: str | None = Field(default=None, max_length=200)
    admin_company: str | None = Field(default=None, max_length=200)
    admin_time_zone: str | None = Field(default=None, max_length=100)
    event_name: str | None = Field(default=None, min_length=1, max_length=200)
    starts_at_ms: int | None = Field(default=None, ge=0)
    ends_at_ms: int | None = Field(default=None, ge=0)
    time_zone: str | None = Field(default=None, min_length=1, max_length=100)
    event_location: str | None = Field(default=None, min_length=1, max_length=500)
    event_description: str | None = Field(default=None, min_length=1, max_length=2000)
    event_delivery_mode: Literal["in_person", "virtual", "hybrid"] | None = None

    @field_validator("admin_email")
    @classmethod
    def validate_admin_email(cls, value: str) -> str:
        return _validated_email(value)

    @model_validator(mode="after")
    def validate_optional_event(self) -> "BootstrapCreate":
        values = (
            self.event_name, self.starts_at_ms, self.ends_at_ms, self.time_zone,
            self.event_location, self.event_description, self.event_delivery_mode,
        )
        if any(value is not None for value in values):
            if any(value is None for value in values):
                raise ValueError("initial event details must be provided together")
            if (
                self.starts_at_ms is not None
                and self.ends_at_ms is not None
                and self.ends_at_ms <= self.starts_at_ms
            ):
                raise ValueError("event end must be after its start")
        return self


class BootstrapView(BaseModel):
    organization_id: str
    event_id: str | None
    admin_user_id: str


class SetupStatus(BaseModel):
    configured: bool


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=320)
    redirect_path: str = Field(default="/", max_length=500)
    form_slug: str | None = Field(default=None, min_length=3, max_length=80)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validated_email(value)


class GenericAccepted(BaseModel):
    accepted: bool = True


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=320)
    role: Literal["event_admin", "evaluator", "speaker"]
    expires_in_days: int = Field(default=14, ge=1, le=30)
    display_name: str = Field(default="", max_length=200)
    job_title: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validated_email(value)


class InvitationView(BaseModel):
    id: str
    event_id: str
    email: str
    role: Literal["event_admin", "evaluator", "speaker"]
    status: Literal["pending", "accepted", "revoked", "expired"] = "pending"


class InvitationCreatedView(InvitationView):
    accept_url: str | None = None


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
    location: str
    delivery_mode: Literal["in_person", "virtual", "hybrid"]
    description: str
    accent_color: str | None = None
    logo_url: str | None = None
    cover_image_url: str | None = None
    website_url: str | None = None
    email_sender_name: str | None = None
    email_reply_to: str | None = None
    status: Literal["draft", "active", "archived"]
    version: int


class EventList(BaseModel):
    data: list[EventView]


class EventLogoView(BaseModel):
    logo_url: str
    version: int


class EventCoverView(BaseModel):
    cover_image_url: str
    version: int


class EventBrandingAssetView(BaseModel):
    asset_url: str
    kind: Literal["logo", "cover"]


class EventCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    starts_at_ms: int = Field(ge=0)
    ends_at_ms: int = Field(ge=0)
    time_zone: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=500)
    delivery_mode: Literal["in_person", "virtual", "hybrid"]
    description: str = Field(min_length=1, max_length=2000)
    accent_color: str | None = Field(default="#3159d9", pattern=r"^#[0-9A-Fa-f]{6}$")
    logo_url: str | None = Field(default=None, max_length=2000)
    cover_image_url: str | None = Field(default=None, max_length=2000)
    website_url: str | None = Field(default=None, max_length=2000)
    email_sender_name: str | None = Field(default=None, max_length=200)
    email_reply_to: str | None = Field(default=None, max_length=320)

    @field_validator("website_url")
    @classmethod
    def validate_public_url(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("branding URLs must be absolute HTTPS URLs")
        return value

    @field_validator("logo_url", "cover_image_url")
    @classmethod
    def validate_logo_reference(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if value.startswith("/api/v1/public/event-assets/"):
            return value
        if value.startswith("/api/v1/public/events/") and any(
            marker in value for marker in ("/logo/", "/cover/")
        ):
            return value
        # Keep existing externally hosted logos editable while new logos move to R2.
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("logo must be an internal upload reference")
        return value

    @field_validator("email_sender_name", "email_reply_to", mode="before")
    @classmethod
    def empty_email_setting_is_inherited(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("email_reply_to")
    @classmethod
    def validate_reply_to_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validated_email(value)

    @model_validator(mode="after")
    def validate_event_times(self) -> "EventCreate":
        if self.ends_at_ms <= self.starts_at_ms:
            raise ValueError("event end must be after its start")
        return self


class EventUpdate(EventCreate):
    version: int = Field(ge=1)
    status: Literal["draft", "active", "archived"] = "active"


class SessionCreated(BaseModel):
    authenticated: bool = True
    user_id: str
    csrf_token: str
    redirect_path: str


class SessionOrganizationAccess(BaseModel):
    organization_id: str
    roles: list[Literal["organization_admin"]]


class SessionEventAccess(BaseModel):
    organization_id: str
    event_id: str
    roles: list[Literal["event_admin", "evaluator", "speaker"]]


class CurrentSession(BaseModel):
    authenticated: bool = True
    user_id: str
    email: str
    display_name: str | None = None
    csrf_token: str
    organization_id: str | None = None
    event_id: str | None = None
    organization_access: list[SessionOrganizationAccess] = Field(default_factory=list)
    event_access: list[SessionEventAccess] = Field(default_factory=list)


class AccountProfileView(BaseModel):
    email: str
    display_name: str | None = None
    job_title: str | None = None
    company: str | None = None
    time_zone: str | None = None
    version: int


class AccountProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    time_zone: str | None = Field(default=None, max_length=100)
    version: int = Field(ge=1)


def _valid_redirect(value: str) -> bool:
    return value.startswith("/") and not value.startswith("//") and "\\" not in value


def _validated_email(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("email must be one valid address")
    try:
        address = Address(addr_spec=value)
    except (IndexError, ValueError) as exc:
        raise ValueError("email must be one valid address") from exc
    if not address.username or not address.domain or "." not in address.domain:
        raise ValueError("email must be one valid address")
    return value


def _email(value: str) -> tuple[str, str]:
    display = _validated_email(value.strip())
    normalized = normalize_email(display)
    local, separator, domain = normalized.partition("@")
    if not separator or not local or "." not in domain or domain.startswith("."):
        raise HTTPException(status_code=422)
    return display, normalized


@access_router.get(
    "/api/v1/setup/status",
    response_model=SetupStatus,
    operation_id="getSetupStatus",
    tags=["administration"],
)
async def setup_status(request: Request) -> SetupStatus:
    return SetupStatus(configured=await setup_is_configured(database(request)))


@access_router.post("/api/v1/bootstrap", response_model=BootstrapView, tags=["administration"])
async def bootstrap_tenant(
    body: BootstrapCreate,
    request: Request,
    bootstrap_token: str | None = Header(default=None, alias="X-Bootstrap-Token"),
) -> BootstrapView:
    event_values = (
        body.event_name, body.starts_at_ms, body.ends_at_ms, body.time_zone,
        body.event_location, body.event_description, body.event_delivery_mode,
    )
    has_event = any(value is not None for value in event_values)
    if has_event:
        if (
            body.event_name is None
            or body.starts_at_ms is None
            or body.ends_at_ms is None
            or body.time_zone is None
            or body.event_location is None
            or body.event_description is None
            or body.event_delivery_mode is None
        ):
            raise HTTPException(status_code=422)
        if body.ends_at_ms <= body.starts_at_ms:
            raise HTTPException(status_code=422)
    db = database(request)
    if await setup_is_configured(db):
        raise HTTPException(status_code=409)
    credential = row_mapping(
        await db.prepare(
            """SELECT deployment_key FROM instance_setup_credentials
               WHERE singleton_key='primary'"""
        ).first()
    )
    expected = str(credential.get("deployment_key", "") if credential else "").encode()
    supplied = (bootstrap_token or "").encode()
    if len(expected) < 32 or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=404)
    now = utc_now_ms()
    organization_id, user_id = new_id(), new_id()
    event_id = new_id() if has_event else None
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO instance_setup (singleton_key,completed_at_ms)
               VALUES('primary',?1)"""
        ).bind(now)
    )
    batch.add_statement(
        db.prepare(
            "DELETE FROM instance_setup_credentials WHERE singleton_key='primary'"
        )
    )
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
           (id,email,normalized_email,display_name,job_title,company,time_zone,status,
            email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES(?1,?2,?3,?4,?5,?6,?7,'active',?8,?8,?8)"""
        ).bind(
            user_id,
            admin_email,
            normalized,
            body.admin_name,
            body.admin_job_title or None,
            body.admin_company or None,
            body.admin_time_zone or None,
            now,
        )
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
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,'active',?10,?10)"""
            ).bind(
                event_id,
                organization_id,
                body.event_name,
                body.starts_at_ms,
                body.ends_at_ms,
                body.time_zone,
                body.event_location,
                body.event_delivery_mode,
                body.event_description,
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
    try:
        await batch.execute()
    except PersistenceError:
        claimed = row_mapping(
            await db.prepare(
                "SELECT singleton_key FROM instance_setup WHERE singleton_key='primary'"
            ).first()
        )
        if claimed is not None:
            raise HTTPException(status_code=409) from None
        raise
    return BootstrapView(organization_id=organization_id, event_id=event_id, admin_user_id=user_id)


@access_router.get(
    "/api/v1/account/profile",
    response_model=AccountProfileView,
    operation_id="getAccountProfile",
    tags=["authentication"],
)
async def account_profile(request: Request) -> AccountProfileView:
    authenticated = await authenticate_request(request)
    row = row_mapping(
        await database(request)
        .prepare(
            """SELECT email,display_name,job_title,company,time_zone,version
               FROM users WHERE id=?1 AND status='active' LIMIT 1"""
        )
        .bind(authenticated.actor.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=401)
    return AccountProfileView(**row)


@access_router.patch(
    "/api/v1/account/profile",
    response_model=AccountProfileView,
    operation_id="updateAccountProfile",
    tags=["authentication"],
)
async def update_account_profile(
    body: AccountProfileUpdate, request: Request
) -> AccountProfileView:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    db, now = database(request), utc_now_ms()
    row = row_mapping(
        await db.prepare(
            """UPDATE users SET display_name=?1,job_title=?2,company=?3,time_zone=?4,
               version=version+1,updated_at_ms=?5
               WHERE id=?6 AND status='active' AND version=?7
               RETURNING email,display_name,job_title,company,time_zone,version"""
        )
        .bind(
            body.display_name,
            body.job_title or None,
            body.company or None,
            body.time_zone or None,
            now,
            authenticated.actor.user_id,
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
            action="account.profile.update",
            target_type="user",
            target_id=authenticated.actor.user_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
        )
    )
    await audit.execute()
    return AccountProfileView(**row)


@access_router.get(
    "/api/v1/admin/organizations",
    response_model=OrganizationList,
    tags=["administration"],
)
async def list_organizations(request: Request) -> OrganizationList:
    authenticated = await authenticate_request(request)
    db = database(request)
    organizations: list[OrganizationView] = []
    organization_ids = {
        organization_id
        for organization_id, roles in authenticated.actor.organization_roles.items()
        if Role.ORGANIZATION_ADMIN in roles
    }
    organization_ids.update(
        organization_id
        for (organization_id, _event_id), roles in authenticated.actor.event_roles.items()
        if Role.EVENT_ADMIN in roles
    )
    for organization_id in organization_ids:
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
                      delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                      email_sender_name,email_reply_to,status,version
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


def _event_logo_bucket(request: Request):
    bucket = getattr(request.scope.get("env"), "ASSETS", None)
    if bucket is None:
        raise HTTPException(status_code=503)
    return bucket


async def _read_event_logo(request: Request) -> tuple[bytes, str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    rule = _EVENT_LOGO_RULES.get(content_type)
    if rule is None:
        raise HTTPException(status_code=415)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _EVENT_LOGO_MAX_BYTES:
                raise HTTPException(status_code=413)
        except ValueError as exc:
            raise HTTPException(status_code=400) from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _EVENT_LOGO_MAX_BYTES:
            raise HTTPException(status_code=413)
        chunks.append(chunk)
    body = b"".join(chunks)
    extension, signature_matches = rule
    if not body or not signature_matches(body):
        raise HTTPException(status_code=415)
    return body, content_type, extension


async def _stream_event_logo(stored):
    body = getattr(stored, "body", stored)
    if isinstance(body, (bytes, bytearray, memoryview)):
        yield bytes(body)
        return
    reader_factory = getattr(body, "getReader", None)
    if callable(reader_factory):
        reader = reader_factory()
        try:
            while True:
                chunk = await reader.read()
                if bool(chunk.done):
                    break
                value = chunk.value
                converter = getattr(value, "to_py", None)
                yield bytes(converter() if callable(converter) else value)
        finally:
            release = getattr(reader, "releaseLock", None)
            if callable(release):
                release()


async def _require_event_branding_reference(
    db: D1Database,
    *,
    organization_id: str,
    kind: Literal["logo", "cover"],
    asset_url: str,
    event_id: str | None = None,
) -> None:
    if not asset_url.startswith("/api/v1/public/event-assets/"):
        raise HTTPException(status_code=422, detail=f"invalid event {kind} asset")
    row = row_mapping(
        await db.prepare(
            """SELECT id FROM event_branding_assets
               WHERE organization_id=?1 AND kind=?2 AND asset_url=?3
                 AND status IN ('pending','attached')
                 AND (event_id IS NULL OR event_id=?4)
               LIMIT 1"""
        )
        .bind(organization_id, kind, asset_url, event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=422, detail=f"invalid event {kind} asset")


@access_router.post(
    "/api/v1/admin/organizations/{organization_id}/event-assets/{kind}",
    response_model=EventBrandingAssetView,
    status_code=201,
    tags=["administration"],
)
async def upload_organization_event_asset(
    organization_id: str,
    kind: Literal["logo", "cover"],
    request: Request,
) -> EventBrandingAssetView:
    authenticated = await require_permission(
        request,
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(organization_id),
        mutation=True,
        mutation_media_types=_EVENT_IMAGE_MEDIA_TYPES,
    )
    body, content_type, extension = await _read_event_logo(request)
    environment = request.scope.get("env")
    asset_id = new_id()
    checksum = hashlib.sha256(body).digest()
    if not malware_scan_disabled(environment):
        try:
            scan = await SignedScannerAdapter(environment).scan(
                body,
                job=ScanJob(
                    schema_version=1,
                    organization_id=organization_id,
                    # A staged upload has no event yet. The opaque asset id gives
                    # the scanner a stable non-empty scope without inventing one.
                    event_id=asset_id,
                    asset_version_id=asset_id,
                    generation=1,
                    checksum_sha256=checksum,
                    job_id=asset_id,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=503) from exc
        if scan.verdict != "clean":
            raise HTTPException(status_code=400)
    object_key = f"public/event-branding/{organization_id}/{asset_id}.{extension}"
    asset_url = f"/api/v1/public/event-assets/{asset_id}.{extension}"
    await _event_logo_bucket(request).put(object_key, body)
    now = utc_now_ms()
    batch = CommandBatch(database(request))
    batch.add_statement(
        database(request)
        .prepare(
            """INSERT INTO event_branding_assets
               (id,organization_id,event_id,kind,object_key,asset_url,content_type,
                byte_size,checksum_sha256,status,created_by_user_id,created_at_ms)
               VALUES(?1,?2,NULL,?3,?4,?5,?6,?7,?8,'pending',?9,?10)"""
        )
        .bind(
            asset_id,
            organization_id,
            kind,
            object_key,
            asset_url,
            content_type,
            len(body),
            checksum,
            authenticated.actor.user_id,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="event.branding_asset.upload",
            target_type="event_branding_asset",
            target_id=asset_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            metadata={"kind": kind, "content_type": content_type, "byte_size": len(body)},
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return EventBrandingAssetView(asset_url=asset_url, kind=kind)


@access_router.get(
    "/api/v1/public/event-assets/{asset_name}",
    response_class=StreamingResponse,
    tags=["public-program"],
)
async def public_event_branding_asset(
    asset_name: str, request: Request
) -> StreamingResponse:
    suffix = asset_name[asset_name.rfind(".") :] if "." in asset_name else ""
    expected_content_type = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix)
    if expected_content_type is None:
        raise HTTPException(status_code=404)
    asset_url = f"/api/v1/public/event-assets/{asset_name}"
    row = row_mapping(
        await database(request)
        .prepare(
            """SELECT object_key,content_type FROM event_branding_assets
               WHERE asset_url=?1 AND status IN ('pending','attached','retired') LIMIT 1"""
        )
        .bind(asset_url)
        .first()
    )
    if row is None or str(row["content_type"]) != expected_content_type:
        raise HTTPException(status_code=404)
    stored = await _event_logo_bucket(request).get(str(row["object_key"]))
    if stored is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        _stream_event_logo(stored),
        media_type=expected_content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@access_router.post(
    "/api/v1/admin/events/{event_id}/logo",
    response_model=EventLogoView,
    tags=["administration"],
)
async def upload_event_logo(event_id: str, request: Request) -> EventLogoView:
    db = database(request)
    event = row_mapping(
        await db.prepare(
            "SELECT organization_id,version FROM events WHERE id=?1 LIMIT 1"
        ).bind(event_id).first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request,
        Permission.EVENT_MANAGE,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
        mutation_media_types=_EVENT_IMAGE_MEDIA_TYPES,
    )
    body, content_type, extension = await _read_event_logo(request)
    environment = request.scope.get("env")
    logo_id = new_id()
    checksum = hashlib.sha256(body).digest()
    if not malware_scan_disabled(environment):
        try:
            scan = await SignedScannerAdapter(environment).scan(
                body,
                job=ScanJob(
                    schema_version=1,
                    organization_id=str(event["organization_id"]),
                    event_id=event_id,
                    asset_version_id=logo_id,
                    generation=1,
                    checksum_sha256=checksum,
                    job_id=logo_id,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=503) from exc
        if scan.verdict != "clean":
            raise HTTPException(status_code=400)
    organization_id = str(event["organization_id"])
    object_key = f"public/event-branding/{organization_id}/{logo_id}.{extension}"
    await _event_logo_bucket(request).put(object_key, body)
    logo_url = f"/api/v1/public/event-assets/{logo_id}.{extension}"
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_branding_assets
               (id,organization_id,event_id,kind,object_key,asset_url,content_type,
                byte_size,checksum_sha256,status,created_by_user_id,created_at_ms)
               VALUES(?1,?2,NULL,'logo',?3,?4,?5,?6,?7,'pending',?8,?9)"""
        ).bind(
            logo_id,
            organization_id,
            object_key,
            logo_url,
            content_type,
            len(body),
            checksum,
            authenticated.actor.user_id,
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE events SET logo_url=?1,version=version+1,updated_at_ms=?2
               WHERE id=?3 AND version=?4"""
        ).bind(logo_url, now, event_id, event["version"])
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="event.logo.upload",
            target_type="event",
            target_id=event_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={"content_type": content_type, "byte_size": len(body)},
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return EventLogoView(logo_url=logo_url, version=int(event["version"]) + 1)


@access_router.post(
    "/api/v1/admin/events/{event_id}/cover",
    response_model=EventCoverView,
    tags=["administration"],
)
async def upload_event_cover(event_id: str, request: Request) -> EventCoverView:
    db = database(request)
    event = row_mapping(
        await db.prepare(
            "SELECT organization_id,version FROM events WHERE id=?1 LIMIT 1"
        ).bind(event_id).first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request, Permission.EVENT_MANAGE,
        ResourceContext(str(event["organization_id"]), event_id), mutation=True,
        mutation_media_types=_EVENT_IMAGE_MEDIA_TYPES,
    )
    body, content_type, extension = await _read_event_logo(request)
    environment = request.scope.get("env")
    image_id = new_id()
    checksum = hashlib.sha256(body).digest()
    if not malware_scan_disabled(environment):
        try:
            scan = await SignedScannerAdapter(environment).scan(
                body,
                job=ScanJob(
                    schema_version=1, organization_id=str(event["organization_id"]),
                    event_id=event_id, asset_version_id=image_id, generation=1,
                    checksum_sha256=checksum, job_id=image_id,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=503) from exc
        if scan.verdict != "clean":
            raise HTTPException(status_code=400)
    organization_id = str(event["organization_id"])
    object_key = f"public/event-branding/{organization_id}/{image_id}.{extension}"
    await _event_logo_bucket(request).put(object_key, body)
    image_url = f"/api/v1/public/event-assets/{image_id}.{extension}"
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_branding_assets
               (id,organization_id,event_id,kind,object_key,asset_url,content_type,
                byte_size,checksum_sha256,status,created_by_user_id,created_at_ms)
               VALUES(?1,?2,NULL,'cover',?3,?4,?5,?6,?7,'pending',?8,?9)"""
        ).bind(
            image_id,
            organization_id,
            object_key,
            image_url,
            content_type,
            len(body),
            checksum,
            authenticated.actor.user_id,
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE events SET cover_image_url=?1,version=version+1,updated_at_ms=?2
               WHERE id=?3 AND version=?4"""
        ).bind(image_url, now, event_id, event["version"])
    )
    batch.audit(AuditEvent(
        actor_type="user", actor_user_id=authenticated.actor.user_id,
        action="event.cover.upload", target_type="event", target_id=event_id,
        result="succeeded", correlation_id=request.state.request_id, occurred_at_ms=now,
        organization_id=organization_id, event_id=event_id,
        metadata={"content_type": content_type, "byte_size": len(body)},
    ))
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return EventCoverView(cover_image_url=image_url, version=int(event["version"]) + 1)


@access_router.get(
    "/api/v1/public/events/{event_id}/logo/{logo_name}",
    response_class=StreamingResponse,
    tags=["public-program"],
)
async def public_event_logo(event_id: str, logo_name: str, request: Request) -> StreamingResponse:
    logo_url = f"/api/v1/public/events/{event_id}/logo/{logo_name}"
    row = row_mapping(
        await database(request).prepare(
            "SELECT organization_id,logo_url FROM events WHERE id=?1 AND logo_url=?2 LIMIT 1"
        ).bind(event_id, logo_url).first()
    )
    if row is None or not logo_name.endswith((".jpg", ".png", ".webp")):
        raise HTTPException(status_code=404)
    content_type = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }[logo_name[logo_name.rfind("."):]]
    object_key = f"public/event-logos/{row['organization_id']}/{event_id}/{logo_name}"
    stored = await _event_logo_bucket(request).get(object_key)
    if stored is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        _stream_event_logo(stored),
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@access_router.get(
    "/api/v1/public/events/{event_id}/cover/{image_name}",
    response_class=StreamingResponse,
    tags=["public-program"],
)
async def public_event_cover(event_id: str, image_name: str, request: Request) -> StreamingResponse:
    image_url = f"/api/v1/public/events/{event_id}/cover/{image_name}"
    row = row_mapping(
        await database(request).prepare(
            """SELECT organization_id,cover_image_url FROM events
               WHERE id=?1 AND cover_image_url=?2 LIMIT 1"""
        ).bind(event_id, image_url).first()
    )
    if row is None or not image_name.endswith((".jpg", ".png", ".webp")):
        raise HTTPException(status_code=404)
    suffix = image_name[image_name.rfind("."):]
    content_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[suffix]
    stored = await _event_logo_bucket(request).get(
        f"public/event-covers/{row['organization_id']}/{event_id}/{image_name}"
    )
    if stored is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        _stream_event_logo(stored), media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


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
    if body.logo_url is not None:
        await _require_event_branding_reference(
            db, organization_id=organization_id, kind="logo", asset_url=body.logo_url
        )
    if body.cover_image_url is not None:
        await _require_event_branding_reference(
            db,
            organization_id=organization_id,
            kind="cover",
            asset_url=body.cover_image_url,
        )
    if body.starts_at_ms <= now:
        raise HTTPException(status_code=422, detail="a new event must start in the future")
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                email_sender_name,email_reply_to,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,'active',?16,?16)"""
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
            body.accent_color,
            body.logo_url,
            body.cover_image_url,
            body.website_url,
            body.email_sender_name,
            body.email_reply_to,
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
        await db.prepare(
            """SELECT organization_id,logo_url,cover_image_url
               FROM events WHERE id=?1 LIMIT 1"""
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
    current_logo_url = str(event["logo_url"]) if event["logo_url"] is not None else None
    logo_url = body.logo_url if "logo_url" in body.model_fields_set else current_logo_url
    if logo_url != current_logo_url and logo_url is not None:
        await _require_event_branding_reference(
            db,
            organization_id=str(event["organization_id"]),
            kind="logo",
            asset_url=logo_url,
            event_id=event_id,
        )
    current_cover_url = (
        str(event["cover_image_url"]) if event["cover_image_url"] is not None else None
    )
    cover_image_url = (
        body.cover_image_url
        if "cover_image_url" in body.model_fields_set
        else current_cover_url
    )
    if cover_image_url != current_cover_url and cover_image_url is not None:
        await _require_event_branding_reference(
            db,
            organization_id=str(event["organization_id"]),
            kind="cover",
            asset_url=cover_image_url,
            event_id=event_id,
        )
    now = utc_now_ms()
    archived_at_ms = now if body.status == "archived" else None
    row = row_mapping(
        await db.prepare(
            """UPDATE events SET name=?1,starts_at_ms=?2,ends_at_ms=?3,time_zone=?4,
               location=?5,delivery_mode=?6,description=?7,accent_color=?8,logo_url=?9,
               cover_image_url=?10,website_url=?11,email_sender_name=?12,email_reply_to=?13,status=?14,
               archived_at_ms=?15,version=version+1,updated_at_ms=?16
               WHERE id=?17 AND version=?18
               RETURNING id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                         delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                         email_sender_name,email_reply_to,status,version"""
        )
        .bind(
            body.name,
            body.starts_at_ms,
            body.ends_at_ms,
            body.time_zone,
            body.location,
            body.delivery_mode,
            body.description,
            body.accent_color,
            logo_url,
            cover_image_url,
            body.website_url,
            body.email_sender_name,
            body.email_reply_to,
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
    response_model=InvitationCreatedView,
    status_code=201,
    tags=["administration"],
)
async def create_invitation(
    event_id: str, body: InvitationCreate, request: Request
) -> InvitationCreatedView:
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
          expires_at_ms,created_at_ms,updated_at_ms,display_name,job_title,company)
         VALUES(?1,?2,?3,?4,?5,?6,'pending',?7,?8,?9,?9,?10,?11,?12)
         ON CONFLICT(organization_id,event_id,normalized_email,role) DO UPDATE SET
           email=excluded.email,status='pending',invited_by_user_id=excluded.invited_by_user_id,
           expires_at_ms=excluded.expires_at_ms,accepted_at_ms=NULL,revoked_at_ms=NULL,
           updated_at_ms=excluded.updated_at_ms,display_name=excluded.display_name,
           job_title=excluded.job_title,company=excluded.company"""
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
            body.display_name,
            body.job_title,
            body.company,
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
    accept_url = await _issue_invitation_link(
        request,
        invitation_id=str(row["id"]),
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        email=email,
        normalized_email=normalized,
        role=body.role,
        now=now,
    )
    return InvitationCreatedView(**row, accept_url=accept_url)


async def _issue_invitation_link(
    request: Request,
    *,
    invitation_id: str,
    organization_id: str,
    event_id: str,
    email: str,
    normalized_email: str,
    role: Literal["event_admin", "evaluator", "speaker"],
    now: int,
) -> str | None:
    """Create a short-lived, one-time acceptance link and queue its delivery."""
    base = str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "")).rstrip("/")
    parsed = urlparse(base)
    is_local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if parsed.scheme != "https" and not is_local:
        return None
    destination = {
        "speaker": "/speaker",
        "evaluator": "/reviews",
        "event_admin": "/admin",
    }[role]
    db = database(request)
    user_id = await (
        db.prepare("SELECT id FROM users WHERE normalized_email=?1 AND status='active' LIMIT 1")
        .bind(normalized_email)
        .first("id")
    )
    raw_token, challenge_id, message_id = generate_token(), new_id(), new_id()
    accept_url = f"{base}/auth/verify?token={raw_token}"
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO authentication_challenges
               (id,normalized_email,token_hash,purpose,provisioning_context,redirect_path,
                expires_at_ms,created_at_ms,user_id,organization_id,event_id,invitation_id)
               VALUES(?1,?2,?3,'sign_in','invitation',?4,?5,?6,?7,?8,?9,?10)"""
        ).bind(
            challenge_id,
            normalized_email,
            hash_token(raw_token),
            destination,
            now + 15 * 60 * 1000,
            now,
            user_id,
            organization_id,
            event_id,
            invitation_id,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'You are invited to SessionBuddy',?5,?6,'queued',?7,?7)"""
        ).bind(
            message_id,
            organization_id,
            event_id,
            email,
            f'<p><a href="{escape(accept_url)}">Accept your SessionBuddy invitation</a></p>',
            f"identity-invitation:{invitation_id}:{challenge_id}",
            now,
        )
    )
    await batch.execute()
    queue = getattr(request.scope.get("env"), "COMMUNICATION_QUEUE", None)
    if queue is not None:
        await queue.send({"schema_version": 1, "message_id": message_id})
    return accept_url


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


@access_router.post(
    "/api/v1/admin/events/{event_id}/invitations/{invitation_id}/resend",
    response_model=InvitationCreatedView,
    tags=["administration"],
)
async def resend_invitation(
    event_id: str, invitation_id: str, request: Request
) -> InvitationCreatedView:
    db, organization_id, authenticated = await _managed_event(request, event_id, mutation=True)
    now = utc_now_ms()
    row = row_mapping(
        await db.prepare(
            """SELECT id,event_id,email,normalized_email,role,status
               FROM identity_invitations
               WHERE id=?1 AND organization_id=?2 AND event_id=?3
                 AND status='pending' AND expires_at_ms>?4 LIMIT 1"""
        )
        .bind(invitation_id, organization_id, event_id, now)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    role: Literal["event_admin", "evaluator", "speaker"] = row["role"]
    accept_url = await _issue_invitation_link(
        request,
        invitation_id=invitation_id,
        organization_id=organization_id,
        event_id=event_id,
        email=str(row["email"]),
        normalized_email=str(row["normalized_email"]),
        role=role,
        now=now,
    )
    audit = CommandBatch(db)
    audit.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="identity.invitation.resend",
            target_type="identity_invitation",
            target_id=invitation_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={"role": role},
        )
    )
    await audit.execute()
    return InvitationCreatedView(**row, accept_url=accept_url)


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


@access_router.post(
    "/api/v1/forms/{slug}/access",
    response_model=GenericAccepted,
    tags=["authentication"],
)
async def ensure_cfp_speaker_access(slug: str, request: Request) -> GenericAccepted:
    """Enroll an authenticated user into an open public CFP without another sign-in."""
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    db, now = database(request), utc_now_ms()
    form = row_mapping(
        await db.prepare(
            """SELECT organization_id,event_id FROM call_for_speaker_forms
               WHERE slug=?1 AND status='published' LIMIT 1"""
        )
        .bind(slug)
        .first()
    )
    if form is None:
        raise HTTPException(status_code=404)
    email = await (
        db.prepare("SELECT email FROM users WHERE id=?1 AND status='active' LIMIT 1")
        .bind(authenticated.actor.user_id)
        .first("email")
    )
    if email is None:
        raise HTTPException(status_code=404)
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,'member','active',?4,?4)
               ON CONFLICT(organization_id,user_id) DO UPDATE SET status='active',
                 revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
        ).bind(new_id(), form["organization_id"], authenticated.actor.user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'speaker','active',?5,?5)
               ON CONFLICT(organization_id,event_id,user_id,role) DO UPDATE SET status='active',
                 revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
        ).bind(
            new_id(),
            form["organization_id"],
            form["event_id"],
            authenticated.actor.user_id,
            now,
        )
    )
    await _add_speaker_profile(
        batch,
        db,
        organization_id=str(form["organization_id"]),
        event_id=str(form["event_id"]),
        user_id=authenticated.actor.user_id,
        email=str(email),
        now=now,
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="cfp.speaker_access.ensure",
            target_type="event",
            target_id=str(form["event_id"]),
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(form["organization_id"]),
            event_id=str(form["event_id"]),
        )
    )
    await batch.execute()
    return GenericAccepted()


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
    audit = CommandBatch(db)
    # A plain organization `member` row is only an affiliation anchor. Once
    # this person has no active event role in the organization, revoke that
    # otherwise-empty affiliation as part of offboarding. Historical rows and
    # their foreign-key relationships remain intact.
    audit.add_statement(
        db.prepare(
            """UPDATE organization_memberships
               SET status='revoked',revoked_at_ms=?1,version=version+1,updated_at_ms=?1
               WHERE organization_id=?2 AND user_id=?3 AND role='member' AND status='active'
                 AND NOT EXISTS (
                   SELECT 1 FROM event_memberships em
                   WHERE em.organization_id=?2 AND em.user_id=?3 AND em.status='active'
                 )"""
        ).bind(now, organization_id, user_id)
    )
    audit.add_statement(
        db.prepare(
            """UPDATE users SET authorization_version=authorization_version+1,
               updated_at_ms=?1 WHERE id=?2"""
        ).bind(now, user_id)
    )
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
    if body.form_slug is not None:
        submission_context = row_mapping(
            await db.prepare(
                """SELECT organization_id,event_id FROM call_for_speaker_forms
                   WHERE slug=?1 AND status='published' LIMIT 1"""
            )
            .bind(body.form_slug)
            .first()
        )
    if invitation is not None:
        context = invitation
        provisioning_context = "invitation"
    elif submission_context is not None:
        context = submission_context
        provisioning_context = "submission"
    elif user is not None:
        context = user
        provisioning_context = "existing_user"
    else:
        return GenericAccepted()
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
            provisioning_context,
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
                """SELECT id,organization_id,event_id,email,normalized_email,role,
                          display_name,job_title,company
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
                display_name=str(invitation["display_name"] or ""),
                job_title=str(invitation["job_title"] or ""),
                company=str(invitation["company"] or ""),
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
    elif challenge["provisioning_context"] == "submission":
        existing_user = row_mapping(
            await db.prepare("SELECT id FROM users WHERE normalized_email=?1 LIMIT 1")
            .bind(challenge["normalized_email"])
            .first()
        )
        user_id = str(existing_user["id"]) if existing_user is not None else new_id()
        batch = CommandBatch(db)
        if existing_user is None:
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
                   VALUES(?1,?2,?3,'member','active',?4,?4)
                   ON CONFLICT(organization_id,user_id) DO UPDATE SET status='active',
                     revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
            ).bind(new_id(), challenge["organization_id"], user_id, now)
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_memberships
                   (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,'speaker','active',?5,?5)
                   ON CONFLICT(organization_id,event_id,user_id,role) DO UPDATE SET status='active',
                     revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
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
    display_name: str = "",
    job_title: str = "",
    company: str = "",
) -> None:
    person = row_mapping(
        await db.prepare("SELECT id FROM people WHERE organization_id=?1 AND user_id=?2 LIMIT 1")
        .bind(organization_id, user_id)
        .first()
    )
    person_id = str(person["id"]) if person is not None else new_id()
    if person is None:
        display_name = display_name or email.partition("@")[0].strip()[:200] or "Speaker"
        batch.add_statement(
            db.prepare(
                """INSERT INTO people
                   (id,organization_id,user_id,display_name,job_title,company,
                    created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?7)"""
            ).bind(person_id, organization_id, user_id, display_name, job_title, company, now)
        )
    elif any((display_name, job_title, company)):
        batch.add_statement(
            db.prepare(
                """UPDATE people SET display_name=COALESCE(NULLIF(?1,''),display_name),
                          job_title=COALESCE(NULLIF(?2,''),job_title),
                          company=COALESCE(NULLIF(?3,''),company),updated_at_ms=?4,
                          version=version+1 WHERE id=?5 AND organization_id=?6"""
            ).bind(display_name, job_title, company, now, person_id, organization_id)
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
                    last_activity_at_ms,created_at_ms,updated_at_ms,selection_status)
                   VALUES(?1,?2,?3,?4,'onboarding',?5,?5,?5,?5,'submitted')"""
            ).bind(new_id(), organization_id, event_id, person_id, now)
        )


@access_router.get("/auth/verify", include_in_schema=False)
async def verify_magic_link_in_browser(token: str = "", *, request: Request) -> Response:
    cookie_response = Response()
    try:
        session = await verify_magic_link(token, request, cookie_response)
    except HTTPException as exception:
        if exception.status_code != 404:
            raise
        return Response(
            _asset("auth_link_error.html"),
            media_type="text/html",
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    response = RedirectResponse(session.redirect_path, status_code=303)
    for key, value in cookie_response.raw_headers:
        if key.lower() == b"set-cookie":
            response.raw_headers.append((key, value))
    return response


@access_router.get("/api/v1/auth/session", response_model=CurrentSession, tags=["authentication"])
async def current_session(request: Request) -> CurrentSession:
    authenticated = await authenticate_request(request)
    user = row_mapping(
        await database(request)
        .prepare("SELECT email,display_name FROM users WHERE id=?1 LIMIT 1")
        .bind(authenticated.actor.user_id)
        .first()
    )
    if user is None:
        raise HTTPException(status_code=401)
    organization_id = next(iter(authenticated.actor.organization_roles), None)
    event_scope = next(iter(authenticated.actor.event_roles), None)
    if organization_id is None and event_scope is not None:
        organization_id = event_scope[0]
    organization_access = [
        SessionOrganizationAccess(
            organization_id=scope_organization_id,
            roles=[role.value for role in sorted(roles, key=lambda item: item.value)],
        )
        for scope_organization_id, roles in sorted(authenticated.actor.organization_roles.items())
    ]
    event_access = [
        SessionEventAccess(
            organization_id=scope_organization_id,
            event_id=scope_event_id,
            roles=[role.value for role in sorted(roles, key=lambda item: item.value)],
        )
        for (scope_organization_id, scope_event_id), roles in sorted(
            authenticated.actor.event_roles.items()
        )
    ]
    return CurrentSession(
        user_id=authenticated.actor.user_id,
        email=str(user["email"]),
        display_name=str(user["display_name"]) if user["display_name"] is not None else None,
        csrf_token=issue_csrf_token(authenticated.session_id, secret(request, "CSRF_HMAC_KEY")),
        organization_id=organization_id,
        event_id=event_scope[1] if event_scope is not None else None,
        organization_access=organization_access,
        event_access=event_access,
    )

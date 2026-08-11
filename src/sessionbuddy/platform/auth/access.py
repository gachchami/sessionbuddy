"""Production passwordless access and one-time tenant bootstrap."""

import binascii
import hashlib
import hmac
import json
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from email.headerregistry import Address
from email.utils import parseaddr
from html import escape
from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sessionbuddy.console import embedded_assets
from sessionbuddy.platform.authorization import Permission, ResourceContext, Role
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch, IdempotencyRecord
from sessionbuddy.platform.db.d1 import (
    D1Database,
    PersistenceError,
    result_rows,
    row_mapping,
    to_python,
)
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit
from sessionbuddy.platform.storage import malware_scan_disabled
from sessionbuddy.speaker_operations.asset_boundary import ScanJob
from sessionbuddy.speaker_operations.scanner_adapter import SignedScannerAdapter

from .cookies import sign_session_cookie
from .csrf import issue_csrf_token
from .http import (
    authenticate_request,
    database,
    environment,
    guard_mutation,
    require_permission,
    secret,
)
from .passwords import PasswordPolicyError, hash_password, verify_password
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
_HEADSHOT_MAX_BYTES = 5 * 1024 * 1024
_IANA_TIME_ZONE = re.compile(
    r"^(?:UTC|[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9][A-Za-z0-9._+-]*)+)$"
)


def _validated_time_zone(value: str) -> str:
    """Require an explicit IANA-style zone, never an ambiguous abbreviation or offset."""
    if not _IANA_TIME_ZONE.fullmatch(value) or ".." in value:
        raise ValueError("time_zone must be UTC or an IANA time zone such as Asia/Kolkata")
    return value

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


@access_router.get("/speakers/{person_id}", include_in_schema=False)
async def speaker_profile_page(person_id: str) -> Response:
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

    @field_validator("time_zone")
    @classmethod
    def validate_event_time_zone(cls, value: str | None) -> str | None:
        return _validated_time_zone(value) if value is not None else None

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


InvitationRole = Literal["organization_admin", "event_admin", "evaluator", "speaker"]


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(min_length=3, max_length=320)
    role: InvitationRole
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
    role: InvitationRole
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
    proposal_count: int = Field(default=0, ge=0)
    pending_review_count: int = Field(default=0, ge=0)
    schedule_status: Literal[
        "not_started", "draft", "ready", "published", "updates_pending"
    ] = "not_started"


class EventList(BaseModel):
    data: list[EventView]
    next_cursor: str | None = None


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

    @field_validator("time_zone")
    @classmethod
    def validate_event_time_zone(cls, value: str) -> str:
        return _validated_time_zone(value)

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


class EventCreateRequest(EventCreate):
    status: Literal["draft", "active"] = "active"


class EventUpdate(EventCreate):
    version: int = Field(ge=1)
    # None keeps the stored status: a client that omits it must never silently
    # resurrect an archived event.
    status: Literal["draft", "active", "archived"] | None = None


class EventDuplicateCreate(EventCreate):
    source_version: int = Field(ge=1)
    status: Literal["draft", "active"] = "draft"
    retain_source_logo: bool = False
    retain_source_cover: bool = False

    @model_validator(mode="after")
    def validate_duplicate_branding_choice(self) -> "EventDuplicateCreate":
        if self.retain_source_logo and self.logo_url is not None:
            raise ValueError("choose either the source logo or a replacement logo")
        if self.retain_source_cover and self.cover_image_url is not None:
            raise ValueError("choose either the source cover or a replacement cover")
        return self


class SessionCreated(BaseModel):
    authenticated: bool = True
    user_id: str
    csrf_token: str
    redirect_path: str


class PasswordSignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    redirect_path: str = "/"


class ActiveRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["organizer", "reviewer", "speaker"]


class DefaultRoleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["organizer", "reviewer", "speaker"]


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
    profile_complete: bool = False
    csrf_token: str
    default_email_sender_name: str
    default_email_address: str
    account_roles: list[Literal["organizer", "reviewer", "speaker"]] = Field(
        default_factory=list
    )
    active_role: Literal["organizer", "reviewer", "speaker"] | None = None
    default_role: Literal["organizer", "reviewer", "speaker"] | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    event_id: str | None = None
    organization_access: list[SessionOrganizationAccess] = Field(default_factory=list)
    event_access: list[SessionEventAccess] = Field(default_factory=list)


def _role_destination(role: str | None) -> str:
    return {
        "organizer": "/admin",
        "reviewer": "/reviews",
        "speaker": "/speaker",
    }.get(role, "/account")


async def _default_account_role(db, user_id: str) -> str | None:
    row = row_mapping(
        await db.prepare(
            """SELECT role FROM user_roles
               WHERE user_id=?1 AND status='active'
               ORDER BY is_default DESC,
                        CASE role WHEN 'organizer' THEN 1 WHEN 'reviewer' THEN 2 ELSE 3 END
               LIMIT 1"""
        ).bind(user_id).first()
    )
    return str(row["role"]) if row is not None else None


class AccountProfileView(BaseModel):
    email: str
    first_name: str | None = None
    last_name: str | None = None
    display_name: str | None = None
    job_title: str | None = None
    company: str | None = None
    time_zone: str | None = None
    description: str | None = None
    website_url: str | None = None
    linkedin_url: str | None = None
    x_url: str | None = None
    headshot_url: str | None = None
    profile_complete: bool
    roles: list[Literal["organizer", "reviewer", "speaker"]] = Field(default_factory=list)
    has_password: bool = False
    version: int


class AccountProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    job_title: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    time_zone: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    website_url: str | None = Field(default=None, max_length=500)
    linkedin_url: str | None = Field(default=None, max_length=500)
    x_url: str | None = Field(default=None, max_length=500)
    password: str | None = Field(default=None, min_length=1, max_length=128)
    password_confirmation: str | None = Field(default=None, min_length=1, max_length=128)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def matching_optional_password(self) -> "AccountProfileUpdate":
        if (self.password is None) != (self.password_confirmation is None):
            raise ValueError("password and password_confirmation must be provided together")
        if self.password is not None and self.password != self.password_confirmation:
            raise ValueError("password confirmation does not match")
        return self

    @field_validator("website_url", "linkedin_url", "x_url")
    @classmethod
    def secure_profile_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("profile URLs must be HTTPS URLs without credentials")
        return value


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
    db = database(request)
    row = row_mapping(
        await db
        .prepare(
            """SELECT u.email,u.first_name,u.last_name,u.display_name,u.job_title,u.company,
                      u.time_zone,u.description,u.website_url,u.linkedin_url,u.x_url,u.version,
                      u.profile_completed_at_ms IS NOT NULL AS profile_complete,
                      CASE WHEN h.user_id IS NULL THEN NULL
                           ELSE '/api/v1/account/headshot' END AS headshot_url,
                      EXISTS(SELECT 1 FROM password_credentials c
                             WHERE c.user_id=u.id AND c.status='active') AS has_password
               FROM users u LEFT JOIN user_headshots h ON h.user_id=u.id
               WHERE u.id=?1 AND u.status='active' LIMIT 1"""
        )
        .bind(authenticated.actor.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=401)
    roles = result_rows(
        await db.prepare(
            """SELECT role FROM user_roles
               WHERE user_id=?1 AND status='active' ORDER BY role"""
        ).bind(authenticated.actor.user_id).all()
    )
    return AccountProfileView(**row, roles=[str(item["role"]) for item in roles])


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
    current = row_mapping(
        await db.prepare(
            "SELECT version FROM users WHERE id=?1 AND status='active' LIMIT 1"
        ).bind(authenticated.actor.user_id).first()
    )
    if current is None or int(current["version"]) != body.version:
        raise HTTPException(status_code=409)
    verifier = None
    if body.password is not None:
        try:
            verifier = hash_password(body.password, secret(request, "PASSWORD_PEPPER"))
        except PasswordPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    display_name = f"{body.first_name} {body.last_name}".strip()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE users SET first_name=?1,last_name=?2,display_name=?3,job_title=?4,
               company=?5,time_zone=?6,description=?7,website_url=?8,linkedin_url=?9,x_url=?10,
               profile_completed_at_ms=COALESCE(profile_completed_at_ms,?11),
               authorization_version=authorization_version+?12,
               version=version+1,updated_at_ms=?11
               WHERE id=?13 AND status='active' AND version=?14"""
        ).bind(
            body.first_name,
            body.last_name,
            display_name,
            body.job_title or None,
            body.company or None,
            body.time_zone or None,
            body.description or None,
            body.website_url or None,
            body.linkedin_url or None,
            body.x_url or None,
            now,
            1 if verifier is not None else 0,
            authenticated.actor.user_id,
            body.version,
        )
    )
    if verifier is not None:
        batch.add_statement(
            db.prepare(
                """INSERT INTO password_credentials
                   (user_id,verifier_phc,pepper_version,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,1,'active',?3,?3)
                   ON CONFLICT(user_id) DO UPDATE SET verifier_phc=excluded.verifier_phc,
                     pepper_version=1,status='active',updated_at_ms=?3"""
            ).bind(authenticated.actor.user_id, verifier, now)
        )
        batch.add_statement(
            db.prepare(
                """UPDATE sessions SET revoked_at_ms=?1,revoke_reason='password_changed'
                   WHERE user_id=?2 AND revoked_at_ms IS NULL"""
            ).bind(now, authenticated.actor.user_id)
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="account.profile_and_password.update" if verifier else "account.profile.update",
            target_type="user",
            target_id=authenticated.actor.user_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
        )
    )
    await batch.execute()
    return await account_profile(request)


async def _read_headshot(request: Request) -> tuple[bytes, str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    rule = _EVENT_LOGO_RULES.get(content_type)
    if rule is None:
        raise HTTPException(status_code=415)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _HEADSHOT_MAX_BYTES:
                raise HTTPException(status_code=413)
        except ValueError as exc:
            raise HTTPException(status_code=400) from exc
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > _HEADSHOT_MAX_BYTES:
            raise HTTPException(status_code=413)
        chunks.append(chunk)
    body = b"".join(chunks)
    extension, signature_matches = rule
    if not body or not signature_matches(body):
        raise HTTPException(status_code=415)
    return body, content_type, extension


@access_router.put(
    "/api/v1/account/headshot",
    status_code=204,
    tags=["authentication"],
)
async def upload_account_headshot(request: Request) -> Response:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id, _EVENT_IMAGE_MEDIA_TYPES)
    body, content_type, extension = await _read_headshot(request)
    environment = request.scope.get("env")
    checksum = hashlib.sha256(body).digest()
    if not malware_scan_disabled(environment):
        asset_id = new_id()
        try:
            scan = await SignedScannerAdapter(environment).scan(
                body,
                job=ScanJob(
                    schema_version=1,
                    organization_id=authenticated.actor.user_id,
                    event_id=authenticated.actor.user_id,
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
    object_key = f"private/user-headshots/{authenticated.actor.user_id}.{extension}"
    await _event_logo_bucket(request).put(object_key, body)
    now = utc_now_ms()
    batch = CommandBatch(database(request))
    batch.add_statement(
        database(request).prepare(
            """INSERT INTO user_headshots
               (user_id,object_key,content_type,byte_size,checksum_sha256,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6)
               ON CONFLICT(user_id) DO UPDATE SET object_key=excluded.object_key,
                 content_type=excluded.content_type,byte_size=excluded.byte_size,
                 checksum_sha256=excluded.checksum_sha256,updated_at_ms=excluded.updated_at_ms"""
        ).bind(authenticated.actor.user_id, object_key, content_type, len(body), checksum, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="account.headshot.update",
            target_type="user",
            target_id=authenticated.actor.user_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"content_type": content_type, "byte_size": len(body)},
        )
    )
    await batch.execute()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@access_router.get(
    "/api/v1/account/headshot",
    response_class=StreamingResponse,
    tags=["authentication"],
)
async def account_headshot(request: Request) -> StreamingResponse:
    authenticated = await authenticate_request(request)
    row = row_mapping(
        await database(request).prepare(
            "SELECT object_key,content_type FROM user_headshots WHERE user_id=?1 LIMIT 1"
        ).bind(authenticated.actor.user_id).first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    stored = await _event_logo_bucket(request).get(str(row["object_key"]))
    if stored is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        _stream_event_logo(stored),
        media_type=str(row["content_type"]),
        headers={"Cache-Control": "private, no-store"},
    )


@access_router.delete(
    "/api/v1/account/headshot",
    status_code=204,
    tags=["authentication"],
)
async def delete_account_headshot(request: Request) -> Response:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    db = database(request)
    row = row_mapping(
        await db.prepare("SELECT object_key FROM user_headshots WHERE user_id=?1 LIMIT 1")
        .bind(authenticated.actor.user_id).first()
    )
    if row is not None:
        await db.prepare("DELETE FROM user_headshots WHERE user_id=?1").bind(
            authenticated.actor.user_id
        ).run()
        await _event_logo_bucket(request).delete(str(row["object_key"]))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


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
    if organization_ids:
        ordered_ids = sorted(organization_ids)
        placeholders = ",".join(f"?{index + 1}" for index in range(len(ordered_ids)))
        result = await (
            db.prepare(
                # Placeholder-only interpolation; every value is bound.
                f"SELECT id,name,status,version FROM organizations WHERE id IN ({placeholders})"  # noqa: S608, E501
            )
            .bind(*ordered_ids)
            .all()
        )
        organizations = [OrganizationView(**row) for row in result_rows(result)]
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


EVENTS_PAGE_LIMIT = 50
EVENT_LIST_VIEWS = frozenset({"all", "active", "draft", "past"})
EVENT_LIST_ORDERS = frozenset({"recent", "upcoming"})
_EVENTS_CURSOR_TTL_MS = 15 * 60 * 1000


@access_router.get(
    "/api/v1/admin/organizations/{organization_id}/events",
    response_model=EventList,
    tags=["administration"],
)
async def list_events(
    organization_id: str,
    request: Request,
    cursor: str | None = None,
    limit: int = EVENTS_PAGE_LIMIT,
    view: str = "all",
    q: str = "",
    order: str = "recent",
) -> EventList:
    authenticated = await authenticate_request(request)
    organization_roles = authenticated.actor.organization_roles.get(organization_id, frozenset())
    is_organization_admin = Role.ORGANIZATION_ADMIN in organization_roles
    manages_any_event = any(
        scope_organization_id == organization_id and Role.EVENT_ADMIN in roles
        for (scope_organization_id, _event_id), roles in authenticated.actor.event_roles.items()
    )
    if not is_organization_admin and not manages_any_event:
        raise HTTPException(status_code=404)
    if view not in EVENT_LIST_VIEWS:
        raise HTTPException(status_code=422)
    if order not in EVENT_LIST_ORDERS:
        raise HTTPException(status_code=422)
    search = q.strip()[:100]
    page_limit = max(1, min(EVENTS_PAGE_LIMIT, limit))
    now = utc_now_ms()
    decoded_cursor = _events_cursor(
        request,
        cursor,
        organization_id=organization_id,
        view=view,
        search=search,
        order=order,
    )
    after_starts_at_ms, after_id = decoded_cursor if decoded_cursor else (None, None)

    # The SQL below is assembled ONLY from the fixed fragments in this
    # function; every user-influenced value is bound as a parameter.
    conditions = ["e.organization_id=?"]
    binds: list[object] = [organization_id]
    membership_join = ""
    if not is_organization_admin:
        # Event administrators see only the events they hold an active
        # event_admin membership for — filtered in SQL, not after the fetch.
        membership_join = (
            " JOIN event_memberships em ON em.organization_id=e.organization_id"
            " AND em.event_id=e.id AND em.user_id=?"
            " AND em.role='event_admin' AND em.status='active'"
        )
        binds.insert(0, authenticated.actor.user_id)
    if view == "active":
        conditions.append("e.status='active' AND e.ends_at_ms>=?")
        binds.append(now)
    elif view == "draft":
        conditions.append("e.status='draft'")
    elif view == "past":
        conditions.append("(e.ends_at_ms<? OR e.status='archived')")
        binds.append(now)
    if search:
        escaped = (
            search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        conditions.append(
            "(e.name LIKE ? ESCAPE '\\' OR COALESCE(e.location,'') LIKE ? ESCAPE '\\')"
        )
        binds.extend([pattern, pattern])
    if after_id is not None:
        comparison = ">" if order == "upcoming" else "<"
        conditions.append(
            f"(e.starts_at_ms{comparison}? OR "  # noqa: S608 - fixed operator
            f"(e.starts_at_ms=? AND e.id{comparison}?))"
        )
        binds.extend([after_starts_at_ms, after_starts_at_ms, after_id])
    binds.append(page_limit + 1)
    statement = database(request).prepare(
        "SELECT e.id,e.organization_id,e.name,e.starts_at_ms,e.ends_at_ms,e.time_zone,"  # noqa: S608, E501
        "e.location,e.delivery_mode,e.description,e.accent_color,e.logo_url,"
        "e.cover_image_url,e.website_url,e.email_sender_name,e.email_reply_to,"
        "e.status,e.version,"
        "(SELECT COUNT(*) FROM submissions s WHERE s.organization_id=e.organization_id "
        "AND s.event_id=e.id AND s.status='submitted') AS proposal_count,"
        "(SELECT COUNT(DISTINCT ea.submission_id) FROM evaluation_rounds er "
        "JOIN evaluation_assignments ea ON ea.round_id=er.id AND ea.status='assigned' "
        "JOIN submissions ps ON ps.id=ea.submission_id AND ps.status='submitted' "
        "WHERE er.organization_id=e.organization_id AND er.event_id=e.id "
        "AND er.status='open') AS pending_review_count,"
        "CASE WHEN EXISTS(SELECT 1 FROM schedule_revisions sr "
        "WHERE sr.organization_id=e.organization_id AND sr.event_id=e.id "
        "AND sr.status='published') AND EXISTS(SELECT 1 FROM schedule_revisions sr "
        "WHERE sr.organization_id=e.organization_id AND sr.event_id=e.id "
        "AND sr.status='draft') THEN 'updates_pending' "
        "WHEN EXISTS(SELECT 1 FROM schedule_revisions sr "
        "WHERE sr.organization_id=e.organization_id AND sr.event_id=e.id "
        "AND sr.status='published') THEN 'published' "
        "WHEN EXISTS(SELECT 1 FROM accepted_sessions ac "
        "WHERE ac.organization_id=e.organization_id AND ac.event_id=e.id) "
        "AND NOT EXISTS(SELECT 1 FROM accepted_sessions ac "
        "WHERE ac.organization_id=e.organization_id AND ac.event_id=e.id "
        "AND NOT EXISTS(SELECT 1 FROM schedule_revisions sr "
        "JOIN agenda_items ai ON ai.revision_id=sr.id AND ai.accepted_session_id=ac.id "
        "WHERE sr.organization_id=e.organization_id AND sr.event_id=e.id "
        "AND sr.status='draft')) THEN 'ready' "
        "WHEN EXISTS(SELECT 1 FROM schedule_revisions sr "
        "WHERE sr.organization_id=e.organization_id AND sr.event_id=e.id "
        "AND sr.status='draft') THEN 'draft' ELSE 'not_started' END AS schedule_status "
        "FROM events e"
        f"{membership_join} WHERE {' AND '.join(conditions)} "
        f"ORDER BY e.starts_at_ms {'ASC' if order == 'upcoming' else 'DESC'},"
        f"e.id {'ASC' if order == 'upcoming' else 'DESC'} LIMIT ?"
    ).bind(*binds)
    rows = result_rows(await statement.all())
    events = [EventView(**row) for row in rows[:page_limit]]
    next_cursor = None
    if len(rows) > page_limit and events:
        next_cursor = _events_next_cursor(
            request,
            organization_id=organization_id,
            view=view,
            search=search,
            order=order,
            starts_at_ms=events[-1].starts_at_ms,
            row_id=events[-1].id,
        )
    return EventList(data=events, next_cursor=next_cursor)


def _events_cursor(
    request: Request,
    value: str | None,
    *,
    organization_id: str,
    view: str,
    search: str,
    order: str,
) -> tuple[int, str] | None:
    """Decode and verify a signed keyset cursor; 400 on tamper or expiry."""
    if value is None:
        return None
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        payload = urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
        signature = urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        expected = hmac.digest(secret(request, "CSRF_HMAC_KEY"), payload, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        decoded = json.loads(payload.decode())
        if not isinstance(decoded, dict) or decoded != {
            "exp": decoded.get("exp"),
            "id": decoded.get("id"),
            "org": organization_id,
            "order": order,
            "q": search,
            "starts": decoded.get("starts"),
            "view": view,
            "v": 1,
        }:
            raise ValueError
        starts_at, row_id, expires = decoded["starts"], decoded["id"], decoded["exp"]
        if (
            not isinstance(starts_at, int)
            or not isinstance(row_id, str)
            or len(row_id) > 100
            or not isinstance(expires, int)
            or expires < utc_now_ms()
        ):
            raise ValueError
        return starts_at, row_id
    except (
        ValueError,
        binascii.Error,  # subclass of ValueError; listed for clarity
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        raise HTTPException(status_code=400) from exc


class RecentSpeakerView(BaseModel):
    person_id: str
    display_name: str
    event_id: str
    event_name: str
    proposal_title: str
    selection_status: str


class OrganizationMetricsView(BaseModel):
    organization_id: str
    event_count: int
    speaker_count: int
    session_count: int
    proposal_count: int
    pending_review_count: int
    recent_speakers: list[RecentSpeakerView] = []


@access_router.get(
    "/api/v1/admin/organizations/{organization_id}/metrics",
    response_model=OrganizationMetricsView,
    tags=["administration"],
)
async def organization_metrics(organization_id: str, request: Request) -> OrganizationMetricsView:
    """Lightweight aggregate counts for dashboards instead of paging every
    event and fanning out per-event requests.

    Organization admins see organization-wide counts; event administrators see
    counts over the events they administer."""
    authenticated = await authenticate_request(request)
    organization_roles = authenticated.actor.organization_roles.get(organization_id, frozenset())
    is_organization_admin = Role.ORGANIZATION_ADMIN in organization_roles
    manages_any_event = any(
        scope_organization_id == organization_id and Role.EVENT_ADMIN in roles
        for (scope_organization_id, _event_id), roles in authenticated.actor.event_roles.items()
    )
    if not is_organization_admin and not manages_any_event:
        raise HTTPException(status_code=404)
    db = database(request)
    # The proposal-attachment probe correlates on (organization, event,
    # event_speaker) so it rides idx_submission_speakers_speaker instead of
    # scanning submission_speakers, and speakers are counted as UNIQUE people
    # (DISTINCT person_id), not per-event appearances.
    has_proposal = (
        "EXISTS (SELECT 1 FROM submission_speakers ss"
        " WHERE ss.organization_id=es.organization_id"
        " AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id)"
    )
    if is_organization_admin:
        event_count = await (
            db.prepare("SELECT COUNT(*) AS total FROM events WHERE organization_id=?1")
            .bind(organization_id)
            .first("total")
        )
        speaker_count = await (
            db.prepare(
                "SELECT COUNT(DISTINCT es.person_id) AS total FROM event_speakers es "  # noqa: S608, E501
                f"WHERE es.organization_id=?1 AND {has_proposal}"
            )
            .bind(organization_id)
            .first("total")
        )
        session_count = await (
            db.prepare(
                "SELECT COUNT(*) AS total FROM accepted_sessions "
                "WHERE organization_id=?1"
            )
            .bind(organization_id)
            .first("total")
        )
        proposal_count = await (
            db.prepare(
                "SELECT COUNT(*) AS total FROM submissions "
                "WHERE organization_id=?1 AND status='submitted'"
            )
            .bind(organization_id)
            .first("total")
        )
        pending_review_count = await (
            db.prepare(
                "SELECT COUNT(DISTINCT ea.submission_id) AS total "
                "FROM evaluation_rounds er "
                "JOIN evaluation_assignments ea ON ea.round_id=er.id "
                "AND ea.status='assigned' "
                "WHERE er.organization_id=?1"
            )
            .bind(organization_id)
            .first("total")
        )
        recent_rows = result_rows(
            await db.prepare(
                "SELECT p.id AS person_id,p.display_name,e.id AS event_id,"  # noqa: S608
                "e.name AS event_name,es.selection_status,"
                "COALESCE("
                # Prefer the ACCEPTED submission; fall back to the newest one.
                "(SELECT s.proposal_title FROM submission_speakers ss"
                " JOIN submissions s ON s.id=ss.submission_id"
                " JOIN accepted_sessions ac ON ac.organization_id=s.organization_id"
                " AND ac.event_id=s.event_id AND ac.submission_id=s.id"
                " WHERE ss.organization_id=es.organization_id AND ss.event_id=es.event_id"
                " AND ss.event_speaker_id=es.id"
                " ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),"
                "(SELECT s.proposal_title FROM submission_speakers ss"
                " JOIN submissions s ON s.id=ss.submission_id"
                " WHERE ss.organization_id=es.organization_id AND ss.event_id=es.event_id"
                " AND ss.event_speaker_id=es.id"
                " ORDER BY s.submitted_at_ms DESC LIMIT 1),'No proposal') AS proposal_title "
                "FROM event_speakers es "
                "JOIN people p ON p.organization_id=es.organization_id AND p.id=es.person_id "
                "JOIN events e ON e.organization_id=es.organization_id AND e.id=es.event_id "
                f"WHERE es.organization_id=?1 AND {has_proposal} "
                "ORDER BY es.last_activity_at_ms DESC,es.id DESC LIMIT 6"
            )
            .bind(organization_id)
            .all()
        )
    else:
        membership_join = (
            "JOIN event_memberships em ON em.organization_id=es.organization_id"
            " AND em.event_id=es.event_id AND em.user_id=?2"
            " AND em.role='event_admin' AND em.status='active' "
        )
        event_count = await (
            db.prepare(
                """SELECT COUNT(*) AS total FROM events e
                   JOIN event_memberships em ON em.organization_id=e.organization_id
                    AND em.event_id=e.id AND em.user_id=?2
                    AND em.role='event_admin' AND em.status='active'
                   WHERE e.organization_id=?1"""
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        speaker_count = await (
            db.prepare(
                "SELECT COUNT(DISTINCT es.person_id) AS total FROM event_speakers es "  # noqa: S608, E501
                f"{membership_join}"
                f"WHERE es.organization_id=?1 AND {has_proposal}"
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        session_count = await (
            db.prepare(
                "SELECT COUNT(*) AS total FROM accepted_sessions ac "
                "JOIN event_memberships em ON em.organization_id=ac.organization_id "
                "AND em.event_id=ac.event_id AND em.user_id=?2 "
                "AND em.role='event_admin' AND em.status='active' "
                "WHERE ac.organization_id=?1"
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        proposal_count = await (
            db.prepare(
                "SELECT COUNT(*) AS total FROM submissions s "
                "JOIN event_memberships em ON em.organization_id=s.organization_id "
                "AND em.event_id=s.event_id AND em.user_id=?2 "
                "AND em.role='event_admin' AND em.status='active' "
                "WHERE s.organization_id=?1 AND s.status='submitted'"
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        pending_review_count = await (
            db.prepare(
                "SELECT COUNT(DISTINCT ea.submission_id) AS total "
                "FROM evaluation_rounds er "
                "JOIN evaluation_assignments ea ON ea.round_id=er.id "
                "AND ea.status='assigned' "
                "JOIN event_memberships em ON em.organization_id=er.organization_id "
                "AND em.event_id=er.event_id AND em.user_id=?2 "
                "AND em.role='event_admin' AND em.status='active' "
                "WHERE er.organization_id=?1"
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        recent_rows = result_rows(
            await db.prepare(
                "SELECT p.id AS person_id,p.display_name,e.id AS event_id,"  # noqa: S608
                "e.name AS event_name,es.selection_status,"
                "COALESCE("
                # Prefer the ACCEPTED submission; fall back to the newest one.
                "(SELECT s.proposal_title FROM submission_speakers ss"
                " JOIN submissions s ON s.id=ss.submission_id"
                " JOIN accepted_sessions ac ON ac.organization_id=s.organization_id"
                " AND ac.event_id=s.event_id AND ac.submission_id=s.id"
                " WHERE ss.organization_id=es.organization_id AND ss.event_id=es.event_id"
                " AND ss.event_speaker_id=es.id"
                " ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),"
                "(SELECT s.proposal_title FROM submission_speakers ss"
                " JOIN submissions s ON s.id=ss.submission_id"
                " WHERE ss.organization_id=es.organization_id AND ss.event_id=es.event_id"
                " AND ss.event_speaker_id=es.id"
                " ORDER BY s.submitted_at_ms DESC LIMIT 1),'No proposal') AS proposal_title "
                "FROM event_speakers es "
                f"{membership_join}"
                "JOIN people p ON p.organization_id=es.organization_id AND p.id=es.person_id "
                "JOIN events e ON e.organization_id=es.organization_id AND e.id=es.event_id "
                f"WHERE es.organization_id=?1 AND {has_proposal} "
                "ORDER BY es.last_activity_at_ms DESC,es.id DESC LIMIT 6"
            )
            .bind(organization_id, authenticated.actor.user_id)
            .all()
        )
    return OrganizationMetricsView(
        organization_id=organization_id,
        event_count=int(event_count or 0),
        speaker_count=int(speaker_count or 0),
        session_count=int(session_count or 0),
        proposal_count=int(proposal_count or 0),
        pending_review_count=int(pending_review_count or 0),
        recent_speakers=[RecentSpeakerView(**row) for row in recent_rows],
    )


def _events_next_cursor(
    request: Request,
    *,
    organization_id: str,
    view: str,
    search: str,
    order: str,
    starts_at_ms: int,
    row_id: str,
) -> str:
    payload = json.dumps(
        {
            "exp": utc_now_ms() + _EVENTS_CURSOR_TTL_MS,
            "id": row_id,
            "org": organization_id,
            "order": order,
            "q": search,
            "starts": starts_at_ms,
            "view": view,
            "v": 1,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.digest(secret(request, "CSRF_HMAC_KEY"), payload, "sha256")
    return ".".join(
        (
            urlsafe_b64encode(payload).decode().rstrip("="),
            urlsafe_b64encode(signature).decode().rstrip("="),
        )
    )


@access_router.get(
    "/api/v1/admin/events/{event_id}",
    response_model=EventView,
    tags=["administration"],
)
async def get_event(event_id: str, request: Request) -> EventView:
    """Read one event directly. Archived events stay readable so admin pages
    can render context instead of going dark."""
    row = row_mapping(
        await database(request)
        .prepare(
            """SELECT id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                      delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                      email_sender_name,email_reply_to,status,version,
                      (SELECT COUNT(*) FROM submissions s
                       WHERE s.organization_id=events.organization_id
                         AND s.event_id=events.id AND s.status='submitted') AS proposal_count,
                      (SELECT COUNT(DISTINCT ea.submission_id)
                       FROM evaluation_rounds er
                       JOIN evaluation_assignments ea
                         ON ea.round_id=er.id AND ea.status='assigned'
                       JOIN submissions ps
                         ON ps.id=ea.submission_id AND ps.status='submitted'
                       WHERE er.organization_id=events.organization_id
                         AND er.event_id=events.id AND er.status='open') AS pending_review_count,
                      CASE
                        WHEN EXISTS(SELECT 1 FROM schedule_revisions sr
                          WHERE sr.organization_id=events.organization_id
                            AND sr.event_id=events.id AND sr.status='published')
                         AND EXISTS(SELECT 1 FROM schedule_revisions sr
                          WHERE sr.organization_id=events.organization_id
                            AND sr.event_id=events.id AND sr.status='draft') THEN 'updates_pending'
                        WHEN EXISTS(SELECT 1 FROM schedule_revisions sr
                          WHERE sr.organization_id=events.organization_id
                            AND sr.event_id=events.id AND sr.status='published') THEN 'published'
                        WHEN EXISTS(SELECT 1 FROM accepted_sessions ac
                          WHERE ac.organization_id=events.organization_id
                            AND ac.event_id=events.id)
                         AND NOT EXISTS(SELECT 1 FROM accepted_sessions ac
                          WHERE ac.organization_id=events.organization_id
                            AND ac.event_id=events.id
                            AND NOT EXISTS(SELECT 1 FROM schedule_revisions sr
                              JOIN agenda_items ai ON ai.revision_id=sr.id
                                AND ai.accepted_session_id=ac.id
                              WHERE sr.organization_id=events.organization_id
                                AND sr.event_id=events.id AND sr.status='draft')) THEN 'ready'
                        WHEN EXISTS(SELECT 1 FROM schedule_revisions sr
                          WHERE sr.organization_id=events.organization_id
                            AND sr.event_id=events.id AND sr.status='draft') THEN 'draft'
                        ELSE 'not_started'
                      END AS schedule_status
               FROM events WHERE id=?1 LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.EVENT_MANAGE,
        ResourceContext(str(row["organization_id"]), event_id),
        mutation=False,
    )
    return EventView(**row)


@access_router.post(
    "/api/v1/admin/events/{event_id}/duplicate",
    response_model=EventView,
    status_code=201,
    tags=["administration"],
)
async def duplicate_event(
    event_id: str,
    body: EventDuplicateCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EventView:
    """Copy safe event setup into a new draft; operational records never cross over."""
    db, now = database(request), utc_now_ms()
    _validate_event_times(body.starts_at_ms, body.ends_at_ms)
    if body.status == "active":
        _validate_event_can_activate(body.ends_at_ms, now)
    source = row_mapping(
        await db.prepare(
            """SELECT id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                      delivery_mode,description,accent_color,logo_url,cover_image_url,
                      website_url,email_sender_name,email_reply_to,version
               FROM events WHERE id=?1 LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if source is None:
        raise HTTPException(status_code=404)
    organization_id = str(source["organization_id"])
    authenticated = await require_permission(
        request,
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(organization_id),
        mutation=True,
    )
    if int(source["version"]) != body.source_version:
        raise HTTPException(
            status_code=409,
            detail="The source event changed; reopen the duplicate form and try again",
        )
    if not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 255:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain between 16 and 255 characters",
        )
    route = "POST /api/v1/admin/events/{event_id}/duplicate"
    fingerprint = hashlib.sha256(
        json.dumps(
            {"event_id": event_id, **body.model_dump()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).digest()
    key_hash = hashlib.sha256(idempotency_key.encode()).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id
               FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND state='completed'"""
        )
        .bind(authenticated.actor.user_id, route, key_hash)
        .first()
    )
    if replay is not None:
        stored_fingerprint = to_python(replay["request_fingerprint"])
        if not isinstance(stored_fingerprint, bytes):
            stored_fingerprint = bytes(stored_fingerprint)
        if stored_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used for a different event",
            )
        duplicated = row_mapping(
            await db.prepare(
                """SELECT id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,
                          location,delivery_mode,description,accent_color,logo_url,
                          cover_image_url,website_url,email_sender_name,email_reply_to,
                          status,version
                   FROM events WHERE id=?1 AND organization_id=?2 LIMIT 1"""
            )
            .bind(str(replay["response_resource_id"]), organization_id)
            .first()
        )
        if duplicated is None:
            raise HTTPException(
                status_code=409,
                detail="The original duplication result is no longer available",
            )
        return EventView(**duplicated)

    if body.logo_url is not None:
        await _require_event_branding_reference(
            db, organization_id=organization_id, kind="logo", asset_url=body.logo_url
        )
    if body.cover_image_url is not None:
        await _require_event_branding_reference(
            db, organization_id=organization_id, kind="cover", asset_url=body.cover_image_url
        )
    copied_logo = None
    if body.retain_source_logo:
        if source["logo_url"] is None:
            raise HTTPException(status_code=422, detail="the source event has no logo")
        copied_logo = await _copy_event_branding_asset(
            request,
            organization_id=organization_id,
            source_event_id=event_id,
            source_url=str(source["logo_url"]),
            kind="logo",
            actor_user_id=authenticated.actor.user_id,
            now=now,
        )
    copied_cover = None
    if body.retain_source_cover:
        if source["cover_image_url"] is None:
            raise HTTPException(status_code=422, detail="the source event has no cover")
        copied_cover = await _copy_event_branding_asset(
            request,
            organization_id=organization_id,
            source_event_id=event_id,
            source_url=str(source["cover_image_url"]),
            kind="cover",
            actor_user_id=authenticated.actor.user_id,
            now=now,
        )

    base_name = body.name
    existing_names = {
        str(row["name"])
        for row in result_rows(
            await db.prepare(
                """SELECT name FROM events
                   WHERE organization_id=?1 AND (name=?2 OR name LIKE ?3 ESCAPE '\\')"""
            )
            .bind(organization_id, base_name, f"{base_name} %")
            .all()
        )
    }
    duplicate_name = base_name
    suffix = 2
    while duplicate_name in existing_names:
        duplicate_name = f"{base_name} {suffix}"
        suffix += 1

    duplicated_event_id = new_id()
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    for copied_asset in (copied_logo, copied_cover):
        if copied_asset is None:
            continue
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_branding_assets
                   (id,organization_id,event_id,kind,object_key,asset_url,content_type,
                    byte_size,checksum_sha256,status,created_by_user_id,created_at_ms)
                   VALUES(?1,?2,NULL,?3,?4,?5,?6,?7,?8,'pending',?9,?10)"""
            ).bind(
                copied_asset["id"],
                organization_id,
                copied_asset["kind"],
                copied_asset["object_key"],
                copied_asset["asset_url"],
                copied_asset["content_type"],
                copied_asset["byte_size"],
                copied_asset["checksum_sha256"],
                authenticated.actor.user_id,
                now,
            )
        )
    logo_url = str(copied_logo["asset_url"]) if copied_logo else body.logo_url
    cover_image_url = (
        str(copied_cover["asset_url"]) if copied_cover else body.cover_image_url
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                email_sender_name,email_reply_to,status,created_at_ms,updated_at_ms)
               SELECT ?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,
                      ?17,?16,?16
               WHERE EXISTS(SELECT 1 FROM events source
                            WHERE source.id=?18 AND source.organization_id=?2
                              AND source.version=?19)"""
        ).bind(
            duplicated_event_id,
            organization_id,
            duplicate_name,
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
            now,
            body.status,
            event_id,
            body.source_version,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'event_admin','active',?5,?5)"""
        ).bind(new_id(), organization_id, duplicated_event_id, authenticated.actor.user_id, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="event.duplicate",
            target_type="event",
            target_id=duplicated_event_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=duplicated_event_id,
            metadata={"source_event_id": event_id},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="event",
        resource_id=duplicated_event_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        current_source = row_mapping(
            await db.prepare("SELECT version FROM events WHERE id=?1 LIMIT 1")
            .bind(event_id)
            .first()
        )
        if current_source is None or int(current_source["version"]) != body.source_version:
            raise HTTPException(
                status_code=409,
                detail="The source event changed; reopen the duplicate form and try again",
            ) from exc
        raise HTTPException(
            status_code=409,
            detail="Event duplication is already being processed; retry with the same key",
        ) from exc
    return EventView(
        id=duplicated_event_id,
        organization_id=organization_id,
        name=duplicate_name,
        starts_at_ms=body.starts_at_ms,
        ends_at_ms=body.ends_at_ms,
        time_zone=body.time_zone,
        location=body.location,
        delivery_mode=body.delivery_mode,
        description=body.description,
        accent_color=body.accent_color,
        logo_url=logo_url,
        cover_image_url=cover_image_url,
        website_url=body.website_url,
        email_sender_name=body.email_sender_name,
        email_reply_to=body.email_reply_to,
        status=body.status,
        version=1,
    )


def _validate_event_times(starts_at_ms: int, ends_at_ms: int) -> None:
    if ends_at_ms <= starts_at_ms:
        raise HTTPException(status_code=422)


def _validate_event_can_activate(ends_at_ms: int, now_ms: int) -> None:
    if ends_at_ms <= now_ms:
        raise HTTPException(
            status_code=422,
            detail="update the event dates before activating",
        )


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


async def _copy_event_branding_asset(
    request: Request,
    *,
    organization_id: str,
    source_event_id: str,
    source_url: str,
    kind: Literal["logo", "cover"],
    actor_user_id: str,
    now: int,
) -> dict[str, object]:
    """Copy an owned source object and describe a fresh pending asset row."""
    db = database(request)
    source_asset = row_mapping(
        await db.prepare(
            """SELECT object_key,content_type FROM event_branding_assets
               WHERE organization_id=?1 AND event_id=?2 AND kind=?3 AND asset_url=?4
                 AND status='attached' LIMIT 1"""
        )
        .bind(organization_id, source_event_id, kind, source_url)
        .first()
    )
    if source_asset is not None:
        object_key = str(source_asset["object_key"])
        content_type = str(source_asset["content_type"])
    elif source_url.startswith(f"/api/v1/public/events/{source_event_id}/{kind}/"):
        asset_name = source_url.rsplit("/", 1)[-1]
        suffix = asset_name[asset_name.rfind(".") :] if "." in asset_name else ""
        content_type = {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(suffix, "")
        directory = "event-logos" if kind == "logo" else "event-covers"
        object_key = f"public/{directory}/{organization_id}/{source_event_id}/{asset_name}"
    else:
        # Never fetch arbitrary external branding URLs from the Worker: that
        # would turn this organizer action into an SSRF surface.
        raise HTTPException(
            status_code=422,
            detail=f"the source {kind} cannot be retained; upload a replacement",
        )
    if content_type not in _EVENT_IMAGE_MEDIA_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid source {kind} asset")
    stored = await _event_logo_bucket(request).get(object_key)
    if stored is None:
        raise HTTPException(status_code=409, detail=f"the source {kind} is unavailable")
    body = b"".join([chunk async for chunk in _stream_event_logo(stored)])
    if not body or len(body) > _EVENT_LOGO_MAX_BYTES:
        raise HTTPException(status_code=422, detail=f"invalid source {kind} asset")
    extension = _EVENT_LOGO_RULES[content_type][0]
    if not _EVENT_LOGO_RULES[content_type][1](body):
        raise HTTPException(status_code=422, detail=f"invalid source {kind} asset")
    asset_id = new_id()
    copied_object_key = f"public/event-branding/{organization_id}/{asset_id}.{extension}"
    asset_url = f"/api/v1/public/event-assets/{asset_id}.{extension}"
    await _event_logo_bucket(request).put(copied_object_key, body)
    return {
        "id": asset_id,
        "organization_id": organization_id,
        "kind": kind,
        "object_key": copied_object_key,
        "asset_url": asset_url,
        "content_type": content_type,
        "byte_size": len(body),
        "checksum_sha256": hashlib.sha256(body).digest(),
        "created_by_user_id": actor_user_id,
        "created_at_ms": now,
    }


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
async def create_event(
    organization_id: str,
    body: EventCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EventView:
    _validate_event_times(body.starts_at_ms, body.ends_at_ms)
    requested_status: Literal["draft", "active"] = getattr(body, "status", "active")
    authenticated = await require_permission(
        request,
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(organization_id),
        mutation=True,
    )
    db, now, event_id = database(request), utc_now_ms(), new_id()
    route = "POST /api/v1/admin/organizations/{organization_id}/events"
    request_data = body.model_dump()
    # Preserve fingerprints produced before create-status was introduced so an
    # in-flight legacy retry still replays the original active event.
    if requested_status == "active":
        request_data.pop("status", None)
    else:
        request_data["status"] = requested_status
    fingerprint = hashlib.sha256(
        json.dumps(
            {"organization_id": organization_id, **request_data},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).digest()
    record: IdempotencyRecord | None = None
    # Direct function-level tests call this route without FastAPI resolving the
    # Header dependency, so only treat an actual string as a supplied key.
    if not isinstance(idempotency_key, str):
        idempotency_key = None
    if idempotency_key is not None:
        if not 16 <= len(idempotency_key) <= 255:
            raise HTTPException(
                status_code=400,
                detail="Idempotency-Key must contain between 16 and 255 characters",
            )
        replay = row_mapping(
            await db.prepare(
                """SELECT request_fingerprint,response_resource_id
                   FROM idempotency_records
                   WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                     AND state='completed'"""
            )
            .bind(
                authenticated.actor.user_id,
                route,
                hashlib.sha256(idempotency_key.encode()).digest(),
            )
            .first()
        )
        if replay is not None:
            stored_fingerprint = to_python(replay["request_fingerprint"])
            if not isinstance(stored_fingerprint, bytes):
                stored_fingerprint = bytes(stored_fingerprint)
            if stored_fingerprint != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used with different event data",
                )
            row = row_mapping(
                await db.prepare(
                    """SELECT id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,
                              location,delivery_mode,description,accent_color,logo_url,
                              cover_image_url,website_url,email_sender_name,email_reply_to,
                              status,version
                       FROM events WHERE id=?1 AND organization_id=?2 LIMIT 1"""
                )
                .bind(str(replay["response_resource_id"]), organization_id)
                .first()
            )
            if row is None:
                raise HTTPException(
                    status_code=409,
                    detail="The original event creation result is no longer available",
                )
            return EventView(**row)
        record = IdempotencyRecord(
            principal_key=authenticated.actor.user_id,
            organization_id=organization_id,
            route_key=route,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            expires_at_ms=now + 86_400_000,
        )
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
    if requested_status == "active":
        _validate_event_can_activate(body.ends_at_ms, now)
    batch = CommandBatch(db)
    if record is not None:
        batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                email_sender_name,email_reply_to,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?17)"""
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
            requested_status,
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
    if record is not None:
        batch.complete_idempotency(
            record,
            status=201,
            resource_type="event",
            resource_id=event_id,
            completed_at_ms=now,
        )
    try:
        await batch.execute()
    except PersistenceError as exc:
        if record is not None:
            raise HTTPException(
                status_code=409,
                detail="Event creation is already being processed; retry with the same key",
            ) from exc
        raise
    return EventView(
        id=event_id,
        organization_id=organization_id,
        status=requested_status,
        version=1,
        **body.model_dump(exclude={"status"}),
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
            """SELECT organization_id,logo_url,cover_image_url,status,archived_at_ms
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
    resolved_status = body.status if body.status is not None else str(event["status"])
    now = utc_now_ms()
    if resolved_status == "active" and str(event["status"]) != "active":
        _validate_event_can_activate(body.ends_at_ms, now)
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
    # Stamp archived_at_ms only on the transition INTO archived; ordinary
    # edits of an already-archived event keep the original timestamp.
    if resolved_status != "archived":
        archived_at_ms = None
    elif str(event["status"]) == "archived" and event["archived_at_ms"] is not None:
        archived_at_ms = int(event["archived_at_ms"])
    else:
        archived_at_ms = now
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
            resolved_status,
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
            metadata={"status": resolved_status},
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
    if body.role == "organization_admin":
        # An event_admin must not be able to escalate anyone (including
        # themselves) to organization-wide administration.
        await require_permission(
            request,
            Permission.ORGANIZATION_MANAGE,
            ResourceContext(str(event["organization_id"])),
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
    await _issue_invitation_link(
        request,
        invitation_id=str(row["id"]),
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        email=email,
        normalized_email=normalized,
        role=body.role,
        now=now,
    )
    return InvitationView(**row)


async def _issue_invitation_link(
    request: Request,
    *,
    invitation_id: str,
    organization_id: str,
    event_id: str,
    email: str,
    normalized_email: str,
    role: InvitationRole,
    now: int,
) -> None:
    """Create a short-lived, one-time acceptance link and queue its delivery.

    The link is delivered ONLY by email to the invitee. It is never returned
    to the caller: an acceptance link signs the invitee in, so exposing it to
    the inviting administrator would let them accept the invitation as the
    invitee.
    """
    base = str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "")).rstrip("/")
    parsed = urlparse(base)
    is_local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if parsed.scheme != "https" and not is_local:
        return
    destination = {
        "speaker": "/speaker",
        "evaluator": "/reviews",
        "event_admin": "/admin",
        "organization_admin": "/admin",
    }[role]
    db = database(request)
    # Bind the challenge to a user id only when that user currently holds an
    # ACTIVE membership in this organization: the challenge-scope integrity
    # trigger requires it, and re-inviting an offboarded (revoked) person must
    # work — their identity is re-established by email at acceptance time.
    user_id = await (
        db.prepare(
            """SELECT u.id FROM users u
               WHERE u.normalized_email=?1 AND u.status='active'
                 AND EXISTS (
                   SELECT 1 FROM organization_memberships m
                   WHERE m.organization_id=?2 AND m.user_id=u.id AND m.status='active'
                 ) LIMIT 1"""
        )
        .bind(normalized_email, organization_id)
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
    response_model=InvitationView,
    tags=["administration"],
)
async def resend_invitation(
    event_id: str, invitation_id: str, request: Request
) -> InvitationView:
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
    if str(row["role"]) == "organization_admin":
        # Only organization admins may keep an org-admin invitation alive.
        await require_permission(
            request,
            Permission.ORGANIZATION_MANAGE,
            ResourceContext(organization_id),
            mutation=True,
        )
    role: InvitationRole = row["role"]
    await _issue_invitation_link(
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
    return InvitationView(**row)


@access_router.delete(
    "/api/v1/admin/events/{event_id}/invitations/{invitation_id}",
    status_code=204,
    tags=["administration"],
)
async def revoke_invitation(event_id: str, invitation_id: str, request: Request) -> Response:
    db, organization_id, authenticated = await _managed_event(request, event_id, mutation=True)
    now = utc_now_ms()
    pending_role = await (
        db.prepare(
            """SELECT role FROM identity_invitations
               WHERE id=?1 AND organization_id=?2 AND event_id=?3 AND status='pending' LIMIT 1"""
        )
        .bind(invitation_id, organization_id, event_id)
        .first("role")
    )
    if pending_role is not None and str(pending_role) == "organization_admin":
        # Symmetric with create/resend: org-admin invitations are managed
        # only by organization admins.
        await require_permission(
            request,
            Permission.ORGANIZATION_MANAGE,
            ResourceContext(organization_id),
            mutation=True,
        )
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


def _request_source(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (
        request.client.host if request.client is not None else "unknown"
    )


@access_router.post(
    "/api/v1/auth/password/sign-in",
    response_model=SessionCreated,
    tags=["authentication"],
)
async def password_sign_in(
    body: PasswordSignIn, request: Request, response: Response
) -> SessionCreated:
    if not _valid_redirect(body.redirect_path):
        raise HTTPException(status_code=422)
    _email(body.email)
    normalized = normalize_email(body.email)
    await enforce_rate_limit(
        request,
        binding_name="AUTH_RATE_LIMITER",
        policy=RateLimitPolicy("auth.password", limit=10, window_seconds=60),
        subject=f"{normalized}:{_request_source(request)}",
    )
    db, now = database(request), utc_now_ms()
    credential = row_mapping(
        await db.prepare(
            """SELECT u.id,u.authorization_version,c.verifier_phc,c.pepper_version,c.status,
                      COALESCE(s.consecutive_failures,0) AS consecutive_failures,
                      s.blocked_until_ms
               FROM users u JOIN password_credentials c ON c.user_id=u.id
               LEFT JOIN password_authentication_state s ON s.user_id=u.id
               WHERE u.normalized_email=?1 AND u.status='active' LIMIT 1"""
        )
        .bind(normalized)
        .first()
    )
    # Always perform an expensive verification, including for unknown users,
    # so response time does not become a reliable account-existence oracle.
    pepper = secret(request, "PASSWORD_PEPPER")
    fake = (
        "$pbkdf2-sha256$i=600000$MDAwMDAwMDAwMDAwMDAwMA$"
        "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA"
    )
    verifier = str(credential["verifier_phc"]) if credential is not None else fake
    valid = verify_password(body.password, verifier, pepper)
    blocked = credential is not None and credential["blocked_until_ms"] is not None and int(
        credential["blocked_until_ms"]
    ) > now
    active = credential is not None and str(credential["status"]) == "active"
    if not valid or blocked or not active:
        if credential is not None:
            failures = min(int(credential["consecutive_failures"]) + 1, 100)
            delay_seconds = min(2 ** min(failures, 10), 3600) if failures >= 5 else 0
            await db.prepare(
                """INSERT INTO password_authentication_state
                   (user_id,consecutive_failures,first_failure_at_ms,last_failure_at_ms,
                    blocked_until_ms,updated_at_ms)
                   VALUES(?1,1,?2,?2,NULL,?2)
                   ON CONFLICT(user_id) DO UPDATE SET
                     consecutive_failures=?3,
                     first_failure_at_ms=COALESCE(first_failure_at_ms,?2),
                     last_failure_at_ms=?2,blocked_until_ms=?4,updated_at_ms=?2"""
            ).bind(
                credential["id"],
                now,
                failures,
                now + delay_seconds * 1000 if delay_seconds else None,
            ).run()
        raise HTTPException(status_code=401)

    session_id, session_token = new_id(), generate_token()
    csrf = issue_csrf_token(session_id, secret(request, "CSRF_HMAC_KEY"))
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO sessions
               (id,user_id,token_hash,csrf_secret_hash,authorization_version,created_at_ms,
                last_seen_at_ms,idle_expires_at_ms,absolute_expires_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?6,?7,?8)"""
        ).bind(
            session_id,
            credential["id"],
            hash_token(session_token),
            hash_token(csrf),
            credential["authorization_version"],
            now,
            now + 12 * 60 * 60 * 1000,
            now + 30 * 24 * 60 * 60 * 1000,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO password_authentication_state
               (user_id,consecutive_failures,last_success_at_ms,updated_at_ms)
               VALUES(?1,0,?2,?2)
               ON CONFLICT(user_id) DO UPDATE SET consecutive_failures=0,
                 first_failure_at_ms=NULL,last_failure_at_ms=NULL,blocked_until_ms=NULL,
                 last_success_at_ms=?2,updated_at_ms=?2"""
        ).bind(credential["id"], now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO session_active_roles(session_id,user_id,role,selected_at_ms)
               SELECT ?1,user_id,role,?2 FROM user_roles
               WHERE user_id=?3 AND status='active'
               ORDER BY is_default DESC,
                        CASE role WHEN 'organizer' THEN 1 WHEN 'reviewer' THEN 2 ELSE 3 END
               LIMIT 1"""
        ).bind(session_id, now, credential["id"])
    )
    batch.add_statement(
        db.prepare(
            "UPDATE password_credentials SET last_verified_at_ms=?1 WHERE user_id=?2"
        ).bind(now, credential["id"])
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=str(credential["id"]),
            action="session.password_sign_in",
            target_type="session",
            target_id=session_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
        )
    )
    await batch.execute()
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
    default_role = await _default_account_role(db, str(credential["id"]))
    return SessionCreated(
        user_id=str(credential["id"]),
        csrf_token=csrf,
        redirect_path=(
            _role_destination(default_role) if body.redirect_path == "/" else body.redirect_path
        ),
    )


@access_router.get("/api/v1/auth/verify", response_model=SessionCreated, tags=["authentication"])
async def verify_magic_link(token: str, request: Request, response: Response) -> SessionCreated:
    if len(token) < 32:
        raise HTTPException(status_code=404)
    db, now = database(request), utc_now_ms()
    challenge = row_mapping(
        await db.prepare(
            """UPDATE authentication_challenges SET consumed_at_ms=?1
           WHERE token_hash=?2 AND consumed_at_ms IS NULL AND expires_at_ms>?1
           RETURNING id,user_id,normalized_email,organization_id,event_id,provisioning_context,
                     invitation_id,redirect_path"""
        )
        .bind(now, hash_token(token))
        .first()
    )
    if challenge is None:
        raise HTTPException(status_code=404)
    try:
        return await _finish_magic_link_sign_in(db, request, response, challenge, now)
    except BaseException:
        # Consume-on-success: if provisioning or session creation fails after
        # the atomic consumption above, restore the challenge so the link is
        # not burned by a transient failure. The guard on consumed_at_ms means
        # only this request's own consumption can be rolled back.
        try:
            await (
                db.prepare(
                    """UPDATE authentication_challenges SET consumed_at_ms=NULL
                   WHERE id=?1 AND consumed_at_ms=?2"""
                )
                .bind(challenge["id"], now)
                .run()
            )
        except Exception:  # noqa: S110 - restoration is strictly best effort
            pass
        raise


async def _finish_magic_link_sign_in(
    db, request: Request, response: Response, challenge, now: int
) -> SessionCreated:
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
        if invitation["role"] == "organization_admin":
            # Grants (or upgrades to) organization-wide administration; no
            # event membership row is written because organization roles are
            # organization-scoped. Existing sessions are invalidated so every
            # surface re-reads the new role set.
            batch.add_statement(
                db.prepare(
                    """INSERT INTO organization_memberships
                   (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,'organization_admin','active',?4,?4)
                   ON CONFLICT(organization_id,user_id) DO UPDATE SET
                     role='organization_admin',status='active',revoked_at_ms=NULL,
                     version=version+1,updated_at_ms=excluded.updated_at_ms"""
                ).bind(new_id(), invitation["organization_id"], user_id, now)
            )
            if existing_user is not None:
                batch.add_statement(
                    db.prepare(
                        """UPDATE users SET authorization_version=authorization_version+1,
                           updated_at_ms=?1 WHERE id=?2"""
                    ).bind(now, user_id)
                )
        else:
            # Reactivating a revoked membership through an event-level
            # invitation must never restore a previously revoked admin role:
            # the row comes back as a plain member. An ACTIVE admin accepting
            # an event invitation keeps their role untouched.
            batch.add_statement(
                db.prepare(
                    """INSERT INTO organization_memberships
                   (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,'member','active',?4,?4)
                   ON CONFLICT(organization_id,user_id) DO UPDATE SET status='active',
                     revoked_at_ms=NULL,
                     role=CASE WHEN organization_memberships.status='revoked'
                          THEN 'member' ELSE organization_memberships.role END,
                     version=version+1,updated_at_ms=excluded.updated_at_ms"""
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
    default_role = await _default_account_role(db, str(user_id))
    if default_role is not None:
        await (
            db.prepare(
                """INSERT INTO session_active_roles(session_id,user_id,role,selected_at_ms)
                   VALUES(?1,?2,?3,?4)"""
            )
            .bind(session_id, user_id, default_role, now)
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
        redirect_path=(
            _role_destination(default_role)
            if str(challenge["redirect_path"]) == "/"
            else str(challenge["redirect_path"])
        ),
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
async def magic_link_interstitial(token: str = "", *, request: Request) -> Response:
    """Render a confirm step instead of consuming the single-use token on GET.

    Corporate mail scanners prefetch emailed links; consuming on GET burned the
    token before the speaker ever clicked. The button posts back to the same
    path (preserving the token and, through the stored challenge, the requested
    redirect), which scanners do not follow. The page is a packaged static
    asset styled by the shared stylesheet so the strict CSP (style-src 'self')
    never blocks it."""
    if not token:
        return Response(
            _asset("auth_link_error.html"),
            media_type="text/html",
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    action = f"/auth/verify?token={quote(token)}"
    page = _asset("auth_link_confirm.html").replace("__CONFIRM_ACTION__", escape(action))
    return Response(page, media_type="text/html", headers={"Cache-Control": "no-store"})


@access_router.post("/auth/verify", include_in_schema=False)
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
    db = database(request)
    user = row_mapping(
        await db
        .prepare(
            """SELECT email,display_name,
                      profile_completed_at_ms IS NOT NULL AS profile_complete
               FROM users WHERE id=?1 LIMIT 1"""
        )
        .bind(authenticated.actor.user_id)
        .first()
    )
    if user is None:
        raise HTTPException(status_code=401)
    account_role_rows = result_rows(
        await db.prepare(
            """SELECT role,is_default FROM user_roles
               WHERE user_id=?1 AND status='active'
               ORDER BY is_default DESC,role"""
        ).bind(authenticated.actor.user_id).all()
    )
    active_role_row = row_mapping(
        await db.prepare(
            """SELECT role FROM session_active_roles
               WHERE session_id=?1 AND user_id=?2 LIMIT 1"""
        ).bind(authenticated.session_id, authenticated.actor.user_id).first()
    )
    account_roles = [str(item["role"]) for item in account_role_rows]
    default_role = next(
        (str(item["role"]) for item in account_role_rows if bool(item["is_default"])),
        account_roles[0] if account_roles else None,
    )
    organization_id = next(iter(authenticated.actor.organization_roles), None)
    event_scope = next(iter(authenticated.actor.event_roles), None)
    if organization_id is None and event_scope is not None:
        organization_id = event_scope[0]
    organization_name = None
    if organization_id is not None:
        organization_row = row_mapping(
            await db.prepare("SELECT name FROM organizations WHERE id=?1 LIMIT 1")
            .bind(organization_id)
            .first()
        )
        if organization_row is not None:
            organization_name = str(organization_row["name"])
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
    configured_sender = str(
        getattr(environment(request), "RESEND_FROM_ADDRESS", "SessionBuddy <events@example.test>")
    )
    default_sender_name, default_email_address = parseaddr(configured_sender)
    return CurrentSession(
        user_id=authenticated.actor.user_id,
        email=str(user["email"]),
        display_name=str(user["display_name"]) if user["display_name"] is not None else None,
        profile_complete=bool(user["profile_complete"]),
        csrf_token=issue_csrf_token(authenticated.session_id, secret(request, "CSRF_HMAC_KEY")),
        default_email_sender_name=default_sender_name or "SessionBuddy",
        default_email_address=default_email_address or "events@example.test",
        account_roles=account_roles,
        active_role=(
            str(active_role_row["role"])
            if active_role_row is not None
            else default_role
        ),
        default_role=default_role,
        organization_id=organization_id,
        organization_name=organization_name,
        event_id=event_scope[1] if event_scope is not None else None,
        organization_access=organization_access,
        event_access=event_access,
    )


@access_router.put(
    "/api/v1/session/active-role",
    response_model=CurrentSession,
    tags=["authentication"],
)
async def update_active_role(body: ActiveRoleUpdate, request: Request) -> CurrentSession:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    db, now = database(request), utc_now_ms()
    assigned = row_mapping(
        await db.prepare(
            """SELECT role FROM user_roles
               WHERE user_id=?1 AND role=?2 AND status='active' LIMIT 1"""
        ).bind(authenticated.actor.user_id, body.role).first()
    )
    if assigned is None:
        raise HTTPException(status_code=403)
    await (
        db.prepare(
            """INSERT INTO session_active_roles(session_id,user_id,role,selected_at_ms)
               VALUES(?1,?2,?3,?4)
               ON CONFLICT(session_id) DO UPDATE SET
                 user_id=excluded.user_id,role=excluded.role,selected_at_ms=excluded.selected_at_ms"""
        )
        .bind(authenticated.session_id, authenticated.actor.user_id, body.role, now)
        .run()
    )
    return await current_session(request)


@access_router.put(
    "/api/v1/account/default-role",
    response_model=CurrentSession,
    tags=["authentication"],
)
async def update_default_role(body: DefaultRoleUpdate, request: Request) -> CurrentSession:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    db, now = database(request), utc_now_ms()
    assigned = row_mapping(
        await db.prepare(
            """SELECT role FROM user_roles
               WHERE user_id=?1 AND role=?2 AND status='active' LIMIT 1"""
        ).bind(authenticated.actor.user_id, body.role).first()
    )
    if assigned is None:
        raise HTTPException(status_code=403)
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            "UPDATE user_roles SET is_default=0,updated_at_ms=?1 WHERE user_id=?2 AND is_default=1"
        ).bind(now, authenticated.actor.user_id)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE user_roles SET is_default=1,updated_at_ms=?1
               WHERE user_id=?2 AND role=?3 AND status='active'"""
        ).bind(now, authenticated.actor.user_id, body.role)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="account.default_role.update",
            target_type="user",
            target_id=authenticated.actor.user_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"role": body.role},
        )
    )
    await batch.execute()
    return await current_session(request)

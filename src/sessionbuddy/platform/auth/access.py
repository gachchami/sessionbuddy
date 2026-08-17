"""Production passwordless access and one-time tenant bootstrap."""

import hashlib
import hmac
import json
import re
from email.headerregistry import Address
from email.utils import parseaddr
from html import escape
from typing import Literal
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sessionbuddy.communications.queue_publish import publish_committed_messages
from sessionbuddy.console import embedded_assets
from sessionbuddy.console.asset_response import content_addressed_asset
from sessionbuddy.observability import record_degradation
from sessionbuddy.platform.authorization import Permission, Persona, ResourceContext, ResourceGrant
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch, IdempotencyRecord
from sessionbuddy.platform.db.d1 import (
    D1Database,
    PersistenceError,
    result_rows,
    row_mapping,
    statement_changes,
    to_python,
)
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit
from sessionbuddy.platform.signed_cursors import BOUNDED_ID, STRICT_INT, SignedCursorContract
from sessionbuddy.platform.storage import malware_scan_disabled
from sessionbuddy.speaker_operations.asset_boundary import ScanJob
from sessionbuddy.speaker_operations.scanner_adapter import SignedScannerAdapter

from .branding_purge import PENDING_BRANDING_RETENTION_MS
from .csrf import issue_csrf_token
from .http import (
    authenticate_request,
    browser_request_is_same_origin,
    database,
    environment,
    guard_mutation,
    require_document_event,
    require_document_event_speaker,
    require_document_persona,
    require_permission,
    secret,
)
from .passwords import PasswordPolicyError, hash_password, verify_password
from .session_factory import confirm_session_established as _confirm_session_established
from .session_factory import establish_session, establish_session_with_current_authorization_version
from .session_factory import revoke_session as _revoke_session
from .session_factory import role_compatible_redirect as _role_compatible_redirect
from .session_factory import set_session_cookie as _set_session_cookie
from .session_factory import valid_redirect as _valid_redirect
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
_ACCOUNT_PROFILE_SOURCE_GUARD_SQL = """EXISTS (
    SELECT 1 FROM users profile_source
    WHERE profile_source.id=?1 AND profile_source.version=?2
      AND profile_source.updated_at_ms=?3 AND profile_source.display_name IS ?4
      AND profile_source.job_title IS ?5 AND profile_source.company IS ?6
      AND profile_source.description IS ?7 AND profile_source.website_url IS ?8
      AND profile_source.linkedin_url IS ?9 AND profile_source.x_url IS ?10
)"""
_IANA_TIME_ZONE = re.compile(r"^(?:UTC|[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9][A-Za-z0-9._+-]*)+)$")


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


@access_router.get("/auth/assets/demo-access.js", include_in_schema=False)
async def demo_access_javascript() -> Response:
    return Response(_asset("demo_access.js"), media_type="text/javascript")


@access_router.get("/auth/assets/auth-link-confirm.js", include_in_schema=False)
async def auth_link_confirm_javascript() -> Response:
    return Response(
        _asset("auth_link_confirm.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


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


@access_router.get("/co-speaker-invitations/assets/invitation.js", include_in_schema=False)
async def co_speaker_invitation_javascript() -> Response:
    return Response(_asset("co_speaker_invitation.js"), media_type="text/javascript")


@access_router.get("/admin", include_in_schema=False)
async def admin_home_page(request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    return Response(
        _asset("admin_home.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/home/assets/home.js", include_in_schema=False)
async def admin_home_javascript(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("admin_home.js"), media_type="text/javascript"
    )


@access_router.get("/admin/home/assets/home.css", include_in_schema=False)
async def admin_home_stylesheet(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("admin_home.css"), media_type="text/css"
    )


@access_router.get("/app-shell/assets/activity-format.js", include_in_schema=False)
async def activity_format_javascript(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("activity_format.js"), media_type="text/javascript"
    )


@access_router.get("/admin/events/new", include_in_schema=False)
async def event_create_page(request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    return Response(
        _asset("event_editor.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/event-editor/assets/event-editor.js", include_in_schema=False)
async def event_editor_javascript(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("event_editor.js"), media_type="text/javascript"
    )


@access_router.get("/admin/event-editor/assets/event-editor.css", include_in_schema=False)
async def event_editor_stylesheet(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("event_editor.css"), media_type="text/css"
    )


@access_router.get("/admin/events/{event_id}", include_in_schema=False)
async def event_overview_page(event_id: str, request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    # get_event deliberately keeps archived events readable so organizers can
    # retain context and navigate to the archived-safe administration tools.
    await require_document_event(request, event_id, include_archived=True)
    return Response(
        _asset("event_overview.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/events/{event_id}/settings", include_in_schema=False)
async def event_settings_page(event_id: str, request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    await require_document_event(request, event_id, include_archived=True)
    return Response(
        _asset("event_editor.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/event-overview/assets/event-overview.js", include_in_schema=False)
async def event_overview_javascript(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("event_overview.js"), media_type="text/javascript"
    )


@access_router.get("/admin/people", include_in_schema=False)
@access_router.get("/admin/events/{event_id}/speakers", include_in_schema=False)
@access_router.get("/admin/events/{event_id}/speakers/{event_speaker_id}", include_in_schema=False)
async def speaker_directory_page(
    request: Request, event_id: str | None = None, event_speaker_id: str | None = None
) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    scope = await require_document_event(request, event_id)
    await require_document_event_speaker(request, scope, event_speaker_id)
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


@access_router.get("/people/{user_id}", include_in_schema=False)
async def public_person_profile_page(user_id: str) -> Response:
    return Response(
        _asset("public_profile.html"),
        media_type="text/html",
        headers={"Cache-Control": "public, max-age=300"},
    )


@access_router.get("/people/assets/profile.js", include_in_schema=False)
async def public_person_profile_javascript() -> Response:
    return Response(
        _asset("public_profile.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@access_router.get("/admin/events/{event_id}/messages", include_in_schema=False)
async def speaker_messages_page(event_id: str, request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    await require_document_event(request, event_id)
    return Response(
        _asset("speaker_messages.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/people/assets/people.js", include_in_schema=False)
async def speaker_directory_javascript(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("speaker_directory.js"), media_type="text/javascript"
    )


@access_router.get("/admin/people/assets/search.js", include_in_schema=False)
async def people_search_javascript() -> Response:
    return Response(_asset("people_search.js"), media_type="text/javascript")


@access_router.get("/admin/speakers/assets/messages.js", include_in_schema=False)
async def speaker_messages_javascript(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("speaker_messages.js"), media_type="text/javascript"
    )


@access_router.get("/account", include_in_schema=False)
async def account_page() -> Response:
    return Response(
        _asset("account.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/organization", include_in_schema=False)
async def organization_settings_page(request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    return Response(
        _asset("organization_admin.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/account/assets/account.js", include_in_schema=False)
async def account_javascript() -> Response:
    return Response(_asset("account.js"), media_type="text/javascript")


@access_router.get("/admin/organization/assets/organization.js", include_in_schema=False)
async def organization_admin_javascript() -> Response:
    return Response(_asset("organization_admin.js"), media_type="text/javascript")


@access_router.get("/admin/events/{event_id}/access", include_in_schema=False)
@access_router.get("/admin/events/{event_id}/reviewers", include_in_schema=False)
async def event_access_page(event_id: str, request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    # Grant APIs opt into archived events so access can still be reviewed and
    # revoked after an event is archived.
    await require_document_event(
        request,
        event_id,
        Permission.RESOURCE_ACCESS_MANAGE,
        include_archived=True,
    )
    return Response(
        _asset("access_admin.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@access_router.get("/admin/access/assets/access.js", include_in_schema=False)
async def event_access_javascript(request: Request) -> Response:
    return content_addressed_asset(request, _asset("access_admin.js"), media_type="text/javascript")


@access_router.get("/admin/events", include_in_schema=False)
async def events_page(request: Request) -> RedirectResponse:
    destination = "/admin"
    if request.url.query:
        destination = f"{destination}?{request.url.query}"
    return RedirectResponse(
        destination,
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )


class BootstrapCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization_name: str = Field(min_length=1, max_length=200)
    admin_name: str = Field(min_length=1, max_length=200)
    admin_first_name: str | None = Field(default=None, min_length=1, max_length=100)
    admin_last_name: str | None = Field(default=None, min_length=1, max_length=100)
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
        if (self.admin_first_name is None) != (self.admin_last_name is None):
            raise ValueError("administrator first and last name must be provided together")
        values = (
            self.event_name,
            self.starts_at_ms,
            self.ends_at_ms,
            self.time_zone,
            self.event_location,
            self.event_description,
            self.event_delivery_mode,
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
    biography: str = Field(default="", max_length=5000)

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
    display_name: str = ""
    job_title: str = ""
    company: str = ""
    biography: str = ""


class InvitationIssued(InvitationView):
    access_url: str


class InvitationList(BaseModel):
    data: list[InvitationView]


SpeakerImportDisposition = Literal["import", "skip", "separate_person"]
SpeakerImportOutcome = Literal[
    "ready",
    "created",
    "skipped",
    "skipped_duplicate",
    "skipped_existing_speaker",
    "skipped_existing_invitation",
    "needs_resolution",
    "rejected",
    "failed",
]


class SpeakerInvitationImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    row_number: int = Field(ge=2)
    # Field limits are checked per row in the endpoint. Putting them on the
    # transport model would make one oversized CSV cell reject the whole batch.
    email: str = ""
    display_name: str = ""
    job_title: str = ""
    company: str = ""
    biography: str = ""
    disposition: SpeakerImportDisposition = "import"


class SpeakerInvitationImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["preview", "execute"]
    rows: list[SpeakerInvitationImportRow] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_row_numbers(self) -> "SpeakerInvitationImportRequest":
        numbers = [row.row_number for row in self.rows]
        if len(numbers) != len(set(numbers)):
            raise ValueError("row_number values must be unique")
        return self


class SpeakerInvitationImportResult(BaseModel):
    row_number: int
    email: str
    display_name: str
    outcome: SpeakerImportOutcome
    reason: str = ""
    allowed_dispositions: list[SpeakerImportDisposition] = Field(default_factory=list)
    invitation_id: str | None = None


class SpeakerInvitationImportResponse(BaseModel):
    mode: Literal["preview", "execute"]
    data: list[SpeakerInvitationImportResult]
    created_count: int = 0
    skipped_count: int = 0
    needs_resolution_count: int = 0
    rejected_count: int = 0
    failed_count: int = 0


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
    draft_starts_at_ms: int | None = None
    draft_ends_at_ms: int | None = None
    draft_delivery_mode: Literal["in_person", "virtual", "hybrid"] | None = None
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
    schedule_status: Literal["not_started", "draft", "ready", "published", "updates_pending"] = (
        "not_started"
    )
    cfp_status: Literal["not_started", "draft", "published", "closed"] = "not_started"


class EventList(BaseModel):
    data: list[EventView]
    next_cursor: str | None = None


class OwnershipRecoveryEventView(BaseModel):
    event_id: str
    name: str
    status: Literal["draft", "active", "archived"]
    current_owner_user_id: str
    current_owner_email: str


class OwnershipRecoveryEventList(BaseModel):
    data: list[OwnershipRecoveryEventView]
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
    starts_at_ms: int | None = Field(default=None, ge=0)
    ends_at_ms: int | None = Field(default=None, ge=0)
    time_zone: str = Field(min_length=1, max_length=100)
    location: str = Field(default="", max_length=500)
    delivery_mode: Literal["in_person", "virtual", "hybrid"] | None = None
    description: str = Field(default="", max_length=2000)
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
        if (
            self.starts_at_ms is not None
            and self.ends_at_ms is not None
            and self.ends_at_ms <= self.starts_at_ms
        ):
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
    organization_name: str
    permissions: list[Literal["owner", "view", "edit", "manage"]]


class SessionEventAccess(BaseModel):
    organization_id: str
    event_id: str
    event_name: str
    permissions: list[Literal["owner", "view", "edit", "manage"]] = Field(default_factory=list)
    assignments: list[Literal["reviewer", "speaker"]] = Field(default_factory=list)


class ResourceGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    permission: Literal["view", "edit", "manage"]


class ResourceGrantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission: Literal["view", "edit", "manage"]


class ResourceGrantView(BaseModel):
    user_id: str
    email: str
    permission: Literal["owner", "view", "edit", "manage"]
    status: Literal["active"] = "active"


class ResourceGrantList(BaseModel):
    data: list[ResourceGrantView]


class OrganizationActivityView(BaseModel):
    activity_id: str
    actor_id: str | None = None
    actor_name: str
    operation: Literal["create", "read", "update", "delete"]
    resource_type: str
    resource_id: str
    subject_name: str | None = None
    event_id: str | None = None
    occurred_at_ms: int


class OrganizationActivityList(BaseModel):
    data: list[OrganizationActivityView]


class EventOwnershipTransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=320)
    reason: str | None = Field(default=None, max_length=1000)
    grant_previous_owner_manage: bool = False


class EventOwnershipTransferView(BaseModel):
    transfer_id: str
    event_id: str
    previous_owner_user_id: str
    new_owner_user_id: str
    previous_owner_permission: Literal["manage"] | None = None
    transferred_at_ms: int


class OrganizationOwnershipTransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    email: str = Field(min_length=3, max_length=320)


class OrganizationOwnershipTransferView(BaseModel):
    transfer_id: str
    organization_id: str
    previous_owner_user_id: str
    new_owner_user_id: str
    previous_owner_permission: Literal["manage"] = "manage"
    transferred_at_ms: int


class CurrentSession(BaseModel):
    authenticated: bool = True
    user_id: str
    email: str
    display_name: str | None = None
    profile_complete: bool = False
    csrf_token: str
    default_email_sender_name: str | None = None
    default_email_address: str | None = None
    account_roles: list[Literal["organizer", "reviewer", "speaker"]] = Field(default_factory=list)
    active_role: Literal["organizer", "reviewer", "speaker"]
    default_role: Literal["organizer", "reviewer", "speaker"] | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    event_id: str | None = None
    organization_access: list[SessionOrganizationAccess] = Field(default_factory=list)
    event_access: list[SessionEventAccess] = Field(default_factory=list)


async def _default_account_role(db, user_id: str) -> str | None:
    row = row_mapping(
        await db.prepare(
            """SELECT role FROM user_roles
               WHERE user_id=?1 AND status='active' AND is_default=1
               LIMIT 1"""
        )
        .bind(user_id)
        .first()
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
    public_profile_enabled: bool = False
    profile_complete: bool
    roles: list[Literal["organizer", "reviewer", "speaker"]] = Field(default_factory=list)
    has_password: bool = False
    version: int


class AccountProfileUpdated(AccountProfileView):
    """Profile mutation result, including a replacement CSRF token when rotated."""

    csrf_token: str | None = None


class PublicPersonProfileView(BaseModel):
    """Intentionally small profile safe for an unauthenticated response."""

    user_id: str
    display_name: str
    job_title: str | None = None
    company: str | None = None
    biography: str | None = None
    website_url: str | None = None
    linkedin_url: str | None = None
    x_url: str | None = None
    headshot_url: str | None = None


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
    public_profile_enabled: bool = False
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


class SubmissionRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    job_title: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    password: str = Field(min_length=15, max_length=128)
    password_confirmation: str = Field(min_length=15, max_length=128)

    @model_validator(mode="after")
    def matching_password(self) -> "SubmissionRegistration":
        if self.password != self.password_confirmation:
            raise ValueError("password confirmation does not match")
        return self


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
        body.event_name,
        body.starts_at_ms,
        body.ends_at_ms,
        body.time_zone,
        body.event_location,
        body.event_description,
        body.event_delivery_mode,
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
        db.prepare("DELETE FROM instance_setup_credentials WHERE singleton_key='primary'")
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO organizations
               (id,name,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,'active',?3,?3)"""
        ).bind(organization_id, body.organization_name, now)
    )
    admin_email, normalized = _email(body.admin_email)
    admin_display_name = (
        f"{body.admin_first_name} {body.admin_last_name}"
        if body.admin_first_name is not None and body.admin_last_name is not None
        else body.admin_name
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO users
           (id,email,normalized_email,first_name,last_name,display_name,
            job_title,company,time_zone,status,
            email_verified_at_ms,created_at_ms,updated_at_ms)
           VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,'active',?10,?10,?10)"""
        ).bind(
            user_id,
            admin_email,
            normalized,
            body.admin_first_name,
            body.admin_last_name,
            admin_display_name,
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
    batch.add_statement(
        db.prepare(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES(?1,'organizer','active',?2,?2,1)"""
        ).bind(user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,
                created_at_ms,updated_at_ms)
               VALUES(?1,'organization',?2,?2,'active',?3,?3)"""
        ).bind(organization_id, user_id, now)
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
                """INSERT INTO owned_resources
                   (id,resource_type,created_by_user_id,owner_user_id,status,
                    created_at_ms,updated_at_ms)
                   VALUES(?1,'event',?2,?2,'active',?3,?3)"""
            ).bind(event_id, user_id, now)
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
    return await _account_profile_for_user(database(request), authenticated.actor.user_id)


async def _account_profile_for_user(db: D1Database, user_id: str) -> AccountProfileView:
    """Read a profile for an already authenticated user identity.

    Mutation handlers use this after rotating the caller's session; re-running
    cookie authentication against the incoming request would necessarily see
    the retired session rather than the replacement cookie on the response.
    """
    row = row_mapping(
        await db.prepare(
            """SELECT u.email,u.first_name,u.last_name,u.display_name,u.job_title,u.company,
                      u.time_zone,u.description,u.website_url,u.linkedin_url,u.x_url,
                      u.public_profile_enabled,u.version,
                      u.profile_completed_at_ms IS NOT NULL AS profile_complete,
                      CASE WHEN h.user_id IS NULL THEN NULL
                           ELSE '/api/v1/account/headshot' END AS headshot_url,
                      EXISTS(SELECT 1 FROM password_credentials c
                             WHERE c.user_id=u.id AND c.status='active') AS has_password
               FROM users u LEFT JOIN user_headshots h ON h.user_id=u.id
               WHERE u.id=?1 AND u.status='active' LIMIT 1"""
        )
        .bind(user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=401)
    roles = result_rows(
        await db.prepare(
            """SELECT role FROM user_roles
               WHERE user_id=?1 AND status='active' ORDER BY role"""
        )
        .bind(user_id)
        .all()
    )
    return AccountProfileView(**row, roles=[str(item["role"]) for item in roles])


@access_router.patch(
    "/api/v1/account/profile",
    response_model=AccountProfileUpdated,
    operation_id="updateAccountProfile",
    tags=["authentication"],
)
async def update_account_profile(
    body: AccountProfileUpdate, request: Request, response: Response
) -> AccountProfileUpdated:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    db, now = database(request), utc_now_ms()
    current = row_mapping(
        await db.prepare(
            """SELECT version,display_name,job_title,company,description,
                      website_url,linkedin_url,x_url
               FROM users WHERE id=?1 AND status='active' LIMIT 1"""
        )
        .bind(authenticated.actor.user_id)
        .first()
    )
    if current is None or int(current["version"]) != body.version:
        raise HTTPException(status_code=409)
    verifier = None
    if body.password is not None:
        try:
            verifier = hash_password(body.password, secret(request, "PASSWORD_PEPPER"))
        except PasswordPolicyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if verifier is not None and authenticated.actor.active_persona is None:
        # A replacement session cannot be scoped without an active role. Keep
        # this precondition ahead of batch construction so no queued mutation
        # obscures that password rotation is all-or-nothing at this boundary.
        raise HTTPException(status_code=409)
    display_name = f"{body.first_name} {body.last_name}".strip()
    batch = CommandBatch(db)
    users_statement_index = batch.statement_count
    batch.add_statement(
        db.prepare(
            """UPDATE users SET first_name=?1,last_name=?2,display_name=?3,job_title=?4,
               company=?5,time_zone=?6,description=?7,website_url=?8,linkedin_url=?9,x_url=?10,
               public_profile_enabled=?11,
               profile_completed_at_ms=COALESCE(profile_completed_at_ms,?12),
               authorization_version=authorization_version+?13,
               version=version+1,updated_at_ms=?12
               WHERE id=?14 AND status='active' AND version=?15"""
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
            int(body.public_profile_enabled),
            now,
            1 if verifier is not None else 0,
            authenticated.actor.user_id,
            body.version,
        )
    )
    account_links = [value for value in (body.website_url, body.linkedin_url, body.x_url) if value]
    previous_account_links = [
        str(value)
        for value in (current["website_url"], current["linkedin_url"], current["x_url"])
        if value
    ]
    source_guard_values = (
        authenticated.actor.user_id,
        body.version + 1,
        now,
        display_name,
        body.job_title or None,
        body.company or None,
        body.description or None,
        body.website_url or None,
        body.linkedin_url or None,
        body.x_url or None,
    )
    # Positions of the mirror statements in the batch, checked after execution.
    # The version predicate makes a stale mirror a zero-row no-op by design (and
    # a row archived mid-request no-ops the same way), but a silent no-op is
    # undiagnosable: the response is 200 and the person record simply never
    # received a real edit. The zero-row case is therefore recorded as a
    # degradation -- the same channel used when a queue publish is absorbed.
    mirror_statement_indexes: list[int] = []
    # Mirror an account field onto the person record only when this save actually
    # changed it on the user record. The account page renders `users`, while the
    # speaker profile form, the organizer roster, and invitation import write
    # `people`; copying every field on every save therefore let an account save
    # that never displayed those values erase them -- an organizer-entered job
    # title, company, or link list vanished the next time the speaker saved their
    # account page. Each `?new IS ?old` guard keeps the person's value when the
    # account page left the field alone, and still propagates a real edit,
    # including an intentional clear. `IS` rather than `=` so NULL compares.
    for person in result_rows(
        await db.prepare(
            """SELECT id,links_json,version FROM people
               WHERE user_id=?1 AND archived_at_ms IS NULL"""
        )
        .bind(authenticated.actor.user_id)
        .all()
    ):
        # Links are a set on the person record and three named slots on the
        # account page, so replace only the slots this save touched and leave any
        # other link the speaker or an organizer added in place.
        person_links = [str(value) for value in json.loads(str(person["links_json"]))]
        retained_links = [
            value
            for value in person_links
            if value not in previous_account_links and value not in account_links
        ]
        new_account_links: list[str] = []
        for value in account_links:
            if value not in new_account_links:
                new_account_links.append(value)
        # The ten-link cap is an application rule; the schema only checks
        # json_valid. When it bites, the links this save is actually carrying
        # must survive, so the retained tail is trimmed instead -- a plain
        # merged[:10] silently dropped the account link the user had just typed
        # while the users row saved it and the page rendered it back as kept.
        merged_links = (
            retained_links[: max(0, 10 - len(new_account_links))] + new_account_links
        )
        # Asked of the batch, not assumed: a hardcoded offset would silently
        # read the wrong statement's row count the day anything is added to the
        # batch above this loop. establish_session records its index this way.
        mirror_statement_indexes.append(batch.statement_count)
        batch.add_statement(
            db.prepare(
                f"""UPDATE people SET
                          display_name=CASE WHEN ?11 IS ?21 THEN display_name ELSE ?11 END,
                          job_title=CASE WHEN ?12 IS ?13 THEN job_title ELSE ?12 END,
                          company=CASE WHEN ?14 IS ?15 THEN company ELSE ?14 END,
                          -- NULL, not "": `users.description` above is bound the
                          -- same way, and the public profile resolves
                          -- `COALESCE(u.description,p.biography)`. Writing "" here
                          -- made an empty account-page save overwrite a bio an
                          -- organizer had set on the person record, and the COALESCE
                          -- then returned the empty string instead of falling
                          -- through to it.
                          biography=CASE WHEN ?16 IS ?17 THEN biography ELSE ?16 END,
                          links_json=?18,version=version+1,updated_at_ms=?19
                   WHERE id=?20 AND user_id=?1 AND archived_at_ms IS NULL
                     -- The speaker profile form and the organizer roster update
                     -- this row under optimistic concurrency; a mirror computed
                     -- from a pre-batch read must not outrank them. If the row
                     -- moved since the read above, this statement matches zero
                     -- rows and the account save completes without the mirror,
                     -- leaving the concurrent author's write -- and their
                     -- version guard -- intact.
                     AND version=?22
                     AND {_ACCOUNT_PROFILE_SOURCE_GUARD_SQL}"""  # noqa: S608
            ).bind(
                *source_guard_values,
                display_name,
                body.job_title or None,
                str(current["job_title"]) if current["job_title"] else None,
                body.company or None,
                str(current["company"]) if current["company"] else None,
                body.description or None,
                str(current["description"]) if current["description"] else None,
                json.dumps(merged_links, separators=(",", ":")),
                now,
                str(person["id"]),
                # The previous DERIVED name, not the person's: the mirror should
                # fire when the account holder actually renamed themselves and
                # stay out of the way of a roster name an organizer curated --
                # the same rule the three guarded fields above follow. This was
                # the one field still copied unconditionally, which re-created
                # the erasure bug this mirror was rebuilt to fix.
                str(current["display_name"]) if current["display_name"] else None,
                int(person["version"]),
            )
        )
    if body.description:
        batch.add_statement(
            db.prepare(
                f"""UPDATE speaker_tasks SET state='completed',completed_at_ms=?11,
                          version=version+1,updated_at_ms=?11
                   WHERE task_type IN ('profile','biography') AND state='open'
                     AND EXISTS (
                       SELECT 1 FROM event_speakers es JOIN people p ON p.id=es.person_id
                       WHERE es.organization_id=speaker_tasks.organization_id
                         AND es.event_id=speaker_tasks.event_id
                         AND es.id=speaker_tasks.event_speaker_id AND p.user_id=?1
                     )
                     AND {_ACCOUNT_PROFILE_SOURCE_GUARD_SQL}"""  # noqa: S608
            ).bind(*source_guard_values, now)
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
        # Queue this after the blanket revoke above. D1 executes the batch in
        # statement order, so the new row cannot be retired by the password
        # change that it replaces.
        replacement_session = establish_session_with_current_authorization_version(
            batch=batch,
            db=db,
            request=request,
            user_id=authenticated.actor.user_id,
            role=authenticated.actor.active_persona,
            now_ms=now,
        )
    else:
        replacement_session = None
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
            metadata={
                "session_rotated": verifier is not None,
                "other_sessions_revoked": verifier is not None,
            },
        )
    )
    results = await batch.execute()
    users_changes = statement_changes(results, users_statement_index)
    if users_changes == 0:
        # The batch is committed. Dependent mirrors/tasks stood down because
        # their source-state guard did not observe this request's users write;
        # the unconditional success audit is the known exception. A rare
        # password/revocation race may also have queued an unissued session.
        if replacement_session is not None:
            cleanup = CommandBatch(db)
            _revoke_session(
                cleanup,
                db,
                replacement_session.session_id,
                "stale_profile_version",
                now,
            )
            try:
                await cleanup.execute()
            except Exception:  # noqa: BLE001 - post-commit cleanup is best effort
                record_degradation(request, "account_stale_session_cleanup_failed")
        raise HTTPException(status_code=409)
    if users_changes is None:
        record_degradation(request, "account_profile_update_unverified")
    for index in mirror_statement_indexes:
        changes = statement_changes(results, index)
        if changes == 0:
            # Skipped: the person row's version moved (or the row was archived)
            # between the read and the batch, so the mirror stood down.
            record_degradation(request, "account_person_mirror_skipped")
        elif changes is None:
            # Unreadable result shape. Unlike a session insert, no follow-up
            # read can settle whether the MIRROR landed -- a rival write leaves
            # the same bumped version -- so this must not quietly pass for
            # success: if the provider's shape ever drifts, every account save
            # records this code and the drift is found the day it ships.
            record_degradation(request, "account_person_mirror_unverified")
    if replacement_session is not None:
        try:
            await _confirm_session_established(results, replacement_session, db)
        except Exception:
            # The batch is already committed: the password changed and old
            # sessions were revoked. Preserve the truthful 409 while ensuring
            # the post-commit failure is visible in completion telemetry.
            record_degradation(request, "account_session_rotation_unconfirmed")
            raise
        _set_session_cookie(response, request, replacement_session.session_token)
    profile = await _account_profile_for_user(db, authenticated.actor.user_id)
    return AccountProfileUpdated(
        **profile.model_dump(),
        csrf_token=(replacement_session.csrf_token if replacement_session else None),
    )


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
    await enforce_rate_limit(
        request,
        binding_name="HEADSHOT_UPLOAD_RATE_LIMITER",
        policy=RateLimitPolicy("account.headshot.upload", limit=3, window_seconds=60),
        subject=authenticated.actor.user_id,
    )
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
        database(request)
        .prepare(
            """INSERT INTO user_headshots
               (user_id,object_key,content_type,byte_size,checksum_sha256,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6)
               ON CONFLICT(user_id) DO UPDATE SET object_key=excluded.object_key,
                 content_type=excluded.content_type,byte_size=excluded.byte_size,
                 checksum_sha256=excluded.checksum_sha256,updated_at_ms=excluded.updated_at_ms"""
        )
        .bind(authenticated.actor.user_id, object_key, content_type, len(body), checksum, now)
    )
    batch.add_statement(
        database(request)
        .prepare(
            """UPDATE speaker_tasks SET state='completed',completed_at_ms=?1,
                      version=version+1,updated_at_ms=?1
               WHERE task_type='headshot' AND state='open'
                 AND EXISTS (
                   SELECT 1 FROM event_speakers es JOIN people p ON p.id=es.person_id
                   WHERE es.organization_id=speaker_tasks.organization_id
                     AND es.event_id=speaker_tasks.event_id
                     AND es.id=speaker_tasks.event_speaker_id AND p.user_id=?2
                 )"""
        )
        .bind(now, authenticated.actor.user_id)
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
        await database(request)
        .prepare("SELECT object_key,content_type FROM user_headshots WHERE user_id=?1 LIMIT 1")
        .bind(authenticated.actor.user_id)
        .first()
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


@access_router.get(
    "/api/v1/public/people/{user_id}",
    response_model=PublicPersonProfileView,
    tags=["public-profiles"],
)
async def public_person_profile(user_id: str, request: Request) -> PublicPersonProfileView:
    row = row_mapping(
        await database(request)
        .prepare(
            """SELECT u.id,u.display_name,u.first_name,u.last_name,
                      COALESCE(u.job_title,p.job_title) AS job_title,
                      COALESCE(u.company,p.company) AS company,
                      COALESCE(u.description,p.biography) AS description,
                      u.website_url,u.linkedin_url,u.x_url,p.display_name AS person_name,
                      CASE WHEN h.user_id IS NULL THEN 0 ELSE 1 END AS has_headshot
               FROM users u LEFT JOIN user_headshots h ON h.user_id=u.id
               LEFT JOIN people p ON p.id=(
                 SELECT p2.id FROM people p2
                 WHERE p2.user_id=u.id AND p2.archived_at_ms IS NULL
                 ORDER BY p2.updated_at_ms DESC,p2.id DESC LIMIT 1
               )
               WHERE u.id=?1 AND u.status='active' AND u.deleted_at_ms IS NULL
                 AND u.public_profile_enabled=1
               LIMIT 1"""
        )
        .bind(user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    name_parts = (
        str(row["first_name"] or "").strip(),
        str(row["last_name"] or "").strip(),
    )
    display_name = (
        str(row["display_name"] or "").strip()
        or " ".join(value for value in name_parts if value)
        or str(row["person_name"] or "").strip()
    )
    if not display_name:
        raise HTTPException(status_code=404)
    return PublicPersonProfileView(
        user_id=user_id,
        display_name=display_name,
        job_title=str(row["job_title"]) if row["job_title"] else None,
        company=str(row["company"]) if row["company"] else None,
        biography=str(row["description"]) if row["description"] else None,
        website_url=str(row["website_url"]) if row["website_url"] else None,
        linkedin_url=str(row["linkedin_url"]) if row["linkedin_url"] else None,
        x_url=str(row["x_url"]) if row["x_url"] else None,
        headshot_url=(f"/api/v1/public/people/{user_id}/headshot" if row["has_headshot"] else None),
    )


@access_router.get(
    "/api/v1/public/people/{user_id}/headshot",
    response_class=StreamingResponse,
    tags=["public-profiles"],
)
async def public_person_headshot(user_id: str, request: Request) -> StreamingResponse:
    row = row_mapping(
        await database(request)
        .prepare(
            """SELECT h.object_key,h.content_type FROM user_headshots h
               JOIN users u ON u.id=h.user_id
               WHERE h.user_id=?1 AND u.status='active' AND u.deleted_at_ms IS NULL
                 AND u.public_profile_enabled=1
               LIMIT 1"""
        )
        .bind(user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    stored = await _event_logo_bucket(request).get(str(row["object_key"]))
    if stored is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        _stream_event_logo(stored),
        media_type=str(row["content_type"]),
        headers={"Cache-Control": "public, max-age=3600"},
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
        .bind(authenticated.actor.user_id)
        .first()
    )
    if row is not None:
        await (
            db.prepare("DELETE FROM user_headshots WHERE user_id=?1")
            .bind(authenticated.actor.user_id)
            .run()
        )
        await _event_logo_bucket(request).delete(str(row["object_key"]))
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


async def _managed_speaker_headshot_target(request: Request, event_id: str, event_speaker_id: str):
    row = row_mapping(
        await database(request)
        .prepare(
            """SELECT es.organization_id,es.event_id,p.user_id
               FROM event_speakers es JOIN people p
                 ON p.organization_id=es.organization_id AND p.id=es.person_id
               WHERE es.id=?1 AND es.event_id=?2 AND p.user_id IS NOT NULL LIMIT 1"""
        )
        .bind(event_speaker_id, event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request,
        Permission.SPEAKER_MANAGE,
        ResourceContext(str(row["organization_id"]), event_id),
        mutation=request.method != "GET",
        mutation_media_types=_EVENT_IMAGE_MEDIA_TYPES,
    )
    return authenticated, row


@access_router.get(
    "/api/v1/admin/events/{event_id}/speakers/{event_speaker_id}/headshot",
    response_class=StreamingResponse,
    tags=["speaker-onboarding"],
)
async def admin_speaker_headshot(
    event_id: str, event_speaker_id: str, request: Request
) -> StreamingResponse:
    _, target = await _managed_speaker_headshot_target(request, event_id, event_speaker_id)
    row = row_mapping(
        await database(request)
        .prepare("SELECT object_key,content_type FROM user_headshots WHERE user_id=?1 LIMIT 1")
        .bind(target["user_id"])
        .first()
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


@access_router.put(
    "/api/v1/admin/events/{event_id}/speakers/{event_speaker_id}/headshot",
    status_code=204,
    tags=["speaker-onboarding"],
)
async def upload_admin_speaker_headshot(
    event_id: str, event_speaker_id: str, request: Request
) -> Response:
    authenticated, target = await _managed_speaker_headshot_target(
        request, event_id, event_speaker_id
    )
    await enforce_rate_limit(
        request,
        binding_name="HEADSHOT_UPLOAD_RATE_LIMITER",
        policy=RateLimitPolicy("admin.speaker.headshot.upload", limit=3, window_seconds=60),
        subject=f"{authenticated.actor.user_id}:{event_id}:{event_speaker_id}",
    )
    body, content_type, extension = await _read_headshot(request)
    checksum = hashlib.sha256(body).digest()
    db = database(request)
    slot = row_mapping(
        await db.prepare(
            """SELECT id FROM speaker_assets
               WHERE organization_id=?1 AND event_id=?2 AND event_speaker_id=?3
                 AND kind='headshot' AND submission_id IS NULL
               ORDER BY created_at_ms,id LIMIT 1"""
        )
        .bind(target["organization_id"], event_id, event_speaker_id)
        .first()
    )
    asset_id = str(slot["id"]) if slot is not None else new_id()
    asset_version_id = new_id()
    generation = int(
        await db.prepare(
            "SELECT COALESCE(MAX(generation),0)+1 AS generation "
            "FROM speaker_asset_versions WHERE asset_id=?1"
        )
        .bind(asset_id)
        .first("generation")
    )
    environment_value = request.scope.get("env")
    if not malware_scan_disabled(environment_value):
        try:
            scan = await SignedScannerAdapter(environment_value).scan(
                body,
                job=ScanJob(
                    schema_version=1,
                    organization_id=str(target["organization_id"]),
                    event_id=event_id,
                    asset_version_id=asset_version_id,
                    generation=generation,
                    checksum_sha256=checksum,
                    job_id=asset_version_id,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=503) from exc
        if scan.verdict != "clean":
            raise HTTPException(status_code=400)
    object_key = f"private/speaker-assets/{asset_version_id}/headshot.{extension}"
    await _event_logo_bucket(request).put(object_key, body)
    now = utc_now_ms()
    batch = CommandBatch(db)
    if slot is None:
        batch.add_statement(
            db.prepare(
                """INSERT INTO speaker_assets
                   (id,organization_id,event_id,event_speaker_id,submission_id,task_id,
                    kind,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,NULL,NULL,'headshot',?5,?5)"""
            ).bind(asset_id, target["organization_id"], event_id, event_speaker_id, now)
        )
    else:
        batch.add_statement(
            db.prepare(
                """UPDATE speaker_asset_versions SET is_current=0,scan_state='superseded'
                   WHERE asset_id=?1 AND is_current=1"""
            ).bind(asset_id)
        )
        batch.add_statement(
            db.prepare(
                "UPDATE speaker_assets SET version=version+1,updated_at_ms=?2 WHERE id=?1"
            ).bind(asset_id, now)
        )
    batch.add_statement(
        db.prepare(
            """INSERT INTO speaker_asset_versions
               (id,organization_id,event_id,event_speaker_id,asset_id,generation,object_key,
                original_filename,content_type,byte_size,checksum_sha256,scan_state,is_current,
                created_at_ms,uploaded_at_ms,scan_started_at_ms,scanned_at_ms,scan_result_code,
                uploaded_by_user_id,version_comment)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,'clean',1,
                      ?12,?12,?12,?12,'clean',?13,'Organizer headshot upload')"""
        ).bind(
            asset_version_id,
            target["organization_id"],
            event_id,
            event_speaker_id,
            asset_id,
            generation,
            object_key,
            f"headshot.{extension}",
            content_type,
            len(body),
            checksum,
            now,
            authenticated.actor.user_id,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO user_headshots
               (user_id,speaker_asset_version_id,object_key,content_type,byte_size,
                checksum_sha256,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7)
               ON CONFLICT(user_id) DO UPDATE SET object_key=excluded.object_key,
                 content_type=excluded.content_type,byte_size=excluded.byte_size,
                 checksum_sha256=excluded.checksum_sha256,
                 speaker_asset_version_id=excluded.speaker_asset_version_id,
                 updated_at_ms=excluded.updated_at_ms"""
        ).bind(
            target["user_id"],
            asset_version_id,
            object_key,
            content_type,
            len(body),
            checksum,
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE speaker_tasks SET state='completed',completed_at_ms=?1,
                      version=version+1,updated_at_ms=?1
               WHERE organization_id=?2 AND event_id=?3 AND event_speaker_id=?4
                 AND task_type='headshot' AND state='open'"""
        ).bind(now, target["organization_id"], event_id, event_speaker_id)
    )
    batch.audit(
        AuditEvent(
            organization_id=str(target["organization_id"]),
            event_id=event_id,
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="speaker.headshot.admin_update",
            target_type="event_speaker",
            target_id=event_speaker_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"content_type": content_type, "byte_size": len(body)},
        )
    )
    await batch.execute()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@access_router.get(
    "/api/v1/admin/organizations",
    response_model=OrganizationList,
    tags=["administration"],
)
async def list_organizations(request: Request) -> OrganizationList:
    authenticated = await authenticate_request(request)
    db = database(request)
    if authenticated.actor.active_persona is not Persona.ORGANIZER:
        raise HTTPException(status_code=403)
    organizations: list[OrganizationView] = []
    organization_ids = {
        resource_id for resource_id in authenticated.actor.owned_resource_ids if resource_id
    }
    organization_ids.update(authenticated.actor.resource_grants)
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


async def _has_event_access_in_organization(db, user_id: str, organization_id: str) -> bool:
    row = row_mapping(
        await db.prepare(
            """SELECT 1 AS found FROM events e
               JOIN owned_resources owned ON owned.id=e.id AND owned.resource_type='event'
               LEFT JOIN resource_access_grants grant_access
                 ON grant_access.resource_id=e.id AND grant_access.user_id=?1
                AND grant_access.status='active'
               WHERE e.organization_id=?2
                 AND (owned.owner_user_id=?1
                      OR grant_access.permission IN ('edit','manage'))
               LIMIT 1"""
        )
        .bind(user_id, organization_id)
        .first()
    )
    return row is not None


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
    if authenticated.actor.active_persona is not Persona.ORGANIZER:
        raise HTTPException(status_code=403)
    organization_grants = authenticated.actor.resource_grants.get(
        organization_id, frozenset()
    )
    has_organization_access = (
        organization_id in authenticated.actor.owned_resource_ids
        or bool(organization_grants)
    )
    manages_organization = (
        organization_id in authenticated.actor.owned_resource_ids
        or ResourceGrant.MANAGE in organization_grants
    )
    db = database(request)
    manages_event_in_organization = await _has_event_access_in_organization(
        db, authenticated.actor.user_id, organization_id
    )
    if not has_organization_access and not manages_event_in_organization:
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
    access_join = ""
    if not manages_organization:
        access_join = (
            " JOIN owned_resources owned ON owned.id=e.id AND owned.resource_type='event'"
            " LEFT JOIN resource_access_grants grant_access ON grant_access.resource_id=e.id"
            " AND grant_access.user_id=? AND grant_access.status='active'"
        )
        binds.insert(0, authenticated.actor.user_id)
        conditions.append(
            "(owned.owner_user_id=? OR grant_access.permission IN ('edit','manage'))"
        )
        binds.append(authenticated.actor.user_id)
    if view == "active":
        conditions.append("e.status='active' AND e.ends_at_ms>=?")
        binds.append(now)
    elif view == "draft":
        conditions.append("e.status='draft'")
    elif view == "past":
        conditions.append("((e.status='active' AND e.ends_at_ms<?) OR e.status='archived')")
        binds.append(now)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
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
    statement = db.prepare(
        "SELECT e.id,e.organization_id,e.name,e.starts_at_ms,e.ends_at_ms,e.time_zone,"  # noqa: S608, E501
        "e.location,e.delivery_mode,e.description,e.accent_color,e.logo_url,"
        "e.cover_image_url,e.website_url,e.email_sender_name,e.email_reply_to,"
        "e.status,e.version,e.draft_starts_at_ms,e.draft_ends_at_ms,e.draft_delivery_mode,"
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
        "AND sr.status='draft') THEN 'draft' ELSE 'not_started' END AS schedule_status,"
        "COALESCE((SELECT f.status FROM call_for_speaker_forms f "
        "WHERE f.organization_id=e.organization_id AND f.event_id=e.id "
        "ORDER BY f.version DESC,f.updated_at_ms DESC LIMIT 1),'not_started') AS cfp_status "
        "FROM events e"
        f"{access_join} WHERE {' AND '.join(conditions)} "
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


_EVENTS_CURSOR = SignedCursorContract(
    "admin_events", {"id": BOUNDED_ID, "starts": STRICT_INT}
)


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
    decoded = _EVENTS_CURSOR.decode(
        request,
        value,
        scope={"org": organization_id, "order": order, "q": search, "view": view},
    )
    if decoded is None:
        return None
    starts_at, row_id = decoded["starts"], decoded["id"]
    return starts_at, row_id


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

    Counts include only events the active organizer owns or can explicitly manage."""
    authenticated = await authenticate_request(request)
    if authenticated.actor.active_persona is not Persona.ORGANIZER:
        raise HTTPException(status_code=403)
    has_organization_access = organization_id in authenticated.actor.owned_resource_ids or bool(
        authenticated.actor.resource_grants.get(organization_id)
    )
    db = database(request)
    manages_event_in_organization = await _has_event_access_in_organization(
        db, authenticated.actor.user_id, organization_id
    )
    if not has_organization_access and not manages_event_in_organization:
        raise HTTPException(status_code=404)
    # The proposal-attachment probe correlates on (organization, event,
    # event_speaker) so it rides idx_submission_speakers_speaker instead of
    # scanning submission_speakers, and speakers are counted as UNIQUE people
    # (DISTINCT person_id), not per-event appearances.
    has_proposal = (
        "(EXISTS (SELECT 1 FROM submission_speakers ss"
        " WHERE ss.organization_id=es.organization_id"
        " AND ss.event_id=es.event_id AND ss.event_speaker_id=es.id)"
        " OR EXISTS (SELECT 1 FROM accepted_session_participants participant"
        " WHERE participant.organization_id=es.organization_id"
        " AND participant.event_id=es.event_id AND participant.event_speaker_id=es.id))"
    )
    if False:  # pragma: no cover - retained temporarily while legacy SQL is removed
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
            db.prepare("SELECT COUNT(*) AS total FROM accepted_sessions WHERE organization_id=?1")
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
                "(SELECT ac.organizer_title FROM accepted_sessions ac"
                " JOIN accepted_session_participants participant"
                " ON participant.accepted_session_id=ac.id"
                " WHERE participant.event_speaker_id=es.id"
                " AND ac.source_type='organizer_created'"
                " ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),"
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
            "JOIN owned_resources owned ON owned.id=es.event_id "
            "LEFT JOIN resource_access_grants grant_access ON grant_access.resource_id=es.event_id"
            " AND grant_access.user_id=?2 AND grant_access.status='active' "
        )
        event_count = await (
            db.prepare(
                """SELECT COUNT(*) AS total FROM events e
                   JOIN owned_resources owned ON owned.id=e.id
                   LEFT JOIN resource_access_grants grant_access
                    ON grant_access.resource_id=e.id AND grant_access.user_id=?2
                    AND grant_access.status='active'
                   WHERE e.organization_id=?1
                    AND (owned.owner_user_id=?2 OR grant_access.permission IN ('edit','manage'))"""
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        speaker_count = await (
            db.prepare(
                "SELECT COUNT(DISTINCT es.person_id) AS total FROM event_speakers es "  # noqa: S608, E501
                f"{membership_join}"
                f"WHERE es.organization_id=?1 AND {has_proposal}"
                " AND (owned.owner_user_id=?2 OR grant_access.permission IN ('edit','manage'))"
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        session_count = await (
            db.prepare(
                "SELECT COUNT(*) AS total FROM accepted_sessions ac "
                "JOIN owned_resources owned ON owned.id=ac.event_id "
                "LEFT JOIN resource_access_grants grant_access "
                "ON grant_access.resource_id=ac.event_id "
                "AND grant_access.user_id=?2 AND grant_access.status='active' "
                "WHERE ac.organization_id=?1 AND (owned.owner_user_id=?2 "
                "OR grant_access.permission IN ('edit','manage'))"
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        proposal_count = await (
            db.prepare(
                "SELECT COUNT(*) AS total FROM submissions s "
                "JOIN owned_resources owned ON owned.id=s.event_id "
                "LEFT JOIN resource_access_grants grant_access "
                "ON grant_access.resource_id=s.event_id "
                "AND grant_access.user_id=?2 AND grant_access.status='active' "
                "WHERE s.organization_id=?1 AND s.status='submitted' "
                "AND (owned.owner_user_id=?2 OR grant_access.permission IN ('edit','manage'))"
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
                "JOIN owned_resources owned ON owned.id=er.event_id "
                "LEFT JOIN resource_access_grants grant_access "
                "ON grant_access.resource_id=er.event_id "
                "AND grant_access.user_id=?2 AND grant_access.status='active' "
                "WHERE er.organization_id=?1 AND (owned.owner_user_id=?2 "
                "OR grant_access.permission IN ('edit','manage'))"
            )
            .bind(organization_id, authenticated.actor.user_id)
            .first("total")
        )
        recent_rows = result_rows(
            await db.prepare(
                "SELECT p.id AS person_id,p.display_name,e.id AS event_id,"  # noqa: S608
                "e.name AS event_name,es.selection_status,"
                "COALESCE("
                "(SELECT ac.organizer_title FROM accepted_sessions ac"
                " JOIN accepted_session_participants participant"
                " ON participant.accepted_session_id=ac.id"
                " WHERE participant.event_speaker_id=es.id"
                " AND ac.source_type='organizer_created'"
                " ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),"
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
                "AND (owned.owner_user_id=?2 OR grant_access.permission IN ('edit','manage')) "
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
) -> str | None:
    return _EVENTS_CURSOR.encode(
        request,
        scope={"org": organization_id, "order": order, "q": search, "view": view},
        position={"id": row_id, "starts": starts_at_ms},
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
                      email_sender_name,email_reply_to,status,version,draft_starts_at_ms,
                      draft_ends_at_ms,draft_delivery_mode,
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
    (
        stored_start,
        stored_end,
        stored_delivery,
        draft_start,
        draft_end,
        draft_delivery,
    ) = _event_storage_values(body, body.status)
    if body.status == "active":
        _validate_event_can_activate(stored_end, now)
    source_scope = row_mapping(
        await db.prepare("SELECT id,organization_id FROM events WHERE id=?1 LIMIT 1")
        .bind(event_id)
        .first()
    )
    if source_scope is None:
        raise HTTPException(status_code=404)
    organization_id = str(source_scope["organization_id"])
    authenticated = await require_permission(
        request,
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(organization_id),
        mutation=True,
    )
    await require_permission(
        request,
        Permission.EVENT_MANAGE,
        ResourceContext(organization_id, event_id),
        mutation=False,
    )
    source = row_mapping(
        await db.prepare(
            """SELECT id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                      delivery_mode,description,accent_color,logo_url,cover_image_url,
                      website_url,email_sender_name,email_reply_to,version
               FROM events WHERE id=?1 AND organization_id=?2 LIMIT 1"""
        )
        .bind(event_id, organization_id)
        .first()
    )
    if source is None:
        raise HTTPException(status_code=404)
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
                          status,version,draft_starts_at_ms,draft_ends_at_ms,
                          draft_delivery_mode
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
    cover_image_url = str(copied_cover["asset_url"]) if copied_cover else body.cover_image_url
    batch.add_statement(
        db.prepare(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                email_sender_name,email_reply_to,status,draft_starts_at_ms,draft_ends_at_ms,
                draft_delivery_mode,created_at_ms,updated_at_ms)
               SELECT ?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,
                      ?17,?18,?19,?20,?16,?16
               WHERE EXISTS(SELECT 1 FROM events source
                            WHERE source.id=?21 AND source.organization_id=?2
                              AND source.version=?22)"""
        ).bind(
            duplicated_event_id,
            organization_id,
            duplicate_name,
            stored_start,
            stored_end,
            body.time_zone,
            body.location,
            stored_delivery,
            body.description,
            body.accent_color,
            logo_url,
            cover_image_url,
            body.website_url,
            body.email_sender_name,
            body.email_reply_to,
            now,
            body.status,
            draft_start,
            draft_end,
            draft_delivery,
            event_id,
            body.source_version,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,
                created_at_ms,updated_at_ms)
               VALUES(?1,'event',?2,?2,'active',?3,?3)"""
        ).bind(duplicated_event_id, authenticated.actor.user_id, now)
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
        starts_at_ms=stored_start,
        ends_at_ms=stored_end,
        time_zone=body.time_zone,
        location=body.location,
        delivery_mode=stored_delivery,
        draft_starts_at_ms=draft_start,
        draft_ends_at_ms=draft_end,
        draft_delivery_mode=draft_delivery,
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


def _validate_event_times(starts_at_ms: int | None, ends_at_ms: int | None) -> None:
    if starts_at_ms is not None and ends_at_ms is not None and ends_at_ms <= starts_at_ms:
        raise HTTPException(status_code=422)


def _event_storage_values(
    body: EventCreate, status: str
) -> tuple[int, int, str, int | None, int | None, str | None]:
    """Keep incomplete draft input without weakening the released event columns."""
    _validate_event_times(body.starts_at_ms, body.ends_at_ms)
    if status != "draft" and (
        body.starts_at_ms is None
        or body.ends_at_ms is None
        or body.delivery_mode is None
        or not body.location
        or not body.description
        or body.starts_at_ms < 0
    ):
        raise HTTPException(status_code=422, detail="complete the event details before activating")
    if status == "draft":
        complete_details = (
            body.starts_at_ms is not None
            and body.ends_at_ms is not None
            and body.delivery_mode is not None
            and bool(body.location)
            and bool(body.description)
        )
        stored_start = body.starts_at_ms if complete_details else 0
        stored_end = body.ends_at_ms if complete_details else 1
        stored_delivery = body.delivery_mode if complete_details else "in_person"
        return (
            stored_start,
            stored_end,
            stored_delivery,
            body.starts_at_ms,
            body.ends_at_ms,
            body.delivery_mode,
        )
    return (
        body.starts_at_ms,
        body.ends_at_ms,
        body.delivery_mode,
        None,
        None,
        None,
    )


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
                 AND (status='attached' OR created_at_ms>?5)
               LIMIT 1"""
        )
        .bind(
            organization_id,
            kind,
            asset_url,
            event_id,
            utc_now_ms() - PENDING_BRANDING_RETENTION_MS,
        )
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
async def public_event_branding_asset(asset_name: str, request: Request) -> StreamingResponse:
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
            """SELECT object_key,content_type,status FROM event_branding_assets
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
    cache_control = (
        "private, no-store"
        if str(row["status"]) == "pending"
        else "public, max-age=31536000, immutable"
    )
    return StreamingResponse(
        _stream_event_logo(stored),
        media_type=expected_content_type,
        headers={"Cache-Control": cache_control},
    )


@access_router.post(
    "/api/v1/admin/events/{event_id}/logo",
    response_model=EventLogoView,
    tags=["administration"],
)
async def upload_event_logo(event_id: str, request: Request) -> EventLogoView:
    db = database(request)
    event = row_mapping(
        await db.prepare("SELECT organization_id,version FROM events WHERE id=?1 LIMIT 1")
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
        await db.prepare("SELECT organization_id,version FROM events WHERE id=?1 LIMIT 1")
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
                    schema_version=1,
                    organization_id=str(event["organization_id"]),
                    event_id=event_id,
                    asset_version_id=image_id,
                    generation=1,
                    checksum_sha256=checksum,
                    job_id=image_id,
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
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="event.cover.upload",
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
    return EventCoverView(cover_image_url=image_url, version=int(event["version"]) + 1)


@access_router.get(
    "/api/v1/public/events/{event_id}/logo/{logo_name}",
    response_class=StreamingResponse,
    tags=["public-program"],
)
async def public_event_logo(event_id: str, logo_name: str, request: Request) -> StreamingResponse:
    logo_url = f"/api/v1/public/events/{event_id}/logo/{logo_name}"
    row = row_mapping(
        await database(request)
        .prepare("SELECT organization_id,logo_url FROM events WHERE id=?1 AND logo_url=?2 LIMIT 1")
        .bind(event_id, logo_url)
        .first()
    )
    if row is None or not logo_name.endswith((".jpg", ".png", ".webp")):
        raise HTTPException(status_code=404)
    content_type = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }[logo_name[logo_name.rfind(".") :]]
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
        await database(request)
        .prepare(
            """SELECT organization_id,cover_image_url FROM events
               WHERE id=?1 AND cover_image_url=?2 LIMIT 1"""
        )
        .bind(event_id, image_url)
        .first()
    )
    if row is None or not image_name.endswith((".jpg", ".png", ".webp")):
        raise HTTPException(status_code=404)
    suffix = image_name[image_name.rfind(".") :]
    content_type = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[suffix]
    stored = await _event_logo_bucket(request).get(
        f"public/event-covers/{row['organization_id']}/{event_id}/{image_name}"
    )
    if stored is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        _stream_event_logo(stored),
        media_type=content_type,
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
    requested_status: Literal["draft", "active"] = getattr(body, "status", "active")
    (
        stored_start,
        stored_end,
        stored_delivery,
        draft_start,
        draft_end,
        draft_delivery,
    ) = _event_storage_values(body, requested_status)
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
                              status,version,draft_starts_at_ms,draft_ends_at_ms,
                              draft_delivery_mode
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
        _validate_event_can_activate(stored_end, now)
    batch = CommandBatch(db)
    if record is not None:
        batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                email_sender_name,email_reply_to,status,draft_starts_at_ms,draft_ends_at_ms,
                draft_delivery_mode,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,
                      ?17,?18,?19,?20,?20)"""
        ).bind(
            event_id,
            organization_id,
            body.name,
            stored_start,
            stored_end,
            body.time_zone,
            body.location,
            stored_delivery,
            body.description,
            body.accent_color,
            body.logo_url,
            body.cover_image_url,
            body.website_url,
            body.email_sender_name,
            body.email_reply_to,
            requested_status,
            draft_start,
            draft_end,
            draft_delivery,
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,
                created_at_ms,updated_at_ms)
               VALUES(?1,'event',?2,?2,'active',?3,?3)"""
        ).bind(event_id, authenticated.actor.user_id, now)
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
        starts_at_ms=stored_start,
        ends_at_ms=stored_end,
        delivery_mode=stored_delivery,
        draft_starts_at_ms=draft_start,
        draft_ends_at_ms=draft_end,
        draft_delivery_mode=draft_delivery,
        **body.model_dump(exclude={"status", "starts_at_ms", "ends_at_ms", "delivery_mode"}),
    )


@access_router.patch(
    "/api/v1/admin/events/{event_id}",
    response_model=EventView,
    tags=["administration"],
)
async def update_event(event_id: str, body: EventUpdate, request: Request) -> EventView:
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
    (
        stored_start,
        stored_end,
        stored_delivery,
        draft_start,
        draft_end,
        draft_delivery,
    ) = _event_storage_values(body, resolved_status)
    current_status = str(event["status"])
    if resolved_status != current_status and "archived" in {
        resolved_status,
        current_status,
    }:
        # Event editors may change event content, but crossing the archived
        # boundary is destructive and reserved for the exact owner/manager.
        authenticated = await require_permission(
            request,
            Permission.RESOURCE_ACCESS_MANAGE,
            ResourceContext(str(event["organization_id"]), event_id),
            mutation=True,
        )
    now = utc_now_ms()
    if resolved_status == "active" and current_status != "active":
        _validate_event_can_activate(stored_end, now)
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
        body.cover_image_url if "cover_image_url" in body.model_fields_set else current_cover_url
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
    elif current_status == "archived" and event["archived_at_ms"] is not None:
        archived_at_ms = int(event["archived_at_ms"])
    else:
        archived_at_ms = now
    row = row_mapping(
        await db.prepare(
            """UPDATE events SET name=?1,starts_at_ms=?2,ends_at_ms=?3,time_zone=?4,
               location=?5,delivery_mode=?6,description=?7,accent_color=?8,logo_url=?9,
               cover_image_url=?10,website_url=?11,email_sender_name=?12,email_reply_to=?13,status=?14,
               archived_at_ms=?15,draft_starts_at_ms=?16,draft_ends_at_ms=?17,
               draft_delivery_mode=?18,version=version+1,updated_at_ms=?19
               WHERE id=?20 AND version=?21
               RETURNING id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                         delivery_mode,description,accent_color,logo_url,cover_image_url,website_url,
                         email_sender_name,email_reply_to,status,version,draft_starts_at_ms,
                         draft_ends_at_ms,draft_delivery_mode"""
        )
        .bind(
            body.name,
            stored_start,
            stored_end,
            body.time_zone,
            body.location,
            stored_delivery,
            body.description,
            body.accent_color,
            logo_url,
            cover_image_url,
            body.website_url,
            body.email_sender_name,
            body.email_reply_to,
            resolved_status,
            archived_at_ms,
            draft_start,
            draft_end,
            draft_delivery,
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


@access_router.get(
    "/api/v1/admin/events/{event_id}/access-grants",
    response_model=ResourceGrantList,
    tags=["administration"],
)
async def list_event_access_grants(event_id: str, request: Request) -> ResourceGrantList:
    db, _organization_id, _authenticated = await _managed_event(
        request, event_id, mutation=False, include_archived=True
    )
    result = await (
        db.prepare(
            """SELECT u.id AS user_id,u.email,'owner' AS permission,'active' AS status,0 AS rank
               FROM owned_resources r JOIN users u ON u.id=r.owner_user_id
               WHERE r.id=?1 AND r.resource_type='event' AND r.status='active'
               UNION ALL
               SELECT u.id,u.email,g.permission,g.status,1 AS rank
               FROM resource_access_grants g JOIN users u ON u.id=g.user_id
               WHERE g.resource_id=?1 AND g.status='active'
               ORDER BY rank,email,permission"""
        )
        .bind(event_id)
        .all()
    )
    return ResourceGrantList(data=[ResourceGrantView(**row) for row in result_rows(result)])


async def _write_event_grant(
    event_id: str,
    user_id: str,
    permission: str,
    request: Request,
) -> ResourceGrantView:
    db, organization_id, authenticated = await _managed_event(
        request, event_id, mutation=True, include_archived=True
    )
    target = row_mapping(
        await db.prepare(
            """SELECT u.id,u.email,r.owner_user_id FROM users u
               JOIN owned_resources r ON r.id=?1 AND r.resource_type='event'
               WHERE u.id=?2 AND u.status='active' LIMIT 1"""
        )
        .bind(event_id, user_id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404)
    if str(target["owner_user_id"]) == user_id:
        raise HTTPException(status_code=409, detail="The resource owner already has full access")
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE resource_access_grants SET status='revoked',revoked_at_ms=?1,
                 revoked_by_user_id=?2,version=version+1,updated_at_ms=?1
               WHERE resource_id=?3 AND user_id=?4 AND status='active' AND permission!=?5"""
        ).bind(now, authenticated.actor.user_id, event_id, user_id, permission)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO resource_access_grants
               (id,resource_id,user_id,permission,status,granted_by_user_id,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'active',?5,?6,?6)
               ON CONFLICT(resource_id,user_id,permission) DO UPDATE SET status='active',
                 granted_by_user_id=excluded.granted_by_user_id,revoked_at_ms=NULL,
                 revoked_by_user_id=NULL,version=version+1,updated_at_ms=excluded.updated_at_ms"""
        ).bind(new_id(), event_id, user_id, permission, authenticated.actor.user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO user_roles(user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES(?1,'organizer','active',?2,?2,
                 CASE WHEN EXISTS(SELECT 1 FROM user_roles
                                  WHERE user_id=?1 AND status='active') THEN 0 ELSE 1 END)
               ON CONFLICT(user_id,role) DO UPDATE SET status='active',revoked_at_ms=NULL,
                 is_default=CASE WHEN NOT EXISTS(
                   SELECT 1 FROM user_roles other
                   WHERE other.user_id=?1 AND other.status='active'
                     AND other.role!='organizer'
                 ) THEN 1 ELSE user_roles.is_default END,
                 updated_at_ms=excluded.updated_at_ms"""
        ).bind(user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE users SET authorization_version=authorization_version+1,
               updated_at_ms=?1 WHERE id=?2"""
        ).bind(now, user_id)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="resource_access_grant.upsert",
            target_type="event",
            target_id=event_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={"grantee_user_id": user_id, "permission": permission},
        )
    )
    await batch.execute()
    return ResourceGrantView(
        user_id=user_id, email=str(target["email"]), permission=permission, status="active"
    )


@access_router.post(
    "/api/v1/admin/events/{event_id}/access-grants",
    response_model=ResourceGrantView,
    status_code=201,
    tags=["administration"],
)
async def create_event_access_grant(
    event_id: str, body: ResourceGrantCreate, request: Request
) -> ResourceGrantView:
    db = database(request)
    # Authenticate and authorize before resolving the email so this endpoint
    # cannot be used to enumerate accounts.
    await _managed_event(request, event_id, mutation=True, include_archived=True)
    _email_value, normalized = _email(body.email)
    user_id = (
        await db.prepare(
            "SELECT id FROM users WHERE normalized_email=?1 AND status='active' LIMIT 1"
        )
        .bind(normalized)
        .first("id")
    )
    if user_id is None:
        raise HTTPException(status_code=404, detail="No active account uses that email address")
    return await _write_event_grant(event_id, str(user_id), body.permission, request)


@access_router.patch(
    "/api/v1/admin/events/{event_id}/access-grants/{user_id}",
    response_model=ResourceGrantView,
    tags=["administration"],
)
async def update_event_access_grant(
    event_id: str, user_id: str, body: ResourceGrantUpdate, request: Request
) -> ResourceGrantView:
    return await _write_event_grant(event_id, user_id, body.permission, request)


@access_router.delete(
    "/api/v1/admin/events/{event_id}/access-grants/{user_id}",
    status_code=204,
    tags=["administration"],
)
async def revoke_event_access_grant(event_id: str, user_id: str, request: Request) -> Response:
    db, organization_id, authenticated = await _managed_event(
        request, event_id, mutation=True, include_archived=True
    )
    owner_id = (
        await db.prepare(
            "SELECT owner_user_id FROM owned_resources WHERE id=?1 AND resource_type='event'"
        )
        .bind(event_id)
        .first("owner_user_id")
    )
    if owner_id is not None and str(owner_id) == user_id:
        raise HTTPException(status_code=409, detail="Resource ownership cannot be revoked")
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE resource_access_grants SET status='revoked',revoked_at_ms=?1,
                 revoked_by_user_id=?2,version=version+1,updated_at_ms=?1
               WHERE resource_id=?3 AND user_id=?4 AND status='active'"""
        ).bind(now, authenticated.actor.user_id, event_id, user_id)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE users SET authorization_version=authorization_version+1,
               updated_at_ms=?1 WHERE id=?2"""
        ).bind(now, user_id)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="resource_access_grant.revoke",
            target_type="event",
            target_id=event_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={"grantee_user_id": user_id},
        )
    )
    await batch.execute()
    return Response(status_code=204)


@access_router.post(
    "/api/v1/admin/events/{event_id}/invitations",
    response_model=InvitationIssued,
    status_code=201,
    tags=["administration"],
)
async def create_invitation(
    event_id: str, body: InvitationCreate, request: Request
) -> InvitationIssued:
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
        Permission.RESOURCE_ACCESS_MANAGE,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
    )
    if body.role == "organization_admin":
        # An event_admin must not be able to escalate anyone (including
        # themselves) to organization-wide administration.
        await require_permission(
            request,
            Permission.RESOURCE_ACCESS_MANAGE,
            ResourceContext(str(event["organization_id"])),
            mutation=True,
        )
    email, normalized = _email(body.email)
    now, invitation_id = utc_now_ms(), new_id()
    if body.role == "speaker":
        existing_event_speaker_id = await (
            db.prepare(
                """SELECT es.id FROM event_speakers es
                   JOIN people p ON p.organization_id=es.organization_id
                    AND p.id=es.person_id
                   JOIN users u ON u.id=p.user_id AND u.status='active'
                   WHERE es.organization_id=?1 AND es.event_id=?2
                     AND u.normalized_email=?3 AND es.status!='withdrawn'
                   LIMIT 1"""
            )
            .bind(event["organization_id"], event_id, normalized)
            .first("id")
        )
        if existing_event_speaker_id is not None:
            raise HTTPException(
                status_code=409,
                detail="This person is already an active speaker for this event",
            )
    accepted_id = await (
        db.prepare(
            """SELECT id FROM identity_invitations
               WHERE organization_id=?1 AND event_id=?2 AND normalized_email=?3
                 AND role=?4 AND status='accepted' LIMIT 1"""
        )
        .bind(event["organization_id"], event_id, normalized, body.role)
        .first("id")
    )
    if accepted_id is not None:
        raise HTTPException(status_code=409, detail="This invitation has already been accepted")
    await (
        db.prepare(
            """INSERT INTO identity_invitations
         (id,organization_id,event_id,normalized_email,email,role,status,invited_by_user_id,
          expires_at_ms,created_at_ms,updated_at_ms,display_name,job_title,company,biography)
         VALUES(?1,?2,?3,?4,?5,?6,'pending',?7,?8,?9,?9,?10,?11,?12,?13)
         ON CONFLICT(organization_id,event_id,normalized_email,role) DO UPDATE SET
           email=excluded.email,status='pending',invited_by_user_id=excluded.invited_by_user_id,
           expires_at_ms=excluded.expires_at_ms,accepted_at_ms=NULL,revoked_at_ms=NULL,
           updated_at_ms=excluded.updated_at_ms,display_name=excluded.display_name,
           job_title=excluded.job_title,company=excluded.company,
           biography=excluded.biography"""
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
            body.biography,
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
    if body.role == "speaker":
        default_tasks = (
            ("profile", "Complete your speaker profile", "profile"),
            ("headshot", "Upload your headshot", "headshot"),
            ("slides", "Upload your presentation slides", "slides"),
        )
        for task_type, title, destination_type in default_tasks:
            audit.add_statement(
                db.prepare(
                    """INSERT INTO speaker_tasks
                       (id,organization_id,event_id,event_speaker_id,pending_invitation_id,
                        task_type,title,help_text,destination_type,state,
                        form_schema_json,created_at_ms,updated_at_ms)
                       SELECT ?1,?2,?3,NULL,?4,?5,?6,'',?7,'open','{}',?8,?8
                       WHERE NOT EXISTS (
                         SELECT 1 FROM speaker_tasks
                         WHERE organization_id=?2 AND event_id=?3
                           AND pending_invitation_id=?4 AND task_type=?5
                           AND state IN ('open','completed'))"""
                ).bind(
                    new_id(),
                    event["organization_id"],
                    event_id,
                    row["id"],
                    task_type,
                    title,
                    destination_type,
                    now,
                )
            )
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
    access_url = await _issue_invitation_link(
        request,
        invitation_id=str(row["id"]),
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        email=email,
        normalized_email=normalized,
        role=body.role,
        now=now,
    )
    return InvitationIssued(**row, access_url=access_url)


def _speaker_import_response(
    mode: Literal["preview", "execute"],
    results: list[SpeakerInvitationImportResult],
) -> SpeakerInvitationImportResponse:
    return SpeakerInvitationImportResponse(
        mode=mode,
        data=results,
        created_count=sum(item.outcome == "created" for item in results),
        skipped_count=sum(item.outcome.startswith("skipped") for item in results),
        needs_resolution_count=sum(item.outcome == "needs_resolution" for item in results),
        rejected_count=sum(item.outcome == "rejected" for item in results),
        failed_count=sum(item.outcome == "failed" for item in results),
    )


def _speaker_import_result(
    row: SpeakerInvitationImportRow,
    outcome: SpeakerImportOutcome,
    reason: str = "",
    *,
    allowed: list[SpeakerImportDisposition] | None = None,
    invitation_id: str | None = None,
) -> SpeakerInvitationImportResult:
    return SpeakerInvitationImportResult(
        row_number=row.row_number,
        email=row.email,
        display_name=row.display_name,
        outcome=outcome,
        reason=reason,
        allowed_dispositions=allowed or [],
        invitation_id=invitation_id,
    )


def _speaker_import_payload(row: SpeakerInvitationImportRow) -> tuple[str, ...]:
    return (
        row.email.strip().casefold(),
        row.display_name,
        row.job_title,
        row.company,
        row.biography,
    )


async def _create_bulk_speaker_invitation(
    request: Request,
    *,
    event_id: str,
    organization_id: str,
    actor_user_id: str,
    row: SpeakerInvitationImportRow,
    batch_key: str,
) -> SpeakerInvitationImportResult:
    """Create exactly one invitation once; a replay never rotates its link."""
    db, now = database(request), utc_now_ms()
    email, normalized = _email(row.email)
    route = "POST /api/v1/admin/events/{event_id}/speaker-invitations/import"
    row_key = "speaker-import-row-" + hashlib.sha256(
        f"{batch_key}:{row.row_number}".encode()
    ).hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps(
            {"event_id": event_id, **row.model_dump()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id
               FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND state='completed'"""
        )
        .bind(actor_user_id, route, hashlib.sha256(row_key.encode()).digest())
        .first()
    )
    if replay is not None:
        stored = to_python(replay["request_fingerprint"])
        stored_bytes = stored if isinstance(stored, bytes) else bytes(stored)
        if stored_bytes != fingerprint:
            return _speaker_import_result(
                row,
                "rejected",
                "This batch row was already used with different speaker data.",
            )
        return _speaker_import_result(
            row,
            "created",
            "Already imported by this batch; no new email was sent.",
            invitation_id=str(replay["response_resource_id"]),
        )

    invitation_id = new_id()
    record = IdempotencyRecord(
        principal_key=actor_user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=row_key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 7 * 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms,
                display_name,job_title,company,biography)
               VALUES(?1,?2,?3,?4,?5,'speaker','pending',?6,?7,?8,?8,?9,?10,?11,?12)"""
        ).bind(
            invitation_id,
            organization_id,
            event_id,
            normalized,
            email,
            actor_user_id,
            now + 14 * 86_400_000,
            now,
            row.display_name,
            row.job_title,
            row.company,
            row.biography,
        )
    )
    for task_type, title, destination_type in (
        ("profile", "Complete your speaker profile", "profile"),
        ("headshot", "Upload your headshot", "headshot"),
        ("slides", "Upload your presentation slides", "slides"),
    ):
        batch.add_statement(
            db.prepare(
                """INSERT INTO speaker_tasks
                   (id,organization_id,event_id,event_speaker_id,pending_invitation_id,
                    task_type,title,help_text,destination_type,state,
                    form_schema_json,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,NULL,?4,?5,?6,'',?7,'open','{}',?8,?8)"""
            ).bind(
                new_id(),
                organization_id,
                event_id,
                invitation_id,
                task_type,
                title,
                destination_type,
                now,
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=actor_user_id,
            action="identity.invitation.bulk.create",
            target_type="identity_invitation",
            target_id=invitation_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={"row_number": row.row_number},
        )
    )
    _access_url, message_id = await _append_invitation_link(
        batch,
        request,
        invitation_id=invitation_id,
        organization_id=organization_id,
        event_id=event_id,
        email=email,
        normalized_email=normalized,
        role="speaker",
        now=now,
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="identity_invitation",
        resource_id=invitation_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError:
        # A concurrent import may have won the unique invitation key. Never
        # reinterpret that as a resend: report it and leave its link untouched.
        existing = await (
            db.prepare(
                """SELECT id,status FROM identity_invitations
                   WHERE organization_id=?1 AND event_id=?2
                     AND normalized_email=?3 AND role='speaker' LIMIT 1"""
            )
            .bind(organization_id, event_id, normalized)
            .first()
        )
        if existing is not None:
            status = str(existing["status"])
            return _speaker_import_result(
                row,
                "skipped_existing_invitation",
                f"A {status} invitation already exists; no new email was sent.",
                invitation_id=str(existing["id"]),
            )
        raise
    # The queue wake-up is deliberately post-commit. The invitation, task,
    # challenge, message, audit, and idempotency record above are one atomic
    # unit; a transient queue failure cannot leave an invitation without a
    # durable delivery message or make a retry rotate its link.
    await publish_committed_messages(request, [message_id])
    return _speaker_import_result(
        row,
        "created",
        "Invitation created and email queued.",
        invitation_id=invitation_id,
    )


@access_router.post(
    "/api/v1/admin/events/{event_id}/speaker-invitations/import",
    response_model=SpeakerInvitationImportResponse,
    tags=["administration"],
)
async def import_speaker_invitations(
    event_id: str,
    body: SpeakerInvitationImportRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SpeakerInvitationImportResponse:
    """Preview or execute a bounded CSV import without fail-fast row semantics."""
    db, organization_id, authenticated = await _managed_event(
        request, event_id, mutation=body.mode == "execute"
    )
    if body.mode == "execute" and (
        not isinstance(idempotency_key, str) or not 16 <= len(idempotency_key) <= 255
    ):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain between 16 and 255 characters",
        )
    if body.mode == "execute":
        await enforce_rate_limit(
            request,
            binding_name="AUTH_RATE_LIMITER",
            policy=RateLimitPolicy(
                "speaker.invitation.bulk_import", limit=5, window_seconds=60
            ),
            subject=f"{authenticated.actor.user_id}:{event_id}",
        )

    results_by_row: dict[int, SpeakerInvitationImportResult] = {}
    normalized_by_row: dict[int, str] = {}
    valid_rows: list[SpeakerInvitationImportRow] = []
    for row in body.rows:
        length_error = next(
            (
                message
                for value, limit, message in (
                    (row.email, 320, "Email must contain at most 320 characters."),
                    (
                        row.display_name,
                        200,
                        "Display name must contain at most 200 characters.",
                    ),
                    (
                        row.job_title,
                        200,
                        "Job title must contain at most 200 characters.",
                    ),
                    (row.company, 200, "Company must contain at most 200 characters."),
                    (
                        row.biography,
                        5000,
                        "Biography must contain at most 5,000 characters.",
                    ),
                )
                if len(value) > limit
            ),
            None,
        )
        if length_error is not None:
            results_by_row[row.row_number] = _speaker_import_result(
                row, "rejected", length_error
            )
            continue
        if not row.display_name:
            results_by_row[row.row_number] = _speaker_import_result(
                row, "rejected", "A display name is required."
            )
            continue
        try:
            normalized_by_row[row.row_number] = _email(row.email)[1]
        except (HTTPException, ValueError):
            results_by_row[row.row_number] = _speaker_import_result(
                row, "rejected", "Enter one valid email address."
            )
            continue
        valid_rows.append(row)
    if not valid_rows:
        return _speaker_import_response(
            body.mode, [results_by_row[row.row_number] for row in body.rows]
        )
    wanted_emails = sorted(set(normalized_by_row.values()))
    active_rows = result_rows(
        await db.prepare(
            """SELECT es.id,u.normalized_email,p.display_name
               FROM event_speakers es
               JOIN people p ON p.organization_id=es.organization_id AND p.id=es.person_id
               JOIN users u ON u.id=p.user_id AND u.status='active'
               WHERE es.organization_id=?1 AND es.event_id=?2 AND es.status!='withdrawn'
                 AND (u.normalized_email IN (SELECT value FROM json_each(?3))
                      OR trim(p.display_name)!='')"""
        ).bind(organization_id, event_id, json.dumps(wanted_emails))
        .all()
    )
    invitation_rows = result_rows(
        await db.prepare(
            """SELECT id,normalized_email,display_name,status
               FROM identity_invitations
               WHERE organization_id=?1 AND event_id=?2 AND role='speaker'
                 AND (normalized_email IN (SELECT value FROM json_each(?3))
                      OR trim(display_name)!='')"""
        ).bind(organization_id, event_id, json.dumps(wanted_emails))
        .all()
    )
    active_by_email = {str(item["normalized_email"]): item for item in active_rows}
    invitation_by_email = {str(item["normalized_email"]): item for item in invitation_rows}
    roster_by_name: dict[str, set[str]] = {}
    for item in [*active_rows, *invitation_rows]:
        name = str(item["display_name"] or "").strip().casefold()
        email = str(item["normalized_email"])
        if name:
            roster_by_name.setdefault(name, set()).add(email)

    groups: dict[str, list[SpeakerInvitationImportRow]] = {}
    for row in valid_rows:
        groups.setdefault(normalized_by_row[row.row_number], []).append(row)
    candidates: list[SpeakerInvitationImportRow] = []
    for _normalized, rows in groups.items():
        payloads = {_speaker_import_payload(row) for row in rows}
        if len(rows) > 1 and len(payloads) == 1:
            winner = next(
                (row for row in rows if row.disposition == "separate_person"),
                next((row for row in rows if row.disposition == "import"), rows[0]),
            )
            candidates.append(winner)
            for duplicate in rows:
                if duplicate is winner:
                    continue
                results_by_row[duplicate.row_number] = _speaker_import_result(
                    duplicate,
                    "skipped_duplicate",
                    f"Same data as row {winner.row_number}; imported only once.",
                )
            continue
        if len(rows) > 1:
            chosen = [row for row in rows if row.disposition == "import"]
            if not chosen and all(row.disposition == "skip" for row in rows):
                for skipped in rows:
                    results_by_row[skipped.row_number] = _speaker_import_result(
                        skipped, "skipped", "Skipped by the organizer."
                    )
                continue
            if len(chosen) != 1:
                for conflict in rows:
                    results_by_row[conflict.row_number] = _speaker_import_result(
                        conflict,
                        "needs_resolution",
                        "Rows with this email disagree. Choose one row to import "
                        "and skip the others.",
                        allowed=["import", "skip"],
                    )
                continue
            candidates.append(chosen[0])
            for skipped in rows:
                if skipped is not chosen[0]:
                    results_by_row[skipped.row_number] = _speaker_import_result(
                        skipped, "skipped", "Skipped by the organizer."
                    )
            continue
        candidates.append(rows[0])

    for row in candidates:
        normalized = normalized_by_row[row.row_number]
        if row.disposition == "skip":
            results_by_row[row.row_number] = _speaker_import_result(
                row, "skipped", "Skipped by the organizer."
            )
            continue
        if normalized in active_by_email:
            results_by_row[row.row_number] = _speaker_import_result(
                row,
                "skipped_existing_speaker",
                "Already an active speaker for this event; no invitation was sent.",
            )
            continue
        existing_invitation = invitation_by_email.get(normalized)
        if body.mode == "preview" and existing_invitation is not None:
            status = str(existing_invitation["status"])
            results_by_row[row.row_number] = _speaker_import_result(
                row,
                "skipped_existing_invitation",
                f"A {status} invitation already exists; no new link or email was created.",
                invitation_id=str(existing_invitation["id"]),
            )
            continue
        name = row.display_name.strip().casefold()
        different_emails = roster_by_name.get(name, set()) - {normalized}
        if different_emails and row.disposition != "separate_person":
            results_by_row[row.row_number] = _speaker_import_result(
                row,
                "needs_resolution",
                "A different email already uses this name. Confirm a separate "
                "person or skip the row.",
                allowed=["separate_person", "skip"],
            )
            continue
        if body.mode == "preview":
            results_by_row[row.row_number] = _speaker_import_result(row, "ready")
            continue
        try:
            results_by_row[row.row_number] = await _create_bulk_speaker_invitation(
                request,
                event_id=event_id,
                organization_id=organization_id,
                actor_user_id=authenticated.actor.user_id,
                row=row,
                batch_key=str(idempotency_key),
            )
        except PersistenceError:
            record_degradation(request, "speaker_import_row_failed")
            results_by_row[row.row_number] = _speaker_import_result(
                row,
                "failed",
                "This row could not be imported. Retry the same batch; completed "
                "rows will not be emailed again.",
            )
    ordered = [results_by_row[row.row_number] for row in body.rows]
    return _speaker_import_response(body.mode, ordered)


async def _append_invitation_link(
    batch: CommandBatch,
    request: Request,
    *,
    invitation_id: str,
    organization_id: str,
    event_id: str,
    email: str,
    normalized_email: str,
    role: InvitationRole,
    now: int,
) -> tuple[str, str]:
    """Append one short-lived acceptance link and message to ``batch``.

    The caller owns execution so invitation creation and delivery persistence
    can share one transaction. The bearer credential is never stored in
    plaintext; the returned message id is used only for the post-commit queue
    wake-up.
    """
    base = str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "")).rstrip("/")
    parsed = urlparse(base)
    is_local = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if parsed.scheme != "https" and not is_local:
        raise HTTPException(status_code=503, detail="Invitation delivery is unavailable")
    destination = {
        "speaker": "/speaker",
        "evaluator": "/account?onboarding=1&next=/reviews",
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
    accept_url = f"{base}/auth/verify#token={raw_token}"
    batch.add_statement(
        db.prepare(
            """UPDATE authentication_challenges SET consumed_at_ms=?1
               WHERE invitation_id=?2 AND consumed_at_ms IS NULL"""
        ).bind(now, invitation_id)
    )
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
    return accept_url, message_id


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
) -> str:
    """Create, persist, and queue one invitation acceptance link."""
    batch = CommandBatch(database(request))
    accept_url, message_id = await _append_invitation_link(
        batch,
        request,
        invitation_id=invitation_id,
        organization_id=organization_id,
        event_id=event_id,
        email=email,
        normalized_email=normalized_email,
        role=role,
        now=now,
    )
    await batch.execute()
    # Post-commit wake-up only: a queue failure must not fail the invitation,
    # whose retry would collide with the committed deterministic_key.
    await publish_committed_messages(request, [message_id])
    return accept_url


async def _managed_event(
    request: Request,
    event_id: str,
    *,
    mutation: bool,
    include_archived: bool = False,
):
    db = database(request)
    event = row_mapping(
        await db.prepare(
            """SELECT organization_id FROM events
               WHERE id=?1 AND (?2=1 OR status!='archived') LIMIT 1"""
        )
        .bind(event_id, int(include_archived))
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request,
        Permission.RESOURCE_ACCESS_MANAGE,
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
    # Access administration remains available after archival so outstanding
    # invitations and grants can still be reviewed or revoked.
    db, organization_id, _ = await _managed_event(
        request, event_id, mutation=False, include_archived=True
    )
    result = await (
        db.prepare(
            """SELECT id,event_id,email,role,display_name,job_title,company,
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
    response_model=InvitationIssued,
    tags=["administration"],
)
async def resend_invitation(
    event_id: str, invitation_id: str, request: Request
) -> InvitationIssued:
    # Archival is maintenance-only: existing access may be revoked, but a new
    # invitation link must not be issued for an event people can no longer join.
    db, organization_id, authenticated = await _managed_event(
        request, event_id, mutation=True
    )
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
            Permission.RESOURCE_ACCESS_MANAGE,
            ResourceContext(organization_id),
            mutation=True,
        )
    role: InvitationRole = row["role"]
    access_url = await _issue_invitation_link(
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
    return InvitationIssued(**row, access_url=access_url)


@access_router.delete(
    "/api/v1/admin/events/{event_id}/invitations/{invitation_id}",
    status_code=204,
    tags=["administration"],
)
async def revoke_invitation(event_id: str, invitation_id: str, request: Request) -> Response:
    db, organization_id, authenticated = await _managed_event(
        request, event_id, mutation=True, include_archived=True
    )
    now = utc_now_ms()
    invitation = row_mapping(
        await db.prepare(
            """SELECT role,status,normalized_email FROM identity_invitations
               WHERE id=?1 AND organization_id=?2 AND event_id=?3
                 AND status IN ('pending','accepted') LIMIT 1"""
        )
        .bind(invitation_id, organization_id, event_id)
        .first()
    )
    if invitation is None:
        raise HTTPException(status_code=404)
    role, status = str(invitation["role"]), str(invitation["status"])
    if status == "accepted" and role != "evaluator":
        raise HTTPException(status_code=404)
    if role == "organization_admin":
        # Symmetric with create/resend: org-admin invitations are managed
        # only by organization admins.
        await require_permission(
            request,
            Permission.RESOURCE_ACCESS_MANAGE,
            ResourceContext(organization_id),
            mutation=True,
        )
    reviewer_user_id = None
    if status == "accepted" and role == "evaluator":
        reviewer_user_id = await (
            db.prepare("SELECT id FROM users WHERE normalized_email=?1 AND status='active' LIMIT 1")
            .bind(invitation["normalized_email"])
            .first("id")
        )
        if reviewer_user_id is not None:
            active_assignment = await (
                db.prepare(
                    """SELECT a.id FROM evaluation_assignments a
                       JOIN evaluation_rounds r ON r.id=a.round_id
                       WHERE r.organization_id=?1 AND r.event_id=?2
                         AND a.evaluator_user_id=?3 AND a.status!='revoked' LIMIT 1"""
                )
                .bind(organization_id, event_id, reviewer_user_id)
                .first("id")
            )
            if active_assignment is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Remove this reviewer from active evaluation assignments first",
                )
    changed = row_mapping(
        await db.prepare(
            """UPDATE identity_invitations SET status='revoked',accepted_at_ms=NULL,
                 revoked_at_ms=?1,updated_at_ms=?1
               WHERE id=?2 AND organization_id=?3 AND event_id=?4
                 AND status IN ('pending','accepted')
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
            action=(
                "evaluator.eligibility.revoke"
                if status == "accepted"
                else "identity.invitation.revoke"
            ),
            target_type="identity_invitation",
            target_id=invitation_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={
                "role": role,
                "previous_status": status,
                "reviewer_user_id": reviewer_user_id,
            },
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


async def _organization_access_control_context(
    request: Request, organization_id: str, *, mutation: bool
):
    db = database(request)
    organization = row_mapping(
        await db.prepare(
            """SELECT o.id,r.owner_user_id FROM organizations o
               JOIN owned_resources r ON r.id=o.id AND r.resource_type='organization'
                AND r.status='active'
               WHERE o.id=?1 LIMIT 1"""
        )
        .bind(organization_id)
        .first()
    )
    if organization is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request,
        Permission.RESOURCE_ACCESS_MANAGE,
        ResourceContext(organization_id),
        mutation=mutation,
    )
    return db, str(organization["owner_user_id"]), authenticated


@access_router.get(
    "/api/v1/admin/organizations/{organization_id}/access-grants",
    response_model=ResourceGrantList,
    tags=["administration"],
)
async def list_organization_access_grants(
    organization_id: str, request: Request
) -> ResourceGrantList:
    db, _owner_user_id, _authenticated = await _organization_access_control_context(
        request, organization_id, mutation=False
    )
    result = await (
        db.prepare(
            """SELECT u.id AS user_id,u.email,'owner' AS permission,
                      'active' AS status,0 AS rank
               FROM owned_resources r JOIN users u ON u.id=r.owner_user_id
               WHERE r.id=?1 AND r.resource_type='organization' AND r.status='active'
               UNION ALL
               SELECT u.id,u.email,g.permission,g.status,1 AS rank
               FROM resource_access_grants g JOIN users u ON u.id=g.user_id
               WHERE g.resource_id=?1 AND g.status='active'
               ORDER BY rank,email,permission"""
        )
        .bind(organization_id)
        .all()
    )
    return ResourceGrantList(data=[ResourceGrantView(**row) for row in result_rows(result)])


@access_router.get(
    "/api/v1/admin/organizations/{organization_id}/activities",
    response_model=OrganizationActivityList,
    tags=["administration"],
)
async def list_organization_activity(
    organization_id: str, request: Request
) -> OrganizationActivityList:
    db, _owner_user_id, _authenticated = await _organization_access_control_context(
        request, organization_id, mutation=False
    )
    result = (
        await db.prepare(
            """SELECT a.id AS activity_id,
                  CASE
                    WHEN a.actor_type='system' THEN 'SessionBuddy'
                    WHEN u.id IS NULL THEN 'Unknown account'
                    ELSE COALESCE(NULLIF(TRIM(u.first_name || ' ' || u.last_name),''),
                                  NULLIF(u.display_name,''),'Account user')
                  END AS actor_name,
                  a.actor_id,a.operation,a.resource_type,a.resource_id,
                  r.event_id,a.occurred_at_ms,
                  CASE resource_entity.entity_type
                    WHEN 'organization' THEN (
                      SELECT o.name FROM organizations o
                      WHERE o.id=resource_entity.internal_id AND o.id=?1 LIMIT 1)
                    WHEN 'event' THEN (
                      SELECT e.name FROM events e WHERE e.id=resource_entity.internal_id
                      AND e.organization_id=?1 LIMIT 1)
                    WHEN 'proposal' THEN (
                      SELECT s.proposal_title FROM submissions s
                      WHERE s.id=resource_entity.internal_id
                      AND s.organization_id=?1 LIMIT 1)
                    WHEN 'invitation' THEN (
                      SELECT COALESCE(NULLIF(i.display_name,''),
                                      REPLACE(i.role,'_',' ') || ' invitation')
                      FROM identity_invitations i WHERE i.id=resource_entity.internal_id
                      AND i.organization_id=?1 LIMIT 1)
                    WHEN 'call_for_speaker_form' THEN (
                      SELECT 'CFP for ' || e.name FROM call_for_speaker_forms f
                      JOIN events e ON e.id=f.event_id AND e.organization_id=f.organization_id
                      WHERE f.id=resource_entity.internal_id
                      AND f.organization_id=?1 LIMIT 1)
                    WHEN 'evaluation_round' THEN (
                      SELECT er.name FROM evaluation_rounds er
                      WHERE er.id=resource_entity.internal_id
                      AND er.organization_id=?1 LIMIT 1)
                    WHEN 'event_speaker' THEN (
                      SELECT pe.display_name FROM event_speakers es
                      JOIN people pe ON pe.id=es.person_id
                        AND pe.organization_id=es.organization_id
                      WHERE es.id=resource_entity.internal_id
                      AND es.organization_id=?1 LIMIT 1)
                    WHEN 'accepted_session' THEN (
                      SELECT COALESCE(s.proposal_title,ac.organizer_title)
                      FROM accepted_sessions ac
                      LEFT JOIN submissions s ON s.id=ac.submission_id
                        AND s.organization_id=ac.organization_id
                        AND s.event_id=ac.event_id
                      WHERE ac.id=resource_entity.internal_id
                      AND ac.organization_id=?1 LIMIT 1)
                    WHEN 'speaker_task' THEN (
                      SELECT st.title FROM speaker_tasks st
                      WHERE st.id=resource_entity.internal_id
                      AND st.organization_id=?1 LIMIT 1)
                    ELSE NULL
                  END AS subject_name
           FROM organization_activity p
           JOIN activities a ON a.id=p.activity_id
           LEFT JOIN activity_routing r ON r.activity_id=a.id
           LEFT JOIN activity_entities actor_entity
             ON actor_entity.public_id=a.actor_id
           LEFT JOIN users u ON u.id=actor_entity.internal_id
           LEFT JOIN activity_entities resource_entity
             ON resource_entity.public_id=a.resource_id
           WHERE p.organization_id=?1
           ORDER BY p.occurred_at_ms DESC,p.activity_id DESC LIMIT 30"""
        )
        .bind(organization_id)
        .all()
    )
    return OrganizationActivityList(
        data=[OrganizationActivityView(**row) for row in result_rows(result)]
    )


@access_router.post(
    "/api/v1/admin/organizations/{organization_id}/ownership-transfers",
    response_model=OrganizationOwnershipTransferView,
    status_code=201,
    tags=["administration"],
)
async def transfer_organization_ownership(
    organization_id: str,
    body: OrganizationOwnershipTransferCreate,
    request: Request,
) -> OrganizationOwnershipTransferView:
    db = database(request)
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    if authenticated.actor.active_persona is not Persona.ORGANIZER:
        raise HTTPException(status_code=403)
    owned = row_mapping(
        await db.prepare(
            """SELECT owner_user_id,version FROM owned_resources
               WHERE id=?1 AND resource_type='organization' AND status='active'
               LIMIT 1"""
        )
        .bind(organization_id)
        .first()
    )
    if owned is None or str(owned["owner_user_id"]) != authenticated.actor.user_id:
        raise HTTPException(status_code=404)
    _email_value, normalized = _email(body.email)
    target = row_mapping(
        await db.prepare(
            """SELECT u.id FROM users u
               JOIN resource_access_grants g ON g.user_id=u.id
                AND g.resource_id=?1 AND g.permission='manage' AND g.status='active'
               WHERE u.normalized_email=?2 AND u.status='active' LIMIT 1"""
        )
        .bind(organization_id, normalized)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404)
    previous_owner_user_id = authenticated.actor.user_id
    new_owner_user_id = str(target["id"])
    now, transfer_id = utc_now_ms(), new_id()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE resource_access_grants SET status='revoked',revoked_at_ms=?1,
                 revoked_by_user_id=?2,version=version+1,updated_at_ms=?1
               WHERE resource_id=?3 AND user_id=?4 AND status='active'"""
        ).bind(now, previous_owner_user_id, organization_id, new_owner_user_id)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE owned_resources SET
                 owner_user_id=CASE
                   WHEN owner_user_id=?1 AND version=?2 AND status='active' THEN ?3
                   ELSE NULL
                 END,
                 version=version+1,updated_at_ms=?4
               WHERE id=?5 AND resource_type='organization'"""
        ).bind(
            previous_owner_user_id,
            int(owned["version"]),
            new_owner_user_id,
            now,
            organization_id,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO resource_access_grants
               (id,resource_id,user_id,permission,status,granted_by_user_id,
                created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,'manage','active',?4,?5,?5)
               ON CONFLICT(resource_id,user_id,permission) DO UPDATE SET
                 status='active',granted_by_user_id=excluded.granted_by_user_id,
                 revoked_at_ms=NULL,revoked_by_user_id=NULL,version=version+1,
                 updated_at_ms=excluded.updated_at_ms"""
        ).bind(new_id(), organization_id, previous_owner_user_id, previous_owner_user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO resource_ownership_transfers
               (id,resource_id,from_user_id,to_user_id,transferred_by_user_id,
                reason,transferred_at_ms)
               VALUES(?1,?2,?3,?4,?3,NULL,?5)"""
        ).bind(
            transfer_id,
            organization_id,
            previous_owner_user_id,
            new_owner_user_id,
            now,
        )
    )
    for changed_user_id in (previous_owner_user_id, new_owner_user_id):
        batch.add_statement(
            db.prepare(
                """UPDATE users SET authorization_version=authorization_version+1,
                   updated_at_ms=?1 WHERE id=?2"""
            ).bind(now, changed_user_id)
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=previous_owner_user_id,
            action="resource_ownership.transfer",
            target_type="organization",
            target_id=organization_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            metadata={
                "previous_owner_user_id": previous_owner_user_id,
                "new_owner_user_id": new_owner_user_id,
                "previous_owner_permission": "manage",
            },
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(
            status_code=409, detail="Organization ownership changed; reload"
        ) from exc
    return OrganizationOwnershipTransferView(
        transfer_id=transfer_id,
        organization_id=organization_id,
        previous_owner_user_id=previous_owner_user_id,
        new_owner_user_id=new_owner_user_id,
        transferred_at_ms=now,
    )


async def _upsert_organization_access_grant(
    *,
    db,
    organization_id: str,
    owner_user_id: str,
    user_id: str,
    permission: Literal["view", "edit", "manage"],
    authenticated,
    request: Request,
) -> ResourceGrantView:
    target = row_mapping(
        await db.prepare("SELECT id,email FROM users WHERE id=?1 AND status='active' LIMIT 1")
        .bind(user_id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404)
    if user_id == owner_user_id:
        raise HTTPException(status_code=409, detail="Resource ownership cannot be changed")
    now = utc_now_ms()
    batch = CommandBatch(db)
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
        ).bind(new_id(), organization_id, user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES(?1,'organizer','active',?2,?2,
                 CASE WHEN EXISTS(SELECT 1 FROM user_roles
                                  WHERE user_id=?1 AND status='active') THEN 0 ELSE 1 END)
               ON CONFLICT(user_id,role) DO UPDATE SET status='active',revoked_at_ms=NULL,
                 is_default=CASE WHEN NOT EXISTS(
                   SELECT 1 FROM user_roles other
                   WHERE other.user_id=?1 AND other.status='active'
                     AND other.role!='organizer'
                 ) THEN 1 ELSE user_roles.is_default END,
                 updated_at_ms=excluded.updated_at_ms"""
        ).bind(user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE resource_access_grants SET status='revoked',revoked_at_ms=?1,
                 revoked_by_user_id=?2,version=version+1,updated_at_ms=?1
               WHERE resource_id=?3 AND user_id=?4 AND status='active' AND permission!=?5"""
        ).bind(
            now,
            authenticated.actor.user_id,
            organization_id,
            user_id,
            permission,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO resource_access_grants
               (id,resource_id,user_id,permission,status,granted_by_user_id,
                created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'active',?5,?6,?6)
               ON CONFLICT(resource_id,user_id,permission) DO UPDATE SET status='active',
                 granted_by_user_id=excluded.granted_by_user_id,revoked_at_ms=NULL,
                 revoked_by_user_id=NULL,version=version+1,
                 updated_at_ms=excluded.updated_at_ms"""
        ).bind(
            new_id(),
            organization_id,
            user_id,
            permission,
            authenticated.actor.user_id,
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE users SET authorization_version=authorization_version+1,
               updated_at_ms=?1 WHERE id=?2"""
        ).bind(now, user_id)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="resource_access_grant.upsert",
            target_type="organization",
            target_id=organization_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            metadata={"grantee_user_id": user_id, "permission": permission},
        )
    )
    await batch.execute()
    return ResourceGrantView(
        user_id=user_id,
        email=str(target["email"]),
        permission=permission,
        status="active",
    )


@access_router.post(
    "/api/v1/admin/organizations/{organization_id}/access-grants",
    response_model=ResourceGrantView,
    status_code=201,
    tags=["administration"],
)
async def create_organization_access_grant(
    organization_id: str, body: ResourceGrantCreate, request: Request
) -> ResourceGrantView:
    db, owner_user_id, authenticated = await _organization_access_control_context(
        request, organization_id, mutation=True
    )
    _email_value, normalized = _email(body.email)
    user_id = (
        await db.prepare(
            "SELECT id FROM users WHERE normalized_email=?1 AND status='active' LIMIT 1"
        )
        .bind(normalized)
        .first("id")
    )
    if user_id is None:
        raise HTTPException(status_code=404)
    return await _upsert_organization_access_grant(
        db=db,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        user_id=str(user_id),
        permission=body.permission,
        authenticated=authenticated,
        request=request,
    )


@access_router.patch(
    "/api/v1/admin/organizations/{organization_id}/access-grants/{user_id}",
    response_model=ResourceGrantView,
    tags=["administration"],
)
async def update_organization_access_grant(
    organization_id: str,
    user_id: str,
    body: ResourceGrantUpdate,
    request: Request,
) -> ResourceGrantView:
    db, owner_user_id, authenticated = await _organization_access_control_context(
        request, organization_id, mutation=True
    )
    return await _upsert_organization_access_grant(
        db=db,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        user_id=user_id,
        permission=body.permission,
        authenticated=authenticated,
        request=request,
    )


@access_router.delete(
    "/api/v1/admin/organizations/{organization_id}/access-grants/{user_id}",
    status_code=204,
    tags=["administration"],
)
async def revoke_organization_access_grant(
    organization_id: str, user_id: str, request: Request
) -> Response:
    db, owner_user_id, authenticated = await _organization_access_control_context(
        request, organization_id, mutation=True
    )
    if user_id == owner_user_id:
        raise HTTPException(status_code=409, detail="Resource ownership cannot be revoked")
    active_grant = (
        await db.prepare(
            """SELECT id FROM resource_access_grants
           WHERE resource_id=?1 AND user_id=?2 AND status='active' LIMIT 1"""
        )
        .bind(organization_id, user_id)
        .first("id")
    )
    if active_grant is None:
        raise HTTPException(status_code=404)
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE resource_access_grants SET status='revoked',revoked_at_ms=?1,
                 revoked_by_user_id=?2,version=version+1,updated_at_ms=?1
               WHERE resource_id=?3 AND user_id=?4 AND status='active'"""
        ).bind(now, authenticated.actor.user_id, organization_id, user_id)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE users SET authorization_version=authorization_version+1,
               updated_at_ms=?1 WHERE id=?2"""
        ).bind(now, user_id)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="resource_access_grant.revoke",
            target_type="organization",
            target_id=organization_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            metadata={"grantee_user_id": user_id},
        )
    )
    await batch.execute()
    return Response(status_code=204)


@access_router.get(
    "/api/v1/admin/organizations/{organization_id}/ownership-recovery/events",
    response_model=OwnershipRecoveryEventList,
    tags=["administration"],
)
async def list_ownership_recovery_events(
    organization_id: str,
    request: Request,
    cursor: str | None = Query(default=None, max_length=512),
    limit: int = Query(default=EVENTS_PAGE_LIMIT, ge=1, le=EVENTS_PAGE_LIMIT),
) -> OwnershipRecoveryEventList:
    """Expose a minimal event index to the exact organization owner only.

    This is a recovery control plane and deliberately does not authorize the
    caller to read any of the returned events through ordinary event APIs.
    """
    authenticated = await authenticate_request(request)
    if authenticated.actor.active_persona is not Persona.ORGANIZER:
        raise HTTPException(status_code=403)
    db = database(request)
    is_exact_owner = await (
        db.prepare(
            """SELECT 1 FROM organizations o
               JOIN owned_resources owned
                 ON owned.id=o.id AND owned.resource_type='organization'
                AND owned.status='active'
               WHERE o.id=?1 AND owned.owner_user_id=?2 LIMIT 1"""
        )
        .bind(organization_id, authenticated.actor.user_id)
        .first()
    )
    if is_exact_owner is None:
        raise HTTPException(status_code=404)
    decoded_cursor = _events_cursor(
        request,
        cursor,
        organization_id=organization_id,
        view="ownership-recovery",
        search="",
        order="recent",
    )
    after_created_at_ms, after_id = decoded_cursor if decoded_cursor else (None, None)
    rows = result_rows(
        await db.prepare(
            """SELECT e.id AS event_id,e.name,e.status,
                          owned.owner_user_id AS current_owner_user_id,
                          owner.email AS current_owner_email,e.created_at_ms
                   FROM events e
                   JOIN owned_resources owned
                     ON owned.id=e.id AND owned.resource_type='event'
                    AND owned.status='active'
                   JOIN users owner ON owner.id=owned.owner_user_id
                   WHERE e.organization_id=?1
                     AND (?2 IS NULL OR e.created_at_ms<?2
                          OR (e.created_at_ms=?2 AND e.id<?3))
                   ORDER BY e.created_at_ms DESC,e.id DESC LIMIT ?4"""
        )
        .bind(organization_id, after_created_at_ms, after_id, limit + 1)
        .all()
    )
    page = rows[:limit]
    events = [
        OwnershipRecoveryEventView(
            event_id=str(row["event_id"]),
            name=str(row["name"]),
            status=row["status"],
            current_owner_user_id=str(row["current_owner_user_id"]),
            current_owner_email=str(row["current_owner_email"]),
        )
        for row in page
    ]
    next_cursor = None
    if len(rows) > limit and page:
        next_cursor = _events_next_cursor(
            request,
            organization_id=organization_id,
            view="ownership-recovery",
            search="",
            order="recent",
            starts_at_ms=int(page[-1]["created_at_ms"]),
            row_id=str(page[-1]["event_id"]),
        )
    return OwnershipRecoveryEventList(data=events, next_cursor=next_cursor)


async def _event_ownership_control_context(request: Request, event_id: str):
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    if authenticated.actor.active_persona is not Persona.ORGANIZER:
        raise HTTPException(status_code=403)
    db = database(request)
    scope = row_mapping(
        await db.prepare(
            """SELECT e.organization_id,event_owner.owner_user_id,
                      event_owner.version AS ownership_version,
                      organization_owner.owner_user_id AS organization_owner_user_id
               FROM events e
               JOIN owned_resources event_owner
                 ON event_owner.id=e.id AND event_owner.resource_type='event'
                AND event_owner.status='active'
               JOIN owned_resources organization_owner
                 ON organization_owner.id=e.organization_id
                AND organization_owner.resource_type='organization'
                AND organization_owner.status='active'
               WHERE e.id=?1 LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if scope is None or authenticated.actor.user_id not in {
        str(scope["owner_user_id"]),
        str(scope["organization_owner_user_id"]),
    }:
        raise HTTPException(status_code=404)
    return db, scope, authenticated


@access_router.post(
    "/api/v1/admin/events/{event_id}/ownership-transfers",
    response_model=EventOwnershipTransferView,
    status_code=201,
    tags=["administration"],
)
async def transfer_event_ownership(
    event_id: str, body: EventOwnershipTransferCreate, request: Request
) -> EventOwnershipTransferView:
    db, scope, authenticated = await _event_ownership_control_context(request, event_id)
    _target_email, normalized = _email(body.email)
    target = row_mapping(
        await db.prepare(
            "SELECT id FROM users WHERE normalized_email=?1 AND status='active' LIMIT 1"
        )
        .bind(normalized)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404)
    previous_owner_user_id = str(scope["owner_user_id"])
    new_owner_user_id = str(target["id"])
    if new_owner_user_id == previous_owner_user_id:
        raise HTTPException(status_code=409, detail="The account already owns this event")
    organization_id = str(scope["organization_id"])
    ownership_version = int(scope["ownership_version"])
    now, transfer_id = utc_now_ms(), new_id()
    batch = CommandBatch(db)
    # The ownership trigger forbids grants for an owner, so target grants must
    # be removed before the owner row changes.
    batch.add_statement(
        db.prepare(
            """UPDATE resource_access_grants SET status='revoked',revoked_at_ms=?1,
                 revoked_by_user_id=?2,version=version+1,updated_at_ms=?1
               WHERE resource_id=?3 AND user_id=?4 AND status='active'"""
        ).bind(now, authenticated.actor.user_id, event_id, new_owner_user_id)
    )
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
        ).bind(new_id(), organization_id, new_owner_user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES(?1,'organizer','active',?2,?2,
                 CASE WHEN EXISTS(SELECT 1 FROM user_roles
                                  WHERE user_id=?1 AND status='active') THEN 0 ELSE 1 END)
               ON CONFLICT(user_id,role) DO UPDATE SET status='active',revoked_at_ms=NULL,
                 is_default=CASE WHEN NOT EXISTS(
                   SELECT 1 FROM user_roles other
                   WHERE other.user_id=?1 AND other.status='active'
                     AND other.role!='organizer'
                 ) THEN 1 ELSE user_roles.is_default END,
                 updated_at_ms=excluded.updated_at_ms"""
        ).bind(new_owner_user_id, now)
    )
    # A concurrent transfer must abort the whole D1 batch rather than leave
    # provisioning or grant changes behind. A stale owner/version deliberately
    # resolves to NULL, violating the NOT NULL owner column and rolling back.
    batch.add_statement(
        db.prepare(
            """UPDATE owned_resources SET
                 owner_user_id=CASE
                   WHEN owner_user_id=?1 AND version=?2 AND status='active' THEN ?3
                   ELSE NULL
                 END,
                 version=version+1,updated_at_ms=?4
               WHERE id=?5 AND resource_type='event'"""
        ).bind(
            previous_owner_user_id,
            ownership_version,
            new_owner_user_id,
            now,
            event_id,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO resource_ownership_transfers
               (id,resource_id,from_user_id,to_user_id,transferred_by_user_id,
                reason,transferred_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7)"""
        ).bind(
            transfer_id,
            event_id,
            previous_owner_user_id,
            new_owner_user_id,
            authenticated.actor.user_id,
            body.reason or None,
            now,
        )
    )
    # A previous owner keeps no exact event authority by default. When the
    # caller explicitly opts in, restore exactly one manage grant after the
    # ownership row has changed.
    batch.add_statement(
        db.prepare(
            """UPDATE resource_access_grants SET status='revoked',revoked_at_ms=?1,
                 revoked_by_user_id=?2,version=version+1,updated_at_ms=?1
               WHERE resource_id=?3 AND user_id=?4 AND status='active'"""
        ).bind(now, authenticated.actor.user_id, event_id, previous_owner_user_id)
    )
    if body.grant_previous_owner_manage:
        batch.add_statement(
            db.prepare(
                """INSERT INTO resource_access_grants
                   (id,resource_id,user_id,permission,status,granted_by_user_id,
                    created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,'manage','active',?4,?5,?5)
                   ON CONFLICT(resource_id,user_id,permission) DO UPDATE SET
                     status='active',granted_by_user_id=excluded.granted_by_user_id,
                     revoked_at_ms=NULL,revoked_by_user_id=NULL,version=version+1,
                     updated_at_ms=excluded.updated_at_ms"""
            ).bind(
                new_id(),
                event_id,
                previous_owner_user_id,
                authenticated.actor.user_id,
                now,
            )
        )
    for changed_user_id in (previous_owner_user_id, new_owner_user_id):
        batch.add_statement(
            db.prepare(
                """UPDATE users SET authorization_version=authorization_version+1,
                   updated_at_ms=?1 WHERE id=?2"""
            ).bind(now, changed_user_id)
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="resource_ownership.transfer",
            target_type="event",
            target_id=event_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={
                "previous_owner_user_id": previous_owner_user_id,
                "new_owner_user_id": new_owner_user_id,
                "grant_previous_owner_manage": body.grant_previous_owner_manage,
            },
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409, detail="Event ownership changed; reload") from exc
    return EventOwnershipTransferView(
        transfer_id=transfer_id,
        event_id=event_id,
        previous_owner_user_id=previous_owner_user_id,
        new_owner_user_id=new_owner_user_id,
        previous_owner_permission="manage" if body.grant_previous_owner_manage else None,
        transferred_at_ms=now,
    )


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
    await enforce_rate_limit(
        request,
        binding_name="MAGIC_LINK_SOURCE_RATE_LIMITER",
        policy=RateLimitPolicy("auth.magic_link.source", limit=10, window_seconds=60),
        subject=_request_source(request),
    )
    await enforce_rate_limit(
        request,
        binding_name="MAGIC_LINK_RECIPIENT_RATE_LIMITER",
        policy=RateLimitPolicy("auth.magic_link.recipient", limit=3, window_seconds=60),
        subject=normalized,
    )
    user = row_mapping(
        await db.prepare(
            """SELECT u.id,context.organization_id,context.event_id FROM users u
               JOIN (
                 SELECT om.user_id,om.organization_id,e.id AS event_id,1 AS priority
                 FROM organization_memberships om
                 LEFT JOIN events e ON e.organization_id=om.organization_id
                                   AND e.status!='archived'
                 WHERE om.status='active'
                 UNION ALL
                 SELECT a.evaluator_user_id,a.organization_id,a.event_id,2 AS priority
                 FROM evaluation_assignments a WHERE a.status!='revoked'
                 UNION ALL
                 SELECT invited.id AS user_id,i.organization_id,i.event_id,3 AS priority
                 FROM identity_invitations i JOIN users invited
                   ON invited.normalized_email=i.normalized_email
                 WHERE i.role='evaluator' AND i.status='accepted'
               ) context ON context.user_id=u.id
               WHERE u.normalized_email=?1 AND u.status='active'
               ORDER BY context.priority,context.organization_id,context.event_id LIMIT 1"""
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
        link = f"{base}/auth/verify#token={raw_token}"
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
        # Post-commit wake-up only: this endpoint answers GenericAccepted for
        # every outcome, so a raised queue failure would both fail a committed
        # sign-in link and become an account-enumeration oracle.
        await publish_committed_messages(request, [message_id])
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
        "$pbkdf2-sha256$i=600000$MDAwMDAwMDAwMDAwMDAwMA$MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA"
    )
    verifier = str(credential["verifier_phc"]) if credential is not None else fake
    valid = verify_password(body.password, verifier, pepper)
    blocked = (
        credential is not None
        and credential["blocked_until_ms"] is not None
        and int(credential["blocked_until_ms"]) > now
    )
    active = credential is not None and str(credential["status"]) == "active"
    if not valid or blocked or not active:
        if credential is not None:
            failures = min(int(credential["consecutive_failures"]) + 1, 100)
            delay_seconds = min(2 ** min(failures, 10), 3600) if failures >= 5 else 0
            await (
                db.prepare(
                    """INSERT INTO password_authentication_state
                   (user_id,consecutive_failures,first_failure_at_ms,last_failure_at_ms,
                    blocked_until_ms,updated_at_ms)
                   VALUES(?1,1,?2,?2,NULL,?2)
                   ON CONFLICT(user_id) DO UPDATE SET
                     consecutive_failures=?3,
                     first_failure_at_ms=COALESCE(first_failure_at_ms,?2),
                     last_failure_at_ms=?2,blocked_until_ms=?4,updated_at_ms=?2"""
                )
                .bind(
                    credential["id"],
                    now,
                    failures,
                    now + delay_seconds * 1000 if delay_seconds else None,
                )
                .run()
            )
        raise HTTPException(status_code=401)

    default_role = await _default_account_role(db, str(credential["id"]))
    if default_role is None:
        raise HTTPException(status_code=403)
    batch = CommandBatch(db)
    established = establish_session(
        batch=batch,
        db=db,
        request=request,
        user_id=str(credential["id"]),
        role=default_role,
        authorization_version=int(credential["authorization_version"]),
        now_ms=now,
    )
    session_id, session_token, csrf = (
        established.session_id,
        established.session_token,
        established.csrf_token,
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
        db.prepare("UPDATE password_credentials SET last_verified_at_ms=?1 WHERE user_id=?2").bind(
            now, credential["id"]
        )
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
    results = await batch.execute()
    await _confirm_session_established(results, established, db)
    _set_session_cookie(response, request, session_token)
    return SessionCreated(
        user_id=str(credential["id"]),
        csrf_token=csrf,
        redirect_path=_role_compatible_redirect(body.redirect_path, default_role),
    )


@access_router.get(
    "/api/v1/auth/verify",
    status_code=405,
    response_model=None,
    tags=["authentication"],
    include_in_schema=False,
)
async def inspect_magic_link_via_api() -> None:
    """Magic-link redemption is intentionally unavailable over GET.

    Keeping the historical path explicit provides a stable, non-mutating
    response for old clients and prevents top-level navigations or embedded
    resources from creating a session.
    """
    raise HTTPException(status_code=405, headers={"Allow": "POST"})


async def _redeem_magic_link(
    token: str,
    request: Request,
    response: Response,
    registration: SubmissionRegistration | None = None,
) -> SessionCreated:
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
        return await _finish_magic_link_sign_in(
            db, request, response, challenge, now, registration=registration
        )
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
    db,
    request: Request,
    response: Response,
    challenge,
    now: int,
    *,
    registration: SubmissionRegistration | None = None,
) -> SessionCreated:
    user_id = challenge["user_id"]
    invited_persona: str | None = None
    provisioned_persona: str | None = None
    session_batch: CommandBatch | None = None
    if challenge["invitation_id"] is not None:
        invitation = row_mapping(
            await db.prepare(
                """SELECT id,organization_id,event_id,email,normalized_email,role,
                          display_name,job_title,company,biography,invited_by_user_id
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
                   (id,email,normalized_email,display_name,status,email_verified_at_ms,
                    public_profile_enabled,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,NULLIF(?4,''),'active',?5,?6,?5,?5)"""
                ).bind(
                    user_id,
                    invitation["email"],
                    invitation["normalized_email"],
                    str(invitation["display_name"] or "").strip(),
                    now,
                    int(invitation["role"] == "speaker"),
                )
            )
        elif str(invitation["display_name"] or "").strip():
            batch.add_statement(
                db.prepare(
                    """UPDATE users SET display_name=CASE
                         WHEN TRIM(COALESCE(display_name,''))='' THEN ?1 ELSE display_name END,
                       updated_at_ms=?2 WHERE id=?3"""
                ).bind(str(invitation["display_name"]).strip(), now, user_id)
            )
        if invitation["role"] in {"organization_admin", "event_admin"}:
            # Keep a non-authorizing affiliation row for legacy composite
            # foreign keys. Administrative authority is the exact resource
            # grant below, never this membership.
            batch.add_statement(
                db.prepare(
                    """INSERT INTO organization_memberships
                   (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,'member','active',?4,?4)
                   ON CONFLICT(organization_id,user_id) DO UPDATE SET
                     role='member',status='active',revoked_at_ms=NULL,
                     version=version+1,updated_at_ms=excluded.updated_at_ms"""
                ).bind(new_id(), invitation["organization_id"], user_id, now)
            )
            resource_id = (
                invitation["organization_id"]
                if invitation["role"] == "organization_admin"
                else invitation["event_id"]
            )
            # An administrative invitation replaces the invitee's exact-
            # resource access level. Do not leave an older view/edit row
            # active beside the new manage grant.
            batch.add_statement(
                db.prepare(
                    """UPDATE resource_access_grants
                       SET status='revoked',revoked_at_ms=?1,revoked_by_user_id=?2,
                           version=version+1,updated_at_ms=?1
                       WHERE resource_id=?3 AND user_id=?4 AND status='active'
                         AND permission!='manage'"""
                ).bind(now, invitation["invited_by_user_id"], resource_id, user_id)
            )
            batch.add_statement(
                db.prepare(
                    """INSERT INTO resource_access_grants
                       (id,resource_id,user_id,permission,status,granted_by_user_id,
                        created_at_ms,updated_at_ms)
                       VALUES(?1,?2,?3,'manage','active',?4,?5,?5)
                       ON CONFLICT(resource_id,user_id,permission) DO UPDATE SET
                         status='active',revoked_at_ms=NULL,revoked_by_user_id=NULL,
                         version=version+1,updated_at_ms=excluded.updated_at_ms"""
                ).bind(new_id(), resource_id, user_id, invitation["invited_by_user_id"], now)
            )
            if existing_user is not None:
                batch.add_statement(
                    db.prepare(
                        """UPDATE users SET authorization_version=authorization_version+1,
                           updated_at_ms=?1 WHERE id=?2"""
                    ).bind(now, user_id)
                )
        elif invitation["role"] != "evaluator":
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
        persona = {
            "organization_admin": "organizer",
            "event_admin": "organizer",
            "evaluator": "reviewer",
            "speaker": "speaker",
        }[str(invitation["role"])]
        invited_persona = persona
        batch.add_statement(
            db.prepare(
                """INSERT INTO user_roles
                   (user_id,role,status,created_at_ms,updated_at_ms,is_default)
                   VALUES(?1,?2,'active',?3,?3,
                     CASE WHEN EXISTS(SELECT 1 FROM user_roles
                                      WHERE user_id=?1 AND status='active') THEN 0 ELSE 1 END)
                   ON CONFLICT(user_id,role) DO UPDATE SET status='active',
                     revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
            ).bind(user_id, persona, now)
        )
        if invitation["role"] == "speaker":
            event_speaker_id = await _add_speaker_profile(
                batch,
                db,
                organization_id=str(invitation["organization_id"]),
                event_id=str(invitation["event_id"]),
                user_id=str(user_id),
                email=str(invitation["email"]),
                display_name=str(invitation["display_name"] or ""),
                job_title=str(invitation["job_title"] or ""),
                company=str(invitation["company"] or ""),
                biography=str(invitation["biography"] or ""),
                now=now,
            )
            batch.add_statement(
                db.prepare(
                    """UPDATE speaker_tasks SET event_speaker_id=?1,pending_invitation_id=NULL,
                              updated_at_ms=?2,version=version+1
                       WHERE pending_invitation_id=?3 AND organization_id=?4 AND event_id=?5"""
                ).bind(
                    event_speaker_id,
                    now,
                    invitation["id"],
                    invitation["organization_id"],
                    invitation["event_id"],
                )
            )
            scheduled_participant_rows = result_rows(
                await db.prepare(
                    """SELECT ai.id AS agenda_item_id,ai.revision_id
                         FROM accepted_session_participants participant
                         JOIN agenda_items ai
                           ON ai.organization_id=participant.organization_id
                          AND ai.event_id=participant.event_id
                          AND ai.accepted_session_id=participant.accepted_session_id
                         JOIN schedule_revisions revision ON revision.id=ai.revision_id
                        WHERE participant.organization_id=?1 AND participant.event_id=?2
                          AND participant.pending_invitation_id=?3
                          AND revision.status!='published'"""
                )
                .bind(
                    invitation["organization_id"],
                    invitation["event_id"],
                    invitation["id"],
                )
                .all()
            )
            batch.add_statement(
                db.prepare(
                    """DELETE FROM accepted_session_participants AS pending
                       WHERE pending.organization_id=?1 AND pending.event_id=?2
                         AND pending.pending_invitation_id=?3
                         AND EXISTS (
                           SELECT 1 FROM accepted_session_participants active
                            WHERE active.accepted_session_id=pending.accepted_session_id
                              AND active.event_speaker_id=?4
                         )"""
                ).bind(
                    invitation["organization_id"],
                    invitation["event_id"],
                    invitation["id"],
                    event_speaker_id,
                )
            )
            batch.add_statement(
                db.prepare(
                    """UPDATE accepted_session_participants
                          SET event_speaker_id=?1,pending_invitation_id=NULL,
                              display_name_snapshot=COALESCE(
                                (SELECT person.display_name FROM people person
                                  JOIN event_speakers speaker
                                    ON speaker.organization_id=person.organization_id
                                   AND speaker.person_id=person.id
                                 WHERE speaker.id=?1 LIMIT 1),
                                display_name_snapshot),
                              updated_at_ms=?2
                        WHERE organization_id=?3 AND event_id=?4
                          AND pending_invitation_id=?5"""
                ).bind(
                    event_speaker_id,
                    now,
                    invitation["organization_id"],
                    invitation["event_id"],
                    invitation["id"],
                )
            )
            for scheduled_participant in scheduled_participant_rows:
                batch.add_statement(
                    db.prepare(
                        """INSERT OR IGNORE INTO agenda_item_speakers
                           (id,organization_id,event_id,revision_id,agenda_item_id,
                            event_speaker_id,created_at_ms)
                           VALUES (?1,?2,?3,?4,?5,?6,?7)"""
                    ).bind(
                        new_id(),
                        invitation["organization_id"],
                        invitation["event_id"],
                        scheduled_participant["revision_id"],
                        scheduled_participant["agenda_item_id"],
                        event_speaker_id,
                        now,
                    )
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
        session_batch = batch
    elif challenge["provisioning_context"] == "submission":
        existing_user = row_mapping(
            await db.prepare("SELECT id FROM users WHERE normalized_email=?1 LIMIT 1")
            .bind(challenge["normalized_email"])
            .first()
        )
        user_id = str(existing_user["id"]) if existing_user is not None else new_id()
        credential_status: str | None = None
        if existing_user is not None:
            existing_credential = row_mapping(
                await db.prepare(
                    "SELECT status FROM password_credentials WHERE user_id=?1 LIMIT 1"
                )
                .bind(user_id)
                .first()
            )
            credential_status = (
                str(existing_credential["status"]) if existing_credential is not None else None
            )
            if credential_status == "disabled":
                # A disabled credential is a lock, not an invitation to replace
                # it. Decide before constructing a mutation batch so a denied
                # redemption cannot rewrite profile or account-completion data.
                raise HTTPException(status_code=403, detail="Password access is disabled")
        batch = CommandBatch(db)
        if existing_user is None or credential_status != "active":
            if registration is None:
                raise HTTPException(
                    status_code=422,
                    detail="Complete registration before signing in.",
                )
            try:
                verifier = hash_password(registration.password, secret(request, "PASSWORD_PEPPER"))
            except PasswordPolicyError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            display_name = f"{registration.first_name} {registration.last_name}".strip()
            if existing_user is None:
                batch.add_statement(
                    db.prepare(
                        """INSERT INTO users
                       (id,email,normalized_email,status,email_verified_at_ms,first_name,last_name,
                        display_name,job_title,company,public_profile_enabled,
                        profile_completed_at_ms,created_at_ms,updated_at_ms)
                       VALUES(?1,?2,?2,'active',?3,?4,?5,?6,?7,?8,1,?3,?3,?3)"""
                    ).bind(
                        user_id,
                        challenge["normalized_email"],
                        now,
                        registration.first_name,
                        registration.last_name,
                        display_name,
                        registration.job_title or None,
                        registration.company or None,
                    )
                )
            else:
                batch.add_statement(
                    db.prepare(
                        """UPDATE users SET first_name=?1,last_name=?2,display_name=?3,
                           job_title=?4,company=?5,profile_completed_at_ms=?6,
                           email_verified_at_ms=COALESCE(email_verified_at_ms,?6),
                           authorization_version=authorization_version+1,updated_at_ms=?6
                           WHERE id=?7"""
                    ).bind(
                        registration.first_name,
                        registration.last_name,
                        display_name,
                        registration.job_title or None,
                        registration.company or None,
                        now,
                        user_id,
                    )
                )
            batch.add_statement(
                db.prepare(
                    """UPDATE sessions SET revoked_at_ms=?1,revoke_reason='password_changed'
                       WHERE user_id=?2 AND revoked_at_ms IS NULL"""
                ).bind(now, user_id)
            )
            batch.add_statement(
                db.prepare(
                    """INSERT INTO password_credentials
                       (user_id,verifier_phc,pepper_version,status,created_at_ms,updated_at_ms)
                       VALUES(?1,?2,1,'active',?3,?3)
                       ON CONFLICT(user_id) DO UPDATE SET verifier_phc=excluded.verifier_phc,
                         pepper_version=1,
                         -- ELSE NULL intentionally aborts the batch if any status
                         -- other than reset_required reaches this conflict branch.
                         status=CASE WHEN password_credentials.status='reset_required'
                                     THEN 'active' ELSE NULL END,
                         updated_at_ms=excluded.updated_at_ms"""
                ).bind(user_id, verifier, now)
            )
            batch.add_statement(
                db.prepare(
                    """INSERT INTO password_authentication_state
                       (user_id,consecutive_failures,first_failure_at_ms,last_failure_at_ms,
                        blocked_until_ms,updated_at_ms)
                       VALUES(?1,0,NULL,NULL,NULL,?2)
                       ON CONFLICT(user_id) DO UPDATE SET consecutive_failures=0,
                         first_failure_at_ms=NULL,last_failure_at_ms=NULL,
                         blocked_until_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
                ).bind(user_id, now)
            )
            batch.add_statement(
                db.prepare(
                    """INSERT INTO user_roles
                       (user_id,role,status,created_at_ms,updated_at_ms,is_default)
                       VALUES(?1,'speaker','active',?2,?2,
                         CASE WHEN EXISTS(SELECT 1 FROM user_roles
                                          WHERE user_id=?1 AND status='active') THEN 0 ELSE 1 END)
                       ON CONFLICT(user_id,role) DO UPDATE SET status='active',
                         revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
                ).bind(user_id, now)
            )
            # A CFP registration authenticates into the speaker persona that
            # this same batch is provisioning. Looking up a default role here
            # would run before the queued user_roles write; choosing another
            # existing default would also send this CFP flow to the wrong UI.
            provisioned_persona = "speaker"
            batch.audit(
                AuditEvent(
                    actor_type="user",
                    actor_user_id=user_id,
                    action="account.registration.complete",
                    target_type="user",
                    target_id=user_id,
                    result="succeeded",
                    correlation_id=request.state.request_id,
                    occurred_at_ms=now,
                    organization_id=str(challenge["organization_id"]),
                    event_id=str(challenge["event_id"]),
                )
            )
        session_batch = batch
    if user_id is None:
        raise HTTPException(status_code=404)
    session_role = (
        invited_persona
        or provisioned_persona
        or await _default_account_role(db, str(user_id))
    )
    if session_role is None:
        raise HTTPException(status_code=403)
    if session_batch is None:
        session_batch = CommandBatch(db)
    established = establish_session_with_current_authorization_version(
        batch=session_batch,
        db=db,
        request=request,
        user_id=str(user_id),
        role=session_role,
        now_ms=now,
    )
    results = await session_batch.execute()
    await _confirm_session_established(results, established, db)
    _set_session_cookie(response, request, established.session_token)
    return SessionCreated(
        user_id=str(user_id),
        csrf_token=established.csrf_token,
        redirect_path=_role_compatible_redirect(str(challenge["redirect_path"]), session_role),
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
    biography: str = "",
) -> str:
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
                   (id,organization_id,user_id,display_name,job_title,company,biography,
                    created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?8)"""
            ).bind(
                person_id,
                organization_id,
                user_id,
                display_name,
                job_title,
                company,
                biography,
                now,
            )
        )
    elif any((display_name, job_title, company, biography)):
        batch.add_statement(
            db.prepare(
                """UPDATE people SET display_name=COALESCE(NULLIF(?1,''),display_name),
                          job_title=COALESCE(NULLIF(?2,''),job_title),
                          company=COALESCE(NULLIF(?3,''),company),
                          biography=COALESCE(NULLIF(?4,''),biography),updated_at_ms=?5,
                          version=version+1 WHERE id=?6 AND organization_id=?7"""
            ).bind(display_name, job_title, company, biography, now, person_id, organization_id)
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
        event_speaker_id = new_id()
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_speakers
                   (id,organization_id,event_id,person_id,status,accepted_at_ms,
                    last_activity_at_ms,created_at_ms,updated_at_ms,selection_status)
                   VALUES(?1,?2,?3,?4,'onboarding',?5,?5,?5,?5,'submitted')"""
            ).bind(event_speaker_id, organization_id, event_id, person_id, now)
        )
    else:
        event_speaker_id = str(event_speaker["id"])
    return event_speaker_id


async def _requires_submission_registration(request: Request, token: str) -> dict | None:
    if len(token) < 32:
        return None
    db, now = database(request), utc_now_ms()
    challenge = row_mapping(
        await db.prepare(
            """SELECT normalized_email,provisioning_context FROM authentication_challenges
               WHERE token_hash=?1 AND consumed_at_ms IS NULL AND expires_at_ms>?2 LIMIT 1"""
        )
        .bind(hash_token(token), now)
        .first()
    )
    if challenge is None or challenge["provisioning_context"] != "submission":
        return None
    existing = row_mapping(
        await db.prepare("SELECT id FROM users WHERE normalized_email=?1 LIMIT 1")
        .bind(challenge["normalized_email"])
        .first()
    )
    if existing is None:
        return challenge
    credential = row_mapping(
        await db.prepare(
            "SELECT user_id FROM password_credentials WHERE user_id=?1 AND status='active' LIMIT 1"
        )
        .bind(existing["id"])
        .first()
    )
    return challenge if credential is None else None


def _magic_link_page(
    *,
    token: str,
    registration_email: str | None = None,
    error: str | None = None,
    values: dict[str, str] | None = None,
) -> str:
    page = _asset("auth_link_confirm.html")
    values = values or {}
    token_field = (
        f'<input type="hidden" name="token" autocomplete="off" value="{escape(token, quote=True)}">'
    )
    if registration_email is None:
        needs_fragment_transfer = not token
        replacements = {
            "__CONFIRM_PAGE_TITLE__": "Confirm sign in",
            "__CONFIRM_HEADING__": "Continue to your account",
            "__CONFIRM_INTRO__": "Confirm that you want to sign in to SessionBuddy.",
            "__CONFIRM_ERROR__": "",
            "__REGISTRATION_FIELDS__": token_field,
            "__CONFIRM_BUTTON__": "Continue to your account",
            "__AUTO_SUBMIT_ATTRIBUTE__": "",
            "__CONFIRM_SCRIPT__": (
                '<script src="/auth/assets/auth-link-confirm.js?v=3" defer></script>'
                if needs_fragment_transfer
                else ""
            ),
        }
    else:

        def field(name: str) -> str:
            return escape(values.get(name, ""), quote=True)

        fields = "".join(
            (
                '<div class="auth-verified-email"><span>Verified email</span>',
                f"<strong>{escape(registration_email)}</strong></div>",
                '<div class="form-grid auth-registration-grid">',
                '<label>First name<input name="first_name" autocomplete="given-name" ',
                f'maxlength="100" value="{field("first_name")}" required></label>',
                '<label>Last name<input name="last_name" autocomplete="family-name" ',
                f'maxlength="100" value="{field("last_name")}" required></label>',
                '<label>Job title <span class="optional">Optional</span>',
                '<input name="job_title" autocomplete="organization-title" ',
                f'maxlength="200" value="{field("job_title")}"></label>',
                '<label>Company or team <span class="optional">Optional</span>',
                '<input name="company" autocomplete="organization" ',
                f'maxlength="200" value="{field("company")}"></label></div>',
                '<label>Choose a password<input name="password" type="password" ',
                'autocomplete="new-password" minlength="15" maxlength="128" ',
                'aria-describedby="registration-password-help" required></label>',
                '<label>Confirm password<input name="password_confirmation" type="password" ',
                'autocomplete="new-password" minlength="15" maxlength="128" ',
                'aria-describedby="registration-password-help" required></label>',
                '<p id="registration-password-help" class="help">',
                "Use at least 15 characters.</p>",
            )
        )
        replacements = {
            "__CONFIRM_PAGE_TITLE__": "Finish registration",
            "__CONFIRM_HEADING__": "Create your speaker account",
            "__CONFIRM_INTRO__": (
                "Your email is verified. Add your details to finish registration, "
                "then return to your proposal."
            ),
            "__CONFIRM_ERROR__": (
                f'<div class="status error" role="alert">{escape(error)}</div>' if error else ""
            ),
            "__REGISTRATION_FIELDS__": token_field + fields,
            "__CONFIRM_BUTTON__": "Create account and continue",
            "__AUTO_SUBMIT_ATTRIBUTE__": "",
            "__CONFIRM_SCRIPT__": "",
        }
    replacements["__CONFIRM_ACTION__"] = "/auth/verify"
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    return page


@access_router.get("/auth/verify", include_in_schema=False)
async def magic_link_interstitial() -> Response:
    """Render a confirm step instead of consuming the single-use token on GET.

    Corporate mail scanners prefetch emailed links; consuming on GET burned the
    token before the speaker ever clicked. Email links keep the bearer token in
    the URL fragment, which browsers do not send in HTTP requests or referrers.
    A same-origin script removes that fragment from history and transfers it to
    the body-only form. Redemption still requires an explicit button press and
    an exact allow-listed Origin."""
    page = _magic_link_page(token="")
    return Response(page, media_type="text/html", headers={"Cache-Control": "no-store"})


@access_router.post("/auth/verify", include_in_schema=False)
async def verify_magic_link_in_browser(*, request: Request) -> Response:
    if not browser_request_is_same_origin(request):
        raise HTTPException(status_code=403)
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(status_code=413)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "application/x-www-form-urlencoded"
    ):
        raise HTTPException(status_code=415)
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    submitted_values = {key: values[-1] for key, values in parsed.items()}
    token = submitted_values.pop("token", "")
    cookie_response = Response()
    registration_challenge = await _requires_submission_registration(request, token)
    registration = None
    if registration_challenge is not None:
        try:
            registration = SubmissionRegistration(
                first_name=submitted_values.get("first_name", ""),
                last_name=submitted_values.get("last_name", ""),
                job_title=submitted_values.get("job_title") or None,
                company=submitted_values.get("company") or None,
                password=submitted_values.get("password", ""),
                password_confirmation=submitted_values.get("password_confirmation", ""),
            )
        except ValueError:
            page = _magic_link_page(
                token=token,
                registration_email=str(registration_challenge["normalized_email"]),
                error="Enter your name and matching passwords of at least 15 characters.",
                values=submitted_values,
            )
            return Response(
                page,
                media_type="text/html",
                status_code=422,
                headers={"Cache-Control": "no-store"},
            )
    try:
        session = await _redeem_magic_link(
            token, request, cookie_response, registration=registration
        )
    except HTTPException as exception:
        if exception.status_code == 422 and registration_challenge is not None:
            detail = (
                str(exception.detail)
                if isinstance(exception.detail, str)
                else "Check your registration details."
            )
            page = _magic_link_page(
                token=token,
                registration_email=str(registration_challenge["normalized_email"]),
                error=detail,
                values=submitted_values,
            )
            return Response(
                page,
                media_type="text/html",
                status_code=422,
                headers={"Cache-Control": "no-store"},
            )
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
        await db.prepare(
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
        )
        .bind(authenticated.actor.user_id)
        .all()
    )
    active_role_row = row_mapping(
        await db.prepare(
            """SELECT active.role FROM session_active_roles active
               JOIN user_roles account
                 ON account.user_id=active.user_id AND account.role=active.role
                AND account.status='active'
               WHERE active.session_id=?1 AND active.user_id=?2 LIMIT 1"""
        )
        .bind(authenticated.session_id, authenticated.actor.user_id)
        .first()
    )
    account_roles = [str(item["role"]) for item in account_role_rows]
    if active_role_row is None:
        raise HTTPException(status_code=403)
    active_role = str(active_role_row["role"])
    default_role = next(
        (str(item["role"]) for item in account_role_rows if bool(item["is_default"])),
        None,
    )
    access_rows = result_rows(
        await db.prepare(
            """SELECT r.id,r.resource_type,
                      CASE WHEN r.owner_user_id=?1 THEN 'owner' ELSE g.permission END AS permission,
                      o.name AS organization_name,e.name AS event_name,e.organization_id
               FROM owned_resources r
               LEFT JOIN resource_access_grants g
                 ON g.resource_id=r.id AND g.user_id=?1 AND g.status='active'
               LEFT JOIN organizations o ON r.resource_type='organization' AND o.id=r.id
               LEFT JOIN events e ON r.resource_type='event' AND e.id=r.id
               WHERE r.status='active' AND (r.owner_user_id=?1 OR g.id IS NOT NULL)
                 AND r.resource_type IN ('organization','event')
               ORDER BY r.resource_type,r.id"""
        )
        .bind(authenticated.actor.user_id)
        .all()
    )
    assignment_rows = result_rows(
        await db.prepare(
            """SELECT memberships.organization_id,memberships.event_id,events.name AS event_name,
                      'speaker' AS assignment
               FROM event_memberships memberships
               JOIN events ON events.id=memberships.event_id
                         AND events.organization_id=memberships.organization_id
               WHERE memberships.user_id=?1 AND memberships.status='active'
                 AND memberships.role='speaker'
               UNION
               SELECT assignments.organization_id,assignments.event_id,events.name AS event_name,
                      'reviewer' AS assignment
               FROM evaluation_assignments assignments
               JOIN events ON events.id=assignments.event_id
                         AND events.organization_id=assignments.organization_id
               WHERE assignments.evaluator_user_id=?1 AND assignments.status!='revoked'
               ORDER BY 1,2,4"""
        )
        .bind(authenticated.actor.user_id)
        .all()
    )
    organizations_by_id: dict[str, dict[str, object]] = {}
    events_by_id: dict[str, dict[str, object]] = {}
    for item in access_rows:
        resource_id = str(item["id"])
        target = organizations_by_id if item["resource_type"] == "organization" else events_by_id
        target.setdefault(resource_id, {"row": item, "permissions": []})["permissions"].append(
            str(item["permission"])
        )
    for item in assignment_rows:
        event_id_value = str(item["event_id"])
        event_entry = events_by_id.setdefault(
            event_id_value,
            {
                "row": {
                    "id": event_id_value,
                    "organization_id": item["organization_id"],
                    "event_name": item["event_name"],
                },
                "permissions": [],
            },
        )
        event_entry.setdefault("assignments", []).append(str(item["assignment"]))
    organization_access = [
        SessionOrganizationAccess(
            organization_id=resource_id,
            organization_name=str(value["row"]["organization_name"]),
            permissions=sorted(set(value["permissions"])),
        )
        for resource_id, value in organizations_by_id.items()
    ]
    event_access = [
        SessionEventAccess(
            organization_id=str(value["row"]["organization_id"]),
            event_id=resource_id,
            event_name=str(value["row"].get("event_name") or "Event"),
            permissions=sorted(set(value["permissions"])),
            assignments=sorted(set(value.get("assignments", []))),
        )
        for resource_id, value in events_by_id.items()
    ]
    organization_id = organization_access[0].organization_id if organization_access else None
    organization_name = organization_access[0].organization_name if organization_access else None
    event_scope = event_access[0] if event_access else None
    configured_sender = str(getattr(environment(request), "RESEND_FROM_ADDRESS", "") or "")
    default_sender_name, default_email_address = parseaddr(configured_sender)
    return CurrentSession(
        user_id=authenticated.actor.user_id,
        email=str(user["email"]),
        display_name=str(user["display_name"]) if user["display_name"] is not None else None,
        profile_complete=bool(user["profile_complete"]),
        csrf_token=issue_csrf_token(authenticated.session_id, secret(request, "CSRF_HMAC_KEY")),
        default_email_sender_name=default_sender_name or None,
        default_email_address=default_email_address or None,
        account_roles=account_roles,
        active_role=active_role,
        default_role=default_role,
        organization_id=organization_id,
        organization_name=organization_name,
        event_id=event_scope.event_id if event_scope is not None else None,
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
        )
        .bind(authenticated.actor.user_id, body.role)
        .first()
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
        )
        .bind(authenticated.actor.user_id, body.role)
        .first()
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

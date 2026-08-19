import hashlib
import hmac
import io
import json
import re
import zipfile
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from html.parser import HTMLParser
from time import perf_counter
from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from sessionbuddy.cfp.availability import form_availability
from sessionbuddy.communications.presentation import message_category
from sessionbuddy.console import embedded_assets
from sessionbuddy.console.asset_response import content_addressed_asset
from sessionbuddy.observability import record_degradation, record_timing
from sessionbuddy.platform.auth import hash_token
from sessionbuddy.platform.auth.http import (
    authenticate_request,
    guard_mutation,
    require_document_event,
    require_document_persona,
    require_permission,
    secret,
)
from sessionbuddy.platform.authorization import Permission, Persona, ResourceContext
from sessionbuddy.platform.db.commands import (
    AuditEvent,
    CommandBatch,
    IdempotencyRecord,
)
from sessionbuddy.platform.db.d1 import (
    PersistenceError,
    result_rows,
    row_mapping,
    to_python,
)
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.http import attachment_header
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit
from sessionbuddy.platform.signed_cursors import BOUNDED_ID, STRICT_INT, SignedCursorContract
from sessionbuddy.platform.storage import (
    ScanResult,
    malware_scan_disabled,
    presign_r2_put,
)
from sessionbuddy.platform.upload_contracts import (
    ASSET_UPLOAD_RULES,
    UploadPolicyError,
    asset_upload_rule_views,
)

from .asset_boundary import AssetAccessScope, AssetRepository, ScanJob
from .models import (
    AdminSpeakerAssetList,
    AdminSpeakerAssetView,
    AssetCommentCreate,
    AssetCommentView,
    AssetDetailView,
    AssetDownloadGrantView,
    AssetDownloadToken,
    AssetExportCreate,
    OnboardingDashboardView,
    OnboardingRow,
    OnboardingSummary,
    SpeakerAssetDetailView,
    SpeakerAssetList,
    SpeakerAssetVersionView,
    SpeakerAssetView,
    SpeakerDiscoverableCallList,
    SpeakerDiscoverableCallView,
    SpeakerEventView,
    SpeakerNotificationView,
    SpeakerOpenCallView,
    SpeakerPortalView,
    SpeakerProfileView,
    SpeakerSessionScheduleView,
    SpeakerSessionView,
    SpeakerSubmissionView,
    SpeakerTaskResponseCreate,
    SpeakerTaskResponseView,
    SpeakerTaskView,
    UploadAuthorizationCreate,
    UploadAuthorizationView,
    UploadCompletionView,
)
from .scanner_adapter import SignedScannerAdapter

# CFP acceptance keeps membership in submission_speakers; direct sessions use
# accepted_session_participants. Union their scoped identities, not copied rows.
SPEAKER_SESSION_PROJECTION_SQL = """WITH participant AS (
   SELECT organization_id,event_id,accepted_session_id,event_speaker_id
   FROM accepted_session_participants
   WHERE organization_id=?1 AND event_id=?2 AND event_speaker_id=?3
   UNION
   SELECT ss.organization_id,ss.event_id,session.id,ss.event_speaker_id
   FROM submission_speakers ss
   JOIN accepted_sessions session
     ON session.organization_id=ss.organization_id AND session.event_id=ss.event_id
    AND session.submission_id=ss.submission_id AND session.source_type='accepted_proposal'
   WHERE ss.organization_id=?1 AND ss.event_id=?2 AND ss.event_speaker_id=?3
   ) SELECT ac.id,
          CASE WHEN approved.id IS NOT NULL THEN approved.title
               ELSE COALESCE(ac.organizer_title,s.proposal_title) END AS title,
          CASE WHEN approved.id IS NOT NULL THEN approved.abstract ELSE NULL END AS abstract,
          ac.source_type,ac.submission_id,
          CASE WHEN ac.source_type='organizer_created' THEN 'speaker'
               ELSE COALESCE(ss.role,'speaker') END AS participant_role,
          ac.content_status,
          ai.starts_at_ms,ai.ends_at_ms,r.name AS room_name,t.name AS track_name
   FROM participant
   JOIN accepted_sessions ac
     ON ac.organization_id=participant.organization_id
    AND ac.event_id=participant.event_id
    AND ac.id=participant.accepted_session_id
   LEFT JOIN submissions s
     ON s.organization_id=ac.organization_id AND s.event_id=ac.event_id
    AND s.id=ac.submission_id
   LEFT JOIN submission_speakers ss
     ON ss.organization_id=ac.organization_id AND ss.event_id=ac.event_id
    AND ss.submission_id=ac.submission_id
    AND ss.event_speaker_id=participant.event_speaker_id
   LEFT JOIN session_content_versions approved
     ON approved.id=(SELECT version.id FROM session_content_versions version
                     WHERE version.organization_id=ac.organization_id
                       AND version.event_id=ac.event_id
                       AND version.accepted_session_id=ac.id
                       AND version.content_status='approved'
                     ORDER BY version.version DESC,version.id DESC LIMIT 1)
   LEFT JOIN agenda_items ai
     ON ai.organization_id=ac.organization_id AND ai.event_id=ac.event_id
    AND ai.accepted_session_id=ac.id
    AND ai.revision_id=(SELECT revision.id FROM schedule_revisions revision
                        WHERE revision.organization_id=ac.organization_id
                          AND revision.event_id=ac.event_id
                          AND revision.status='published'
                        ORDER BY revision.revision_number DESC LIMIT 1)
   LEFT JOIN event_rooms r
     ON r.organization_id=ai.organization_id AND r.event_id=ai.event_id AND r.id=ai.room_id
   LEFT JOIN event_tracks t
     ON t.organization_id=ai.organization_id AND t.event_id=ai.event_id AND t.id=ai.track_id
   WHERE participant.organization_id=?1 AND participant.event_id=?2
     AND participant.event_speaker_id=?3 AND ac.lifecycle_status='active'
   ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 100"""

speaker_operations_router = APIRouter()


class _SpeakerMessageParser(HTMLParser):
    """Reduce delivered email HTML to text plus explicitly safe web links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.blocked_depth = 0

    @staticmethod
    def safe_link(value: object) -> str | None:
        try:
            parsed = urlparse(str(value))
        except ValueError:
            return None
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        return parsed.geturl()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.blocked_depth += 1
            return
        if self.blocked_depth:
            return
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")
        if tag == "a":
            href = next((value for name, value in attrs if name == "href"), None)
            safe = self.safe_link(href)
            if safe and safe not in self.links and len(self.links) < 20:
                self.links.append(safe)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.blocked_depth:
            self.blocked_depth -= 1
            return
        if self.blocked_depth:
            return
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.blocked_depth:
            self.parts.append(data)


def _speaker_message_content(value: object) -> tuple[str, list[str]]:
    parser = _SpeakerMessageParser()
    parser.feed(str(value))
    text = re.sub(r"[ \t]+", " ", "".join(parser.parts))
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    for candidate in re.findall(r"https?://[^\s<>\"']+", text):
        safe = parser.safe_link(candidate.rstrip(".,;:!?)"))
        if safe and safe not in parser.links and len(parser.links) < 20:
            parser.links.append(safe)
    return text, parser.links


def _speaker_notification_view(row) -> SpeakerNotificationView:
    body_text, links = _speaker_message_content(row["html_body"])
    deterministic_key = str(row["deterministic_key"])
    subject = str(row["subject"])
    return SpeakerNotificationView(
        id=str(row["id"]),
        subject=subject,
        category=message_category(deterministic_key, subject),
        delivered_at_ms=int(row["delivered_at_ms"]),
        body_text=body_text,
        links=links,
    )


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


def _db(request: Request):
    db = getattr(request.scope.get("env"), "DB", None)
    if db is None:
        raise HTTPException(status_code=503)
    return db


def _product_asset(request: Request, name: str, media_type: str) -> Response:
    if name == "admin_onboarding.js":
        return content_addressed_asset(request, _asset(name), media_type=media_type)
    return Response(_asset(name), media_type=media_type, headers={"Cache-Control": "no-store"})


def _speaker_asset_version_view(row: dict[str, object]) -> SpeakerAssetVersionView:
    return SpeakerAssetVersionView(
        id=str(row["id"]),
        generation=int(row["generation"]),
        filename=str(row["original_filename"]),
        content_type=str(row["content_type"]),
        byte_size=int(row["byte_size"]),
        state="current" if int(row["is_current"]) == 1 else "superseded",
        uploaded_at_ms=int(row["uploaded_at_ms"]),
        version_comment=row["version_comment"],
    )


async def _asset_versions_by_asset(
    request: Request,
    organization_id: str,
    event_id: str,
    event_speaker_id: str | None = None,
    asset_id: str | None = None,
) -> dict[str, list[SpeakerAssetVersionView]]:
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT version.asset_id,version.id,version.generation,
                      version.original_filename,version.content_type,version.byte_size,
                      version.is_current,version.uploaded_at_ms,version.version_comment
               FROM speaker_asset_versions version
               JOIN speaker_assets asset
                 ON asset.organization_id=version.organization_id
                AND asset.event_id=version.event_id AND asset.id=version.asset_id
               WHERE version.organization_id=?1 AND version.event_id=?2
                 AND (?3 IS NULL OR asset.event_speaker_id=?3)
                 AND (?4 IS NULL OR version.asset_id=?4)
                 AND version.scan_state IN ('clean','superseded')
               ORDER BY version.asset_id,version.generation DESC,version.id"""
        )
        .bind(organization_id, event_id, event_speaker_id, asset_id)
        .all()
    )
    versions: dict[str, list[SpeakerAssetVersionView]] = {}
    for row in rows:
        versions.setdefault(str(row["asset_id"]), []).append(_speaker_asset_version_view(row))
    return versions


@speaker_operations_router.get("/speaker", response_class=HTMLResponse, include_in_schema=False)
async def speaker_page(request: Request) -> Response:
    await require_document_persona(request, Persona.SPEAKER)
    return _product_asset(request, "speaker_portal.html", "text/html")


@speaker_operations_router.get(
    "/speaker/assets/speaker.css", response_class=Response, include_in_schema=False
)
async def speaker_css(request: Request) -> Response:
    return _product_asset(request, "speaker.css", "text/css")


@speaker_operations_router.get(
    "/speaker/assets/speaker-portal.js", response_class=Response, include_in_schema=False
)
async def speaker_js(request: Request) -> Response:
    return _product_asset(request, "speaker_portal.js", "text/javascript")


@speaker_operations_router.get(
    "/api/v1/speaker/open-calls",
    response_model=SpeakerDiscoverableCallList,
    tags=["speaker-portal"],
)
async def list_speaker_open_calls(request: Request) -> SpeakerDiscoverableCallList:
    """Return cross-event public calls plus only the caller's submission totals.

    This intentionally skips event/org permission checks: discovery must include
    public calls for events where the speaker has no membership yet. The only
    private facts added to public form data are rows owned by the caller.
    """
    await require_document_persona(request, Persona.SPEAKER)
    authenticated = await authenticate_request(request)
    db = _db(request)
    now = utc_now_ms()
    rows = result_rows(
        await db.prepare(
            """SELECT e.id AS event_id,e.name AS event_name,e.starts_at_ms,e.ends_at_ms,
                      e.time_zone,COALESCE(e.location,'') AS location,e.delivery_mode,
                      f.id AS form_id,f.slug,f.opens_at_ms,f.closes_at_ms,f.submission_limit
               FROM events e JOIN call_for_speaker_forms f
                 ON f.organization_id=e.organization_id AND f.event_id=e.id
               WHERE e.status='active' AND f.status='published'
                 -- Keep this inclusive close boundary aligned with
                 -- availability_state and the defensive Python filter below.
                 AND (f.closes_at_ms IS NULL OR f.closes_at_ms>?1)
                 AND NOT EXISTS (
                   SELECT 1 FROM call_for_speaker_forms newer
                   WHERE newer.organization_id=f.organization_id
                     AND newer.event_id=f.event_id AND newer.status='published'
                     AND (newer.version>f.version OR
                          (newer.version=f.version AND newer.published_at_ms>f.published_at_ms) OR
                          (newer.version=f.version AND newer.published_at_ms=f.published_at_ms
                           AND newer.id>f.id)))
               ORDER BY e.starts_at_ms,e.id LIMIT 100"""
        ).bind(now).all()
    )
    counts = result_rows(
        await db.prepare(
            """SELECT form_id,COUNT(*) AS submission_count FROM submissions
               WHERE submitter_user_id=?1 AND status='submitted' GROUP BY form_id"""
        ).bind(authenticated.actor.user_id).all()
    )
    counts_by_form = {str(row["form_id"]): int(row["submission_count"]) for row in counts}
    data = []
    for row in rows:
        availability = form_availability(row["opens_at_ms"], row["closes_at_ms"], now)
        # Defensive parity with the SQL pre-limit filter above. If the shared
        # boundary changes, update both so closed calls cannot consume LIMIT 100.
        if availability.state == "closed":
            continue
        form_id = str(row["form_id"])
        count = counts_by_form.get(form_id, 0)
        limit = int(row["submission_limit"]) if row["submission_limit"] is not None else None
        remaining = max(0, limit - count) if limit is not None else None
        data.append(SpeakerDiscoverableCallView(
            event_id=str(row["event_id"]), event_name=str(row["event_name"]),
            starts_at_ms=int(row["starts_at_ms"]), ends_at_ms=int(row["ends_at_ms"]),
            time_zone=str(row["time_zone"]), location=str(row["location"]),
            delivery_mode=str(row["delivery_mode"]), form_id=form_id, slug=str(row["slug"]),
            cfp_state=availability.state, cfp_boundary_at_ms=availability.boundary_at_ms,
            cfp_boundary_kind=availability.boundary_kind, submission_limit=limit,
            submission_count=count, remaining_submissions=remaining,
            already_submitted=count > 0,
            actionable=availability.accepting and remaining != 0,
        ))
    return SpeakerDiscoverableCallList(data=data)


@speaker_operations_router.get(
    "/admin/events/{event_id}/onboarding",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_onboarding_page(event_id: str, request: Request) -> Response:
    await require_document_persona(request, Persona.ORGANIZER)
    await require_document_event(request, event_id)
    return _product_asset(request, "admin_onboarding.html", "text/html")


@speaker_operations_router.get(
    "/admin/onboarding/assets/onboarding.css",
    response_class=Response,
    include_in_schema=False,
)
async def admin_onboarding_css(request: Request) -> Response:
    return _product_asset(request, "admin_onboarding.css", "text/css")


@speaker_operations_router.get(
    "/admin/onboarding/assets/onboarding.js",
    response_class=Response,
    include_in_schema=False,
)
async def admin_onboarding_js(request: Request) -> Response:
    return _product_asset(request, "admin_onboarding.js", "text/javascript")


async def _timed_first(request: Request, statement, column: str | None = None):
    started = perf_counter()
    try:
        return await (statement.first(column) if column is not None else statement.first())
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)


async def _timed_all(request: Request, statement):
    started = perf_counter()
    try:
        return await statement.all()
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)


_ONBOARDING_CURSOR = SignedCursorContract(
    "speaker_onboarding",
    {"as_of": STRICT_INT, "due": STRICT_INT, "id": BOUNDED_ID},
)


def _cursor(
    request: Request,
    value: str | None,
    *,
    event_id: str,
    state: str,
    task_type: str | None,
) -> tuple[int, int, str, int] | None:
    decoded = _ONBOARDING_CURSOR.decode(
        request,
        value,
        scope={"event": event_id, "state": state, "task_type": task_type},
    )
    if decoded is None:
        return None
    values = (decoded["due"], decoded["id"], decoded["as_of"], decoded["exp"])
    return values


def _next_cursor(
    request: Request,
    *,
    event_id: str,
    state: str,
    task_type: str | None,
    due_at_ms: int | None,
    task_id: str,
    as_of: int,
) -> str | None:
    return _ONBOARDING_CURSOR.encode(
        request,
        scope={"event": event_id, "state": state, "task_type": task_type},
        position={
            "as_of": as_of,
            "due": due_at_ms if due_at_ms is not None else 9_223_372_036_854_775_807,
            "id": task_id,
        },
        expires_at_ms=as_of + 15 * 60 * 1000,
    )


async def _admin_event(request: Request, event_id: str):
    await authenticate_request(request)
    event = row_mapping(
        await _timed_first(
            request,
            _db(request)
            .prepare(
                """SELECT id, organization_id, name, time_zone FROM events
                   WHERE id = ?1 AND status != 'archived' LIMIT 1"""
            )
            .bind(event_id),
        )
    )
    if event is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.DASHBOARD_READ,
        ResourceContext(str(event["organization_id"]), str(event["id"])),
        mutation=False,
    )
    return event


@speaker_operations_router.get(
    "/api/v1/admin/events/{event_id}/onboarding",
    response_model=OnboardingDashboardView,
    operation_id="getAdminOnboardingDashboard",
    tags=["speaker-onboarding"],
)
async def get_admin_onboarding_dashboard(
    event_id: str,
    request: Request,
    state: Literal["all", "open", "completed", "overdue", "due_soon"] = "all",
    task_type: Literal["profile", "headshot", "slides", "supporting_document", "custom"]
    | None = None,
    cursor: str | None = None,
    limit: int = Query(default=25, ge=1, le=50),
) -> OnboardingDashboardView:
    event = await _admin_event(request, event_id)
    db = _db(request)
    decoded_cursor = _cursor(
        request,
        cursor,
        event_id=event_id,
        state=state,
        task_type=task_type,
    )
    now = decoded_cursor[2] if decoded_cursor is not None else utc_now_ms()
    due_soon_at = now + 7 * 86_400_000
    # The tiles must describe the SAME population the table lists: TASK rows
    # (all speakers), scoped by BOTH filters — task type AND state — exactly
    # like the rows query. The original version counted accepted SPEAKERS,
    # and an interim fix honoured only the type filter; either way the
    # headline numbers could contradict the visible rows.
    summary_state_sql = {
        "all": "t.state IN ('open', 'completed', 'waived')",
        "open": "t.state = 'open'",
        "completed": "t.state = 'completed'",
        "overdue": "t.state = 'open' AND t.due_at_ms < ?3",
        "due_soon": "t.state = 'open' AND t.due_at_ms >= ?3 AND t.due_at_ms <= ?4",
    }[state]
    summary = (
        row_mapping(
            await _timed_first(
                request,
                db.prepare(
                    f"""SELECT
                     SUM(CASE WHEN t.event_speaker_id IS NOT NULL
                         AND t.state IN ('completed','waived')
                         THEN 1 ELSE 0 END) AS complete,
                     SUM(CASE WHEN t.event_speaker_id IS NOT NULL
                         AND t.state = 'open' THEN 1 ELSE 0 END) AS incomplete,
                     SUM(CASE WHEN t.event_speaker_id IS NOT NULL
                         AND t.state = 'open' AND t.due_at_ms < ?3
                         THEN 1 ELSE 0 END) AS overdue,
                     SUM(CASE WHEN t.event_speaker_id IS NOT NULL
                         AND t.state = 'open' AND t.due_at_ms >= ?3
                         AND t.due_at_ms <= ?4 THEN 1 ELSE 0 END) AS due_soon
                     ,SUM(CASE WHEN t.pending_invitation_id IS NOT NULL
                         AND t.state='open' THEN 1 ELSE 0 END) AS awaiting_acceptance
                   FROM speaker_tasks t
                   WHERE t.organization_id = ?1 AND t.event_id = ?2
                     AND (?5 IS NULL OR t.task_type = ?5)
                     AND {summary_state_sql}"""  # noqa: S608 - fixed fragment map
                ).bind(event["organization_id"], event["id"], now, due_soon_at, task_type),
            )
        )
        or {}
    )
    secondary = (
        row_mapping(
            await _timed_first(
                request,
                db.prepare(
                    """SELECT
                     SUM(CASE WHEN d.decision IS NULL THEN 1 ELSE 0 END) AS submitted,
                     SUM(CASE WHEN d.decision = 'accepted' THEN 1 ELSE 0 END) AS accepted,
                     SUM(CASE WHEN d.decision = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                     (SELECT COUNT(*) FROM evaluations ev
                       WHERE ev.organization_id = ?1 AND ev.event_id = ?2
                         AND ev.state = 'final') AS evaluations_finalized,
                     (SELECT COUNT(*) FROM evaluation_assignments ea
                       WHERE ea.organization_id = ?1 AND ea.event_id = ?2
                         AND ea.status != 'revoked') AS evaluations_total
                   FROM submissions s
                   LEFT JOIN submission_decisions d
                     ON d.organization_id = s.organization_id AND d.event_id = s.event_id
                    AND d.submission_id = s.id
                   WHERE s.organization_id = ?1 AND s.event_id = ?2"""
                ).bind(event["organization_id"], event["id"]),
            )
        )
        or {}
    )
    state_sql = {
        "all": "t.state IN ('open', 'completed', 'waived')",
        "open": "t.state = 'open'",
        "completed": "t.state = 'completed'",
        "overdue": "t.state = 'open' AND t.due_at_ms < ?5",
        "due_soon": "t.state = 'open' AND t.due_at_ms >= ?5 AND t.due_at_ms <= ?6",
    }[state]
    cursor_due = decoded_cursor[0] if decoded_cursor is not None else None
    cursor_id = decoded_cursor[1] if decoded_cursor is not None else None
    parameters: list[object] = [
        event["organization_id"],
        event["id"],
        limit + 1,
        task_type,
        now,
        due_soon_at,
        cursor_due,
        cursor_id,
    ]
    rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                f"""SELECT t.id AS task_id, t.task_type, t.title AS task_title, t.state,
                           t.due_at_ms, t.updated_at_ms, es.id AS event_speaker_id,
                           i.id AS invitation_id,
                           COALESCE(es.last_activity_at_ms,i.updated_at_ms) AS last_activity_at_ms,
                           COALESCE(p.display_name,i.display_name,i.email) AS display_name,
                           -- A roster's preferred proposal is not a task association.
                           COALESCE((SELECT s.proposal_title FROM submissions s
                               WHERE s.organization_id=t.organization_id
                                 AND s.event_id=t.event_id AND s.id=t.submission_id
                                 AND EXISTS (SELECT 1 FROM submission_speakers ss
                                   WHERE ss.organization_id=t.organization_id
                                     AND ss.event_id=t.event_id
                                     AND ss.submission_id=s.id
                                     AND ss.event_speaker_id=t.event_speaker_id)), '')
                             AS proposal_title
                    FROM speaker_tasks t
                    LEFT JOIN event_speakers es ON es.organization_id = t.organization_id
                     AND es.event_id = t.event_id AND es.id = t.event_speaker_id
                    LEFT JOIN people p ON p.organization_id = es.organization_id
                     AND p.id = es.person_id
                    LEFT JOIN identity_invitations i ON i.organization_id=t.organization_id
                     AND i.event_id=t.event_id AND i.id=t.pending_invitation_id
                     AND i.role='speaker' AND i.status='pending'
                    WHERE t.organization_id = ?1 AND t.event_id = ?2
                      AND (es.id IS NOT NULL OR i.id IS NOT NULL)
                      AND {state_sql}
                      AND (?4 IS NULL OR t.task_type = ?4)
                      AND (?7 IS NULL OR COALESCE(t.due_at_ms, 9223372036854775807) > ?7
                        OR (COALESCE(t.due_at_ms, 9223372036854775807) = ?7 AND t.id > ?8))
                    ORDER BY (t.due_at_ms IS NULL), t.due_at_ms, t.id LIMIT ?3"""  # noqa: S608
            ).bind(*parameters),
        )
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    row_views = []
    for row in page:
        is_invited = row["invitation_id"] is not None
        row_state = "awaiting_acceptance" if is_invited else str(row["state"])
        due_at = int(row["due_at_ms"]) if row["due_at_ms"] is not None else None
        if row_state == "open" and due_at is not None:
            if due_at < now:
                row_state = "overdue"
            elif due_at <= due_soon_at:
                row_state = "due_soon"
        row_views.append(
            OnboardingRow(
                event_speaker_id=(
                    str(row["event_speaker_id"]) if row["event_speaker_id"] is not None else None
                ),
                invitation_id=(
                    str(row["invitation_id"]) if row["invitation_id"] is not None else None
                ),
                recipient_state="invited" if is_invited else "active",
                display_name=str(row["display_name"]),
                proposal_title=str(row["proposal_title"]),
                task_id=str(row["task_id"]),
                task_type=str(row["task_type"]),
                task_title=str(row["task_title"]),
                state=row_state,
                due_at_ms=due_at,
                last_activity_at_ms=int(row["last_activity_at_ms"]),
            )
        )
    next_value = None
    if has_more and page:
        next_value = _next_cursor(
            request,
            event_id=event_id,
            state=state,
            task_type=task_type,
            due_at_ms=(int(page[-1]["due_at_ms"]) if page[-1]["due_at_ms"] is not None else None),
            task_id=str(page[-1]["task_id"]),
            as_of=now,
        )

    def count(values, key: str) -> int:
        return int(values.get(key) or 0)

    return OnboardingDashboardView(
        event_name=str(event["name"]),
        time_zone=str(event["time_zone"]),
        generated_at_ms=now,
        summary=OnboardingSummary(
            complete=count(summary, "complete"),
            incomplete=count(summary, "incomplete"),
            overdue=count(summary, "overdue"),
            due_soon=count(summary, "due_soon"),
            awaiting_acceptance=count(summary, "awaiting_acceptance"),
            submitted=count(secondary, "submitted"),
            accepted=count(secondary, "accepted"),
            rejected=count(secondary, "rejected"),
            evaluations_finalized=count(secondary, "evaluations_finalized"),
            evaluations_total=count(secondary, "evaluations_total"),
        ),
        data=row_views,
        next_cursor=next_value,
    )


async def _speaker_row(request: Request, event_id: str | None = None):
    authenticated = await authenticate_request(request)
    row = row_mapping(
        await _timed_first(
            request,
            _db(request)
            .prepare(
                """SELECT es.id AS event_speaker_id, es.organization_id, es.event_id,
                          p.id AS person_id, p.user_id, p.display_name, p.job_title,
                          p.company, p.biography, p.location, p.links_json, p.version,
                          e.name AS event_name, e.starts_at_ms, e.ends_at_ms, e.time_zone,
                          e.accent_color, e.logo_url,
                          es.selection_status
                   FROM people p
                   JOIN event_speakers es
                     ON es.organization_id = p.organization_id AND es.person_id = p.id
                   JOIN events e
                     ON e.organization_id = es.organization_id AND e.id = es.event_id
                   JOIN event_memberships em
                     ON em.organization_id = es.organization_id AND em.event_id = es.event_id
                    AND em.user_id = p.user_id AND em.role = 'speaker'
                    AND em.status = 'active'
                   WHERE p.user_id = ?1 AND p.archived_at_ms IS NULL
                     AND (?2 IS NULL OR es.event_id = ?2)
                     AND es.status IN ('onboarding', 'complete')
                   ORDER BY es.last_activity_at_ms DESC, es.id DESC LIMIT 1"""
            )
            .bind(authenticated.actor.user_id, event_id),
        )
    )
    if row is None:
        raise HTTPException(status_code=404)
    # The lookup derives ownership from the authenticated user. Re-run the shared
    # policy with the real tenant facts before returning any domain data.
    await require_permission(
        request,
        Permission.SPEAKER_PROFILE_READ_OWN,
        ResourceContext(
            str(row["organization_id"]),
            str(row["event_id"]),
            resource_owner_user_id=str(row["user_id"]),
        ),
        mutation=False,
    )
    return authenticated, row


def _profile(row) -> SpeakerProfileView:
    links = json.loads(str(row["links_json"]))
    return SpeakerProfileView(
        display_name=str(row["display_name"]),
        job_title=str(row["job_title"] or ""),
        company=str(row["company"] or ""),
        biography=str(row["biography"] or ""),
        location=str(row["location"] or ""),
        links=[str(value) for value in links],
        version=int(row["version"]),
    )


@speaker_operations_router.get(
    "/api/v1/speaker/portal",
    response_model=SpeakerPortalView,
    operation_id="getSpeakerPortal",
    tags=["speaker-portal"],
)
async def get_speaker_portal(
    request: Request, event_id: str | None = Query(default=None, max_length=100)
) -> SpeakerPortalView:
    authenticated, row = await _speaker_row(request, event_id)
    db = _db(request)
    event_rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT e.id,e.name,e.starts_at_ms,e.ends_at_ms,e.time_zone,
                          e.accent_color,e.logo_url,
                          es.organization_id,p.user_id
                   FROM people p
                   JOIN event_speakers es
                     ON es.organization_id=p.organization_id AND es.person_id=p.id
                   JOIN events e
                     ON e.organization_id=es.organization_id AND e.id=es.event_id
                   JOIN event_memberships em
                     ON em.organization_id=es.organization_id AND em.event_id=es.event_id
                    AND em.user_id=p.user_id AND em.role='speaker' AND em.status='active'
                   WHERE p.user_id=?1 AND p.archived_at_ms IS NULL
                     AND es.status IN ('onboarding','complete')
                   ORDER BY e.starts_at_ms DESC,e.id DESC LIMIT 100"""
            ).bind(authenticated.actor.user_id),
        )
    )
    for event_row in event_rows:
        await require_permission(
            request,
            Permission.SPEAKER_PROFILE_READ_OWN,
            ResourceContext(
                str(event_row["organization_id"]),
                str(event_row["id"]),
                resource_owner_user_id=str(event_row["user_id"]),
            ),
            mutation=False,
        )
    tasks = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT id, task_type, title, COALESCE(help_text, '') AS help_text,
                          state, due_at_ms, completed_at_ms,
                          form_schema_json,response_json,version
                   FROM speaker_tasks
                   WHERE organization_id = ?1 AND event_id = ?2 AND event_speaker_id = ?3
                   ORDER BY CASE state WHEN 'open' THEN 0 ELSE 1 END,
                            due_at_ms IS NULL, due_at_ms, id LIMIT 100"""
            ).bind(row["organization_id"], row["event_id"], row["event_speaker_id"]),
        )
    )
    sessions = result_rows(
        await _timed_all(
            request,
            db.prepare(SPEAKER_SESSION_PROJECTION_SQL).bind(
                row["organization_id"], row["event_id"], row["event_speaker_id"]
            ),
        )
    )
    submissions = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT s.id,s.speaker_name,s.speaker_email,s.proposal_title,
                          s.proposal_abstract,s.answers_json,s.version,s.submitter_user_id,
                          s.submitted_at_ms,f.slug AS form_slug,ss.role AS participant_role,
                          COALESCE((SELECT correction.corrected_decision
                            FROM submission_decision_corrections correction
                            WHERE correction.organization_id=s.organization_id
                              AND correction.event_id=s.event_id
                              AND correction.submission_id=s.id
                            ORDER BY correction.corrected_at_ms DESC,correction.id DESC LIMIT 1),
                            (SELECT d.decision FROM submission_decisions d
                             WHERE d.organization_id=s.organization_id AND d.event_id=s.event_id
                               AND d.submission_id=s.id
                             ORDER BY d.decided_at_ms DESC,d.id DESC LIMIT 1),s.status) AS status
                   FROM submission_speakers ss
                   JOIN submissions s
                     ON s.organization_id = ss.organization_id
                    AND s.event_id = ss.event_id AND s.id = ss.submission_id
                   JOIN call_for_speaker_forms f ON f.id=s.form_id
                   WHERE ss.organization_id = ?1 AND ss.event_id = ?2
                     AND ss.event_speaker_id = ?3
                   ORDER BY s.submitted_at_ms DESC, s.id DESC LIMIT 25"""
            ).bind(row["organization_id"], row["event_id"], row["event_speaker_id"]),
        )
    )
    open_call = await _open_call_view(
        request,
        db,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        submitter_user_id=authenticated.actor.user_id,
    )
    notifications = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT id,subject,html_body,deterministic_key,delivered_at_ms
                   FROM communication_messages
                   WHERE organization_id=?1 AND event_id=?2
                     AND recipient_user_id=?3 AND status='delivered'
                     AND delivered_at_ms IS NOT NULL
                     AND deterministic_key NOT LIKE 'auth:%'
                   ORDER BY delivered_at_ms DESC,id DESC LIMIT 50"""
            ).bind(
                row["organization_id"],
                row["event_id"],
                authenticated.actor.user_id,
            ),
        )
    )
    task_views = [
        SpeakerTaskView(
            id=str(task["id"]),
            task_type=str(task["task_type"]),
            title=str(task["title"]),
            help_text=str(task["help_text"]),
            destination_path=f"#{task['task_type']}",
            state=str(task["state"]),
            due_at_ms=int(task["due_at_ms"]) if task["due_at_ms"] is not None else None,
            completed_at_ms=(
                int(task["completed_at_ms"]) if task["completed_at_ms"] is not None else None
            ),
            form_fields=(
                json.loads(str(task["form_schema_json"])).get("fields", [])
                if task["form_schema_json"]
                else []
            ),
            upload_rules=(
                json.loads(str(task["form_schema_json"])).get("upload", {})
                if task["form_schema_json"]
                else {}
            ),
            response=(json.loads(str(task["response_json"])) if task["response_json"] else {}),
            version=int(task["version"]),
        )
        for task in tasks
    ]
    return SpeakerPortalView(
        event=SpeakerEventView(
            id=str(row["event_id"]),
            name=str(row["event_name"]),
            starts_at_ms=int(row["starts_at_ms"]),
            ends_at_ms=int(row["ends_at_ms"]),
            time_zone=str(row["time_zone"]),
            accent_color=(
                str(row["accent_color"]) if row.get("accent_color") is not None else None
            ),
            logo_url=str(row["logo_url"]) if row.get("logo_url") is not None else None,
        ),
        events=[
            SpeakerEventView(
                id=str(event_row["id"]),
                name=str(event_row["name"]),
                starts_at_ms=int(event_row["starts_at_ms"]),
                ends_at_ms=int(event_row["ends_at_ms"]),
                time_zone=str(event_row["time_zone"]),
                accent_color=(
                    str(event_row["accent_color"])
                    if event_row["accent_color"] is not None
                    else None
                ),
                logo_url=(
                    str(event_row["logo_url"]) if event_row["logo_url"] is not None else None
                ),
            )
            for event_row in event_rows
        ],
        event_speaker_id=str(row["event_speaker_id"]),
        public_profile_url=(
            f"/events/{quote(str(row['event_id']))}/speakers"
            f"?speaker={quote(str(row['event_speaker_id']))}"
            if str(row["selection_status"]) == "accepted"
            else None
        ),
        profile=_profile(row),
        tasks=task_views,
        asset_upload_rules=asset_upload_rule_views(),
        sessions=[
            SpeakerSessionView(
                id=str(session["id"]),
                title=str(session["title"]),
                abstract=(str(session["abstract"]) if session["abstract"] is not None else None),
                source=str(session["source_type"]),
                submission_id=(
                    str(session["submission_id"])
                    if session["submission_id"] is not None
                    else None
                ),
                participant_role=str(session["participant_role"]),
                content_status=str(session["content_status"]),
                schedule=(
                    SpeakerSessionScheduleView(
                        starts_at_ms=int(session["starts_at_ms"]),
                        ends_at_ms=int(session["ends_at_ms"]),
                        room_name=str(session["room_name"]),
                        track_name=(
                            str(session["track_name"])
                            if session["track_name"] is not None
                            else None
                        ),
                    )
                    if session["starts_at_ms"] is not None
                    else None
                ),
            )
            for session in sessions
        ],
        submissions=[
            SpeakerSubmissionView(
                id=str(submission["id"]),
                speaker_name=str(submission["speaker_name"]),
                speaker_email=str(submission["speaker_email"]),
                proposal_title=str(submission["proposal_title"]),
                proposal_abstract=str(submission["proposal_abstract"]),
                answers=json.loads(str(submission["answers_json"])),
                status=str(submission["status"]),
                form_slug=str(submission["form_slug"]),
                submitted_at_ms=int(submission["submitted_at_ms"]),
                version=int(submission["version"]),
                editable=(
                    str(submission["status"]) == "submitted"
                    and str(submission["submitter_user_id"] or "") == authenticated.actor.user_id
                ),
                is_primary_submitter=(
                    str(submission["submitter_user_id"] or "")
                    == authenticated.actor.user_id
                ),
                participant_role=str(submission["participant_role"]),
            )
            for submission in submissions
        ],
        notifications=[_speaker_notification_view(notification) for notification in notifications],
        open_call=open_call,
        completed_tasks=sum(task.state == "completed" for task in task_views),
        total_tasks=len(task_views),
    )


async def _open_call_view(
    request: Request,
    db,
    *,
    organization_id: str,
    event_id: str,
    submitter_user_id: str,
) -> SpeakerOpenCallView | None:
    """Summarise this event's published call for the portal's submit control.

    Returns None when the event has no published form at all. A form that is
    published but outside its window is still returned, so the portal can
    explain the closure rather than silently hiding the control.
    """
    form = row_mapping(
        await _timed_first(
            request,
            db.prepare(
                """SELECT f.id,f.slug,f.opens_at_ms,f.closes_at_ms,f.submission_limit
                   FROM call_for_speaker_forms f
                   JOIN events e
                     ON e.organization_id=f.organization_id AND e.id=f.event_id
                   WHERE f.organization_id=?1 AND f.event_id=?2
                     AND f.status='published' AND e.status='active'
                   ORDER BY f.version DESC,f.published_at_ms DESC,f.id DESC LIMIT 1"""
            ).bind(organization_id, event_id),
        )
    )
    if form is None:
        return None
    submitted_count = int(
        await _timed_first(
            request,
            db.prepare(
                """SELECT COUNT(*) AS count_value FROM submissions
                   WHERE form_id=?1 AND submitter_user_id=?2 AND status='submitted'"""
            ).bind(form["id"], submitter_user_id),
            "count_value",
        )
        or 0
    )
    opens_at_ms = int(form["opens_at_ms"]) if form["opens_at_ms"] is not None else None
    closes_at_ms = int(form["closes_at_ms"]) if form["closes_at_ms"] is not None else None
    limit = int(form["submission_limit"]) if form["submission_limit"] is not None else None
    remaining = max(0, limit - submitted_count) if limit is not None else None
    availability = form_availability(opens_at_ms, closes_at_ms, utc_now_ms())
    accepting = availability.accepting
    message = availability.message
    if accepting and remaining == 0:
        accepting = False
        message = "You have reached the proposal limit for this Call for Proposals."
    return SpeakerOpenCallView(
        form_id=str(form["id"]),
        slug=str(form["slug"]),
        accepting_submissions=accepting,
        availability_message=message,
        opens_at_ms=opens_at_ms,
        closes_at_ms=closes_at_ms,
        submission_limit=limit,
        submitted_count=submitted_count,
        remaining_submissions=remaining,
    )


def _key(value: str | None) -> str:
    if value is None or not 16 <= len(value) <= 255:
        raise HTTPException(status_code=400)
    return value


def _fingerprint(body) -> bytes:
    canonical = json.dumps(body.model_dump(), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).digest()


def _blob(value: object) -> bytes:
    converted = to_python(value)
    return converted if isinstance(converted, bytes) else bytes(converted)


def _completion_state(observed: object, *, attempted: str) -> str:
    """Report the version's real scan_state to an upload-completion caller.

    UploadCompletionView.state is narrower than scan_state, so the two states a
    completed version can hold that the caller has no word for are mapped here.
    'superseded' means this upload was scanned and accepted and then outranked by
    a newer generation, so 'clean' is the honest answer to "did my upload pass".
    Anything unrecognised falls back to the verdict the request attempted rather
    than failing the response, since by then the write has already committed.
    """
    state = str(observed) if observed is not None else ""
    if state in {"uploaded", "scanning", "clean", "rejected"}:
        return state
    return "clean" if state == "superseded" else attempted


def _validate_task_response(schema: object, answers: dict[str, object]) -> None:
    if not isinstance(schema, dict) or not isinstance(schema.get("fields", []), list):
        raise HTTPException(status_code=409)
    fields = schema.get("fields", [])
    known = {str(field.get("key")) for field in fields if isinstance(field, dict)}
    if not set(answers) <= known:
        raise HTTPException(status_code=422)
    for field in fields:
        if not isinstance(field, dict):
            raise HTTPException(status_code=409)
        value = answers.get(str(field.get("key", "")))
        field_type = field.get("type")
        blank = (
            value is None
            or value == ""
            or value == []
            or (isinstance(value, str) and not value.strip())
        )
        if field.get("required") and (blank or (field_type == "checkbox" and value is not True)):
            raise HTTPException(status_code=422)
        if blank:
            continue
        if field_type in {"text", "textarea", "url"} and (
            not isinstance(value, str) or len(value) > 4000
        ):
            raise HTTPException(status_code=422)
        if field_type == "url":
            parsed = urlparse(str(value))
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise HTTPException(status_code=422)
        if field_type == "checkbox" and value not in (True, False):
            raise HTTPException(status_code=422)
        if field_type == "select":
            if value not in field.get("choices", []):
                raise HTTPException(status_code=422)


@speaker_operations_router.post(
    "/api/v1/speaker/tasks/{task_id}/response",
    response_model=SpeakerTaskResponseView,
    operation_id="completeOwnCustomSpeakerTask",
    tags=["speaker-portal"],
)
async def complete_custom_speaker_task(
    task_id: str,
    request: Request,
    body: SpeakerTaskResponseCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SpeakerTaskResponseView:
    authenticated, speaker = await _speaker_row(request)
    await require_permission(
        request,
        Permission.SPEAKER_PROFILE_EDIT_OWN,
        ResourceContext(
            str(speaker["organization_id"]),
            str(speaker["event_id"]),
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    key = _key(idempotency_key)
    route = "POST /api/v1/speaker/tasks/{task_id}/response"
    fingerprint = _fingerprint(body)
    db = _db(request)
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND state='completed'"""
        )
        .bind(authenticated.actor.user_id, route, hashlib.sha256(key.encode()).digest())
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        current = row_mapping(
            await db.prepare(
                """SELECT id,state,response_json,version FROM speaker_tasks
                   WHERE id=?1 AND organization_id=?2 AND event_id=?3
                     AND event_speaker_id=?4 AND task_type='custom' LIMIT 1"""
            )
            .bind(
                str(replay["response_resource_id"]),
                speaker["organization_id"],
                speaker["event_id"],
                speaker["event_speaker_id"],
            )
            .first()
        )
        if current is None or current["response_json"] is None:
            raise HTTPException(status_code=409)
        return SpeakerTaskResponseView(
            id=str(current["id"]),
            response=json.loads(str(current["response_json"])),
            version=int(current["version"]),
        )
    task = row_mapping(
        await db.prepare(
            """SELECT id,form_schema_json,version,state FROM speaker_tasks
               WHERE id=?1 AND organization_id=?2 AND event_id=?3 AND event_speaker_id=?4
                 AND task_type='custom' LIMIT 1"""
        )
        .bind(
            task_id,
            speaker["organization_id"],
            speaker["event_id"],
            speaker["event_speaker_id"],
        )
        .first()
    )
    if task is None:
        raise HTTPException(status_code=404)
    if task["state"] != "open" or int(task["version"]) != body.version:
        raise HTTPException(status_code=409)
    schema = json.loads(str(task["form_schema_json"] or "{}"))
    _validate_task_response(schema, body.answers)
    now = utc_now_ms()
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        organization_id=str(speaker["organization_id"]),
        event_id=str(speaker["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """UPDATE speaker_tasks SET response_json=?1,responded_at_ms=?2,state='completed',
                      completed_at_ms=?2,version=version+1,updated_at_ms=?2
               WHERE id=?3 AND organization_id=?4 AND event_id=?5 AND event_speaker_id=?6
                 AND state='open' AND version=?7"""
        ).bind(
            json.dumps(body.answers, separators=(",", ":"), sort_keys=True),
            now,
            task_id,
            speaker["organization_id"],
            speaker["event_id"],
            speaker["event_speaker_id"],
            body.version,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE event_speakers SET last_activity_at_ms=?1,updated_at_ms=?1
               WHERE id=?2 AND organization_id=?3 AND event_id=?4"""
        ).bind(now, speaker["event_speaker_id"], speaker["organization_id"], speaker["event_id"])
    )
    batch.audit(
        AuditEvent(
            organization_id=str(speaker["organization_id"]),
            event_id=str(speaker["event_id"]),
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="speaker.task.complete",
            target_type="speaker_task",
            target_id=task_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"custom_fields": len(body.answers)},
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="speaker_task",
        resource_id=task_id,
        completed_at_ms=now,
    )
    started = perf_counter()
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)
    return SpeakerTaskResponseView(id=task_id, response=body.answers, version=body.version + 1)


_SPEAKER_UPLOAD_PENDING_LIMIT = 3
_SPEAKER_UPLOAD_STORAGE_LIMIT_BYTES = 250 * 1024 * 1024


async def _enforce_speaker_upload_quota(
    db,
    *,
    organization_id: str,
    event_id: str,
    event_speaker_id: str,
    requested_bytes: int,
    now_ms: int,
) -> None:
    pending = row_mapping(
        await db.prepare(
            """SELECT COUNT(*) AS pending_count,
                      COALESCE(SUM(ui.expected_byte_size),0) AS pending_bytes
               FROM upload_intents ui
               JOIN speaker_asset_versions av
                 ON av.organization_id=ui.organization_id
                AND av.event_id=ui.event_id AND av.id=ui.asset_version_id
               WHERE ui.organization_id=?1 AND ui.event_id=?2
                 AND ui.event_speaker_id=?3 AND ui.consumed_at_ms IS NULL
                 AND ui.expires_at_ms>?4 AND av.scan_state='pending_upload'"""
        )
        .bind(organization_id, event_id, event_speaker_id, now_ms)
        .first()
    )
    stored_bytes = int(
        await db.prepare(
            """SELECT COALESCE(SUM(byte_size),0) AS stored_bytes
               FROM speaker_asset_versions
               WHERE organization_id=?1 AND event_id=?2 AND event_speaker_id=?3
                 AND scan_state!='pending_upload'"""
        )
        .bind(organization_id, event_id, event_speaker_id)
        .first("stored_bytes")
    )
    if pending is None:
        raise HTTPException(status_code=503)
    if int(pending["pending_count"]) >= _SPEAKER_UPLOAD_PENDING_LIMIT:
        raise HTTPException(status_code=429, headers={"Retry-After": "600"})
    if (
        stored_bytes + int(pending["pending_bytes"]) + requested_bytes
        > _SPEAKER_UPLOAD_STORAGE_LIMIT_BYTES
    ):
        raise HTTPException(
            status_code=409,
            detail="Speaker asset storage quota exceeded for this event.",
        )


def _bucket(request: Request):
    bucket = getattr(request.scope.get("env"), "ASSETS", None)
    if bucket is None:
        raise HTTPException(status_code=503)
    return bucket


async def _enqueue_asset_scan(request: Request, row) -> None:
    """Best-effort scan-job wake-up for a committed upload.

    The version row is already durably 'uploaded', so the upload must not be
    reported as failed over a transient queue outage — especially since the
    portal's retry state lives only in page memory, and a user who reloads or
    walks away can never re-drive this publish. The scheduled
    dispatch_stuck_asset_scans pass is the recovery guarantee: it republishes
    any version stuck in 'uploaded', and the asset stays quarantined (never
    current, never downloadable) until its scan completes. A missing queue
    binding is a deployment misconfiguration no retry can heal, so that still
    fails loudly.
    """
    queue = getattr(request.scope.get("env"), "ASSET_SCAN_QUEUE", None)
    if queue is None:
        raise HTTPException(status_code=503)
    job = ScanJob(
        schema_version=1,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        asset_version_id=str(row["version_id"]),
        generation=int(row["generation"]),
        checksum_sha256=_blob(row["expected_checksum_sha256"]),
        job_id=str(row["version_id"]),
    )
    try:
        await queue.send(job.to_message())
    except Exception:
        record_degradation(request, "asset_scan_queue_publish_failed")


async def _speaker_for_event(request: Request, event_id: str):
    authenticated = await authenticate_request(request)
    row = row_mapping(
        await _timed_first(
            request,
            _db(request)
            .prepare(
                """SELECT es.id AS event_speaker_id, es.organization_id, es.event_id,
                          p.user_id
                   FROM event_speakers es
                   JOIN people p ON p.organization_id = es.organization_id
                    AND p.id = es.person_id
                   JOIN event_memberships em ON em.organization_id = es.organization_id
                    AND em.event_id = es.event_id AND em.user_id = p.user_id
                    AND em.role = 'speaker' AND em.status = 'active'
                   WHERE es.event_id = ?1 AND p.user_id = ?2
                     AND es.status IN ('onboarding', 'complete') LIMIT 1"""
            )
            .bind(event_id, authenticated.actor.user_id),
        )
    )
    if row is None:
        raise HTTPException(status_code=404)
    return authenticated, row


def _upload_token(request: Request, intent_id: str) -> str:
    digest = hmac.digest(
        secret(request, "UPLOAD_HMAC_KEY"), f"speaker-upload:{intent_id}".encode(), "sha256"
    )
    return urlsafe_b64encode(digest).decode().rstrip("=")


async def _authorization_view(
    request: Request, intent_id: str, token: str
) -> UploadAuthorizationView:
    row = row_mapping(
        await _timed_first(
            request,
            _db(request)
            .prepare(
                """SELECT ui.id, ui.expected_content_type, ui.expected_byte_size,
                          ui.expires_at_ms,
                          av.object_key
                   FROM upload_intents ui
                   JOIN speaker_asset_versions av
                     ON av.organization_id = ui.organization_id
                    AND av.event_id = ui.event_id AND av.id = ui.asset_version_id
                   WHERE ui.id = ?1 LIMIT 1"""
            )
            .bind(intent_id),
        )
    )
    if row is None:
        raise HTTPException(status_code=404)
    environment = request.scope.get("env")
    content_type = str(row["expected_content_type"])
    if getattr(environment, "APP_ENV", "production") == "local":
        upload_url = f"/api/v1/uploads/{intent_id}/content?token={token}"
        headers = {"content-type": content_type}
    else:
        required = (
            str(getattr(environment, "CLOUDFLARE_ACCOUNT_ID", "")),
            str(getattr(environment, "R2_BUCKET_NAME", "")),
            str(getattr(environment, "R2_ACCESS_KEY_ID", "")),
            str(getattr(environment, "R2_SECRET_ACCESS_KEY", "")),
        )
        if not all(required):
            raise HTTPException(status_code=503)
        upload_url, headers = presign_r2_put(
            account_id=required[0],
            bucket=required[1],
            object_key=str(row["object_key"]),
            access_key_id=required[2],
            secret_access_key=required[3],
            content_type=content_type,
            content_length=int(row["expected_byte_size"]),
            now=datetime.now(UTC),
            expires_seconds=max(1, (int(row["expires_at_ms"]) - utc_now_ms()) // 1000),
        )
    return UploadAuthorizationView(
        intent_id=intent_id,
        upload_url=upload_url,
        headers=headers,
        expires_at_ms=int(row["expires_at_ms"]),
    )


@speaker_operations_router.get(
    "/api/v1/speaker/events/{event_id}/assets",
    response_model=SpeakerAssetList,
    operation_id="listOwnSpeakerAssets",
    tags=["speaker-assets"],
)
async def list_speaker_assets(event_id: str, request: Request) -> SpeakerAssetList:
    authenticated, speaker = await _speaker_for_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_READ_OWN,
        ResourceContext(
            str(speaker["organization_id"]),
            event_id,
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=False,
    )
    rows = result_rows(
        await _timed_all(
            request,
            _db(request)
            .prepare(
                """SELECT a.id,a.kind,a.submission_id,av.original_filename,
                          av.content_type,av.byte_size,
                          av.generation,av.uploaded_at_ms,av.version_comment,
                          (SELECT COUNT(*) FROM speaker_asset_versions history
                           WHERE history.asset_id=a.id AND history.scan_state IN
                             ('clean','superseded')) AS version_count
                   FROM speaker_assets a
                   JOIN speaker_asset_versions av
                     ON av.organization_id = a.organization_id AND av.event_id = a.event_id
                    AND av.asset_id = a.id AND av.is_current = 1 AND av.scan_state = 'clean'
                   WHERE a.organization_id = ?1 AND a.event_id = ?2
                     AND a.event_speaker_id = ?3
                   ORDER BY a.kind, a.id LIMIT 100"""
            )
            .bind(speaker["organization_id"], event_id, speaker["event_speaker_id"]),
        )
    )
    versions_by_asset = await _asset_versions_by_asset(
        request,
        str(speaker["organization_id"]),
        event_id,
        str(speaker["event_speaker_id"]),
    )
    data = []
    for row in rows:
        data.append(
            SpeakerAssetView(
                id=str(row["id"]),
                kind=str(row["kind"]),
                submission_id=(str(row["submission_id"]) if row["submission_id"] else None),
                filename=str(row["original_filename"]),
                content_type=str(row["content_type"]),
                byte_size=int(row["byte_size"]),
                state="clean",
                generation=int(row["generation"]),
                uploaded_at_ms=int(row["uploaded_at_ms"]),
                version_count=int(row["version_count"]),
                version_comment=row["version_comment"],
                versions=versions_by_asset.get(str(row["id"]), []),
            )
        )
    return SpeakerAssetList(data=data)


@speaker_operations_router.get(
    "/api/v1/speaker/events/{event_id}/assets/{asset_id}",
    response_model=SpeakerAssetDetailView,
    operation_id="readOwnSpeakerAsset",
    tags=["speaker-assets"],
)
async def read_speaker_asset(
    event_id: str, asset_id: str, request: Request
) -> SpeakerAssetDetailView:
    """One of the speaker's own assets, with the shared half of its discussion.

    Organizer notes default to 'internal' and are filtered out here; only
    comments explicitly marked 'shared' cross the boundary.
    """
    authenticated, speaker = await _speaker_for_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_READ_OWN,
        ResourceContext(
            str(speaker["organization_id"]),
            event_id,
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=False,
    )
    db = _db(request)
    row = row_mapping(
        await _timed_first(
            request,
            db.prepare(
                """SELECT a.id,a.kind,a.submission_id,av.original_filename,
                          av.content_type,av.byte_size,
                          av.generation,av.uploaded_at_ms,av.version_comment,
                          (SELECT COUNT(*) FROM speaker_asset_versions history
                           WHERE history.asset_id=a.id AND history.scan_state IN
                             ('clean','superseded')) AS version_count
                   FROM speaker_assets a
                   JOIN speaker_asset_versions av
                     ON av.organization_id = a.organization_id AND av.event_id = a.event_id
                    AND av.asset_id = a.id AND av.is_current = 1 AND av.scan_state = 'clean'
                   WHERE a.organization_id = ?1 AND a.event_id = ?2
                     AND a.event_speaker_id = ?3 AND a.id = ?4 LIMIT 1"""
            ).bind(
                speaker["organization_id"],
                event_id,
                speaker["event_speaker_id"],
                asset_id,
            ),
        )
    )
    if row is None:
        raise HTTPException(status_code=404)
    versions_by_asset = await _asset_versions_by_asset(
        request,
        str(speaker["organization_id"]),
        event_id,
        str(speaker["event_speaker_id"]),
    )
    comments = result_rows(
        await db.prepare(
            """SELECT c.id,c.version_id,c.body_text,c.parent_comment_id,c.visibility,
                      c.created_at_ms,
                      COALESCE(NULLIF(trim(p.display_name),''),
                        CASE WHEN EXISTS (
                          SELECT 1 FROM people author_person
                          JOIN event_speakers author_speaker
                            ON author_speaker.person_id=author_person.id
                            AND author_speaker.organization_id=author_person.organization_id
                          WHERE author_person.organization_id=c.organization_id
                            AND author_person.user_id=c.author_user_id
                            AND author_speaker.event_id=c.event_id
                        ) THEN 'Speaker' ELSE 'Organizer' END) AS author_name
               FROM speaker_asset_comments c JOIN users u ON u.id=c.author_user_id
               LEFT JOIN people p ON p.user_id=u.id AND p.organization_id=c.organization_id
               WHERE c.organization_id=?1 AND c.event_id=?2 AND c.asset_id=?3
                 AND c.visibility='shared'
               ORDER BY c.created_at_ms,c.id LIMIT 500"""
        )
        .bind(speaker["organization_id"], event_id, asset_id)
        .all()
    )
    return SpeakerAssetDetailView(
        id=str(row["id"]),
        kind=str(row["kind"]),
        submission_id=(str(row["submission_id"]) if row["submission_id"] else None),
        filename=str(row["original_filename"]),
        content_type=str(row["content_type"]),
        byte_size=int(row["byte_size"]),
        state="clean",
        generation=int(row["generation"]),
        uploaded_at_ms=int(row["uploaded_at_ms"]),
        version_count=int(row["version_count"]),
        version_comment=row["version_comment"],
        versions=versions_by_asset.get(str(row["id"]), []),
        comments=[AssetCommentView(**dict(comment)) for comment in comments],
    )


@speaker_operations_router.post(
    "/api/v1/speaker/events/{event_id}/assets/{asset_id}/versions/{version_id}/comments",
    response_model=AssetCommentView,
    status_code=201,
    operation_id="commentOnOwnSpeakerAssetVersion",
    tags=["speaker-assets"],
)
async def create_speaker_asset_comment(
    event_id: str,
    asset_id: str,
    version_id: str,
    body: AssetCommentCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AssetCommentView:
    """A speaker replying on their own deliverable.

    Always 'shared': a speaker cannot write into the organizers' private notes,
    and would not be able to read back anything they wrote as 'internal'.
    """
    if not idempotency_key or not 16 <= len(idempotency_key) <= 255:
        raise HTTPException(status_code=400)
    authenticated, speaker = await _speaker_for_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_COMMENT_OWN,
        ResourceContext(
            str(speaker["organization_id"]),
            event_id,
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    db, now = _db(request), utc_now_ms()
    # Ownership and the version both resolve in one statement: an asset that is
    # not this speaker's simply does not match.
    version = row_mapping(
        await db.prepare(
            """SELECT v.id FROM speaker_asset_versions v
               JOIN speaker_assets a ON a.id=v.asset_id
                 AND a.organization_id=v.organization_id AND a.event_id=v.event_id
               WHERE v.organization_id=?1 AND v.event_id=?2 AND v.asset_id=?3 AND v.id=?4
                 AND a.event_speaker_id=?5
                 AND v.scan_state IN ('clean','superseded') LIMIT 1"""
        )
        .bind(
            speaker["organization_id"],
            event_id,
            asset_id,
            version_id,
            speaker["event_speaker_id"],
        )
        .first()
    )
    if version is None:
        raise HTTPException(status_code=404)
    if body.parent_comment_id:
        # Scoped to this version, mirroring the composite foreign key: a reply
        # may not reach across versions.
        parent = (
            await db.prepare(
                """SELECT id FROM speaker_asset_comments
                   WHERE organization_id=?1 AND event_id=?2 AND asset_id=?3
                     AND version_id=?4 AND id=?5 AND visibility='shared' LIMIT 1"""
            )
            .bind(
                speaker["organization_id"],
                event_id,
                asset_id,
                version_id,
                body.parent_comment_id,
            )
            .first("id")
        )
        if parent is None:
            raise HTTPException(status_code=404)
    fingerprint = hashlib.sha256(body.model_dump_json().encode()).digest()
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        route_key=f"speaker-assets.own-comments:{event_id}:{asset_id}:{version_id}",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        organization_id=str(speaker["organization_id"]),
        event_id=event_id,
        expires_at_ms=now + 86_400_000,
    )
    existing = row_mapping(
        await db.prepare(
            """SELECT ir.request_fingerprint,ir.response_resource_id AS resource_id,
                      c.version_id,c.body_text,c.visibility,
                      c.parent_comment_id,c.created_at_ms,
                      COALESCE(NULLIF(trim(p.display_name),''),
                        CASE WHEN EXISTS (
                          SELECT 1 FROM people author_person
                          JOIN event_speakers author_speaker
                            ON author_speaker.person_id=author_person.id
                            AND author_speaker.organization_id=author_person.organization_id
                          WHERE author_person.organization_id=c.organization_id
                            AND author_person.user_id=c.author_user_id
                            AND author_speaker.event_id=c.event_id
                        ) THEN 'Speaker' ELSE 'Organizer' END) author_name
               FROM idempotency_records ir
               JOIN speaker_asset_comments c ON c.id=ir.response_resource_id
               JOIN users u ON u.id=c.author_user_id
               LEFT JOIN people p ON p.user_id=u.id AND p.organization_id=c.organization_id
               WHERE ir.principal_key=?1 AND ir.route_key=?2 AND ir.idempotency_key_hash=?3
                 AND ir.event_id=?4 AND ir.state='completed' LIMIT 1"""
        )
        .bind(record.principal_key, record.route_key, record.key_hash, event_id)
        .first()
    )
    if existing:
        if bytes(existing["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return AssetCommentView(
            id=str(existing["resource_id"]),
            **{
                k: existing[k]
                for k in (
                    "version_id",
                    "author_name",
                    "body_text",
                    "parent_comment_id",
                    "visibility",
                    "created_at_ms",
                )
            },
        )
    comment_id = new_id()
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO speaker_asset_comments
               (id,organization_id,event_id,asset_id,version_id,author_user_id,
                parent_comment_id,body_text,visibility,created_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'shared',?9)"""
        ).bind(
            comment_id,
            speaker["organization_id"],
            event_id,
            asset_id,
            version_id,
            authenticated.actor.user_id,
            body.parent_comment_id,
            body.body_text,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            organization_id=str(speaker["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="speaker.asset.comment.create_own",
            target_type="speaker_asset",
            target_id=asset_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"version_id": version_id},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="speaker_asset_comment",
        resource_id=comment_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    author = (
        await db.prepare(
            """SELECT NULLIF(trim(display_name),'') AS author_name FROM people
               WHERE organization_id=?1 AND user_id=?2 LIMIT 1"""
        )
        .bind(speaker["organization_id"], authenticated.actor.user_id)
        .first("author_name")
        or "Speaker"
    )
    return AssetCommentView(
        id=comment_id,
        version_id=version_id,
        author_name=author,
        body_text=body.body_text,
        parent_comment_id=body.parent_comment_id,
        visibility="shared",
        created_at_ms=now,
    )


def _admin_asset_association_sql(sql: str) -> str:
    """Resolve only stored associations, never other proposals by this speaker."""
    return sql.replace(
        "/* asset_association_columns */",
        "proposal.id AS submission_id,proposal.proposal_title,"
        "asset_task.id AS task_id,asset_task.title AS task_title",
    ).replace(
        "/* asset_association_join */",
        """LEFT JOIN speaker_tasks asset_task ON asset_task.id=a.task_id
             AND asset_task.organization_id=a.organization_id
             AND asset_task.event_id=a.event_id
             AND asset_task.event_speaker_id=a.event_speaker_id
           LEFT JOIN submissions proposal
             ON proposal.id=COALESCE(a.submission_id,asset_task.submission_id)
             AND proposal.organization_id=a.organization_id AND proposal.event_id=a.event_id""",
    )


@speaker_operations_router.get(
    "/api/v1/admin/events/{event_id}/assets",
    response_model=AdminSpeakerAssetList,
    operation_id="listAdminSpeakerAssets",
    tags=["speaker-assets"],
)
async def list_admin_speaker_assets(event_id: str, request: Request) -> AdminSpeakerAssetList:
    event = await _admin_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_READ,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=False,
    )
    rows = result_rows(
        await _timed_all(
            request,
            _db(request)
            .prepare(
                _admin_asset_association_sql("""SELECT a.id,a.event_speaker_id,
                          p.display_name AS speaker_name,a.kind,
                          /* asset_association_columns */,
                          current.original_filename,current.content_type,current.byte_size,
                          current.generation,current.uploaded_at_ms,current.version_comment,
                          COALESCE(u.email,'System') AS uploaded_by,0 AS profile_only,
                          (SELECT count(*) FROM speaker_asset_versions history
                           WHERE history.asset_id=a.id AND history.scan_state IN
                             ('clean','superseded')) AS version_count
                   FROM speaker_assets a
                   JOIN event_speakers es ON es.id=a.event_speaker_id
                     AND es.organization_id=a.organization_id AND es.event_id=a.event_id
                   JOIN people p ON p.id=es.person_id AND p.organization_id=es.organization_id
                   JOIN speaker_asset_versions current ON current.asset_id=a.id
                     AND current.is_current=1 AND current.scan_state='clean'
                   LEFT JOIN users u ON u.id=current.uploaded_by_user_id
                   /* asset_association_join */
                   WHERE a.organization_id=?1 AND a.event_id=?2
                   UNION ALL
                   SELECT 'profile-headshot:' || es.id AS id,es.id AS event_speaker_id,
                          p.display_name AS speaker_name,'headshot' AS kind,
                          NULL AS submission_id,NULL AS proposal_title,
                          NULL AS task_id,NULL AS task_title,
                          CASE h.content_type WHEN 'image/jpeg' THEN 'headshot.jpg'
                            WHEN 'image/webp' THEN 'headshot.webp'
                            ELSE 'headshot.png' END AS original_filename,
                          h.content_type,h.byte_size,
                          1 AS generation,h.updated_at_ms AS uploaded_at_ms,
                          'Profile headshot' AS version_comment,
                          COALESCE(owner.email,'Speaker') AS uploaded_by,1 AS profile_only,
                          1 AS version_count
                   FROM event_speakers es
                   JOIN people p ON p.id=es.person_id AND p.organization_id=es.organization_id
                   JOIN users owner ON owner.id=p.user_id
                   JOIN user_headshots h ON h.user_id=owner.id
                   WHERE es.organization_id=?1 AND es.event_id=?2
                     AND h.speaker_asset_version_id IS NULL
                   ORDER BY uploaded_at_ms DESC,id LIMIT 500""")
            )
            .bind(event["organization_id"], event_id),
        )
    )
    versions_by_asset = await _asset_versions_by_asset(
        request, str(event["organization_id"]), event_id
    )
    data = []
    for row in rows:
        data.append(
            AdminSpeakerAssetView(
                id=str(row["id"]),
                event_speaker_id=str(row["event_speaker_id"]),
                speaker_name=str(row["speaker_name"]),
                submission_id=row["submission_id"],
                proposal_title=row["proposal_title"],
                task_id=row["task_id"],
                task_title=row["task_title"],
                kind=str(row["kind"]),
                filename=str(row["original_filename"]),
                content_type=str(row["content_type"]),
                byte_size=int(row["byte_size"]),
                generation=int(row["generation"]),
                version_count=int(row["version_count"]),
                uploaded_at_ms=int(row["uploaded_at_ms"]),
                uploaded_by=str(row["uploaded_by"]),
                preview_url=(
                    f"/api/v1/admin/events/{event_id}/speakers/{row['event_speaker_id']}/headshot"
                    if row["kind"] == "headshot"
                    else None
                ),
                download_grant_url=(
                    f"/api/v1/admin/events/{event_id}/assets/{row['id']}/download-grants"
                    if not row["profile_only"]
                    else None
                ),
                direct_download_url=(
                    f"/api/v1/admin/events/{event_id}/speakers/{row['event_speaker_id']}/headshot"
                    if row["profile_only"]
                    else None
                ),
                version_comment=row["version_comment"],
                versions=([] if row["profile_only"] else versions_by_asset.get(str(row["id"]), [])),
            )
        )
    return AdminSpeakerAssetList(data=data)


async def _private_object_bytes(stored) -> bytes:
    chunks = bytearray()
    async for chunk in _stream_private_object(stored):
        chunks.extend(chunk)
    return bytes(chunks)


@speaker_operations_router.get(
    "/api/v1/admin/events/{event_id}/assets/{asset_id}",
    response_model=AssetDetailView,
    operation_id="getAdminSpeakerAssetDetail",
    tags=["speaker-assets"],
)
async def get_admin_asset_detail(event_id: str, asset_id: str, request: Request) -> AssetDetailView:
    event = await _admin_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_READ,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=False,
    )
    asset_row = row_mapping(
        await _db(request)
        .prepare(
            _admin_asset_association_sql("""SELECT a.id,a.event_speaker_id,
                      p.display_name AS speaker_name,a.kind,
                      /* asset_association_columns */,
                      current.original_filename,current.content_type,current.byte_size,
                      current.generation,current.uploaded_at_ms,current.version_comment,
                      COALESCE(u.email,'System') AS uploaded_by,
                      (SELECT count(*) FROM speaker_asset_versions history
                       WHERE history.asset_id=a.id AND history.scan_state IN
                         ('clean','superseded')) AS version_count
               FROM speaker_assets a
               JOIN event_speakers es ON es.id=a.event_speaker_id
                 AND es.organization_id=a.organization_id AND es.event_id=a.event_id
               JOIN people p ON p.id=es.person_id AND p.organization_id=es.organization_id
               JOIN speaker_asset_versions current ON current.asset_id=a.id
                 AND current.is_current=1 AND current.scan_state='clean'
               LEFT JOIN users u ON u.id=current.uploaded_by_user_id
               /* asset_association_join */
               WHERE a.organization_id=?1 AND a.event_id=?2 AND a.id=?3 LIMIT 1""")
        )
        .bind(event["organization_id"], event_id, asset_id)
        .first()
    )
    if asset_row is None:
        raise HTTPException(status_code=404)
    versions = (
        await _asset_versions_by_asset(
            request, str(event["organization_id"]), event_id, asset_id=asset_id
        )
    ).get(asset_id, [])
    asset = AdminSpeakerAssetView(
        id=asset_id,
        event_speaker_id=str(asset_row["event_speaker_id"]),
        speaker_name=str(asset_row["speaker_name"]),
        submission_id=asset_row["submission_id"],
        proposal_title=asset_row["proposal_title"],
        task_id=asset_row["task_id"],
        task_title=asset_row["task_title"],
        kind=str(asset_row["kind"]),
        filename=str(asset_row["original_filename"]),
        content_type=str(asset_row["content_type"]),
        byte_size=int(asset_row["byte_size"]),
        generation=int(asset_row["generation"]),
        version_count=int(asset_row["version_count"]),
        uploaded_at_ms=int(asset_row["uploaded_at_ms"]),
        uploaded_by=str(asset_row["uploaded_by"]),
        preview_url=(
            f"/api/v1/admin/events/{event_id}/speakers/{asset_row['event_speaker_id']}/headshot"
            if asset_row["kind"] == "headshot"
            else None
        ),
        download_grant_url=f"/api/v1/admin/events/{event_id}/assets/{asset_id}/download-grants",
        direct_download_url=None,
        version_comment=asset_row["version_comment"],
        versions=versions,
    )
    rows = result_rows(
        await _db(request)
        .prepare(
            """SELECT c.id,c.version_id,c.body_text,c.parent_comment_id,c.visibility,
                  c.created_at_ms,
                  COALESCE(NULLIF(trim(p.display_name),''),
                    CASE WHEN EXISTS (
                          SELECT 1 FROM people author_person
                          JOIN event_speakers author_speaker
                            ON author_speaker.person_id=author_person.id
                            AND author_speaker.organization_id=author_person.organization_id
                          WHERE author_person.organization_id=c.organization_id
                            AND author_person.user_id=c.author_user_id
                            AND author_speaker.event_id=c.event_id
                        ) THEN 'Speaker' ELSE 'Organizer' END) AS author_name
           FROM speaker_asset_comments c JOIN users u ON u.id=c.author_user_id
           LEFT JOIN people p ON p.user_id=u.id AND p.organization_id=c.organization_id
           WHERE c.organization_id=?1 AND c.event_id=?2 AND c.asset_id=?3
           ORDER BY c.created_at_ms,c.id LIMIT 500"""
        )
        .bind(event["organization_id"], event_id, asset_id)
        .all()
    )
    return AssetDetailView(
        **asset.model_dump(), comments=[AssetCommentView(**dict(row)) for row in rows]
    )


@speaker_operations_router.post(
    "/api/v1/admin/events/{event_id}/assets/{asset_id}/versions/{version_id}/comments",
    response_model=AssetCommentView,
    status_code=201,
    operation_id="commentOnAdminSpeakerAssetVersion",
    tags=["speaker-assets"],
)
async def create_admin_asset_comment(
    event_id: str,
    asset_id: str,
    version_id: str,
    body: AssetCommentCreate,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AssetCommentView:
    if not idempotency_key or not 16 <= len(idempotency_key) <= 255:
        raise HTTPException(status_code=400)
    authenticated = await authenticate_request(request)
    event = await _admin_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_COMMENT,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
    )
    db, now = _db(request), utc_now_ms()
    version = row_mapping(
        await db.prepare(
            """SELECT v.id FROM speaker_asset_versions v JOIN speaker_assets a ON a.id=v.asset_id
           AND a.organization_id=v.organization_id AND a.event_id=v.event_id
           WHERE v.organization_id=?1 AND v.event_id=?2 AND v.asset_id=?3 AND v.id=?4
             AND v.scan_state IN ('clean','superseded') LIMIT 1"""
        )
        .bind(event["organization_id"], event_id, asset_id, version_id)
        .first()
    )
    if version is None:
        raise HTTPException(status_code=404)
    if body.parent_comment_id:
        parent = (
            await db.prepare(
                """SELECT id,visibility FROM speaker_asset_comments
                   WHERE organization_id=?1 AND event_id=?2 AND asset_id=?3
                     AND version_id=?4 AND id=?5 LIMIT 1"""
            )
            .bind(event["organization_id"], event_id, asset_id, version_id, body.parent_comment_id)
            .first()
        )
        if parent is None:
            raise HTTPException(status_code=404)
        if body.visibility == "shared" and parent["visibility"] != "shared":
            raise HTTPException(
                status_code=422,
                detail="A shared reply must reference a comment already shared with the speaker.",
            )
    fingerprint = hashlib.sha256(body.model_dump_json().encode()).digest()
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        route_key=f"speaker-assets.comments:{event_id}:{asset_id}:{version_id}",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        expires_at_ms=now + 86_400_000,
    )
    existing = row_mapping(
        await db.prepare(
            """SELECT ir.request_fingerprint,ir.response_resource_id AS resource_id,
                  c.version_id,c.body_text,c.visibility,
                  c.parent_comment_id,c.created_at_ms,
                  COALESCE(NULLIF(trim(p.display_name),''),
                    CASE WHEN EXISTS (
                          SELECT 1 FROM people author_person
                          JOIN event_speakers author_speaker
                            ON author_speaker.person_id=author_person.id
                            AND author_speaker.organization_id=author_person.organization_id
                          WHERE author_person.organization_id=c.organization_id
                            AND author_person.user_id=c.author_user_id
                            AND author_speaker.event_id=c.event_id
                        ) THEN 'Speaker' ELSE 'Organizer' END) author_name
           FROM idempotency_records ir
           JOIN speaker_asset_comments c ON c.id=ir.response_resource_id
           JOIN users u ON u.id=c.author_user_id LEFT JOIN people p ON p.user_id=u.id
             AND p.organization_id=c.organization_id
           WHERE ir.principal_key=?1 AND ir.route_key=?2 AND ir.idempotency_key_hash=?3
             AND ir.event_id=?4 AND ir.state='completed' LIMIT 1"""
        )
        .bind(record.principal_key, record.route_key, record.key_hash, event_id)
        .first()
    )
    if existing:
        if bytes(existing["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return AssetCommentView(
            id=str(existing["resource_id"]),
            **{
                k: existing[k]
                for k in (
                    "version_id",
                    "author_name",
                    "body_text",
                    "parent_comment_id",
                    "visibility",
                    "created_at_ms",
                )
            },
        )
    comment_id = new_id()
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO speaker_asset_comments
           (id,organization_id,event_id,asset_id,version_id,author_user_id,
            parent_comment_id,body_text,visibility,created_at_ms)
           VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)"""
        ).bind(
            comment_id,
            event["organization_id"],
            event_id,
            asset_id,
            version_id,
            authenticated.actor.user_id,
            body.parent_comment_id,
            body.body_text,
            body.visibility,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="speaker.asset.comment.create",
            target_type="speaker_asset",
            target_id=asset_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"version_id": version_id},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="speaker_asset_comment",
        resource_id=comment_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    author = (
        await db.prepare(
            """SELECT NULLIF(trim(display_name),'') AS author_name FROM people
               WHERE organization_id=?1 AND user_id=?2 LIMIT 1"""
        )
        .bind(event["organization_id"], authenticated.actor.user_id)
        .first("author_name")
        or "Organizer"
    )
    return AssetCommentView(
        id=comment_id,
        version_id=version_id,
        author_name=author,
        body_text=body.body_text,
        parent_comment_id=body.parent_comment_id,
        visibility=body.visibility,
        created_at_ms=now,
    )


@speaker_operations_router.post(
    "/api/v1/admin/events/{event_id}/deliverables/export",
    response_class=Response,
    operation_id="exportAdminSpeakerDeliverables",
    tags=["speaker-assets"],
)
async def export_admin_deliverables(
    event_id: str, body: AssetExportCreate, request: Request
) -> Response:
    authenticated = await authenticate_request(request)
    event = await _admin_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_EXPORT,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
    )
    placeholders = ",".join(f"?{index + 3}" for index in range(len(body.asset_ids)))
    rows = result_rows(
        await _db(request)
        .prepare(
            f"""SELECT a.id,p.display_name,v.object_key,
        v.original_filename,v.byte_size FROM speaker_assets a JOIN speaker_asset_versions v
        ON v.asset_id=a.id AND v.organization_id=a.organization_id AND v.event_id=a.event_id
        JOIN event_speakers es ON es.id=a.event_speaker_id AND es.organization_id=a.organization_id
        JOIN people p ON p.id=es.person_id AND p.organization_id=es.organization_id
        WHERE a.organization_id=?1 AND a.event_id=?2 AND a.id IN ({placeholders})
        AND v.is_current=1 AND v.scan_state='clean' ORDER BY p.display_name,a.id"""  # noqa: S608
        )
        .bind(event["organization_id"], event_id, *body.asset_ids)
        .all()
    )
    found_ids = {str(row["id"]) for row in rows}
    missing_ids = [asset_id for asset_id in body.asset_ids if asset_id not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=422,
            detail=("Unavailable or unchecked file selections: " + ", ".join(missing_ids)),
        )
    if sum(int(row["byte_size"]) for row in rows) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Export exceeds the 25 MB limit")
    archive = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED, allowZip64=False) as bundle:
        for row in rows:
            stored = await _bucket(request).get(str(row["object_key"]))
            if stored is None:
                raise HTTPException(status_code=404)
            folder = (
                re.sub(r"[^A-Za-z0-9._ -]+", "_", str(row["display_name"])).strip() or "speaker"
            )
            filename = (
                re.sub(r"[^A-Za-z0-9._ -]+", "_", str(row["original_filename"])).strip() or "file"
            )
            name = f"{folder}/{filename}"
            suffix = 2
            while name in used:
                name = f"{folder}/{suffix}-{filename}"
                suffix += 1
            used.add(name)
            bundle.writestr(name, await _private_object_bytes(stored))
    now = utc_now_ms()
    batch = CommandBatch(_db(request))
    batch.audit(
        AuditEvent(
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="speaker.assets.export",
            target_type="event",
            target_id=event_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"asset_count": len(rows)},
        )
    )
    await batch.execute()
    return Response(
        archive.getvalue(),
        media_type="application/zip",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": attachment_header(f"deliverables-{event_id}.zip"),
        },
    )


async def _stream_private_object(stored):
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
        return
    array_buffer = getattr(stored, "arrayBuffer", None)
    if callable(array_buffer):
        yield bytes(await array_buffer())
        return
    raise RuntimeError("unsupported private object body")


@speaker_operations_router.post(
    "/api/v1/speaker/events/{event_id}/assets/{asset_id}/download-grants",
    response_model=AssetDownloadGrantView,
    status_code=201,
    operation_id="createOwnSpeakerAssetDownloadGrant",
    tags=["speaker-assets"],
)
async def create_speaker_asset_download_grant(
    event_id: str, asset_id: str, request: Request
) -> AssetDownloadGrantView:
    authenticated, speaker = await _speaker_for_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_READ_OWN,
        ResourceContext(
            str(speaker["organization_id"]),
            event_id,
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    grant = await AssetRepository(_db(request)).create_download_grant(
        AssetAccessScope(
            organization_id=str(speaker["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            event_speaker_id=str(speaker["event_speaker_id"]),
        ),
        asset_id,
        now_ms=utc_now_ms(),
    )
    if grant is None:
        raise HTTPException(status_code=404)
    return AssetDownloadGrantView(token=grant.token, expires_at_ms=grant.expires_at_ms)


@speaker_operations_router.post(
    "/api/v1/speaker/events/{event_id}/assets/{asset_id}/versions/{version_id}/download-grants",
    response_model=AssetDownloadGrantView,
    status_code=201,
    operation_id="createOwnSpeakerAssetVersionDownloadGrant",
    tags=["speaker-assets"],
)
async def create_speaker_asset_version_download_grant(
    event_id: str, asset_id: str, version_id: str, request: Request
) -> AssetDownloadGrantView:
    authenticated, speaker = await _speaker_for_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_READ_OWN,
        ResourceContext(
            str(speaker["organization_id"]),
            event_id,
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    grant = await AssetRepository(_db(request)).create_download_grant(
        AssetAccessScope(
            organization_id=str(speaker["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            event_speaker_id=str(speaker["event_speaker_id"]),
        ),
        asset_id,
        version_id=version_id,
        now_ms=utc_now_ms(),
    )
    if grant is None:
        raise HTTPException(status_code=404)
    return AssetDownloadGrantView(token=grant.token, expires_at_ms=grant.expires_at_ms)


@speaker_operations_router.post(
    "/api/v1/admin/events/{event_id}/assets/{asset_id}/download-grants",
    response_model=AssetDownloadGrantView,
    status_code=201,
    operation_id="createAdminSpeakerAssetDownloadGrant",
    tags=["speaker-assets"],
)
async def create_admin_asset_download_grant(
    event_id: str, asset_id: str, request: Request
) -> AssetDownloadGrantView:
    authenticated = await authenticate_request(request)
    event = row_mapping(
        await _db(request)
        .prepare(
            """SELECT id, organization_id FROM events
               WHERE id=?1 AND status!='archived' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_READ,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
    )
    grant = await AssetRepository(_db(request)).create_download_grant(
        AssetAccessScope(
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            organizer_access=True,
        ),
        asset_id,
        now_ms=utc_now_ms(),
    )
    if grant is None:
        raise HTTPException(status_code=404)
    return AssetDownloadGrantView(token=grant.token, expires_at_ms=grant.expires_at_ms)


@speaker_operations_router.post(
    "/api/v1/admin/events/{event_id}/assets/{asset_id}/versions/{version_id}/download-grants",
    response_model=AssetDownloadGrantView,
    status_code=201,
    operation_id="createAdminSpeakerAssetVersionDownloadGrant",
    tags=["speaker-assets"],
)
async def create_admin_asset_version_download_grant(
    event_id: str, asset_id: str, version_id: str, request: Request
) -> AssetDownloadGrantView:
    authenticated = await authenticate_request(request)
    event = row_mapping(
        await _db(request)
        .prepare("SELECT id,organization_id FROM events WHERE id=?1 AND status!='archived' LIMIT 1")
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_READ,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
    )
    grant = await AssetRepository(_db(request)).create_download_grant(
        AssetAccessScope(
            organization_id=str(event["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            organizer_access=True,
        ),
        asset_id,
        version_id=version_id,
        now_ms=utc_now_ms(),
    )
    if grant is None:
        raise HTTPException(status_code=404)
    return AssetDownloadGrantView(token=grant.token, expires_at_ms=grant.expires_at_ms)


@speaker_operations_router.post(
    "/api/v1/assets/download",
    response_class=StreamingResponse,
    operation_id="consumePrivateAssetDownloadGrant",
    tags=["speaker-assets"],
)
async def consume_asset_download_grant(
    body: AssetDownloadToken, request: Request
) -> StreamingResponse:
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    download = await AssetRepository(_db(request)).consume_download_grant(
        _bucket(request),
        actor_user_id=authenticated.actor.user_id,
        token=body.token,
        now_ms=utc_now_ms(),
    )
    if download is None:
        raise HTTPException(status_code=404)
    return StreamingResponse(
        _stream_private_object(download.body),
        media_type=download.content_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": attachment_header(download.filename),
            "Content-Length": str(download.byte_size),
        },
    )


@speaker_operations_router.post(
    "/api/v1/speaker/events/{event_id}/upload-authorizations",
    response_model=UploadAuthorizationView,
    status_code=201,
    operation_id="authorizeOwnSpeakerAssetUpload",
    tags=["speaker-assets"],
)
async def authorize_speaker_upload(
    event_id: str,
    request: Request,
    body: UploadAuthorizationCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> UploadAuthorizationView:
    authenticated, speaker = await _speaker_for_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_UPLOAD_OWN,
        ResourceContext(
            str(speaker["organization_id"]),
            event_id,
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    allowed_types, max_bytes = ASSET_UPLOAD_RULES[body.kind]
    if body.content_type not in allowed_types:
        raise UploadPolicyError(
            415,
            "upload_type_not_allowed",
            "This file type is not allowed for that upload.",
        )
    if body.byte_size > max_bytes:
        raise UploadPolicyError(
            413,
            "upload_too_large",
            f"This file is larger than the {max_bytes // 1024 // 1024} MB limit.",
        )
    db = _db(request)
    if body.submission_id is not None:
        owned = (
            await db.prepare(
                """SELECT 1 AS found FROM submission_speakers
               WHERE organization_id = ?1 AND event_id = ?2 AND event_speaker_id = ?3
                 AND submission_id = ?4 LIMIT 1"""
            )
            .bind(
                speaker["organization_id"],
                event_id,
                speaker["event_speaker_id"],
                body.submission_id,
            )
            .first("found")
        )
        if owned is None:
            raise HTTPException(status_code=404)
    if body.task_id is not None:
        task = row_mapping(
            await db.prepare(
                """SELECT task_type,form_schema_json FROM speaker_tasks WHERE organization_id = ?1
               AND event_id = ?2 AND event_speaker_id = ?3 AND id = ?4
               AND state = 'open' LIMIT 1"""
            )
            .bind(
                speaker["organization_id"],
                event_id,
                speaker["event_speaker_id"],
                body.task_id,
            )
            .first()
        )
        if task is None or str(task["task_type"]) != body.kind:
            raise HTTPException(status_code=404)
        try:
            task_schema = json.loads(str(task["form_schema_json"] or "{}"))
            upload_rules = task_schema.get("upload", {})
            task_types = set(upload_rules.get("allowed_content_types", ()))
            task_max_bytes = int(upload_rules.get("max_file_bytes") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise UploadPolicyError(
                409,
                "upload_task_not_configured",
                "This upload request is not configured. Ask an organizer to update it.",
            ) from None
        if (
            upload_rules.get("enabled") is not True
            or not task_types
            or task_max_bytes <= 0
        ):
            raise UploadPolicyError(
                409,
                "upload_task_not_configured",
                "This upload request is not configured. Ask an organizer to update it.",
            )
        if body.content_type not in task_types:
            raise UploadPolicyError(
                415,
                "upload_type_not_allowed",
                "This file type is not allowed for this request.",
            )
        if body.byte_size > task_max_bytes:
            raise UploadPolicyError(
                413,
                "upload_too_large",
                f"This request allows files up to {task_max_bytes // 1024 // 1024} MB.",
            )
    key = _key(idempotency_key)
    route = "POST /api/v1/speaker/events/{event_id}/upload-authorizations"
    fingerprint = _fingerprint(body)
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint, response_resource_id FROM idempotency_records
           WHERE principal_key = ?1 AND route_key = ?2 AND idempotency_key_hash = ?3
             AND event_id = ?4 AND state = 'completed'"""
        )
        .bind(
            authenticated.actor.user_id,
            route,
            hashlib.sha256(key.encode()).digest(),
            event_id,
        )
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        intent_id = str(replay["response_resource_id"])
        return await _authorization_view(request, intent_id, _upload_token(request, intent_id))

    await enforce_rate_limit(
        request,
        binding_name="SPEAKER_UPLOAD_AUTH_RATE_LIMITER",
        policy=RateLimitPolicy("speaker.asset.upload_authorize", limit=3, window_seconds=60),
        subject=f"{authenticated.actor.user_id}:{event_id}",
    )
    now = utc_now_ms()
    await _enforce_speaker_upload_quota(
        db,
        organization_id=str(speaker["organization_id"]),
        event_id=event_id,
        event_speaker_id=str(speaker["event_speaker_id"]),
        requested_bytes=body.byte_size,
        now_ms=now,
    )

    slot = row_mapping(
        await db.prepare(
            """SELECT id FROM speaker_assets WHERE organization_id = ?1 AND event_id = ?2
           AND event_speaker_id = ?3 AND COALESCE(submission_id, '') = COALESCE(?4, '')
           AND kind = ?5 ORDER BY created_at_ms, id LIMIT 1"""
        )
        .bind(
            speaker["organization_id"],
            event_id,
            speaker["event_speaker_id"],
            body.submission_id,
            body.kind,
        )
        .first()
    )
    version_comment = body.version_comment or None
    asset_id = str(slot["id"]) if slot is not None else new_id()
    generation = int(
        await db.prepare(
            """SELECT COALESCE(MAX(generation), 0) + 1 AS generation
           FROM speaker_asset_versions WHERE asset_id = ?1"""
        )
        .bind(asset_id)
        .first("generation")
    )
    intent_id, version_id = new_id(), new_id()
    upload_token = _upload_token(request, intent_id)
    expires = now + 10 * 60 * 1000
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        organization_id=str(speaker["organization_id"]),
        event_id=event_id,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    if slot is None:
        batch.add_statement(
            db.prepare(
                """INSERT INTO speaker_assets
               (id, organization_id, event_id, event_speaker_id, submission_id, task_id,
                kind, created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?8)"""
            ).bind(
                asset_id,
                speaker["organization_id"],
                event_id,
                speaker["event_speaker_id"],
                body.submission_id,
                body.task_id,
                body.kind,
                now,
            )
        )
    version_object_key = f"private/{version_id}/{new_id()}"
    if version_comment is None:
        batch.add_statement(
            db.prepare(
                """INSERT INTO speaker_asset_versions
               (id,organization_id,event_id,event_speaker_id,asset_id,generation,
                object_key,original_filename,scan_state,created_at_ms,uploaded_by_user_id)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'pending_upload',?9,?10)"""
            ).bind(
                version_id,
                speaker["organization_id"],
                event_id,
                speaker["event_speaker_id"],
                asset_id,
                generation,
                version_object_key,
                body.filename,
                now,
                authenticated.actor.user_id,
            )
        )
    else:
        batch.add_statement(
            db.prepare(
                """INSERT INTO speaker_asset_versions
               (id,organization_id,event_id,event_speaker_id,asset_id,generation,
                object_key,original_filename,scan_state,created_at_ms,version_comment,
                uploaded_by_user_id)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'pending_upload',?9,?10,?11)"""
            ).bind(
                version_id,
                speaker["organization_id"],
                event_id,
                speaker["event_speaker_id"],
                asset_id,
                generation,
                version_object_key,
                body.filename,
                now,
                version_comment,
                authenticated.actor.user_id,
            )
        )
    batch.add_statement(
        db.prepare(
            """INSERT INTO upload_intents
           (id, organization_id, event_id, event_speaker_id, asset_version_id, purpose,
            token_hash, expected_content_type, expected_byte_size, expected_checksum_sha256,
            expires_at_ms, created_at_ms)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)"""
        ).bind(
            intent_id,
            speaker["organization_id"],
            event_id,
            speaker["event_speaker_id"],
            version_id,
            "replace" if slot is not None else "create",
            hash_token(upload_token),
            body.content_type,
            body.byte_size,
            bytes.fromhex(body.checksum_sha256),
            expires,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            organization_id=str(speaker["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="speaker.asset.upload_authorize",
            target_type="speaker_asset_version",
            target_id=version_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"kind": body.kind, "generation": generation},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="upload_intent",
        resource_id=intent_id,
        completed_at_ms=now,
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    return await _authorization_view(request, intent_id, upload_token)


@speaker_operations_router.put(
    "/api/v1/uploads/{intent_id}/content",
    status_code=204,
    include_in_schema=False,
)
async def local_upload_content(intent_id: str, request: Request, token: str) -> None:
    if getattr(request.scope.get("env"), "APP_ENV", "production") != "local":
        raise HTTPException(status_code=404)
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT ui.token_hash, ui.expected_content_type, ui.expected_byte_size,
                  ui.expected_checksum_sha256, ui.expires_at_ms, ui.consumed_at_ms,
                  av.object_key, av.scan_state
           FROM upload_intents ui JOIN speaker_asset_versions av ON av.id = ui.asset_version_id
           WHERE ui.id = ?1 LIMIT 1"""
        )
        .bind(intent_id)
        .first()
    )
    if (
        row is None
        or int(row["expires_at_ms"]) < utc_now_ms()
        or row["consumed_at_ms"] is not None
        or str(row["scan_state"]) != "pending_upload"
        or not hmac.compare_digest(_blob(row["token_hash"]), hash_token(token))
    ):
        raise HTTPException(status_code=404)
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    content_length = request.headers.get("content-length")
    if content_type != str(row["expected_content_type"]):
        raise HTTPException(status_code=400)
    if content_length is not None and int(content_length) != int(row["expected_byte_size"]):
        raise HTTPException(status_code=400)
    body = await request.body()
    if len(body) != int(row["expected_byte_size"]):
        raise HTTPException(status_code=400)
    if not hmac.compare_digest(
        hashlib.sha256(body).digest(), _blob(row["expected_checksum_sha256"])
    ):
        raise HTTPException(status_code=400)
    await _bucket(request).put(str(row["object_key"]), body)


@speaker_operations_router.post(
    "/api/v1/speaker/events/{event_id}/upload-intents/{intent_id}/complete",
    response_model=UploadCompletionView,
    operation_id="completeOwnSpeakerAssetUpload",
    tags=["speaker-assets"],
)
async def complete_speaker_upload(
    event_id: str,
    intent_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> UploadCompletionView:
    authenticated, speaker = await _speaker_for_event(request, event_id)
    await require_permission(
        request,
        Permission.SPEAKER_ASSET_UPLOAD_OWN,
        ResourceContext(
            str(speaker["organization_id"]),
            event_id,
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    _key(idempotency_key)
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT ui.id, ui.organization_id, ui.event_id,
                  ui.expected_content_type, ui.expected_byte_size,
                  ui.expected_checksum_sha256, ui.expires_at_ms, ui.consumed_at_ms,
                  av.id AS version_id, av.asset_id, av.object_key, av.generation,
                  av.scan_state, a.kind, a.submission_id
           FROM upload_intents ui
           JOIN speaker_asset_versions av ON av.organization_id = ui.organization_id
            AND av.event_id = ui.event_id AND av.id = ui.asset_version_id
           JOIN speaker_assets a ON a.organization_id = av.organization_id
            AND a.event_id = av.event_id AND a.id = av.asset_id
           WHERE ui.id = ?1 AND ui.organization_id = ?2 AND ui.event_id = ?3
             AND ui.event_speaker_id = ?4 LIMIT 1"""
        )
        .bind(
            intent_id,
            speaker["organization_id"],
            event_id,
            speaker["event_speaker_id"],
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    # A spent intent is the same answer as an already-transitioned version, and both
    # are checked: every branch below writes consumed_at_ms in the same batch as the
    # state transition, so the two can only disagree if a future branch forgets one.
    # Neither closes a genuine race -- two requests reading 'pending_upload' with
    # consumed_at_ms still NULL both get past here -- which is why the write path is
    # guarded on its own compare-and-swap rather than on this read.
    if (
        str(row["scan_state"]) in {"uploaded", "scanning", "clean", "rejected"}
        or row["consumed_at_ms"] is not None
    ):
        if (
            str(row["scan_state"]) == "uploaded"
            and getattr(request.scope.get("env"), "APP_ENV", "production") != "local"
        ):
            await _enqueue_asset_scan(request, row)
        return UploadCompletionView(intent_id=intent_id, state=str(row["scan_state"]))
    if int(row["expires_at_ms"]) < utc_now_ms():
        raise HTTPException(status_code=404)
    stored = await _bucket(request).head(str(row["object_key"]))
    if stored is None or int(stored.size) != int(row["expected_byte_size"]):
        raise HTTPException(status_code=409)
    now = utc_now_ms()
    environment = request.scope.get("env")
    local = getattr(environment, "APP_ENV", "production") == "local"
    scan_bypassed = malware_scan_disabled(environment)
    inline_completion = local or scan_bypassed
    scan_result = None
    if scan_bypassed:
        scan_result = ScanResult(
            job_id=str(row["version_id"]),
            verdict="clean",
            engine="disabled",
            signature=None,
        )
    elif local:
        stored_body = await _bucket(request).get(str(row["object_key"]))
        if stored_body is None:
            raise HTTPException(status_code=409)
        job = ScanJob(
            schema_version=1,
            organization_id=str(row["organization_id"]),
            event_id=event_id,
            asset_version_id=str(row["version_id"]),
            generation=int(row["generation"]),
            checksum_sha256=_blob(row["expected_checksum_sha256"]),
            job_id=str(row["version_id"]),
        )
        try:
            provider_result = await SignedScannerAdapter(environment).scan(stored_body, job=job)
        except Exception as exc:
            raise HTTPException(status_code=503) from exc
        if provider_result.verdict == "error":
            raise HTTPException(status_code=503)
        scan_result = ScanResult(
            job_id=job.job_id,
            verdict=provider_result.verdict,
            engine=provider_result.engine,
            signature=provider_result.signature_code,
        )
    batch = CommandBatch(db)
    if inline_completion and scan_result is not None:
        batch.add_statement(
            db.prepare(
                """UPDATE speaker_asset_versions SET content_type = ?1, byte_size = ?2,
                      checksum_sha256 = ?3, scan_state = 'scanning',
                      uploaded_at_ms = ?4, scan_started_at_ms = ?4
               WHERE id = ?5 AND scan_state = 'pending_upload'"""
            ).bind(
                row["expected_content_type"],
                row["expected_byte_size"],
                row["expected_checksum_sha256"],
                now,
                row["version_id"],
            )
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO asset_scan_events
               (id, organization_id, event_id, asset_version_id, generation,
                checksum_sha256, provider_event_id, job_id, verdict, engine,
                signature_code, received_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)
               ON CONFLICT DO NOTHING"""
            ).bind(
                new_id(),
                speaker["organization_id"],
                event_id,
                row["version_id"],
                row["generation"],
                row["expected_checksum_sha256"],
                f"{'bypass' if scan_bypassed else 'local'}:{row['version_id']}",
                row["version_id"],
                scan_result.verdict,
                scan_result.engine,
                scan_result.signature,
                now,
            )
        )
    if inline_completion and scan_result is not None and scan_result.verdict == "clean":
        batch.add_statement(
            db.prepare(
                # Demote the outgoing version only when the promote below is certain to
                # land, so the two can never disagree. Every condition the promote checks
                # is therefore checked here too, against the same candidate: it is the
                # version this request just claimed, its receipt is on file, and no newer
                # generation outranks it. Anything less strands the asset with no current
                # version -- the outgoing one demoted, the incoming one left in 'scanning'.
                #
                # id <> ?3 covers the case none of that would: two completions that both
                # read 'pending_upload' before either batch commits serialise through here
                # twice, and on the second pass the candidate IS the current clean version.
                # The receipt guard cannot catch that one, because the first pass's receipt
                # satisfies it. This mirrors _clean_result_statements, which excludes its
                # own candidate and gates on the same three conditions.
                """UPDATE speaker_asset_versions SET is_current = 0, scan_state = 'superseded',
                      scanned_at_ms = ?1
               WHERE asset_id = ?2 AND is_current = 1 AND scan_state = 'clean'
                 AND id <> ?3
                 AND EXISTS (
                   SELECT 1 FROM speaker_asset_versions candidate
                   WHERE candidate.id = ?3 AND candidate.scan_state = 'scanning'
                     AND candidate.asset_id = speaker_asset_versions.asset_id
                     AND EXISTS (
                       SELECT 1 FROM asset_scan_events receipt
                       WHERE receipt.organization_id = candidate.organization_id
                         AND receipt.event_id = candidate.event_id
                         AND receipt.asset_version_id = candidate.id
                         AND receipt.generation = candidate.generation
                         AND receipt.checksum_sha256 = candidate.checksum_sha256
                         AND receipt.verdict = 'clean'
                     )
                     AND NOT EXISTS (
                       SELECT 1 FROM speaker_asset_versions newer
                       WHERE newer.asset_id = candidate.asset_id
                         AND newer.generation > candidate.generation
                     )
                 )"""
            ).bind(now, row["asset_id"], row["version_id"])
        )
        batch.add_statement(
            db.prepare(
                """UPDATE speaker_asset_versions SET content_type = ?1, byte_size = ?2,
                      checksum_sha256 = ?3, scan_state = 'clean', is_current = 1,
                      scanned_at_ms = ?4,
                      scan_result_code = ?5
               WHERE id = ?6 AND scan_state = 'scanning'
                 AND generation = ?7 AND checksum_sha256 = ?3
                 AND EXISTS (
                   SELECT 1 FROM asset_scan_events receipt
                   WHERE receipt.organization_id = speaker_asset_versions.organization_id
                     AND receipt.event_id = speaker_asset_versions.event_id
                     AND receipt.asset_version_id = speaker_asset_versions.id
                     AND receipt.generation = speaker_asset_versions.generation
                     AND receipt.checksum_sha256 = speaker_asset_versions.checksum_sha256
                     AND receipt.verdict = 'clean'
                 )
                 AND NOT EXISTS (
                   SELECT 1 FROM speaker_asset_versions newer
                   WHERE newer.asset_id = speaker_asset_versions.asset_id
                     AND newer.generation > speaker_asset_versions.generation
                 )"""
            ).bind(
                row["expected_content_type"],
                row["expected_byte_size"],
                row["expected_checksum_sha256"],
                now,
                "development_bypass" if scan_bypassed else "clamav_clean",
                row["version_id"],
                row["generation"],
            )
        )
        batch.add_statement(
            db.prepare(
                # Only a promote that actually landed completes the task, exactly as the
                # queued path requires v.is_current=1 AND v.scan_state='clean'. Without
                # this the task is marked done off the back of a no-op promote, telling
                # the speaker their headshot is accepted while the version sits in
                # 'scanning'.
                """UPDATE speaker_tasks SET state = 'completed', completed_at_ms = ?1,
                      version = version + 1, updated_at_ms = ?1
               WHERE organization_id = ?2 AND event_id = ?3 AND event_speaker_id = ?4
                 AND task_type = ?5 AND state = 'open'
                 AND COALESCE(submission_id, '') = COALESCE(?6, '')
                 AND EXISTS (
                   SELECT 1 FROM speaker_asset_versions promoted
                   WHERE promoted.id = ?7 AND promoted.is_current = 1
                     AND promoted.scan_state = 'clean'
                 )"""
            ).bind(
                now,
                speaker["organization_id"],
                event_id,
                speaker["event_speaker_id"],
                row["kind"],
                row["submission_id"],
                row["version_id"],
            )
        )
        state = "clean"
    elif local and scan_result is not None:
        batch.add_statement(
            db.prepare(
                # Symmetric with the promote above: a terminal verdict is only written
                # when the stored receipt says the same thing. Rejecting off a receipt
                # that says 'clean' is the same defect as promoting off one that says
                # 'malicious', just in the direction nobody notices. Also scoped to this
                # generation and checksum, which the promote already was.
                """UPDATE speaker_asset_versions SET content_type = ?1, byte_size = ?2,
                      checksum_sha256 = ?3, scan_state = 'rejected', is_current = 0,
                      scanned_at_ms = ?4,
                      scan_result_code = ?5
               WHERE id = ?6 AND scan_state = 'scanning'
                 AND generation = ?7 AND checksum_sha256 = ?3
                 AND EXISTS (
                   SELECT 1 FROM asset_scan_events receipt
                   WHERE receipt.organization_id = speaker_asset_versions.organization_id
                     AND receipt.event_id = speaker_asset_versions.event_id
                     AND receipt.asset_version_id = speaker_asset_versions.id
                     AND receipt.generation = speaker_asset_versions.generation
                     AND receipt.checksum_sha256 = speaker_asset_versions.checksum_sha256
                     AND receipt.verdict = 'malicious'
                 )"""
            ).bind(
                row["expected_content_type"],
                row["expected_byte_size"],
                row["expected_checksum_sha256"],
                now,
                scan_result.signature or "malware_detected",
                row["version_id"],
                row["generation"],
            )
        )
        state = "rejected"
    else:
        batch.add_statement(
            db.prepare(
                """UPDATE speaker_asset_versions SET content_type = ?1, byte_size = ?2,
                      checksum_sha256 = ?3, scan_state = 'uploaded', uploaded_at_ms = ?4
               WHERE id = ?5 AND scan_state = 'pending_upload'"""
            ).bind(
                row["expected_content_type"],
                row["expected_byte_size"],
                row["expected_checksum_sha256"],
                now,
                row["version_id"],
            )
        )
        state = "uploaded"
    batch.add_statement(
        db.prepare(
            "UPDATE upload_intents SET consumed_at_ms = ?1 WHERE id = ?2 AND consumed_at_ms IS NULL"
        ).bind(now, intent_id)
    )
    batch.audit(
        AuditEvent(
            organization_id=str(speaker["organization_id"]),
            event_id=event_id,
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="speaker.asset.upload_complete",
            target_type="speaker_asset_version",
            target_id=str(row["version_id"]),
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={
                "local_scan": int(local and not scan_bypassed),
                "malware_scan_bypassed": int(scan_bypassed),
                "generation": int(row["generation"]),
            },
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    if inline_completion and scan_result is not None:
        # Both inline terminal writes are conditional, so 'clean'/'rejected' above is
        # the verdict this request INTENDED. Report what the row actually holds: a
        # promote or rejection that no-oped leaves 'scanning', and answering 'clean' to
        # a version still sitting in quarantine is the response half of the same bug the
        # task guard closes. Only the inline branches need this -- the queued path
        # answers 'uploaded' for work that has not happened yet by design.
        state = _completion_state(
            await db.prepare(
                "SELECT scan_state FROM speaker_asset_versions WHERE id = ?1"
            )
            .bind(row["version_id"])
            .first("scan_state"),
            attempted=state,
        )
    if state == "uploaded":
        await _enqueue_asset_scan(request, row)
    return UploadCompletionView(intent_id=intent_id, state=state)

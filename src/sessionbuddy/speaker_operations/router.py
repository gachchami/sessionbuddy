import hashlib
import hmac
import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from sessionbuddy.cfp.router import DEMO_EVENT_ID, DEMO_ORG_ID
from sessionbuddy.console import embedded_assets
from sessionbuddy.observability import record_timing
from sessionbuddy.platform.auth import generate_token, hash_token, issue_csrf_token
from sessionbuddy.platform.auth.cookies import sign_session_cookie
from sessionbuddy.platform.auth.http import (
    authenticate_request,
    guard_mutation,
    require_permission,
    secret,
)
from sessionbuddy.platform.authorization import Permission, ResourceContext
from sessionbuddy.platform.db.commands import (
    AuditEvent,
    CommandBatch,
    IdempotencyRecord,
    OutboxMessage,
)
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.storage import (
    ScanResult,
    malware_scan_disabled,
    parse_signed_scan_response,
    presign_r2_put,
    scan_request_headers,
)

from .asset_boundary import AssetAccessScope, AssetRepository, ScanJob
from .models import (
    AssetDownloadGrantView,
    AssetDownloadToken,
    DemoSpeakerSession,
    OnboardingDashboardView,
    OnboardingRow,
    OnboardingSummary,
    SpeakerAssetList,
    SpeakerAssetView,
    SpeakerEventView,
    SpeakerPortalView,
    SpeakerProfileUpdate,
    SpeakerProfileView,
    SpeakerSubmissionView,
    SpeakerTaskView,
    UploadAuthorizationCreate,
    UploadAuthorizationView,
    UploadCompletionView,
)

speaker_operations_router = APIRouter()

DEMO_SPEAKER_USER_ID = "99999999-9999-4999-8999-999999999999"
DEMO_SPEAKER_ORG_MEMBERSHIP_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
DEMO_SPEAKER_EVENT_MEMBERSHIP_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
DEMO_PERSON_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
DEMO_EVENT_SPEAKER_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
DEMO_PROGRAM_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
DEMO_FORM_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
DEMO_SUBMISSION_ID = "12121212-1212-4212-8212-121212121212"
DEMO_SUBMISSION_SPEAKER_ID = "13131313-1313-4313-8313-131313131313"
DEMO_PROFILE_TASK_ID = "14141414-1414-4414-8414-141414141414"


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


def _db(request: Request):
    db = getattr(request.scope.get("env"), "DB", None)
    if db is None:
        raise HTTPException(status_code=503)
    return db


def _product_asset(request: Request, name: str, media_type: str) -> Response:
    return Response(_asset(name), media_type=media_type, headers={"Cache-Control": "no-store"})


@speaker_operations_router.get("/speaker", response_class=HTMLResponse, include_in_schema=False)
async def speaker_page(request: Request) -> Response:
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
    "/admin/events/{event_id}/onboarding",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_onboarding_page(event_id: str, request: Request) -> Response:
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


@speaker_operations_router.post(
    "/api/v1/demo/speaker-session",
    response_model=DemoSpeakerSession,
    operation_id="createLocalDemoSpeakerSession",
    tags=["demo"],
)
async def create_demo_speaker_session(request: Request, response: Response) -> DemoSpeakerSession:
    if getattr(request.scope.get("env"), "APP_ENV", "production") != "local":
        raise HTTPException(status_code=404)
    db = _db(request)
    now = utc_now_ms()
    session_id = new_id()
    token = generate_token()
    csrf_token = issue_csrf_token(session_id, secret(request, "CSRF_HMAC_KEY"))
    await db.batch(
        [
            db.prepare(
                """INSERT OR IGNORE INTO organizations
                   (id, name, status, created_at_ms, updated_at_ms)
                   VALUES (?1, 'SessionBuddy Demo', 'active', ?2, ?2)"""
            ).bind(DEMO_ORG_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO events
                   (id, organization_id, name, starts_at_ms, ends_at_ms, time_zone,
                    delivery_mode, status, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, 'Demo Conference', ?3, ?4, 'Asia/Kolkata', 'hybrid',
                           'active', ?3, ?3)"""
            ).bind(DEMO_EVENT_ID, DEMO_ORG_ID, now, now + 30 * 86_400_000),
            db.prepare(
                """INSERT OR IGNORE INTO users
                   (id, email, normalized_email, status, email_verified_at_ms,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, 'speaker@local.invalid', 'speaker@local.invalid',
                           'active', ?2, ?2, ?2)"""
            ).bind(DEMO_SPEAKER_USER_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO organization_memberships
                   (id, organization_id, user_id, role, status, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, 'member', 'active', ?4, ?4)"""
            ).bind(DEMO_SPEAKER_ORG_MEMBERSHIP_ID, DEMO_ORG_ID, DEMO_SPEAKER_USER_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO event_memberships
                   (id, organization_id, event_id, user_id, role, status,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 'speaker', 'active', ?5, ?5)"""
            ).bind(
                DEMO_SPEAKER_EVENT_MEMBERSHIP_ID,
                DEMO_ORG_ID,
                DEMO_EVENT_ID,
                DEMO_SPEAKER_USER_ID,
                now,
            ),
            db.prepare(
                """INSERT OR IGNORE INTO programs
                   (id, organization_id, event_id, name, status, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, 'Main stage', 'closed', ?4, ?4)"""
            ).bind(DEMO_PROGRAM_ID, DEMO_ORG_ID, DEMO_EVENT_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO call_for_speaker_forms
                   (id, organization_id, event_id, program_id, version, slug, welcome_text,
                    schema_json, status, published_at_ms, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 1, 'speaker-portal-demo', 'Welcome', '{}',
                           'published', ?5, ?5, ?5)"""
            ).bind(DEMO_FORM_ID, DEMO_ORG_ID, DEMO_EVENT_ID, DEMO_PROGRAM_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO submissions
                   (id, organization_id, event_id, program_id, form_id, public_session_id,
                    proposal_title, proposal_abstract, speaker_name, status, submitted_at_ms,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, ?5, 'speaker-portal-demo',
                           'Building reliable AI systems', 'A practical session.',
                           'Demo Speaker', 'submitted', ?6, ?6, ?6)"""
            ).bind(
                DEMO_SUBMISSION_ID,
                DEMO_ORG_ID,
                DEMO_EVENT_ID,
                DEMO_PROGRAM_ID,
                DEMO_FORM_ID,
                now,
            ),
            db.prepare(
                """INSERT OR IGNORE INTO people
                   (id, organization_id, user_id, display_name, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, 'Demo Speaker', ?4, ?4)"""
            ).bind(DEMO_PERSON_ID, DEMO_ORG_ID, DEMO_SPEAKER_USER_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO event_speakers
                   (id, organization_id, event_id, person_id, status, accepted_at_ms,
                    last_activity_at_ms, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 'onboarding', ?5, ?5, ?5, ?5)"""
            ).bind(DEMO_EVENT_SPEAKER_ID, DEMO_ORG_ID, DEMO_EVENT_ID, DEMO_PERSON_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO submission_speakers
                   (id, organization_id, event_id, submission_id, event_speaker_id, role,
                    snapshot_name, created_at_ms)
                   VALUES (?1, ?2, ?3, ?4, ?5, 'primary', 'Demo Speaker', ?6)"""
            ).bind(
                DEMO_SUBMISSION_SPEAKER_ID,
                DEMO_ORG_ID,
                DEMO_EVENT_ID,
                DEMO_SUBMISSION_ID,
                DEMO_EVENT_SPEAKER_ID,
                now,
            ),
            db.prepare(
                """INSERT OR IGNORE INTO speaker_tasks
                   (id, organization_id, event_id, event_speaker_id, submission_id,
                    task_type, title, help_text, destination_type, state, due_at_ms,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, ?5, 'profile', 'Complete your biography',
                           'Add a biography for the event program.', 'profile', 'open',
                           ?6, ?7, ?7)"""
            ).bind(
                DEMO_PROFILE_TASK_ID,
                DEMO_ORG_ID,
                DEMO_EVENT_ID,
                DEMO_EVENT_SPEAKER_ID,
                DEMO_SUBMISSION_ID,
                now + 7 * 86_400_000,
                now,
            ),
            db.prepare(
                """INSERT INTO sessions
                   (id, user_id, token_hash, csrf_secret_hash, authorization_version,
                    created_at_ms, last_seen_at_ms, idle_expires_at_ms, absolute_expires_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 1, ?5, ?5, ?6, ?7)"""
            ).bind(
                session_id,
                DEMO_SPEAKER_USER_ID,
                hash_token(token),
                hash_token(csrf_token),
                now,
                now + 12 * 60 * 60 * 1000,
                now + 30 * 24 * 60 * 60 * 1000,
            ),
        ]
    )
    response.set_cookie(
        "sessionbuddy-local",
        sign_session_cookie(token, secret(request, "SESSION_HMAC_KEY")),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return DemoSpeakerSession(csrf_token=csrf_token)


async def _timed_first(request: Request, statement):
    started = perf_counter()
    try:
        return await statement.first()
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)


async def _timed_all(request: Request, statement):
    started = perf_counter()
    try:
        return await statement.all()
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)


def _cursor(
    request: Request,
    value: str | None,
    *,
    event_id: str,
    state: str,
    task_type: str | None,
) -> tuple[int, int, str, int] | None:
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
            "as_of": decoded.get("as_of"),
            "due": decoded.get("due"),
            "event": event_id,
            "exp": decoded.get("exp"),
            "id": decoded.get("id"),
            "state": state,
            "task_type": task_type,
            "v": 1,
        }:
            raise ValueError
        values = (decoded["due"], decoded["id"], decoded["as_of"], decoded["exp"])
        if (
            not isinstance(values[0], int)
            or not isinstance(values[1], str)
            or len(values[1]) > 100
            or not isinstance(values[2], int)
            or not isinstance(values[3], int)
            or values[3] < utc_now_ms()
        ):
            raise ValueError
        return values
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
        raise HTTPException(status_code=400) from exc


def _next_cursor(
    request: Request,
    *,
    event_id: str,
    state: str,
    task_type: str | None,
    due_at_ms: int | None,
    task_id: str,
    as_of: int,
) -> str:
    payload = json.dumps(
        {
            "as_of": as_of,
            "due": due_at_ms if due_at_ms is not None else 9_223_372_036_854_775_807,
            "event": event_id,
            "exp": as_of + 15 * 60 * 1000,
            "id": task_id,
            "state": state,
            "task_type": task_type,
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
    state: Literal["open", "completed", "overdue", "due_soon"] = "open",
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
    summary = (
        row_mapping(
            await _timed_first(
                request,
                db.prepare(
                    """SELECT
                     SUM(CASE WHEN NOT EXISTS (
                       SELECT 1 FROM speaker_tasks t WHERE t.organization_id = es.organization_id
                         AND t.event_id = es.event_id AND t.event_speaker_id = es.id
                         AND t.state = 'open') THEN 1 ELSE 0 END) AS complete,
                     SUM(CASE WHEN EXISTS (
                       SELECT 1 FROM speaker_tasks t WHERE t.organization_id = es.organization_id
                         AND t.event_id = es.event_id AND t.event_speaker_id = es.id
                         AND t.state = 'open') THEN 1 ELSE 0 END) AS incomplete,
                     SUM(CASE WHEN EXISTS (
                       SELECT 1 FROM speaker_tasks t WHERE t.organization_id = es.organization_id
                         AND t.event_id = es.event_id AND t.event_speaker_id = es.id
                         AND t.state = 'open' AND t.due_at_ms < ?3) THEN 1 ELSE 0 END) AS overdue,
                     SUM(CASE WHEN EXISTS (
                       SELECT 1 FROM speaker_tasks t WHERE t.organization_id = es.organization_id
                         AND t.event_id = es.event_id AND t.event_speaker_id = es.id
                         AND t.state = 'open' AND t.due_at_ms >= ?3
                         AND t.due_at_ms <= ?4) THEN 1 ELSE 0 END) AS due_soon
                   FROM event_speakers es
                   WHERE es.organization_id = ?1 AND es.event_id = ?2
                     AND es.status IN ('onboarding', 'complete')"""
                ).bind(event["organization_id"], event["id"], now, due_soon_at),
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
                           es.last_activity_at_ms, p.display_name,
                           COALESCE((SELECT s.proposal_title FROM submission_speakers ss
                             JOIN submissions s ON s.organization_id = ss.organization_id
                              AND s.event_id = ss.event_id AND s.id = ss.submission_id
                             WHERE ss.organization_id = es.organization_id
                               AND ss.event_id = es.event_id
                               AND ss.event_speaker_id = es.id
                             ORDER BY s.submitted_at_ms DESC, s.id DESC LIMIT 1), '')
                             AS proposal_title
                    FROM speaker_tasks t
                    JOIN event_speakers es ON es.organization_id = t.organization_id
                     AND es.event_id = t.event_id AND es.id = t.event_speaker_id
                    JOIN people p ON p.organization_id = es.organization_id
                     AND p.id = es.person_id
                    WHERE t.organization_id = ?1 AND t.event_id = ?2
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
        row_state = str(row["state"])
        due_at = int(row["due_at_ms"]) if row["due_at_ms"] is not None else None
        if row_state == "open" and due_at is not None:
            if due_at < now:
                row_state = "overdue"
            elif due_at <= due_soon_at:
                row_state = "due_soon"
        row_views.append(
            OnboardingRow(
                event_speaker_id=str(row["event_speaker_id"]),
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
            submitted=count(secondary, "submitted"),
            accepted=count(secondary, "accepted"),
            rejected=count(secondary, "rejected"),
            evaluations_finalized=count(secondary, "evaluations_finalized"),
            evaluations_total=count(secondary, "evaluations_total"),
        ),
        data=row_views,
        next_cursor=next_value,
    )


async def _speaker_row(request: Request):
    authenticated = await authenticate_request(request)
    row = row_mapping(
        await _timed_first(
            request,
            _db(request)
            .prepare(
                """SELECT es.id AS event_speaker_id, es.organization_id, es.event_id,
                          p.id AS person_id, p.user_id, p.display_name, p.job_title,
                          p.company, p.biography, p.location, p.links_json, p.version,
                          e.name AS event_name, e.starts_at_ms, e.ends_at_ms, e.time_zone
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
                     AND es.status IN ('onboarding', 'complete')
                   ORDER BY es.last_activity_at_ms DESC, es.id DESC LIMIT 1"""
            )
            .bind(authenticated.actor.user_id),
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
async def get_speaker_portal(request: Request) -> SpeakerPortalView:
    _, row = await _speaker_row(request)
    db = _db(request)
    tasks = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT id, task_type, title, COALESCE(help_text, '') AS help_text,
                          destination_type, state, due_at_ms, completed_at_ms
                   FROM speaker_tasks
                   WHERE organization_id = ?1 AND event_id = ?2 AND event_speaker_id = ?3
                   ORDER BY CASE state WHEN 'open' THEN 0 ELSE 1 END,
                            due_at_ms IS NULL, due_at_ms, id LIMIT 100"""
            ).bind(row["organization_id"], row["event_id"], row["event_speaker_id"]),
        )
    )
    submissions = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT s.id, s.proposal_title,
                          'accepted' AS status
                   FROM submission_speakers ss
                   JOIN submissions s
                     ON s.organization_id = ss.organization_id
                    AND s.event_id = ss.event_id AND s.id = ss.submission_id
                   WHERE ss.organization_id = ?1 AND ss.event_id = ?2
                     AND ss.event_speaker_id = ?3
                   ORDER BY s.submitted_at_ms DESC, s.id DESC LIMIT 25"""
            ).bind(row["organization_id"], row["event_id"], row["event_speaker_id"]),
        )
    )
    task_views = [
        SpeakerTaskView(
            id=str(task["id"]),
            task_type=str(task["task_type"]),
            title=str(task["title"]),
            help_text=str(task["help_text"]),
            destination_path=f"#{task['destination_type']}",
            state=str(task["state"]),
            due_at_ms=int(task["due_at_ms"]) if task["due_at_ms"] is not None else None,
            completed_at_ms=(
                int(task["completed_at_ms"]) if task["completed_at_ms"] is not None else None
            ),
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
        ),
        profile=_profile(row),
        tasks=task_views,
        submissions=[
            SpeakerSubmissionView(
                id=str(submission["id"]),
                proposal_title=str(submission["proposal_title"]),
                status=str(submission["status"]),
            )
            for submission in submissions
        ],
        completed_tasks=sum(task.state == "completed" for task in task_views),
        total_tasks=len(task_views),
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


@speaker_operations_router.patch(
    "/api/v1/speaker/profile",
    response_model=SpeakerProfileView,
    operation_id="updateOwnSpeakerProfile",
    tags=["speaker-portal"],
)
async def update_speaker_profile(
    request: Request,
    body: SpeakerProfileUpdate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SpeakerProfileView:
    authenticated, row = await _speaker_row(request)
    await require_permission(
        request,
        Permission.SPEAKER_PROFILE_EDIT_OWN,
        ResourceContext(
            str(row["organization_id"]),
            str(row["event_id"]),
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    db = _db(request)
    key = _key(idempotency_key)
    route = "PATCH /api/v1/speaker/profile"
    fingerprint = _fingerprint(body)
    replay = row_mapping(
        await _timed_first(
            request,
            db.prepare(
                """SELECT request_fingerprint FROM idempotency_records
                   WHERE principal_key = ?1 AND route_key = ?2
                     AND idempotency_key_hash = ?3 AND state = 'completed'"""
            ).bind(authenticated.actor.user_id, route, hashlib.sha256(key.encode()).digest()),
        )
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        _, latest = await _speaker_row(request)
        return _profile(latest)
    if int(row["version"]) != body.version:
        raise HTTPException(status_code=409)

    now = utc_now_ms()
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """UPDATE people SET display_name = ?1, job_title = ?2, company = ?3,
                      biography = ?4, location = ?5, links_json = ?6,
                      version = version + 1, updated_at_ms = ?7
               WHERE id = ?8 AND organization_id = ?9 AND user_id = ?10 AND version = ?11"""
        ).bind(
            body.display_name,
            body.job_title or None,
            body.company or None,
            body.biography,
            body.location or None,
            json.dumps(body.links, separators=(",", ":")),
            now,
            row["person_id"],
            row["organization_id"],
            authenticated.actor.user_id,
            body.version,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE speaker_tasks SET state = 'completed', completed_at_ms = ?1,
                      version = version + 1, updated_at_ms = ?1
               WHERE organization_id = ?2 AND event_id = ?3 AND event_speaker_id = ?4
                 AND task_type = 'profile' AND state = 'open'"""
        ).bind(now, row["organization_id"], row["event_id"], row["event_speaker_id"])
    )
    batch.audit(
        AuditEvent(
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="speaker.profile.update",
            target_type="person",
            target_id=str(row["person_id"]),
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"fields_changed": 6, "task_reconciled": 1},
        )
    )
    batch.outbox(
        OutboxMessage(
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            topic="speaker.onboarding.changed",
            aggregate_type="event_speaker",
            aggregate_id=str(row["event_speaker_id"]),
            deduplication_key=f"profile:{row['person_id']}:{body.version + 1}",
            payload={"event_speaker_id": str(row["event_speaker_id"]), "version": body.version + 1},
            available_at_ms=now,
            created_at_ms=now,
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="person",
        resource_id=str(row["person_id"]),
        completed_at_ms=now,
    )
    started = perf_counter()
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)
    _, updated = await _speaker_row(request)
    return _profile(updated)


ASSET_RULES = {
    "headshot": ({"image/jpeg", "image/png", "image/webp"}, 5 * 1024 * 1024),
    "slides": (
        {
            "application/pdf",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.oasis.opendocument.presentation",
        },
        50 * 1024 * 1024,
    ),
    "supporting_document": ({"application/pdf"}, 20 * 1024 * 1024),
}


def _bucket(request: Request):
    bucket = getattr(request.scope.get("env"), "ASSETS", None)
    if bucket is None:
        raise HTTPException(status_code=503)
    return bucket


async def _enqueue_asset_scan(request: Request, row) -> None:
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
    except Exception as exc:
        raise HTTPException(status_code=503) from exc


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
                """SELECT ui.id, ui.expected_content_type, ui.expires_at_ms,
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
                """SELECT a.id, a.kind, av.original_filename, av.content_type,
                          av.byte_size, av.generation
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
    return SpeakerAssetList(
        data=[
            SpeakerAssetView(
                id=str(row["id"]),
                kind=str(row["kind"]),
                filename=str(row["original_filename"]),
                content_type=str(row["content_type"]),
                byte_size=int(row["byte_size"]),
                state="clean",
                generation=int(row["generation"]),
            )
            for row in rows
        ]
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


def _attachment_header(filename: str) -> str:
    safe_ascii = "".join(
        character for character in filename if character.isalnum() or character in ".-_ "
    )
    safe_ascii = safe_ascii.strip()[:120] or "download"
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{safe_ascii}\"; filename*=UTF-8''{encoded}"


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
            event_admin=True,
        ),
        asset_id,
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
            "Content-Disposition": _attachment_header(download.filename),
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
    allowed_types, max_bytes = ASSET_RULES[body.kind]
    if body.content_type not in allowed_types or body.byte_size > max_bytes:
        raise HTTPException(status_code=400)
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
                """SELECT task_type FROM speaker_tasks WHERE organization_id = ?1
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

    slot = row_mapping(
        await db.prepare(
            """SELECT id FROM speaker_assets WHERE organization_id = ?1 AND event_id = ?2
           AND event_speaker_id = ?3 AND COALESCE(submission_id, '') = COALESCE(?4, '')
           AND COALESCE(task_id, '') = COALESCE(?5, '') AND kind = ?6 LIMIT 1"""
        )
        .bind(
            speaker["organization_id"],
            event_id,
            speaker["event_speaker_id"],
            body.submission_id,
            body.task_id,
            body.kind,
        )
        .first()
    )
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
    now = utc_now_ms()
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
    batch.add_statement(
        db.prepare(
            """INSERT INTO speaker_asset_versions
           (id, organization_id, event_id, event_speaker_id, asset_id, generation,
            object_key, original_filename, scan_state, created_at_ms)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'pending_upload', ?9)"""
        ).bind(
            version_id,
            speaker["organization_id"],
            event_id,
            speaker["event_speaker_id"],
            asset_id,
            generation,
            f"private/{version_id}/{new_id()}",
            body.filename,
            now,
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


async def _scan_local_asset(request: Request, job_id: str, content: bytes):
    from workers import fetch

    environment = request.scope.get("env")
    scanner_url = str(getattr(environment, "SCANNER_URL", ""))
    scanner_secret = secret(request, "SCANNER_HMAC_KEY")
    if not scanner_url.startswith("http://scanner:"):
        raise HTTPException(status_code=503)
    headers = scan_request_headers(
        scanner_secret,
        job_id=job_id,
        timestamp_ms=utc_now_ms(),
        content=content,
    )
    try:
        response = await fetch(
            scanner_url.rstrip("/") + "/scan",
            method="POST",
            headers=headers,
            body=content,
        )
        response_body = await response.bytes()
        if response.status != 200:
            raise HTTPException(status_code=503)
        result = parse_signed_scan_response(
            scanner_secret, response_body, response.headers.get("x-scan-signature")
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503) from exc
    if result.job_id != job_id or result.verdict == "error":
        raise HTTPException(status_code=503)
    return result


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
                  av.scan_state, a.kind
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
    if str(row["scan_state"]) in {"uploaded", "scanning", "clean", "rejected"}:
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
        try:
            content = bytes(await stored_body.arrayBuffer())
        except Exception as exc:
            raise HTTPException(status_code=503) from exc
        scan_result = await _scan_local_asset(request, str(row["version_id"]), content)
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
                """UPDATE speaker_asset_versions SET is_current = 0, scan_state = 'superseded',
                      scanned_at_ms = ?1
               WHERE asset_id = ?2 AND is_current = 1 AND scan_state = 'clean'"""
            ).bind(now, row["asset_id"])
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
                """UPDATE speaker_tasks SET state = 'completed', completed_at_ms = ?1,
                      version = version + 1, updated_at_ms = ?1
               WHERE organization_id = ?2 AND event_id = ?3 AND event_speaker_id = ?4
                 AND task_type = ?5 AND state = 'open'"""
            ).bind(
                now,
                speaker["organization_id"],
                event_id,
                speaker["event_speaker_id"],
                row["kind"],
            )
        )
        state = "clean"
    elif local and scan_result is not None:
        batch.add_statement(
            db.prepare(
                """UPDATE speaker_asset_versions SET content_type = ?1, byte_size = ?2,
                      checksum_sha256 = ?3, scan_state = 'rejected', is_current = 0,
                      scanned_at_ms = ?4,
                      scan_result_code = ?5
               WHERE id = ?6 AND scan_state = 'scanning'"""
            ).bind(
                row["expected_content_type"],
                row["expected_byte_size"],
                row["expected_checksum_sha256"],
                now,
                scan_result.signature or "malware_detected",
                row["version_id"],
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
    batch.outbox(
        OutboxMessage(
            organization_id=str(speaker["organization_id"]),
            event_id=event_id,
            topic=(
                "speaker.onboarding.changed"
                if inline_completion
                else "speaker.asset.scan_requested"
            ),
            aggregate_type="speaker_asset_version",
            aggregate_id=str(row["version_id"]),
            deduplication_key=f"asset:{row['version_id']}:uploaded",
            payload={
                "asset_version_id": str(row["version_id"]),
                "generation": int(row["generation"]),
            },
            available_at_ms=now,
            created_at_ms=now,
        )
    )
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    if state == "uploaded":
        await _enqueue_asset_scan(request, row)
    return UploadCompletionView(intent_id=intent_id, state=state)

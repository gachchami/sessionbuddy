import hashlib
import json
from time import perf_counter

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from sessionbuddy.console import embedded_assets
from sessionbuddy.observability import record_timing
from sessionbuddy.platform.auth import (
    generate_token,
    hash_token,
    issue_csrf_token,
    validate_session,
)
from sessionbuddy.platform.auth.cookies import sign_session_cookie, verify_session_cookie
from sessionbuddy.platform.auth.d1 import D1SessionStore
from sessionbuddy.platform.auth.http import require_permission, secret
from sessionbuddy.platform.authorization import Permission, ResourceContext
from sessionbuddy.platform.db.commands import (
    AuditEvent,
    CommandBatch,
    IdempotencyRecord,
    OutboxMessage,
)
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit

from .models import (
    DemoContext,
    DemoSession,
    FormPublish,
    ProgramCreate,
    ProgramView,
    PublishedFormView,
    SubmissionCreate,
    SubmissionList,
    SubmissionView,
)

cfp_router = APIRouter()
FIELDS = ("speaker_name", "proposal_title", "proposal_abstract")
DEMO_ORG_ID = "11111111-1111-4111-8111-111111111111"
DEMO_EVENT_ID = "22222222-2222-4222-8222-222222222222"
DEMO_USER_ID = "33333333-3333-4333-8333-333333333333"
DEMO_ORG_MEMBERSHIP_ID = "44444444-4444-4444-8444-444444444444"
DEMO_EVALUATOR_MEMBERSHIP_ID = "55555555-5555-4555-8555-555555555555"
DEMO_REVIEWER_USER_ID = "66666666-6666-4666-8666-666666666666"
DEMO_REVIEWER_ORG_MEMBERSHIP_ID = "77777777-7777-4777-8777-777777777777"
DEMO_REVIEWER_EVENT_MEMBERSHIP_ID = "88888888-8888-4888-8888-888888888888"


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


def _env(request: Request):
    return request.scope.get("env")


def _db(request: Request):
    db = getattr(_env(request), "DB", None)
    if db is None:
        raise HTTPException(status_code=503)
    return db


def _idempotency_key(value: str | None) -> str:
    if value is None or not 16 <= len(value) <= 255:
        raise HTTPException(status_code=400)
    return value


def _fingerprint(model) -> bytes:
    canonical = json.dumps(model.model_dump(), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).digest()


def _local_page(request: Request, asset: str) -> HTMLResponse:
    if getattr(_env(request), "APP_ENV", "local") != "local":
        raise HTTPException(status_code=404)
    return HTMLResponse(_asset(asset), headers={"Cache-Control": "no-store"})


@cfp_router.get("/admin/programs", response_class=HTMLResponse, include_in_schema=False)
async def admin_programs_page(request: Request) -> HTMLResponse:
    return _local_page(request, "admin_programs.html")


@cfp_router.get(
    "/admin/programs/{program_id}/submissions",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_submissions_page(program_id: str, request: Request) -> HTMLResponse:
    return _local_page(request, "admin_submissions.html")


@cfp_router.get("/cfp/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def public_cfp_page(slug: str) -> HTMLResponse:
    return HTMLResponse(_asset("public_cfp.html"), headers={"Cache-Control": "no-store"})


@cfp_router.get("/product/assets/product.css", response_class=Response, include_in_schema=False)
async def product_css() -> Response:
    return Response(_asset("product.css"), media_type="text/css")


@cfp_router.get(
    "/product/assets/admin-programs.js", response_class=Response, include_in_schema=False
)
async def admin_programs_js() -> Response:
    return Response(_asset("admin_programs.js"), media_type="text/javascript")


@cfp_router.get(
    "/product/assets/public-cfp.js", response_class=Response, include_in_schema=False
)
async def public_cfp_js() -> Response:
    return Response(_asset("public_cfp.js"), media_type="text/javascript")


@cfp_router.get(
    "/product/assets/admin-submissions.js",
    response_class=Response,
    include_in_schema=False,
)
async def admin_submissions_js() -> Response:
    return Response(_asset("admin_submissions.js"), media_type="text/javascript")


@cfp_router.get("/cfp-integration", response_class=HTMLResponse, include_in_schema=False)
async def cfp_integration(request: Request) -> HTMLResponse:
    if getattr(_env(request), "APP_ENV", "local") != "local":
        raise HTTPException(status_code=404)
    return HTMLResponse(_asset("cfp_integration.html"), headers={"Cache-Control": "no-store"})


@cfp_router.get(
    "/cfp-integration/assets/cfp_integration.css",
    response_class=Response,
    include_in_schema=False,
)
async def cfp_integration_css() -> Response:
    return Response(
        _asset("cfp_integration.css"), media_type="text/css", headers={"Cache-Control": "no-store"}
    )


@cfp_router.get(
    "/cfp-integration/assets/cfp_integration.js",
    response_class=Response,
    include_in_schema=False,
)
async def cfp_integration_js() -> Response:
    return Response(
        _asset("cfp_integration.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@cfp_router.post(
    "/api/v1/demo/context",
    response_model=DemoContext,
    operation_id="initializeLocalDemoContext",
    tags=["demo"],
)
async def initialize_demo_context(request: Request) -> DemoContext:
    if getattr(_env(request), "APP_ENV", "production") != "local":
        raise HTTPException(status_code=404)
    db = _db(request)
    now = utc_now_ms()
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
                   VALUES (?1, ?2, 'Demo Conference', ?3, ?4, 'UTC', 'hybrid',
                           'active', ?3, ?3)"""
            ).bind(DEMO_EVENT_ID, DEMO_ORG_ID, now, now + 86_400_000),
        ]
    )
    return DemoContext(organization_id=DEMO_ORG_ID, event_id=DEMO_EVENT_ID)


@cfp_router.post(
    "/api/v1/demo/session",
    response_model=DemoSession,
    operation_id="createLocalDemoSession",
    tags=["demo"],
)
async def create_demo_session(request: Request, response: Response) -> DemoSession:
    if getattr(_env(request), "APP_ENV", "production") != "local":
        raise HTTPException(status_code=404)
    await enforce_rate_limit(
        request,
        binding_name="AUTH_RATE_LIMITER",
        policy=RateLimitPolicy("auth.request", limit=10, window_seconds=60),
        subject=_request_source(request),
    )
    db = _db(request)
    now = utc_now_ms()
    signing_secret = secret(request, "SESSION_HMAC_KEY")
    existing_token = verify_session_cookie(
        request.cookies.get("sessionbuddy-local", ""), signing_secret
    )
    if existing_token is not None:
        existing = await D1SessionStore(db).resolve(hash_token(existing_token), now)
        if (
            existing is not None
            and existing.user_id == DEMO_USER_ID
            and validate_session(existing, now).active
        ):
            await db.batch([
                db.prepare(
                    """INSERT OR IGNORE INTO users
                       (id, email, normalized_email, status, email_verified_at_ms,
                        created_at_ms, updated_at_ms)
                       VALUES (?1, 'reviewer@local.invalid', 'reviewer@local.invalid',
                               'active', ?2, ?2, ?2)"""
                ).bind(DEMO_REVIEWER_USER_ID, now),
                db.prepare(
                    """INSERT OR IGNORE INTO organization_memberships
                       (id, organization_id, user_id, role, status, created_at_ms, updated_at_ms)
                       VALUES (?1, ?2, ?3, 'member', 'active', ?4, ?4)"""
                ).bind(DEMO_REVIEWER_ORG_MEMBERSHIP_ID, DEMO_ORG_ID,
                       DEMO_REVIEWER_USER_ID, now),
                db.prepare(
                    """INSERT OR IGNORE INTO event_memberships
                       (id, organization_id, event_id, user_id, role, status,
                        created_at_ms, updated_at_ms)
                       VALUES (?1, ?2, ?3, ?4, 'evaluator', 'active', ?5, ?5)"""
                ).bind(DEMO_EVALUATOR_MEMBERSHIP_ID, DEMO_ORG_ID, DEMO_EVENT_ID,
                       DEMO_USER_ID, now),
                db.prepare(
                    """INSERT OR IGNORE INTO event_memberships
                       (id, organization_id, event_id, user_id, role, status,
                        created_at_ms, updated_at_ms)
                       VALUES (?1, ?2, ?3, ?4, 'evaluator', 'active', ?5, ?5)"""
                ).bind(DEMO_REVIEWER_EVENT_MEMBERSHIP_ID, DEMO_ORG_ID, DEMO_EVENT_ID,
                       DEMO_REVIEWER_USER_ID, now),
            ])
            return DemoSession(
                organization_id=DEMO_ORG_ID,
                event_id=DEMO_EVENT_ID,
                user_id=DEMO_USER_ID,
                csrf_token=issue_csrf_token(
                    existing.id, secret(request, "CSRF_HMAC_KEY")
                ),
            )
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
                   VALUES (?1, ?2, 'Demo Conference', ?3, ?4, 'UTC', 'hybrid',
                           'active', ?3, ?3)"""
            ).bind(DEMO_EVENT_ID, DEMO_ORG_ID, now, now + 86_400_000),
            db.prepare(
                """INSERT OR IGNORE INTO users
                   (id, email, normalized_email, status, email_verified_at_ms,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, 'admin@local.invalid', 'admin@local.invalid',
                           'active', ?2, ?2, ?2)"""
            ).bind(DEMO_USER_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO organization_memberships
                   (id, organization_id, user_id, role, status, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, 'organization_admin', 'active', ?4, ?4)"""
            ).bind(DEMO_ORG_MEMBERSHIP_ID, DEMO_ORG_ID, DEMO_USER_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO users
                   (id, email, normalized_email, status, email_verified_at_ms,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, 'reviewer@local.invalid', 'reviewer@local.invalid',
                           'active', ?2, ?2, ?2)"""
            ).bind(DEMO_REVIEWER_USER_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO organization_memberships
                   (id, organization_id, user_id, role, status, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, 'member', 'active', ?4, ?4)"""
            ).bind(DEMO_REVIEWER_ORG_MEMBERSHIP_ID, DEMO_ORG_ID,
                   DEMO_REVIEWER_USER_ID, now),
            db.prepare(
                """INSERT OR IGNORE INTO event_memberships
                   (id, organization_id, event_id, user_id, role, status,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 'evaluator', 'active', ?5, ?5)"""
            ).bind(
                DEMO_EVALUATOR_MEMBERSHIP_ID,
                DEMO_ORG_ID,
                DEMO_EVENT_ID,
                DEMO_USER_ID,
                now,
            ),
            db.prepare(
                """INSERT OR IGNORE INTO event_memberships
                   (id, organization_id, event_id, user_id, role, status,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 'evaluator', 'active', ?5, ?5)"""
            ).bind(
                DEMO_REVIEWER_EVENT_MEMBERSHIP_ID,
                DEMO_ORG_ID,
                DEMO_EVENT_ID,
                DEMO_REVIEWER_USER_ID,
                now,
            ),
            db.prepare(
                """INSERT INTO sessions
                   (id, user_id, token_hash, csrf_secret_hash, authorization_version,
                    created_at_ms, last_seen_at_ms, idle_expires_at_ms, absolute_expires_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 1, ?5, ?5, ?6, ?7)"""
            ).bind(
                session_id,
                DEMO_USER_ID,
                hash_token(token),
                hash_token(csrf_token),
                now,
                now + 12 * 60 * 60 * 1000,
                now + 30 * 24 * 60 * 60 * 1000,
            ),
        ]
    )
    response.set_cookie(
        key="sessionbuddy-local",
        value=sign_session_cookie(token, signing_secret),
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=False,
        samesite="lax",
        path="/",
    )
    return DemoSession(
        organization_id=DEMO_ORG_ID,
        event_id=DEMO_EVENT_ID,
        user_id=DEMO_USER_ID,
        csrf_token=csrf_token,
    )


@cfp_router.post(
    "/api/v1/admin/programs",
    response_model=ProgramView,
    status_code=201,
    operation_id="createProgram",
    tags=["programs"],
)
async def create_program(
    request: Request,
    body: ProgramCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProgramView:
    auth = await require_permission(
        request,
        Permission.PROGRAM_MANAGE,
        ResourceContext(body.organization_id, body.event_id),
        mutation=True,
    )
    db = _db(request)
    event_exists = (
        await db.prepare(
            """SELECT 1 AS exists_value FROM events
               WHERE organization_id = ?1 AND id = ?2 LIMIT 1"""
        )
        .bind(body.organization_id, body.event_id)
        .first("exists_value")
    )
    if event_exists is None:
        raise HTTPException(status_code=404)
    key = _idempotency_key(idempotency_key)
    fingerprint = _fingerprint(body)
    replay = await _find_replay(
        db, auth.actor.user_id, "POST /api/v1/admin/programs", key
    )
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        row = (
            await db.prepare(
                "SELECT id, organization_id, event_id, name, status FROM programs WHERE id = ?1"
            )
            .bind(replay["response_resource_id"])
            .first()
        )
        return ProgramView.model_validate(row_mapping(row))

    now = utc_now_ms()
    program_id = new_id()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=body.organization_id,
        event_id=body.event_id,
        route_key="POST /api/v1/admin/programs",
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO programs
               (id, organization_id, event_id, name, status, created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, 'draft', ?5, ?5)"""
        ).bind(program_id, body.organization_id, body.event_id, body.name, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="program.create",
            target_type="program",
            target_id=program_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=body.organization_id,
            event_id=body.event_id,
            metadata={"changed_fields": ["name", "status"]},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="program",
        resource_id=program_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return ProgramView(id=program_id, **body.model_dump(), status="draft")


@cfp_router.post(
    "/api/v1/admin/programs/{program_id}/forms/publish",
    response_model=PublishedFormView,
    status_code=201,
    operation_id="publishCallForSpeakersForm",
    tags=["forms"],
)
async def publish_form(
    program_id: str,
    request: Request,
    body: FormPublish,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PublishedFormView:
    key = _idempotency_key(idempotency_key)
    db = _db(request)
    program = row_mapping(
        await db.prepare("SELECT organization_id, event_id FROM programs WHERE id = ?1")
        .bind(program_id)
        .first()
    )
    if program is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.FORM_MANAGE,
        ResourceContext(str(program["organization_id"]), str(program["event_id"])),
        mutation=True,
    )
    route_key = "POST /api/v1/admin/programs/{program_id}/forms/publish"
    fingerprint = _fingerprint(body)
    replay = await _find_replay(db, auth.actor.user_id, route_key, key)
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _form_by_id(db, str(replay["response_resource_id"]))
    now = utc_now_ms()
    form_id = new_id()
    version = (
        await db.prepare(
            """SELECT COALESCE(MAX(version), 0) + 1 AS version
           FROM call_for_speaker_forms WHERE program_id = ?1"""
        )
        .bind(program_id)
        .first("version")
    )
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(program["organization_id"]),
        event_id=str(program["event_id"]),
        route_key=route_key,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO call_for_speaker_forms
               (id, organization_id, event_id, program_id, version, slug, welcome_text,
                schema_json, status, published_at_ms, created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 'published', ?9, ?9, ?9)"""
        ).bind(
            form_id,
            program["organization_id"],
            program["event_id"],
            program_id,
            version,
            body.slug,
            body.welcome_text,
            json.dumps({"fields": FIELDS}, separators=(",", ":")),
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE programs SET status = 'open', updated_at_ms = ?1
               WHERE id = ?2 AND organization_id = ?3 AND event_id = ?4"""
        ).bind(now, program_id, program["organization_id"], program["event_id"])
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="form.publish",
            target_type="call_for_speaker_form",
            target_id=form_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(program["organization_id"]),
            event_id=str(program["event_id"]),
            metadata={"version": int(version)},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="call_for_speaker_form",
        resource_id=form_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return PublishedFormView(
        id=form_id,
        program_id=program_id,
        version=int(version),
        slug=body.slug,
        welcome_text=body.welcome_text,
        fields=FIELDS,
    )


@cfp_router.get(
    "/api/v1/forms/{slug}",
    response_model=PublishedFormView,
    operation_id="getPublishedCallForSpeakersForm",
    tags=["forms"],
)
async def get_form(slug: str, request: Request) -> PublishedFormView:
    row = row_mapping(
        await _db(request)
        .prepare(
            """SELECT id, program_id, version, slug, welcome_text FROM call_for_speaker_forms
               WHERE slug = ?1 AND status = 'published'"""
        )
        .bind(slug)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return PublishedFormView.model_validate({**row, "fields": FIELDS})


@cfp_router.post(
    "/api/v1/forms/{slug}/submissions",
    response_model=SubmissionView,
    status_code=201,
    operation_id="createCallForSpeakersSubmission",
    tags=["submissions"],
)
async def create_submission(
    slug: str,
    request: Request,
    body: SubmissionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    public_session: str | None = Header(default=None, alias="X-Public-Session-ID"),
) -> SubmissionView:
    key = _idempotency_key(idempotency_key)
    if public_session is None or not 16 <= len(public_session) <= 128:
        raise HTTPException(status_code=400)
    await enforce_rate_limit(
        request,
        binding_name="PUBLIC_RATE_LIMITER",
        policy=RateLimitPolicy("public.submit", limit=50, window_seconds=60),
        subject=f"{public_session}:{_request_source(request)}",
    )
    db = _db(request)
    form = row_mapping(
        await db.prepare(
            """SELECT id, organization_id, event_id, program_id FROM call_for_speaker_forms
               WHERE slug = ?1 AND status = 'published'"""
        )
        .bind(slug)
        .first()
    )
    if form is None:
        raise HTTPException(status_code=404)
    fingerprint = _fingerprint(body)
    route_key = "POST /api/v1/forms/{slug}/submissions"
    replay = await _find_replay(db, public_session, route_key, key)
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _submission_by_id(db, str(replay["response_resource_id"]))
    now = utc_now_ms()
    submission_id = new_id()
    record = IdempotencyRecord(
        principal_key=public_session,
        organization_id=str(form["organization_id"]),
        event_id=str(form["event_id"]),
        route_key=route_key,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO submissions
               (id, organization_id, event_id, program_id, form_id, public_session_id,
                proposal_title, proposal_abstract, speaker_name, status, submitted_at_ms,
                created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, 'submitted', ?10, ?10, ?10)"""
        ).bind(
            submission_id,
            form["organization_id"],
            form["event_id"],
            form["program_id"],
            form["id"],
            public_session,
            body.proposal_title,
            body.proposal_abstract,
            body.speaker_name,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="anonymous",
            action="submission.create",
            target_type="submission",
            target_id=submission_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(form["organization_id"]),
            event_id=str(form["event_id"]),
            metadata={"form_version": 1},
        )
    )
    batch.outbox(
        OutboxMessage(
            topic="submission.confirmation.requested",
            aggregate_type="submission",
            aggregate_id=submission_id,
            deduplication_key=f"submission:{submission_id}:confirmation:v1",
            payload={"submission_id": submission_id, "form_id": str(form["id"])},
            available_at_ms=now,
            created_at_ms=now,
            organization_id=str(form["organization_id"]),
            event_id=str(form["event_id"]),
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="submission",
        resource_id=submission_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return SubmissionView(
        id=submission_id,
        program_id=str(form["program_id"]),
        status="submitted",
        submitted_at_ms=now,
        **body.model_dump(),
    )


@cfp_router.get(
    "/api/v1/admin/programs/{program_id}/submissions",
    response_model=SubmissionList,
    operation_id="listProgramSubmissions",
    tags=["submissions"],
)
async def list_submissions(
    program_id: str,
    request: Request,
) -> SubmissionList:
    program = row_mapping(
        await _db(request)
        .prepare("SELECT organization_id, event_id FROM programs WHERE id = ?1")
        .bind(program_id)
        .first()
    )
    if program is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(program["organization_id"]), str(program["event_id"])),
        mutation=False,
    )
    result = (
        await _db(request)
        .prepare(
            """SELECT id, program_id, speaker_name, proposal_title, proposal_abstract,
                  status, submitted_at_ms FROM submissions
           WHERE organization_id = ?1 AND event_id = ?2 AND program_id = ?3
           ORDER BY submitted_at_ms DESC, id DESC LIMIT 100"""
        )
        .bind(program["organization_id"], program["event_id"], program_id)
        .all()
    )
    return SubmissionList(data=[SubmissionView.model_validate(row) for row in result_rows(result)])


async def _execute(request: Request, batch: CommandBatch) -> None:
    started = perf_counter()
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)


async def _find_replay(db, principal: str, route: str, key: str):
    return row_mapping(
        await db.prepare(
            """SELECT request_fingerprint, response_resource_id FROM idempotency_records
               WHERE principal_key = ?1 AND route_key = ?2 AND idempotency_key_hash = ?3
                 AND state = 'completed'"""
        )
        .bind(principal, route, hashlib.sha256(key.encode()).digest())
        .first()
    )


def _blob(value: object) -> bytes:
    converted = to_python(value)
    return converted if isinstance(converted, bytes) else bytes(converted)


async def _form_by_id(db, form_id: str) -> PublishedFormView:
    row = row_mapping(
        await db.prepare(
            """SELECT id, program_id, version, slug, welcome_text
               FROM call_for_speaker_forms WHERE id = ?1"""
        )
        .bind(form_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return PublishedFormView.model_validate({**row, "fields": FIELDS})


async def _submission_by_id(db, submission_id: str) -> SubmissionView:
    row = row_mapping(
        await db.prepare(
            """SELECT id, program_id, speaker_name, proposal_title, proposal_abstract,
                      status, submitted_at_ms FROM submissions WHERE id = ?1"""
        )
        .bind(submission_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return SubmissionView.model_validate(row)


def _request_source(request: Request) -> str:
    connecting_ip = request.headers.get("cf-connecting-ip")
    if connecting_ip:
        return connecting_ip
    return request.client.host if request.client is not None else "unknown"

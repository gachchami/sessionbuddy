import hashlib
import json
from html import escape
from time import perf_counter

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from sessionbuddy.console import embedded_assets
from sessionbuddy.observability import record_timing
from sessionbuddy.platform.auth import (
    authenticate_request,
    generate_token,
    hash_token,
    issue_csrf_token,
    normalize_email,
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
    SubmissionDraftUpsert,
    SubmissionDraftView,
    SubmissionList,
    SubmissionView,
)

cfp_router = APIRouter()
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


def _product_page(request: Request, asset: str) -> HTMLResponse:
    return HTMLResponse(_asset(asset), headers={"Cache-Control": "no-store"})


@cfp_router.get("/admin/programs", response_class=HTMLResponse, include_in_schema=False)
async def admin_programs_page(request: Request) -> HTMLResponse:
    return _product_page(request, "admin_programs.html")


@cfp_router.get(
    "/admin/programs/{program_id}/submissions",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_submissions_page(program_id: str, request: Request) -> HTMLResponse:
    return _product_page(request, "admin_submissions.html")


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


@cfp_router.get("/product/assets/public-cfp.js", response_class=Response, include_in_schema=False)
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
            await db.batch(
                [
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
                    ).bind(
                        DEMO_REVIEWER_ORG_MEMBERSHIP_ID, DEMO_ORG_ID, DEMO_REVIEWER_USER_ID, now
                    ),
                    db.prepare(
                        """INSERT OR IGNORE INTO event_memberships
                       (id, organization_id, event_id, user_id, role, status,
                        created_at_ms, updated_at_ms)
                       VALUES (?1, ?2, ?3, ?4, 'evaluator', 'active', ?5, ?5)"""
                    ).bind(
                        DEMO_EVALUATOR_MEMBERSHIP_ID, DEMO_ORG_ID, DEMO_EVENT_ID, DEMO_USER_ID, now
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
                ]
            )
            return DemoSession(
                organization_id=DEMO_ORG_ID,
                event_id=DEMO_EVENT_ID,
                user_id=DEMO_USER_ID,
                csrf_token=issue_csrf_token(existing.id, secret(request, "CSRF_HMAC_KEY")),
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
            ).bind(DEMO_REVIEWER_ORG_MEMBERSHIP_ID, DEMO_ORG_ID, DEMO_REVIEWER_USER_ID, now),
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
    replay = await _find_replay(db, auth.actor.user_id, "POST /api/v1/admin/programs", key)
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
                schema_json, opens_at_ms, closes_at_ms, submission_limit, success_title,
                success_message, redirect_to_portal, confirmation_subject, confirmation_body,
                status, published_at_ms, created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13,
                       ?14, ?15, ?16, 'published', ?17, ?17, ?17)"""
        ).bind(
            form_id,
            program["organization_id"],
            program["event_id"],
            program_id,
            version,
            body.slug,
            body.welcome_text,
            json.dumps(
                {
                    "fields": [field.model_dump() for field in body.fields],
                    "conditions": [condition.model_dump() for condition in body.conditions],
                    "routing_rules": [rule.model_dump() for rule in body.routing_rules],
                },
                separators=(",", ":"),
            ),
            body.opens_at_ms,
            body.closes_at_ms,
            body.submission_limit,
            body.success_title,
            body.success_message,
            int(body.redirect_to_portal),
            body.confirmation_subject,
            body.confirmation_body,
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
    return await _form_by_id(db, form_id)


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
            """SELECT f.id, f.event_id, f.program_id, f.version, f.slug, f.welcome_text,
                      f.schema_json, f.opens_at_ms, f.closes_at_ms, f.submission_limit,
                      f.success_title, f.success_message, f.redirect_to_portal,
                      e.name AS event_name,e.accent_color,e.logo_url,
                      COUNT(s.id) AS submissions_received
               FROM call_for_speaker_forms f
               JOIN events e ON e.organization_id=f.organization_id AND e.id=f.event_id
               LEFT JOIN submissions s ON s.form_id=f.id AND s.status='submitted'
               WHERE f.slug = ?1 AND f.status = 'published' GROUP BY f.id"""
        )
        .bind(slug)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return _published_form_view(row, utc_now_ms())


async def _form_context(db, slug: str):
    return row_mapping(
        await db.prepare(
            """SELECT id,organization_id,event_id,program_id,schema_json,version,
                     opens_at_ms,closes_at_ms,submission_limit,confirmation_subject,
                     confirmation_body
           FROM call_for_speaker_forms WHERE slug=?1 AND status='published' LIMIT 1"""
        )
        .bind(slug)
        .first()
    )


@cfp_router.get(
    "/api/v1/forms/{slug}/draft",
    response_model=SubmissionDraftView | None,
    tags=["submissions"],
)
async def get_submission_draft(slug: str, request: Request) -> SubmissionDraftView | None:
    authenticated = await authenticate_request(request)
    db = _db(request)
    form = await _form_context(db, slug)
    if form is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SUBMISSION_READ_OWN,
        ResourceContext(
            str(form["organization_id"]),
            str(form["event_id"]),
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=False,
    )
    row = row_mapping(
        await db.prepare(
            """SELECT id,form_id,answers_json,version,updated_at_ms FROM submission_drafts
           WHERE form_id=?1 AND user_id=?2 LIMIT 1"""
        )
        .bind(form["id"], authenticated.actor.user_id)
        .first()
    )
    if row is None:
        return None
    answers = json.loads(str(row.pop("answers_json")))
    return SubmissionDraftView.model_validate({**row, "answers": answers})


@cfp_router.put(
    "/api/v1/forms/{slug}/draft",
    response_model=SubmissionDraftView,
    tags=["submissions"],
)
async def save_submission_draft(
    slug: str, body: SubmissionDraftUpsert, request: Request
) -> SubmissionDraftView:
    authenticated = await authenticate_request(request)
    db = _db(request)
    form = await _form_context(db, slug)
    if form is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SUBMISSION_READ_OWN,
        ResourceContext(
            str(form["organization_id"]),
            str(form["event_id"]),
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    schema = json.loads(str(form["schema_json"]))
    known = {str(field.get("key")) for field in schema.get("fields", []) if isinstance(field, dict)}
    if not set(body.answers) <= known:
        raise HTTPException(status_code=422)
    now, draft_id = utc_now_ms(), new_id()
    answers_json = json.dumps(body.answers, separators=(",", ":"), sort_keys=True)
    await (
        db.prepare(
            """INSERT INTO submission_drafts
           (id,organization_id,event_id,program_id,form_id,user_id,answers_json,version,
            created_at_ms,updated_at_ms)
           VALUES(?1,?2,?3,?4,?5,?6,?7,1,?8,?8)
           ON CONFLICT(form_id,user_id) DO UPDATE SET answers_json=excluded.answers_json,
             version=submission_drafts.version+1,updated_at_ms=excluded.updated_at_ms
           WHERE submission_drafts.version=?9"""
        )
        .bind(
            draft_id,
            form["organization_id"],
            form["event_id"],
            form["program_id"],
            form["id"],
            authenticated.actor.user_id,
            answers_json,
            now,
            body.version,
        )
        .run()
    )
    row = row_mapping(
        await db.prepare(
            """SELECT id,form_id,answers_json,version,updated_at_ms FROM submission_drafts
           WHERE form_id=?1 AND user_id=?2 LIMIT 1"""
        )
        .bind(form["id"], authenticated.actor.user_id)
        .first()
    )
    if row is None or int(row["version"]) != body.version + 1:
        raise HTTPException(status_code=409)
    answers = json.loads(str(row.pop("answers_json")))
    return SubmissionDraftView.model_validate({**row, "answers": answers})


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
            """SELECT id, organization_id, event_id, program_id, version, schema_json,
                      opens_at_ms, closes_at_ms, submission_limit, confirmation_subject,
                      confirmation_body
               FROM call_for_speaker_forms
               WHERE slug = ?1 AND status = 'published'"""
        )
        .bind(slug)
        .first()
    )
    if form is None:
        raise HTTPException(status_code=404)
    now = utc_now_ms()
    submissions_received = int(
        await db.prepare(
            """SELECT COUNT(*) AS count_value FROM submissions
               WHERE form_id=?1 AND status='submitted'"""
        )
        .bind(form["id"])
        .first("count_value")
        or 0
    )
    accepting, _ = _form_availability(form, submissions_received, now)
    if not accepting:
        raise HTTPException(status_code=409)
    schema = json.loads(str(form["schema_json"]))
    _validate_submission_schema(schema, body)
    deployed = getattr(_env(request), "APP_ENV", "production") != "local"
    authenticated = None
    if (
        deployed
        or request.cookies.get("__Host-session")
        or request.cookies.get("sessionbuddy-local")
    ):
        authenticated = await authenticate_request(request)
    submitter_user_id = authenticated.actor.user_id if authenticated is not None else None
    if authenticated is not None:
        user_email = (
            await db.prepare("SELECT normalized_email FROM users WHERE id=?1")
            .bind(submitter_user_id)
            .first("normalized_email")
        )
        if user_email is None or normalize_email(body.speaker_email) != str(user_email):
            raise HTTPException(status_code=403)
        await _validate_upload_answers(
            db,
            schema,
            body.answers,
            event_id=str(form["event_id"]),
            user_id=authenticated.actor.user_id,
        )
    principal_key = submitter_user_id or public_session
    fingerprint = _fingerprint(body)
    route_key = "POST /api/v1/forms/{slug}/submissions"
    replay = await _find_replay(db, principal_key, route_key, key)
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _submission_by_id(db, str(replay["response_resource_id"]))
    submission_id = new_id()
    routing = _route_submission(schema, body.answers)
    message_id = new_id()
    record = IdempotencyRecord(
        principal_key=principal_key,
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
                proposal_title, proposal_abstract, speaker_name, speaker_email, answers_json,
                submitter_user_id, status, submitted_at_ms, created_at_ms, updated_at_ms,
                routed_category, routed_track, routed_review_queue)
               SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11,
                      ?12, 'submitted', ?13, ?13, ?13, ?14, ?15, ?16
               WHERE (SELECT COUNT(*) FROM submissions
                      WHERE form_id=?5 AND status='submitted')
                     < COALESCE((SELECT submission_limit FROM call_for_speaker_forms
                                 WHERE id=?5), 1000001)"""
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
            body.speaker_email,
            json.dumps(body.answers, separators=(",", ":"), sort_keys=True),
            submitter_user_id,
            now,
            routing["category"],
            routing["track"],
            routing["review_queue"],
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO submission_write_guards
               (id,submission_id,applied_changes,created_at_ms)
               VALUES(?1,?2,changes(),?3)"""
        ).bind(new_id(), submission_id, now)
    )
    if submitter_user_id is not None:
        batch.add_statement(
            db.prepare("DELETE FROM submission_drafts WHERE form_id=?1 AND user_id=?2").bind(
                form["id"], submitter_user_id
            )
        )
        person = row_mapping(
            await db.prepare(
                "SELECT id FROM people WHERE organization_id=?1 AND user_id=?2 LIMIT 1"
            )
            .bind(form["organization_id"], submitter_user_id)
            .first()
        )
        person_id = str(person["id"]) if person is not None else new_id()
        if person is None:
            batch.add_statement(
                db.prepare(
                    """INSERT INTO people
                   (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?5)"""
                ).bind(
                    person_id, form["organization_id"], submitter_user_id, body.speaker_name, now
                )
            )
        speaker = row_mapping(
            await db.prepare(
                """SELECT id FROM event_speakers
               WHERE organization_id=?1 AND event_id=?2 AND person_id=?3 LIMIT 1"""
            )
            .bind(form["organization_id"], form["event_id"], person_id)
            .first()
        )
        event_speaker_id = str(speaker["id"]) if speaker is not None else new_id()
        if speaker is None:
            batch.add_statement(
                db.prepare(
                    """INSERT INTO event_speakers
                   (id,organization_id,event_id,person_id,status,accepted_at_ms,last_activity_at_ms,
                    created_at_ms,updated_at_ms,selection_status)
                   VALUES(?1,?2,?3,?4,'onboarding',?5,?5,?5,?5,'submitted')"""
                ).bind(event_speaker_id, form["organization_id"], form["event_id"], person_id, now)
            )
        batch.add_statement(
            db.prepare(
                """UPDATE event_speakers SET selection_status=
                         CASE WHEN selection_status='accepted' THEN 'accepted' ELSE 'submitted' END,
                         last_activity_at_ms=?1,updated_at_ms=?1
                   WHERE organization_id=?2 AND event_id=?3 AND id=?4"""
            ).bind(now, form["organization_id"], form["event_id"], event_speaker_id)
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO submission_speakers
               (id,organization_id,event_id,submission_id,event_speaker_id,role,snapshot_name,
                created_at_ms) VALUES(?1,?2,?3,?4,?5,'primary',?6,?7)"""
            ).bind(
                new_id(),
                form["organization_id"],
                form["event_id"],
                submission_id,
                event_speaker_id,
                body.speaker_name,
                now,
            )
        )
        for answer in _upload_references(schema, body.answers):
            batch.add_statement(
                db.prepare(
                    """UPDATE speaker_assets SET submission_id=?1,updated_at_ms=?2
                       WHERE id=(SELECT av.asset_id FROM upload_intents ui
                         JOIN speaker_asset_versions av ON av.id=ui.asset_version_id
                         WHERE ui.id=?3 LIMIT 1)"""
                ).bind(submission_id, now, answer)
            )
    batch.audit(
        AuditEvent(
            actor_type="user" if submitter_user_id is not None else "anonymous",
            actor_user_id=submitter_user_id,
            action="submission.create",
            target_type="submission",
            target_id=submission_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(form["organization_id"]),
            event_id=str(form["event_id"]),
            metadata={"form_version": int(form["version"]), "routing": routing},
        )
    )
    confirmation_html = (
        f"<p>{escape(str(form['confirmation_body']))}</p>"
        f"<p><strong>{escape(body.proposal_title)}</strong></p>"
        f"<p>Receipt: {escape(submission_id)}</p>"
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_user_id,recipient_email,subject,
                html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9)"""
        ).bind(
            message_id,
            form["organization_id"],
            form["event_id"],
            submitter_user_id,
            body.speaker_email,
            form["confirmation_subject"],
            confirmation_html,
            f"submission-confirmation:{submission_id}:v1",
            now,
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
    queue = getattr(_env(request), "COMMUNICATION_QUEUE", None)
    if queue is not None:
        try:
            await queue.send({"schema_version": 1, "message_id": message_id})
        except Exception:
            # The durable queued row remains visible to operators for replay.
            record_timing(request, "domain", 0)
    return SubmissionView(
        id=submission_id,
        program_id=str(form["program_id"]),
        status="submitted",
        submitted_at_ms=now,
        routed_category=routing["category"],
        routed_track=routing["track"],
        routed_review_queue=routing["review_queue"],
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
            """SELECT id, program_id, speaker_name, speaker_email, proposal_title,
                  proposal_abstract,answers_json,status,submitted_at_ms,routed_category,
                  routed_track,routed_review_queue FROM submissions
           WHERE organization_id = ?1 AND event_id = ?2 AND program_id = ?3
           ORDER BY submitted_at_ms DESC, id DESC LIMIT 100"""
        )
        .bind(program["organization_id"], program["event_id"], program_id)
        .all()
    )
    data = []
    for row in result_rows(result):
        answers = json.loads(str(row.pop("answers_json")))
        data.append(SubmissionView.model_validate({**row, "answers": answers}))
    return SubmissionList(data=data)


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
            """SELECT f.id,f.event_id,f.program_id,f.version,f.slug,f.welcome_text,
                      f.schema_json,f.opens_at_ms,f.closes_at_ms,f.submission_limit,
                      f.success_title,f.success_message,f.redirect_to_portal,
                      e.name AS event_name,e.accent_color,e.logo_url,
                      COUNT(s.id) AS submissions_received
               FROM call_for_speaker_forms f
               JOIN events e ON e.organization_id=f.organization_id AND e.id=f.event_id
               LEFT JOIN submissions s ON s.form_id=f.id AND s.status='submitted'
               WHERE f.id = ?1 GROUP BY f.id"""
        )
        .bind(form_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return _published_form_view(row, utc_now_ms())


async def _submission_by_id(db, submission_id: str) -> SubmissionView:
    row = row_mapping(
        await db.prepare(
            """SELECT id, program_id, speaker_name, speaker_email, proposal_title,
                      proposal_abstract, answers_json, status, submitted_at_ms,
                      routed_category,routed_track,routed_review_queue
               FROM submissions WHERE id = ?1"""
        )
        .bind(submission_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    answers = json.loads(str(row.pop("answers_json")))
    return SubmissionView.model_validate({**row, "answers": answers})


def _form_availability(row, submissions_received: int, now_ms: int) -> tuple[bool, str]:
    opens_at = int(row["opens_at_ms"]) if row.get("opens_at_ms") is not None else None
    closes_at = int(row["closes_at_ms"]) if row.get("closes_at_ms") is not None else None
    limit = int(row["submission_limit"]) if row.get("submission_limit") is not None else None
    if opens_at is not None and now_ms < opens_at:
        return False, "Applications have not opened yet."
    if closes_at is not None and now_ms >= closes_at:
        return False, "Applications are closed."
    if limit is not None and submissions_received >= limit:
        return False, "This call for speakers has reached its submission limit."
    return True, "Applications are open."


def _published_form_view(row, now_ms: int) -> PublishedFormView:
    schema = json.loads(str(row.pop("schema_json")))
    submissions_received = int(row.get("submissions_received") or 0)
    accepting, message = _form_availability(row, submissions_received, now_ms)
    return PublishedFormView.model_validate(
        {
            **row,
            **schema,
            "redirect_to_portal": bool(row["redirect_to_portal"]),
            "submissions_received": submissions_received,
            "accepting_submissions": accepting,
            "availability_message": message,
        }
    )


def _condition_matches(operator: object, actual: object, expected: str) -> bool:
    if operator == "contains":
        return expected in actual if isinstance(actual, list) else expected in str(actual or "")
    matches = str(actual if actual is not None else "") == expected
    return matches if operator == "equals" else not matches


def _route_submission(
    schema: dict[str, object], answers: dict[str, object]
) -> dict[str, str | None]:
    routed: dict[str, str | None] = {"category": None, "track": None, "review_queue": None}
    raw_rules = schema.get("routing_rules", [])
    if not isinstance(raw_rules, list):
        raise HTTPException(status_code=409)
    for rule in raw_rules:
        if not isinstance(rule, dict):
            raise HTTPException(status_code=409)
        if not _condition_matches(
            rule.get("operator"),
            answers.get(str(rule.get("source_key", ""))),
            str(rule.get("value", "")),
        ):
            continue
        for destination in routed:
            value = rule.get(destination)
            if routed[destination] is None and isinstance(value, str) and value:
                routed[destination] = value
    return routed


def _upload_references(schema: dict[str, object], answers: dict[str, object]) -> list[str]:
    references: list[str] = []
    raw_fields = schema.get("fields", [])
    if not isinstance(raw_fields, list):
        return references
    for field in raw_fields:
        if not isinstance(field, dict) or field.get("type") not in {"file", "image"}:
            continue
        value = answers.get(str(field.get("key", "")))
        if isinstance(value, str) and value.startswith("upload:"):
            references.append(value.removeprefix("upload:"))
    return references


async def _validate_upload_answers(
    db,
    schema: dict[str, object],
    answers: dict[str, object],
    *,
    event_id: str,
    user_id: str,
) -> None:
    raw_fields = schema.get("fields", [])
    if not isinstance(raw_fields, list):
        raise HTTPException(status_code=409)
    for field in raw_fields:
        if not isinstance(field, dict) or field.get("type") not in {"file", "image"}:
            continue
        value = answers.get(str(field.get("key", "")))
        if value in (None, "") and not field.get("required"):
            continue
        if not isinstance(value, str) or not value.startswith("upload:"):
            raise HTTPException(status_code=422)
        intent_id = value.removeprefix("upload:")
        expected_kind = "headshot" if field.get("type") == "image" else "supporting_document"
        found = await db.prepare(
            """SELECT 1 AS found FROM upload_intents ui
               JOIN speaker_asset_versions av ON av.id=ui.asset_version_id
               JOIN speaker_assets a ON a.id=av.asset_id
               JOIN event_speakers es ON es.id=a.event_speaker_id
               JOIN people p ON p.organization_id=es.organization_id AND p.id=es.person_id
               WHERE ui.id=?1 AND ui.event_id=?2 AND p.user_id=?3 AND a.kind=?4
                 AND av.scan_state='clean' AND av.is_current=1 LIMIT 1"""
        ).bind(intent_id, event_id, user_id, expected_kind).first("found")
        if found is None:
            raise HTTPException(status_code=422)


def _request_source(request: Request) -> str:
    connecting_ip = request.headers.get("cf-connecting-ip")
    if connecting_ip:
        return connecting_ip
    return request.client.host if request.client is not None else "unknown"


def _validate_submission_schema(schema: dict[str, object], body: SubmissionCreate) -> None:
    raw_fields = schema.get("fields", [])
    if not isinstance(raw_fields, list):
        raise HTTPException(status_code=409)
    values: dict[str, object] = {
        "speaker_name": body.speaker_name,
        "speaker_email": body.speaker_email,
        "proposal_title": body.proposal_title,
        "proposal_abstract": body.proposal_abstract,
        **body.answers,
    }
    known = {str(field.get("key")) for field in raw_fields if isinstance(field, dict)}
    if not set(body.answers) <= known:
        raise HTTPException(status_code=422)
    inactive_targets: set[str] = set()
    raw_conditions = schema.get("conditions", [])
    if not isinstance(raw_conditions, list):
        raise HTTPException(status_code=409)
    for condition in raw_conditions:
        if not isinstance(condition, dict):
            raise HTTPException(status_code=409)
        source_value = values.get(str(condition.get("source_key", "")), "")
        expected = str(condition.get("value", ""))
        visible = _condition_matches(condition.get("operator"), source_value, expected)
        if not visible:
            inactive_targets.add(str(condition.get("target_key", "")))
    for field in raw_fields:
        if not isinstance(field, dict):
            raise HTTPException(status_code=409)
        field_key = str(field.get("key", ""))
        if field_key in inactive_targets:
            continue
        value = values.get(field_key)
        if field.get("required") and (value is None or value == "" or value == []):
            raise HTTPException(status_code=422)
        if isinstance(value, str) and len(value) > 20_000:
            raise HTTPException(status_code=422)
        if isinstance(value, list) and (
            len(value) > 100
            or any(not isinstance(item, str) or len(item) > 500 for item in value)
        ):
            raise HTTPException(status_code=422)
        field_type = field.get("type")
        choices = field.get("choices", [])
        if field_type == "select" and value not in (None, "") and value not in choices:
            raise HTTPException(status_code=422)
        if field_type == "multiselect" and value not in (None, ""):
            if (
                not isinstance(value, list)
                or not value
                or any(choice not in choices for choice in value)
            ):
                raise HTTPException(status_code=422)
        if field_type == "checkbox" and value not in (None, True, False):
            raise HTTPException(status_code=422)
        if field_type in {"file", "image"} and value not in (None, ""):
            if not isinstance(value, str) or not value.startswith("upload:"):
                raise HTTPException(status_code=422)

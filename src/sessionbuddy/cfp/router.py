import hashlib
import json
import re
from email.headerregistry import Address
from html import escape
from time import perf_counter
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from sessionbuddy.console import embedded_assets
from sessionbuddy.observability import record_timing
from sessionbuddy.platform.auth import (
    authenticate_request,
    generate_token,
    hash_token,
    normalize_email,
)
from sessionbuddy.platform.auth.http import require_permission
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
    CfpWorkspaceView,
    CoSpeakerInvitationCreated,
    CoSpeakerInvitationView,
    CoSpeakerView,
    FormPublish,
    FormRoutingRule,
    FormUpdate,
    OwnedSubmissionList,
    PrivateSubmissionView,
    PublishedFormView,
    SubmissionCreate,
    SubmissionDraftUpsert,
    SubmissionDraftView,
    SubmissionList,
    SubmissionUpdate,
    SubmissionView,
)

cfp_router = APIRouter()


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


def _env(request: Request):
    return request.scope.get("env")


def _db(request: Request):
    db = getattr(_env(request), "DB", None)
    if db is None:
        raise HTTPException(status_code=503)
    return db


def _co_speaker_view(row) -> CoSpeakerView:
    values = dict(row)
    values["expires_at_ms"] = values.pop("invitation_expires_at_ms", None)
    return CoSpeakerView.model_validate(values)


def _co_speaker_page_url(request: Request, token: str) -> str:
    base = str(getattr(_env(request), "PUBLIC_BASE_URL", "")).rstrip("/")
    return f"{base}/co-speaker-invitations/{token}"


def _co_speaker_expiry(now: int, closes_at_ms: object) -> int:
    expiry = now + 7 * 86_400_000
    return min(expiry, int(closes_at_ms)) if closes_at_ms is not None else expiry


async def _owned_co_speaker_context(
    request: Request, slug: str, submission_id: str, co_speaker_id: str
):
    authenticated = await authenticate_request(request)
    row = row_mapping(
        await _db(request)
        .prepare(
            """SELECT c.id,c.organization_id,c.event_id,c.submission_id,c.display_name,
                      c.email,c.normalized_email,c.role,c.invitation_status,
                      c.invitation_expires_at_ms,c.invitation_version,c.user_id,
                      s.submitter_user_id,s.proposal_title,f.closes_at_ms,e.name AS event_name
               FROM submission_contributors c
               JOIN submissions s ON s.id=c.submission_id
               JOIN call_for_speaker_forms f ON f.id=s.form_id
               JOIN events e ON e.id=s.event_id
               WHERE c.id=?1 AND c.submission_id=?2 AND f.slug=?3
                 AND s.submitter_user_id=?4 AND c.invitation_status!='removed' LIMIT 1"""
        )
        .bind(co_speaker_id, submission_id, slug, authenticated.actor.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SUBMISSION_READ_OWN,
        ResourceContext(
            str(row["organization_id"]),
            str(row["event_id"]),
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    return authenticated, row


async def _reconcile_co_speakers(
    request: Request,
    *,
    submission_id: str,
    organization_id: str,
    event_id: str,
    invitation_deadline_ms: object,
    proposal_title: str,
    primary_name: str,
    desired,
    actor_user_id: str,
) -> None:
    db, now = _db(request), utc_now_ms()
    existing = result_rows(
        await db.prepare(
            """SELECT id,normalized_email,user_id,invitation_status
               FROM submission_contributors WHERE submission_id=?1
                 AND invitation_status!='removed'"""
        )
        .bind(submission_id)
        .all()
    )
    desired_by_email = {normalize_email(item.email): item for item in desired}
    existing_by_email = {str(row["normalized_email"]): row for row in existing}
    batch = CommandBatch(db)
    queued: list[str] = []
    for normalized, current in existing_by_email.items():
        contributor = desired_by_email.get(normalized)
        if contributor is not None:
            batch.add_statement(
                db.prepare(
                    """UPDATE submission_contributors SET display_name=?1,email=?2,
                         updated_at_ms=?3 WHERE id=?4 AND invitation_status!='removed'"""
                ).bind(contributor.display_name, contributor.email, now, current["id"])
            )
            continue
        batch.add_statement(
            db.prepare(
                """DELETE FROM submission_speakers
                   WHERE submission_id=?1 AND role='co_speaker' AND event_speaker_id IN (
                     SELECT es.id FROM event_speakers es JOIN people p ON p.id=es.person_id
                     WHERE es.event_id=?2 AND p.user_id=?3)"""
            ).bind(submission_id, event_id, current["user_id"])
        )
        batch.add_statement(
            db.prepare(
                """UPDATE submission_contributors SET invitation_status='removed',
                     invitation_token_hash=NULL,invitation_expires_at_ms=NULL,removed_at_ms=?1,
                     updated_at_ms=?1 WHERE id=?2 AND invitation_status!='removed'"""
            ).bind(now, current["id"])
        )
        if current["user_id"] is not None:
            batch.add_statement(
                db.prepare(
                    """UPDATE event_memberships SET status='revoked',revoked_at_ms=?1,
                         updated_at_ms=?1 WHERE organization_id=?2 AND event_id=?3
                         AND user_id=?4 AND role='speaker' AND NOT EXISTS (
                           SELECT 1 FROM submission_contributors c
                           WHERE c.organization_id=?2 AND c.event_id=?3 AND c.user_id=?4
                             AND c.invitation_status='accepted' AND c.id!=?5)
                         AND NOT EXISTS (
                           SELECT 1 FROM submission_speakers ss
                           JOIN event_speakers es ON es.id=ss.event_speaker_id
                           JOIN people p ON p.id=es.person_id
                           WHERE ss.event_id=?3 AND p.user_id=?4 AND ss.role='primary')"""
                ).bind(now, organization_id, event_id, current["user_id"], current["id"])
            )
        batch.audit(
            AuditEvent(
                actor_type="user", actor_user_id=actor_user_id,
                action="submission.co_speaker.remove", target_type="submission_contributor",
                target_id=str(current["id"]), result="succeeded",
                correlation_id=request.state.request_id, occurred_at_ms=now,
                organization_id=organization_id, event_id=event_id,
            )
        )
    for normalized, contributor in desired_by_email.items():
        if normalized in existing_by_email:
            continue
        contributor_id, token, message_id = new_id(), generate_token(), new_id()
        expires_at = _co_speaker_expiry(now, invitation_deadline_ms)
        if expires_at <= now:
            raise HTTPException(status_code=409, detail="Applications are closed.")
        invitation_url = _co_speaker_page_url(request, token)
        batch.add_statement(
            db.prepare(
                """INSERT INTO submission_contributors
                   (id,organization_id,event_id,submission_id,display_name,email,
                    normalized_email,role,created_at_ms,updated_at_ms,invitation_status,
                    invitation_token_hash,invitation_expires_at_ms,invited_at_ms,
                    invitation_version)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,'co_speaker',?8,?8,'pending',
                          ?9,?10,?8,1)
                   ON CONFLICT(submission_id,normalized_email) DO UPDATE SET
                     display_name=excluded.display_name,email=excluded.email,
                     invitation_status='pending',invitation_token_hash=excluded.invitation_token_hash,
                     invitation_expires_at_ms=excluded.invitation_expires_at_ms,
                     invited_at_ms=excluded.invited_at_ms,declined_at_ms=NULL,removed_at_ms=NULL,
                     invitation_version=submission_contributors.invitation_version+1,
                     updated_at_ms=excluded.updated_at_ms"""
            ).bind(
                contributor_id, organization_id, event_id, submission_id,
                contributor.display_name, contributor.email, normalized, now,
                hash_token(token), expires_at,
            )
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO communication_messages
                   (id,organization_id,event_id,recipient_email,subject,html_body,
                    deterministic_key,status,queued_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,'queued',?8,?8)"""
            ).bind(
                message_id, organization_id, event_id, contributor.email,
                f"Invitation to co-present {proposal_title}",
                f'<p>{escape(primary_name)} invited you to co-present.</p>'
                f'<p><a href="{escape(invitation_url)}">Respond to the invitation</a>.</p>',
                f"co-speaker:{contributor_id}:v1", now,
            )
        )
        prior = await db.prepare(
            "SELECT id FROM submission_contributors WHERE submission_id=?1 AND normalized_email=?2"
        ).bind(submission_id, normalized).first("id")
        if prior is not None:
            contributor_id = str(prior)
        batch.audit(
            AuditEvent(
                actor_type="user", actor_user_id=actor_user_id,
                action="submission.co_speaker.invite", target_type="submission_contributor",
                target_id=contributor_id, result="succeeded",
                correlation_id=request.state.request_id, occurred_at_ms=now,
                organization_id=organization_id, event_id=event_id,
            )
        )
        queued.append(message_id)
    await _execute(request, batch)
    queue = getattr(_env(request), "COMMUNICATION_QUEUE", None)
    if queue is not None:
        for message_id in queued:
            await queue.send({"schema_version": 1, "message_id": message_id})


async def _timed_first(request: Request, statement, column: str | None = None):
    started = perf_counter()
    try:
        # D1 treats an explicit JavaScript null as a requested column named
        # "null". Omit the argument entirely when the caller wants the row.
        return await statement.first() if column is None else await statement.first(column)
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)


def _idempotency_key(value: str | None) -> str:
    if value is None or not 16 <= len(value) <= 255:
        raise HTTPException(status_code=400)
    return value


def _fingerprint(model) -> bytes:
    canonical = json.dumps(model.model_dump(), separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).digest()


def _product_page(request: Request, asset: str) -> HTMLResponse:
    return HTMLResponse(_asset(asset), headers={"Cache-Control": "no-store"})


@cfp_router.get(
    "/admin/events/{event_id}/cfp",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_event_cfp_page(event_id: str, request: Request) -> HTMLResponse:
    return _product_page(request, "admin_programs.html")


@cfp_router.get(
    "/admin/events/{event_id}/submissions",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_submissions_page(event_id: str, request: Request) -> HTMLResponse:
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


@cfp_router.get(
    "/api/v1/admin/events/{event_id}/cfp",
    response_model=CfpWorkspaceView,
    operation_id="getCallForSpeakersWorkspace",
    tags=["forms"],
)
async def get_cfp_workspace(event_id: str, request: Request) -> CfpWorkspaceView:
    await authenticate_request(request)
    db = _db(request)
    event = row_mapping(
        await _timed_first(
            request,
            db.prepare(
                """SELECT organization_id,name,starts_at_ms FROM events
                   WHERE id = ?1 AND status != 'archived' LIMIT 1"""
            ).bind(event_id),
        )
    )
    if event is None:
        raise HTTPException(status_code=404)
    organization_id = str(event["organization_id"])
    await require_permission(
        request,
        Permission.FORM_MANAGE,
        ResourceContext(organization_id, event_id),
        mutation=False,
    )
    form_id = await _timed_first(
        request,
        db.prepare(
            """SELECT id FROM call_for_speaker_forms
               WHERE organization_id = ?1 AND event_id = ?2 AND status = 'published'
               ORDER BY version DESC,published_at_ms DESC,id DESC LIMIT 1"""
        ).bind(organization_id, event_id),
        "id",
    )
    published_form = await _form_by_id(db, str(form_id)) if form_id is not None else None
    return CfpWorkspaceView(
        organization_id=organization_id,
        event_id=event_id,
        event_name=str(event["name"]),
        event_starts_at_ms=int(event["starts_at_ms"]),
        published_form=published_form,
    )


def _validate_cfp_deadline(closes_at_ms: int | None, event_starts_at_ms: int) -> None:
    if closes_at_ms is not None and closes_at_ms >= event_starts_at_ms:
        raise HTTPException(
            status_code=422,
            detail="The Call for Proposals must close before the event starts.",
        )


@cfp_router.post(
    "/api/v1/admin/events/{event_id}/cfp/publish",
    response_model=PublishedFormView,
    status_code=201,
    operation_id="publishCallForSpeakersForm",
    tags=["forms"],
)
async def publish_form(
    event_id: str,
    request: Request,
    body: FormPublish,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PublishedFormView:
    await authenticate_request(request)
    key = _idempotency_key(idempotency_key)
    db = _db(request)
    event = row_mapping(
        await db.prepare(
            """SELECT organization_id,starts_at_ms FROM events
               WHERE id=?1 AND status != 'archived' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.FORM_MANAGE,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=True,
    )
    _validate_cfp_deadline(body.closes_at_ms, int(event["starts_at_ms"]))
    await _validate_form_routing_tracks(
        db,
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        routing_rules=body.routing_rules,
    )
    route_key = "POST /api/v1/admin/events/{event_id}/cfp/publish"
    fingerprint = _fingerprint(body)
    replay = await _find_replay(db, auth.actor.user_id, route_key, key)
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _form_by_id(db, str(replay["response_resource_id"]))
    now = utc_now_ms()
    form_id = new_id()
    existing_form = await db.prepare(
        """SELECT 1 AS found FROM call_for_speaker_forms
           WHERE organization_id=?1 AND event_id=?2 AND status='published' LIMIT 1"""
    ).bind(event["organization_id"], event_id).first("found")
    if existing_form is not None:
        raise HTTPException(status_code=409, detail="This event already has a published form.")
    version = (
        await db.prepare(
            """SELECT COALESCE(MAX(version), 0) + 1 AS version
           FROM call_for_speaker_forms WHERE organization_id=?1 AND event_id=?2"""
        )
        .bind(event["organization_id"], event_id)
        .first("version")
    )
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(event["organization_id"]),
        event_id=event_id,
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
               (id, organization_id, event_id, version, slug, welcome_text,
                schema_json, opens_at_ms, closes_at_ms, submission_limit, success_title,
                success_message, redirect_to_portal, confirmation_subject, confirmation_body,
                status, published_at_ms, created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       ?13, ?14, ?15, 'published', ?16, ?16, ?16)"""
        ).bind(
            form_id,
            event["organization_id"],
            event_id,
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
            organization_id=str(event["organization_id"]),
            event_id=event_id,
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


@cfp_router.patch(
    "/api/v1/admin/events/{event_id}/cfp",
    response_model=PublishedFormView,
    operation_id="updatePublishedCallForSpeakersForm",
    tags=["forms"],
)
async def update_published_form(
    event_id: str,
    request: Request,
    body: FormUpdate,
) -> PublishedFormView:
    db = _db(request)
    current = row_mapping(
        await db.prepare(
            """SELECT f.id,f.organization_id,f.event_id,f.version,e.starts_at_ms
               FROM call_for_speaker_forms f
               JOIN events e ON e.organization_id=f.organization_id AND e.id=f.event_id
               WHERE f.event_id=?1 AND f.status='published'
               ORDER BY f.version DESC,f.updated_at_ms DESC,f.id DESC LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if current is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.FORM_MANAGE,
        ResourceContext(str(current["organization_id"]), str(current["event_id"])),
        mutation=True,
    )
    if int(current["version"]) != body.version:
        raise HTTPException(status_code=409)
    _validate_cfp_deadline(body.closes_at_ms, int(current["starts_at_ms"]))
    await _validate_form_routing_tracks(
        db,
        organization_id=str(current["organization_id"]),
        event_id=event_id,
        routing_rules=body.routing_rules,
    )
    form_id = str(current["id"])
    now = utc_now_ms()
    schema_json = json.dumps(
        {
            "fields": [field.model_dump() for field in body.fields],
            "conditions": [condition.model_dump() for condition in body.conditions],
            "routing_rules": [rule.model_dump() for rule in body.routing_rules],
        },
        separators=(",", ":"),
    )
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE call_for_speaker_forms
               SET version=version+1,slug=?1,welcome_text=?2,schema_json=?3,
                   opens_at_ms=?4,closes_at_ms=?5,submission_limit=?6,
                   success_title=?7,success_message=?8,redirect_to_portal=?9,
                   updated_at_ms=?10
               WHERE id=?11 AND event_id=?12 AND status='published' AND version=?13"""
        ).bind(
            body.slug,
            body.welcome_text,
            schema_json,
            body.opens_at_ms,
            body.closes_at_ms,
            body.submission_limit,
            body.success_title,
            body.success_message,
            int(body.redirect_to_portal),
            now,
            form_id,
            event_id,
            body.version,
        )
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO cfp_form_write_guards
               (id,form_id,applied_changes,created_at_ms)
               VALUES (?1,?2,changes(),?3)"""
        ).bind(new_id(), form_id, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="form.update",
            target_type="call_for_speaker_form",
            target_id=form_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(current["organization_id"]),
            event_id=str(current["event_id"]),
            metadata={"version": body.version + 1},
        )
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
            """SELECT f.id, f.event_id, f.version, f.slug, f.welcome_text,
                      f.schema_json, f.opens_at_ms, f.closes_at_ms, f.submission_limit,
                      f.success_title, f.success_message, f.redirect_to_portal,
                      e.name AS event_name,e.accent_color,e.logo_url,e.cover_image_url,
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
            """SELECT id,organization_id,event_id,schema_json,version,
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
    _validate_draft_schema(schema, body.answers)
    now, draft_id = utc_now_ms(), new_id()
    answers_json = json.dumps(body.answers, separators=(",", ":"), sort_keys=True)
    await (
        db.prepare(
            """INSERT INTO submission_drafts
           (id,organization_id,event_id,form_id,user_id,answers_json,version,
            created_at_ms,updated_at_ms)
           VALUES(?1,?2,?3,?4,?5,?6,1,?7,?7)
           ON CONFLICT(form_id,user_id) DO UPDATE SET answers_json=excluded.answers_json,
             version=submission_drafts.version+1,updated_at_ms=excluded.updated_at_ms
           WHERE submission_drafts.version=?8"""
        )
        .bind(
            draft_id,
            form["organization_id"],
            form["event_id"],
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


@cfp_router.get(
    "/api/v1/forms/{slug}/submissions/mine",
    response_model=OwnedSubmissionList,
    operation_id="listMyCallForSpeakersSubmissions",
    tags=["submissions"],
)
async def list_my_submissions(slug: str, request: Request) -> OwnedSubmissionList:
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
    normalized_email = await db.prepare(
        "SELECT normalized_email FROM users WHERE id=?1 AND status='active' LIMIT 1"
    ).bind(authenticated.actor.user_id).first("normalized_email")
    if normalized_email is None:
        return OwnedSubmissionList(data=[])
    rows = result_rows(
        await db.prepare(
            """SELECT s.id,s.speaker_name,s.speaker_email,s.proposal_title,
                      s.proposal_abstract,s.answers_json,
                      COALESCE(d.decision,s.status) AS status,s.submitted_at_ms,s.version,
                      s.routed_category,s.routed_track,s.routed_review_queue,
                      CASE WHEN s.submitter_user_id=?2 THEN 1 ELSE 0 END AS editable
               FROM submissions s LEFT JOIN submission_decisions d ON d.submission_id=s.id
               WHERE s.form_id=?1 AND (
                 s.submitter_user_id=?2 OR EXISTS (
                   SELECT 1 FROM submission_contributors c
                   WHERE c.submission_id=s.id AND c.normalized_email=?3
                     AND c.invitation_status='accepted'
                 )
               )
               ORDER BY s.updated_at_ms DESC,s.id DESC LIMIT 25"""
        )
        .bind(form["id"], authenticated.actor.user_id, normalized_email)
        .all()
    )
    data = []
    for row in rows:
        values = dict(row)
        answers = json.loads(str(values.pop("answers_json")))
        contributors = result_rows(
            await db.prepare(
                """SELECT id,display_name,email,role,invitation_status,
                          invitation_expires_at_ms AS expires_at_ms
                   FROM submission_contributors WHERE submission_id=?1
                     AND invitation_status!='removed' ORDER BY display_name,id"""
            )
            .bind(values["id"])
            .all()
        )
        data.append(
            PrivateSubmissionView.model_validate(
                {
                    **values,
                    "editable": bool(values["editable"]),
                    "answers": answers,
                    "co_speakers": contributors,
                }
            )
        )
    return OwnedSubmissionList(data=data)


@cfp_router.get(
    "/api/v1/co-speaker-invitations/{token}",
    response_model=CoSpeakerInvitationView,
    tags=["submissions"],
)
async def get_co_speaker_invitation(token: str, request: Request) -> CoSpeakerInvitationView:
    if len(token) < 32:
        raise HTTPException(status_code=404)
    row = row_mapping(
        await _db(request)
        .prepare(
            """SELECT c.id,c.submission_id,c.display_name,c.email,c.role,c.invitation_status,
                      c.invitation_expires_at_ms,s.proposal_title,e.name AS event_name
               FROM submission_contributors c
               JOIN submissions s ON s.id=c.submission_id
               JOIN events e ON e.id=c.event_id
               WHERE c.invitation_token_hash=?1 AND c.invitation_status='pending'
                 AND c.invitation_expires_at_ms>?2 LIMIT 1"""
        )
        .bind(hash_token(token), utc_now_ms())
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    values = dict(row)
    values["expires_at_ms"] = values.pop("invitation_expires_at_ms")
    return CoSpeakerInvitationView.model_validate(values)


async def _respond_to_co_speaker_invitation(
    token: str, request: Request, response_status: str
) -> CoSpeakerInvitationView:
    if len(token) < 32:
        raise HTTPException(status_code=404)
    db, now = _db(request), utc_now_ms()
    row = row_mapping(
        await db.prepare(
            """SELECT c.id,c.organization_id,c.event_id,c.submission_id,c.display_name,
                      c.email,c.normalized_email,c.role,c.invitation_status,
                      c.invitation_expires_at_ms,s.proposal_title,e.name AS event_name
               FROM submission_contributors c
               JOIN submissions s ON s.id=c.submission_id
               JOIN events e ON e.id=c.event_id
               WHERE c.invitation_token_hash=?1 AND c.invitation_status='pending'
                 AND c.invitation_expires_at_ms>?2 LIMIT 1"""
        )
        .bind(hash_token(token), now)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    batch = CommandBatch(db)
    user_id: str | None = None
    if response_status == "accepted":
        existing_user = row_mapping(
            await db.prepare("SELECT id FROM users WHERE normalized_email=?1 LIMIT 1")
            .bind(row["normalized_email"])
            .first()
        )
        user_id = str(existing_user["id"]) if existing_user is not None else new_id()
        if existing_user is None:
            batch.add_statement(
                db.prepare(
                    """INSERT INTO users
                       (id,email,normalized_email,status,email_verified_at_ms,created_at_ms,updated_at_ms)
                       VALUES(?1,?2,?3,'active',?4,?4,?4)"""
                ).bind(user_id, row["email"], row["normalized_email"], now)
            )
        batch.add_statement(
            db.prepare(
                """INSERT INTO organization_memberships
                   (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,'member','active',?4,?4)
                   ON CONFLICT(organization_id,user_id) DO UPDATE SET status='active',
                     revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
            ).bind(new_id(), row["organization_id"], user_id, now)
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO event_memberships
                   (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,'speaker','active',?5,?5)
                   ON CONFLICT(organization_id,event_id,user_id,role) DO UPDATE SET status='active',
                     revoked_at_ms=NULL,updated_at_ms=excluded.updated_at_ms"""
            ).bind(new_id(), row["organization_id"], row["event_id"], user_id, now)
        )
        person = row_mapping(
            await db.prepare(
                "SELECT id FROM people WHERE organization_id=?1 AND user_id=?2 LIMIT 1"
            )
            .bind(row["organization_id"], user_id)
            .first()
        )
        person_id = str(person["id"]) if person is not None else new_id()
        if person is None:
            batch.add_statement(
                db.prepare(
                    """INSERT INTO people
                       (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
                       VALUES(?1,?2,?3,?4,?5,?5)"""
                ).bind(person_id, row["organization_id"], user_id, row["display_name"], now)
            )
        speaker = row_mapping(
            await db.prepare(
                """SELECT id FROM event_speakers
                   WHERE organization_id=?1 AND event_id=?2 AND person_id=?3 LIMIT 1"""
            )
            .bind(row["organization_id"], row["event_id"], person_id)
            .first()
        )
        speaker_id = str(speaker["id"]) if speaker is not None else new_id()
        if speaker is None:
            batch.add_statement(
                db.prepare(
                    """INSERT INTO event_speakers
                       (id,organization_id,event_id,person_id,status,accepted_at_ms,
                        last_activity_at_ms,created_at_ms,updated_at_ms,selection_status)
                       VALUES(?1,?2,?3,?4,'onboarding',?5,?5,?5,?5,'submitted')"""
                ).bind(speaker_id, row["organization_id"], row["event_id"], person_id, now)
            )
        batch.add_statement(
            db.prepare(
                """INSERT INTO submission_speakers
                   (id,organization_id,event_id,submission_id,event_speaker_id,role,
                    snapshot_name,created_at_ms)
                   VALUES(?1,?2,?3,?4,?5,'co_speaker',?6,?7)
                   ON CONFLICT(organization_id,event_id,submission_id,event_speaker_id)
                   DO UPDATE SET snapshot_name=excluded.snapshot_name"""
            ).bind(
                new_id(), row["organization_id"], row["event_id"], row["submission_id"],
                speaker_id, row["display_name"], now,
            )
        )
    batch.add_statement(
        db.prepare(
            """UPDATE submission_contributors SET invitation_status=?1,user_id=?2,
                 invitation_token_hash=NULL,invitation_expires_at_ms=NULL,
                 accepted_at_ms=CASE WHEN ?1='accepted' THEN ?3 ELSE accepted_at_ms END,
                 declined_at_ms=CASE WHEN ?1='declined' THEN ?3 ELSE declined_at_ms END,
                 updated_at_ms=?3
               WHERE id=?4 AND invitation_token_hash=?5 AND invitation_status='pending'
                 AND invitation_expires_at_ms>?3"""
        ).bind(response_status, user_id, now, row["id"], hash_token(token))
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO submission_contributor_invitation_guards
               (id,contributor_id,applied_changes,created_at_ms)
               VALUES(?1,?2,changes(),?3)"""
        ).bind(new_id(), row["id"], now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user" if user_id else "anonymous",
            actor_user_id=user_id,
            action=f"submission.co_speaker.{response_status}",
            target_type="submission_contributor",
            target_id=str(row["id"]),
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
        )
    )
    await _execute(request, batch)
    return CoSpeakerInvitationView(
        id=str(row["id"]),
        submission_id=str(row["submission_id"]),
        display_name=str(row["display_name"]),
        email=str(row["email"]),
        role="co_speaker",
        invitation_status=response_status,
        expires_at_ms=None,
        proposal_title=str(row["proposal_title"]),
        event_name=str(row["event_name"]),
    )


@cfp_router.post(
    "/api/v1/co-speaker-invitations/{token}/accept",
    response_model=CoSpeakerInvitationView,
    tags=["submissions"],
)
async def accept_co_speaker_invitation(
    token: str, request: Request
) -> CoSpeakerInvitationView:
    return await _respond_to_co_speaker_invitation(token, request, "accepted")


@cfp_router.post(
    "/api/v1/co-speaker-invitations/{token}/decline",
    response_model=CoSpeakerInvitationView,
    tags=["submissions"],
)
async def decline_co_speaker_invitation(
    token: str, request: Request
) -> CoSpeakerInvitationView:
    return await _respond_to_co_speaker_invitation(token, request, "declined")


@cfp_router.post(
    "/api/v1/forms/{slug}/submissions/{submission_id}/co-speakers/{co_speaker_id}/resend",
    response_model=CoSpeakerInvitationCreated,
    tags=["submissions"],
)
async def resend_co_speaker_invitation(
    slug: str,
    submission_id: str,
    co_speaker_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> CoSpeakerInvitationCreated:
    key = _idempotency_key(idempotency_key)
    authenticated, row = await _owned_co_speaker_context(
        request, slug, submission_id, co_speaker_id
    )
    if row["invitation_status"] == "accepted":
        raise HTTPException(status_code=409)
    db, now = _db(request), utc_now_ms()
    token, message_id = generate_token(), new_id()
    expires_at = _co_speaker_expiry(now, row["closes_at_ms"])
    if expires_at <= now:
        raise HTTPException(status_code=409, detail="Applications are closed.")
    fingerprint = hashlib.sha256(
        f"{co_speaker_id}:{row['invitation_version']}".encode()
    ).digest()
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        route_key="POST /api/v1/forms/{slug}/submissions/{submission_id}/co-speakers/{id}/resend",
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    invitation_url = _co_speaker_page_url(request, token)
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """UPDATE submission_contributors SET invitation_status='pending',
                 invitation_token_hash=?1,invitation_expires_at_ms=?2,invited_at_ms=?3,
                 declined_at_ms=NULL,invitation_version=invitation_version+1,updated_at_ms=?3
               WHERE id=?4 AND invitation_status IN ('pending','declined')"""
        ).bind(hash_token(token), expires_at, now, co_speaker_id)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO submission_contributor_invitation_guards
               (id,contributor_id,applied_changes,created_at_ms)
               VALUES(?1,?2,changes(),?3)"""
        ).bind(new_id(), co_speaker_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,'queued',?8,?8)"""
        ).bind(
            message_id,
            row["organization_id"],
            row["event_id"],
            row["email"],
            f"Invitation to co-present {row['proposal_title']}",
            f'<p>You were invited to co-present at {escape(str(row["event_name"]))}.</p>'
            f'<p><a href="{escape(invitation_url)}">Respond to the invitation</a>.</p>',
            f"co-speaker:{co_speaker_id}:v{int(row['invitation_version']) + 1}",
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="submission.co_speaker.resend",
            target_type="submission_contributor",
            target_id=co_speaker_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="submission_contributor",
        resource_id=co_speaker_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    queue = getattr(_env(request), "COMMUNICATION_QUEUE", None)
    if queue is not None:
        await queue.send({"schema_version": 1, "message_id": message_id})
    view = CoSpeakerView(
        id=co_speaker_id,
        display_name=str(row["display_name"]),
        email=str(row["email"]),
        role="co_speaker",
        invitation_status="pending",
        expires_at_ms=expires_at,
    )
    return CoSpeakerInvitationCreated(
        co_speaker=view,
        invitation_url=(
            invitation_url
            if getattr(_env(request), "APP_ENV", "production") == "local"
            else None
        ),
    )


@cfp_router.delete(
    "/api/v1/forms/{slug}/submissions/{submission_id}/co-speakers/{co_speaker_id}",
    status_code=204,
    tags=["submissions"],
)
async def remove_co_speaker(
    slug: str, submission_id: str, co_speaker_id: str, request: Request
) -> None:
    authenticated, row = await _owned_co_speaker_context(
        request, slug, submission_id, co_speaker_id
    )
    db, now = _db(request), utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """DELETE FROM submission_speakers
               WHERE submission_id=?1 AND role='co_speaker' AND event_speaker_id IN (
                 SELECT es.id FROM event_speakers es JOIN people p ON p.id=es.person_id
                 WHERE es.event_id=?2 AND p.user_id=?3)"""
        ).bind(submission_id, row["event_id"], row["user_id"])
    )
    batch.add_statement(
        db.prepare(
            """UPDATE submission_contributors SET invitation_status='removed',
                 invitation_token_hash=NULL,invitation_expires_at_ms=NULL,removed_at_ms=?1,
                 updated_at_ms=?1 WHERE id=?2 AND invitation_status!='removed'"""
        ).bind(now, co_speaker_id)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO submission_contributor_invitation_guards
               (id,contributor_id,applied_changes,created_at_ms)
               VALUES(?1,?2,changes(),?3)"""
        ).bind(new_id(), co_speaker_id, now)
    )
    if row["user_id"] is not None:
        batch.add_statement(
            db.prepare(
                """UPDATE event_memberships SET status='revoked',revoked_at_ms=?1,updated_at_ms=?1
                   WHERE organization_id=?2 AND event_id=?3 AND user_id=?4 AND role='speaker'
                     AND NOT EXISTS (
                       SELECT 1 FROM submission_contributors c
                       WHERE c.organization_id=?2 AND c.event_id=?3 AND c.user_id=?4
                         AND c.invitation_status='accepted' AND c.id!=?5)
                     AND NOT EXISTS (
                       SELECT 1 FROM submission_speakers ss
                       JOIN event_speakers es ON es.id=ss.event_speaker_id
                       JOIN people p ON p.id=es.person_id
                       WHERE ss.event_id=?3 AND p.user_id=?4 AND ss.role='primary')"""
            ).bind(
                now, row["organization_id"], row["event_id"], row["user_id"], co_speaker_id
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="submission.co_speaker.remove",
            target_type="submission_contributor",
            target_id=co_speaker_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
        )
    )
    await _execute(request, batch)


@cfp_router.patch(
    "/api/v1/forms/{slug}/submissions/{submission_id}",
    response_model=PrivateSubmissionView,
    operation_id="updateMyCallForSpeakersSubmission",
    tags=["submissions"],
)
async def update_submission(
    slug: str,
    submission_id: str,
    body: SubmissionUpdate,
    request: Request,
) -> PrivateSubmissionView:
    authenticated = await authenticate_request(request)
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT s.organization_id,s.event_id,s.form_id,s.status,
                      s.submitter_user_id,f.schema_json,f.opens_at_ms,f.closes_at_ms
               FROM submissions s JOIN call_for_speaker_forms f ON f.id=s.form_id
               WHERE s.id=?1 AND f.slug=?2 LIMIT 1"""
        )
        .bind(submission_id, slug)
        .first()
    )
    if row is None or str(row["submitter_user_id"] or "") != authenticated.actor.user_id:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SUBMISSION_READ_OWN,
        ResourceContext(
            str(row["organization_id"]),
            str(row["event_id"]),
            resource_owner_user_id=authenticated.actor.user_id,
        ),
        mutation=True,
    )
    if row["status"] != "submitted":
        raise HTTPException(status_code=409)
    now = utc_now_ms()
    if row["opens_at_ms"] is not None and now < int(row["opens_at_ms"]):
        raise HTTPException(status_code=409, detail="Applications have not opened yet.")
    if row["closes_at_ms"] is not None and now >= int(row["closes_at_ms"]):
        raise HTTPException(status_code=409, detail="Applications are closed.")
    decided = (
        await db.prepare(
            "SELECT 1 AS found FROM submission_decisions WHERE submission_id=?1 LIMIT 1"
        )
        .bind(submission_id)
        .first("found")
    )
    if decided is not None:
        raise HTTPException(status_code=409)
    normalized_email = (
        await db.prepare("SELECT normalized_email FROM users WHERE id=?1")
        .bind(authenticated.actor.user_id)
        .first("normalized_email")
    )
    if normalized_email is None or normalize_email(body.speaker_email) != str(normalized_email):
        raise HTTPException(status_code=422)
    schema = json.loads(str(row["schema_json"]))
    _validate_submission_schema(schema, body)
    await _validate_upload_answers(
        db,
        schema,
        body.answers,
        event_id=str(row["event_id"]),
        user_id=authenticated.actor.user_id,
    )
    routing = _route_submission(schema, body.answers)
    await _validate_routed_track(
        db,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        routing=routing,
    )
    await (
        db.prepare(
            """UPDATE submissions SET proposal_title=?1,proposal_abstract=?2,
                  speaker_name=?3,speaker_email=?4,answers_json=?5,
                  routed_category=?6,routed_track=?7,routed_review_queue=?8,
                  version=version+1,updated_at_ms=?9
           WHERE id=?10 AND submitter_user_id=?11 AND status='submitted' AND version=?12"""
        )
        .bind(
            body.proposal_title,
            body.proposal_abstract,
            body.speaker_name,
            body.speaker_email,
            json.dumps(body.answers, separators=(",", ":"), sort_keys=True),
            routing["category"],
            routing["track"],
            routing["review_queue"],
            now,
            submission_id,
            authenticated.actor.user_id,
            body.version,
        )
        .run()
    )
    updated_version = (
        await db.prepare("SELECT version FROM submissions WHERE id=?1 AND submitter_user_id=?2")
        .bind(submission_id, authenticated.actor.user_id)
        .first("version")
    )
    if updated_version is None or int(updated_version) != body.version + 1:
        raise HTTPException(status_code=409)
    await (
        db.prepare(
            """UPDATE submission_speakers SET snapshot_name=?1
           WHERE submission_id=?2 AND role='primary'"""
        )
        .bind(body.speaker_name, submission_id)
        .run()
    )
    await _reconcile_co_speakers(
        request,
        submission_id=submission_id,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        invitation_deadline_ms=row["closes_at_ms"],
        proposal_title=body.proposal_title,
        primary_name=body.speaker_name,
        desired=body.co_speakers,
        actor_user_id=authenticated.actor.user_id,
    )
    return await _editable_submission_by_id(db, submission_id)


@cfp_router.patch(
    "/api/v1/admin/submissions/{submission_id}",
    response_model=SubmissionView,
    operation_id="updateEventSubmissionAsOrganizer",
    tags=["submissions"],
)
async def update_submission_as_organizer(
    submission_id: str, body: SubmissionUpdate, request: Request
) -> SubmissionView:
    authenticated = await authenticate_request(request)
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT s.organization_id,s.event_id,s.form_id,s.status,
                      s.version,f.schema_json
               FROM submissions s JOIN call_for_speaker_forms f ON f.id=s.form_id
               WHERE s.id=?1 LIMIT 1"""
        )
        .bind(submission_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(row["organization_id"]), str(row["event_id"])),
        mutation=True,
    )
    if row["status"] != "submitted":
        raise HTTPException(status_code=409, detail="Only submitted proposals can be edited.")
    if (
        await db.prepare(
            "SELECT 1 AS found FROM submission_decisions WHERE submission_id=?1 LIMIT 1"
        )
        .bind(submission_id)
        .first("found")
        is not None
    ):
        raise HTTPException(status_code=409, detail="A decided proposal cannot be edited.")
    schema = json.loads(str(row["schema_json"]))
    _validate_submission_schema(schema, body)
    routing = _route_submission(schema, body.answers)
    await _validate_routed_track(
        db,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        routing=routing,
    )
    now = utc_now_ms()
    updated = (
        await db.prepare(
            """UPDATE submissions SET proposal_title=?1,proposal_abstract=?2,
                  speaker_name=?3,speaker_email=?4,answers_json=?5,
                  routed_category=?6,routed_track=?7,routed_review_queue=?8,
                  version=version+1,updated_at_ms=?9
           WHERE id=?10 AND status='submitted' AND version=?11"""
        )
        .bind(
            body.proposal_title,
            body.proposal_abstract,
            body.speaker_name,
            body.speaker_email,
            json.dumps(body.answers, separators=(",", ":"), sort_keys=True),
            routing["category"],
            routing["track"],
            routing["review_queue"],
            now,
            submission_id,
            body.version,
        )
        .run()
    )
    if not int(to_python(getattr(updated, "meta", {}).get("changes", 0)) or 0):
        raise HTTPException(status_code=409)
    await (
        db.prepare(
            """UPDATE submission_speakers SET snapshot_name=?1
               WHERE submission_id=?2 AND role='primary'"""
        )
        .bind(body.speaker_name, submission_id)
        .run()
    )
    await _reconcile_co_speakers(
        request,
        submission_id=submission_id,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        invitation_deadline_ms=None,
        proposal_title=body.proposal_title,
        primary_name=body.speaker_name,
        desired=body.co_speakers,
        actor_user_id=authenticated.actor.user_id,
    )
    batch = CommandBatch(db)
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="submission.organizer_update",
            target_type="submission",
            target_id=submission_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            metadata={"version": body.version + 1},
        )
    )
    await _execute(request, batch)
    return await _submission_by_id(db, submission_id)


@cfp_router.post(
    "/api/v1/forms/{slug}/submissions",
    response_model=PrivateSubmissionView,
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
) -> PrivateSubmissionView:
    key = _idempotency_key(idempotency_key)
    if public_session is None or not 16 <= len(public_session) <= 128:
        raise HTTPException(status_code=400)
    await enforce_rate_limit(
        request,
        binding_name="PUBLIC_RATE_LIMITER",
        policy=RateLimitPolicy("public.submit", limit=50, window_seconds=60),
        subject=f"{public_session}:{_request_source(request)}",
    )
    authenticated = await authenticate_request(request)
    db = _db(request)
    form = row_mapping(
        await db.prepare(
            """SELECT id, organization_id, event_id, slug, version, schema_json,
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
    submitter_user_id = authenticated.actor.user_id
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
    principal_key = submitter_user_id
    fingerprint = _fingerprint(body)
    route_key = "POST /api/v1/forms/{slug}/submissions"
    replay = await _find_replay(db, principal_key, route_key, key)
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _editable_submission_by_id(db, str(replay["response_resource_id"]))
    submission_id = new_id()
    routing = _route_submission(schema, body.answers)
    await _validate_routed_track(
        db,
        organization_id=str(form["organization_id"]),
        event_id=str(form["event_id"]),
        routing=routing,
    )
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
               (id, organization_id, event_id, form_id, public_session_id,
                proposal_title, proposal_abstract, speaker_name, speaker_email, answers_json,
                submitter_user_id, status, submitted_at_ms, created_at_ms, updated_at_ms,
                routed_category, routed_track, routed_review_queue)
               SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10,
                      ?11, 'submitted', ?12, ?12, ?12, ?13, ?14, ?15
               WHERE (SELECT COUNT(*) FROM submissions
                      WHERE form_id=?4 AND status='submitted')
                     < COALESCE((SELECT submission_limit FROM call_for_speaker_forms
                                 WHERE id=?4), 1000001)"""
        ).bind(
            submission_id,
            form["organization_id"],
            form["event_id"],
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
    co_speaker_message_ids: list[str] = []
    for contributor in body.co_speakers:
        contributor_id = new_id()
        invitation_token = generate_token()
        invitation_expires_at = _co_speaker_expiry(now, form["closes_at_ms"])
        invitation_url = _co_speaker_page_url(request, invitation_token)
        invitation_message_id = new_id()
        co_speaker_message_ids.append(invitation_message_id)
        batch.add_statement(
            db.prepare(
                """INSERT INTO submission_contributors
                   (id,organization_id,event_id,submission_id,display_name,email,
                    normalized_email,role,created_at_ms,updated_at_ms,invitation_status,
                    invitation_token_hash,invitation_expires_at_ms,invited_at_ms,
                    invitation_version)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,'co_speaker',?8,?8,'pending',
                          ?9,?10,?8,1)"""
            ).bind(
                contributor_id,
                form["organization_id"],
                form["event_id"],
                submission_id,
                contributor.display_name,
                contributor.email,
                normalize_email(contributor.email),
                now,
                hash_token(invitation_token),
                invitation_expires_at,
            )
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO communication_messages
                   (id,organization_id,event_id,recipient_email,subject,html_body,
                    deterministic_key,status,queued_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,'queued',?8,?8)"""
            ).bind(
                invitation_message_id,
                form["organization_id"],
                form["event_id"],
                contributor.email,
                f"Invitation to co-present {body.proposal_title}",
                f'<p>{escape(body.speaker_name)} invited you to co-present.</p>'
                f'<p><a href="{escape(invitation_url)}">Respond to the invitation</a>.</p>',
                f"co-speaker:{contributor_id}:v1",
                now,
            )
        )
        batch.audit(
            AuditEvent(
                actor_type="user",
                actor_user_id=submitter_user_id,
                action="submission.co_speaker.invite",
                target_type="submission_contributor",
                target_id=contributor_id,
                result="succeeded",
                correlation_id=request.state.request_id,
                occurred_at_ms=now,
                organization_id=str(form["organization_id"]),
                event_id=str(form["event_id"]),
            )
        )
    batch.add_statement(
        db.prepare("DELETE FROM submission_drafts WHERE form_id=?1 AND user_id=?2").bind(
            form["id"], submitter_user_id
        )
    )
    person = row_mapping(
        await db.prepare("SELECT id FROM people WHERE organization_id=?1 AND user_id=?2 LIMIT 1")
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
            ).bind(person_id, form["organization_id"], submitter_user_id, body.speaker_name, now)
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
            actor_type="user",
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
    proposal_url = (
        f"{str(request.base_url).rstrip('/')}/cfp/{escape(str(form['slug']))}"
        f"?submission_id={escape(submission_id)}"
    )
    confirmation_html = (
        f"<p>{escape(str(form['confirmation_body']))}</p>"
        f"<p><strong>{escape(body.proposal_title)}</strong></p>"
        f'<p><a href="{proposal_url}">View your proposal</a></p>'
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
            for queued_message_id in [message_id, *co_speaker_message_ids]:
                await queue.send({"schema_version": 1, "message_id": queued_message_id})
        except Exception:
            # The durable queued row remains visible to operators for replay.
            record_timing(request, "domain", 0)
    return await _editable_submission_by_id(db, submission_id)


@cfp_router.get(
    "/api/v1/admin/events/{event_id}/submissions",
    response_model=SubmissionList,
    operation_id="listEventSubmissions",
    tags=["submissions"],
)
async def list_submissions(
    event_id: str,
    request: Request,
) -> SubmissionList:
    event = row_mapping(
        await _db(request)
        .prepare(
            """SELECT organization_id FROM events
               WHERE id=?1 AND status != 'archived' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(event["organization_id"]), event_id),
        mutation=False,
    )
    result = (
        await _db(request)
        .prepare(
            """SELECT s.id,s.speaker_name,s.speaker_email,s.proposal_title,
                  s.proposal_abstract,s.answers_json,
                  COALESCE(d.decision,s.status) AS status,s.submitted_at_ms,s.version,
                  s.routed_category,s.routed_track,s.routed_review_queue
               FROM submissions s LEFT JOIN submission_decisions d ON d.submission_id=s.id
               WHERE s.organization_id=?1 AND s.event_id=?2
               ORDER BY s.submitted_at_ms DESC,s.id DESC LIMIT 100"""
        )
        .bind(event["organization_id"], event_id)
        .all()
    )
    data = []
    for row in result_rows(result):
        answers = json.loads(str(row.pop("answers_json")))
        contributors = result_rows(
            await _db(request)
            .prepare(
                """SELECT id,display_name,email,role,invitation_status,
                          invitation_expires_at_ms AS expires_at_ms
                   FROM submission_contributors WHERE submission_id=?1
                     AND invitation_status!='removed' ORDER BY display_name,id"""
            )
            .bind(row["id"])
            .all()
        )
        data.append(
            SubmissionView.model_validate({**row, "answers": answers, "co_speakers": contributors})
        )
    return SubmissionList(
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        data=data,
    )


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
            """SELECT f.id,f.event_id,f.version,f.slug,f.welcome_text,
                      f.schema_json,f.opens_at_ms,f.closes_at_ms,f.submission_limit,
                      f.success_title,f.success_message,f.redirect_to_portal,
                      e.name AS event_name,e.accent_color,e.logo_url,e.cover_image_url,
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
            """SELECT id, speaker_name, speaker_email, proposal_title,
                      proposal_abstract, answers_json, status, submitted_at_ms,version,
                      routed_category,routed_track,routed_review_queue
               FROM submissions WHERE id = ?1"""
        )
        .bind(submission_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    answers = json.loads(str(row.pop("answers_json")))
    contributors = result_rows(
        await db.prepare(
            """SELECT id,display_name,email,role,invitation_status,
                      invitation_expires_at_ms AS expires_at_ms
               FROM submission_contributors WHERE submission_id=?1
                 AND invitation_status!='removed' ORDER BY display_name,id"""
        )
        .bind(submission_id)
        .all()
    )
    return SubmissionView.model_validate({**row, "answers": answers, "co_speakers": contributors})


async def _editable_submission_by_id(db, submission_id: str) -> PrivateSubmissionView:
    submission = await _submission_by_id(db, submission_id)
    return PrivateSubmissionView.model_validate({**submission.model_dump(), "editable": True})


def _form_availability(row, submissions_received: int, now_ms: int) -> tuple[bool, str]:
    opens_at = int(row["opens_at_ms"]) if row.get("opens_at_ms") is not None else None
    closes_at = int(row["closes_at_ms"]) if row.get("closes_at_ms") is not None else None
    limit = int(row["submission_limit"]) if row.get("submission_limit") is not None else None
    if opens_at is not None and now_ms < opens_at:
        return False, "Applications have not opened yet."
    if closes_at is not None and now_ms >= closes_at:
        return False, "Applications are closed."
    if limit is not None and submissions_received >= limit:
        return False, "This Call for Proposals has reached its submission limit."
    return True, "Applications are open."


def _published_form_view(row, now_ms: int) -> PublishedFormView:
    schema = json.loads(str(row.pop("schema_json")))
    submissions_received = int(row.get("submissions_received") or 0)
    accepting, message = _form_availability(row, submissions_received, now_ms)
    return PublishedFormView.model_validate(
        {
            **row,
            **schema,
            "accent_color": row.get("accent_color") or "#3159d9",
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
    # A conventional field named for a routing destination should remain useful
    # without forcing an organizer to duplicate it as a routing rule. Explicit
    # rules always win; these values are only fallbacks.
    for destination in routed:
        if routed[destination] is not None:
            continue
        value = answers.get(destination)
        if isinstance(value, str) and value.strip():
            routed[destination] = value.strip()[:120]
    return routed


async def _active_event_track_names(
    db, *, organization_id: str, event_id: str
) -> set[str]:
    rows = result_rows(
        await db.prepare(
            """SELECT name FROM event_tracks
               WHERE organization_id=?1 AND event_id=?2 AND status='active'"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    return {str(row["name"]) for row in rows}


async def _validate_form_routing_tracks(
    db,
    *,
    organization_id: str,
    event_id: str,
    routing_rules: tuple[FormRoutingRule, ...],
) -> None:
    requested = {rule.track for rule in routing_rules if rule.track is not None}
    if not requested:
        return
    active = await _active_event_track_names(
        db, organization_id=organization_id, event_id=event_id
    )
    missing = sorted(requested - active)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Choose an active track from this event: {', '.join(missing)}",
        )


async def _validate_routed_track(
    db,
    *,
    organization_id: str,
    event_id: str,
    routing: dict[str, str | None],
) -> None:
    track = routing["track"]
    if track is None:
        return
    canonical_name = (
        await db.prepare(
            """SELECT name FROM event_tracks
               WHERE organization_id=?1 AND event_id=?2 AND status='active'
                 AND lower(name)=lower(?3)
               LIMIT 1"""
        )
        .bind(organization_id, event_id, track)
        .first("name")
    )
    if canonical_name is None:
        raise HTTPException(
            status_code=422,
            detail="The selected track is not available for this event.",
        )
    routing["track"] = str(canonical_name)


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
        found = (
            await db.prepare(
                """SELECT 1 AS found FROM upload_intents ui
               JOIN speaker_asset_versions av ON av.id=ui.asset_version_id
               JOIN speaker_assets a ON a.id=av.asset_id
               JOIN event_speakers es ON es.id=a.event_speaker_id
               JOIN people p ON p.organization_id=es.organization_id AND p.id=es.person_id
               WHERE ui.id=?1 AND ui.event_id=?2 AND p.user_id=?3 AND a.kind=?4
                 AND av.scan_state='clean' AND av.is_current=1 LIMIT 1"""
            )
            .bind(intent_id, event_id, user_id, expected_kind)
            .first("found")
        )
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
    canonical: dict[str, object] = {
        "speaker_name": body.speaker_name,
        "speaker_email": body.speaker_email,
        "proposal_title": body.proposal_title,
        "proposal_abstract": body.proposal_abstract,
    }
    known = {str(field.get("key")) for field in raw_fields if isinstance(field, dict)}
    if not set(body.answers) <= known:
        raise HTTPException(status_code=422)
    if any(key in body.answers and body.answers[key] != value for key, value in canonical.items()):
        raise HTTPException(status_code=422)
    values = {**body.answers, **canonical}
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
        _validate_form_field_value(field, value, enforce_required=True)


def _validate_draft_schema(schema: dict[str, object], answers: dict[str, object]) -> None:
    raw_fields = schema.get("fields", [])
    if not isinstance(raw_fields, list):
        raise HTTPException(status_code=409)
    known = {str(field.get("key")) for field in raw_fields if isinstance(field, dict)}
    if not set(answers) <= known:
        raise HTTPException(status_code=422)
    for field in raw_fields:
        if not isinstance(field, dict):
            raise HTTPException(status_code=409)
        _validate_form_field_value(
            field,
            answers.get(str(field.get("key", ""))),
            enforce_required=False,
        )


def _validate_form_field_value(
    field: dict[str, object], value: object, *, enforce_required: bool
) -> None:
    field_type = field.get("type")
    required = bool(field.get("required")) and enforce_required
    blank = (
        value is None
        or value == ""
        or value == []
        or (isinstance(value, str) and not value.strip())
    )
    if required and (blank or (field_type == "checkbox" and value is not True)):
        raise HTTPException(status_code=422)
    if blank:
        return
    choices = field.get("choices", [])
    if field_type in {"text", "email", "url", "phone", "textarea", "file", "image"}:
        if not isinstance(value, str):
            raise HTTPException(status_code=422)
        maximum = 5_000 if field_type == "textarea" else 500
        if len(value) > maximum:
            raise HTTPException(status_code=422)
    if field_type == "email":
        try:
            address = Address(addr_spec=str(value))
        except (IndexError, ValueError) as exc:
            raise HTTPException(status_code=422) from exc
        if (
            not address.username
            or not address.domain
            or "." not in address.domain
            or "\r" in str(value)
            or "\n" in str(value)
        ):
            raise HTTPException(status_code=422)
    elif field_type == "url":
        parsed = urlparse(str(value))
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(status_code=422)
    elif field_type == "phone":
        phone = str(value)
        if (
            len(phone) > 50
            or len(re.findall(r"\d", phone)) < 3
            or re.fullmatch(r"[0-9+().\- xX]+", phone) is None
        ):
            raise HTTPException(status_code=422)
    elif field_type == "select" and value not in choices:
        raise HTTPException(status_code=422)
    elif field_type == "multiselect":
        if (
            not isinstance(value, list)
            or len(value) > 100
            or any(
                not isinstance(item, str) or len(item) > 500 or item not in choices
                for item in value
            )
        ):
            raise HTTPException(status_code=422)
    elif field_type == "checkbox" and value not in (True, False):
        raise HTTPException(status_code=422)
    elif field_type in {"file", "image"} and not str(value).startswith("upload:"):
        raise HTTPException(status_code=422)

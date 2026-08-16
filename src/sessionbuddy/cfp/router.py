import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from email.headerregistry import Address
from html import escape
from time import perf_counter
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from sessionbuddy.communications.queue_publish import publish_committed_messages
from sessionbuddy.console import embedded_assets
from sessionbuddy.console.asset_response import content_addressed_asset
from sessionbuddy.observability import record_degradation, record_timing
from sessionbuddy.platform.auth import (
    authenticate_request,
    generate_token,
    hash_token,
    normalize_email,
)
from sessionbuddy.platform.auth.http import (
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
    execute_batch,
    result_rows,
    row_mapping,
    to_python,
)
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.rate_limits import RateLimitPolicy, enforce_rate_limit
from sessionbuddy.platform.signed_cursors import BOUNDED_ID, STRICT_INT, SignedCursorContract
from sessionbuddy.platform.storage import malware_scan_disabled, presign_r2_put
from sessionbuddy.speaker_operations.acceptance_tasks import (
    acceptance_speaker_tasks,
    append_acceptance_speaker_tasks,
    reconcile_accepted_submission_speakers,
)
from sessionbuddy.speaker_operations.asset_boundary import ScanJob
from sessionbuddy.speaker_operations.scanner_adapter import SignedScannerAdapter

from .availability import FormAvailability, form_availability, public_event_key
from .models import (
    AcceptedSubmissionParticipantsUpdate,
    AdminPublishedFormView,
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
    SpeakerProposalDraftList,
    SpeakerProposalDraftSummary,
    StagedUploadAuthorizationView,
    StagedUploadCompletionView,
    StagedUploadCreate,
    SubmissionCreate,
    SubmissionDraftUpsert,
    SubmissionDraftView,
    SubmissionList,
    SubmissionTitleMatch,
    SubmissionUpdate,
    SubmissionView,
    contributor_role_label,
)
from .staged_uploads import (
    MAX_ACTIVE_STAGED_BYTES,
    MAX_ACTIVE_STAGED_FILES,
    MAX_STAGED_AUTHORIZATIONS_PER_HOUR,
    STAGED_ASSET_RULES,
    STAGED_AUTHORIZATION_WINDOW_MS,
    STAGED_UPLOAD_TTL_MS,
    STAGED_UPLOAD_URL_TTL_MS,
    build_staged_claim,
    staged_references,
    staged_upload_token,
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


async def _guard_new_co_speaker_invitations(
    request: Request,
    *,
    actor_user_id: str,
    event_id: str,
    desired,
    submission_id: str | None = None,
) -> None:
    """Bound invitation email creation before any related submission mutation."""
    desired_emails = {normalize_email(item.email) for item in desired}
    if not desired_emails:
        return
    if submission_id is not None:
        active_rows = result_rows(
            await _db(request)
            .prepare(
                """SELECT normalized_email FROM submission_contributors
                   WHERE submission_id=?1 AND invitation_status!='removed'"""
            )
            .bind(submission_id)
            .all()
        )
        desired_emails.difference_update(
            str(row["normalized_email"]) for row in active_rows
        )
        if not desired_emails:
            return
    subject = f"{actor_user_id}:{event_id}:{_request_source(request)}"
    # Charge each new recipient, not merely each HTTP request: one request may
    # contain the form's full ten-address co-speaker allowance.
    for _normalized_email in desired_emails:
        await enforce_rate_limit(
            request,
            binding_name="PUBLIC_RATE_LIMITER",
            policy=RateLimitPolicy(
                "cfp.co_speaker.email", limit=20, window_seconds=3_600
            ),
            subject=subject,
        )


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
                      s.submitter_user_id,s.proposal_title,f.closes_at_ms,e.name AS event_name,
                      COALESCE((SELECT correction.corrected_decision
                        FROM submission_decision_corrections correction
                        WHERE correction.organization_id=s.organization_id
                          AND correction.event_id=s.event_id
                          AND correction.submission_id=s.id
                        ORDER BY correction.corrected_at_ms DESC,correction.id DESC LIMIT 1),
                        decision.decision) AS effective_decision
               FROM submission_contributors c
               JOIN submissions s ON s.id=c.submission_id
               JOIN call_for_speaker_forms f ON f.id=s.form_id
               JOIN events e ON e.id=s.event_id
               LEFT JOIN submission_decisions decision
                 ON decision.organization_id=s.organization_id
                AND decision.event_id=s.event_id AND decision.submission_id=s.id
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
    expected_submission_version: int | None = None,
    idempotency_record: IdempotencyRecord | None = None,
) -> None:
    db, now = _db(request), utc_now_ms()
    existing = result_rows(
        await db.prepare(
            """SELECT c.id,c.normalized_email,c.user_id,c.display_name,c.email,c.role,
                      c.invitation_status,c.invitation_version,
                      (SELECT es.id FROM event_speakers es
                         JOIN people p ON p.id=es.person_id
                        WHERE es.organization_id=c.organization_id
                          AND es.event_id=c.event_id AND p.user_id=c.user_id LIMIT 1)
                        AS event_speaker_id
               FROM submission_contributors c WHERE c.submission_id=?1"""
        )
        .bind(submission_id)
        .all()
    )
    desired_by_email = {normalize_email(item.email): item for item in desired}
    all_existing_by_email = {str(row["normalized_email"]): row for row in existing}
    active_existing_by_email = {
        email: row
        for email, row in all_existing_by_email.items()
        if row["invitation_status"] != "removed"
    }
    if (
        not desired_by_email
        and not active_existing_by_email
        and expected_submission_version is None
    ):
        # Nothing to reconcile; an empty command batch is not executable.
        return
    batch = CommandBatch(db)
    if idempotency_record is not None:
        batch.begin_idempotency(idempotency_record, now)
    if expected_submission_version is not None:
        batch.add_statement(
            db.prepare(
                """UPDATE submissions SET version=version+1,updated_at_ms=?1
                   WHERE id=?2 AND organization_id=?3 AND event_id=?4 AND version=?5"""
            ).bind(now, submission_id, organization_id, event_id, expected_submission_version)
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO submission_write_guards
                   (id,submission_id,applied_changes,created_at_ms)
                   VALUES(?1,?2,changes(),?3)"""
            ).bind(new_id(), submission_id, now)
        )
        batch.audit(
            AuditEvent(
                actor_type="user",
                actor_user_id=actor_user_id,
                action="submission.participants.correct",
                target_type="submission",
                target_id=submission_id,
                result="succeeded",
                correlation_id=request.state.request_id,
                occurred_at_ms=now,
                organization_id=organization_id,
                event_id=event_id,
                metadata={"participant_count": len(desired)},
            )
        )
    queued: list[str] = []
    for normalized, current in active_existing_by_email.items():
        contributor = desired_by_email.get(normalized)
        if contributor is not None:
            batch.add_statement(
                db.prepare(
                    """UPDATE submission_contributors SET display_name=?1,email=?2,role=?3,
                         updated_at_ms=?4 WHERE id=?5 AND invitation_status!='removed'"""
                ).bind(
                    contributor.display_name,
                    contributor.email,
                    contributor.role,
                    now,
                    current["id"],
                )
            )
            if current["user_id"] is not None:
                batch.add_statement(
                    db.prepare(
                        """UPDATE submission_speakers SET role=?1,snapshot_name=?2
                           WHERE submission_id=?3 AND role!='primary' AND event_speaker_id IN (
                             SELECT es.id FROM event_speakers es
                             JOIN people p ON p.id=es.person_id
                             WHERE es.event_id=?4 AND p.user_id=?5)"""
                    ).bind(
                        contributor.role,
                        contributor.display_name,
                        submission_id,
                        event_id,
                        current["user_id"],
                    )
                )
            if (
                str(current["display_name"]) != contributor.display_name
                or str(current["email"]) != contributor.email
                or str(current["role"]) != contributor.role
            ):
                batch.audit(
                    AuditEvent(
                        actor_type="user",
                        actor_user_id=actor_user_id,
                        action="submission.co_speaker.update",
                        target_type="submission_contributor",
                        target_id=str(current["id"]),
                        result="succeeded",
                        correlation_id=request.state.request_id,
                        occurred_at_ms=now,
                        organization_id=organization_id,
                        event_id=event_id,
                        metadata={"participant_role": contributor.role},
                    )
                )
            continue
        batch.add_statement(
            db.prepare(
                """DELETE FROM submission_speakers
                   WHERE submission_id=?1 AND role!='primary' AND event_speaker_id IN (
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
                           WHERE ss.event_id=?3 AND p.user_id=?4 AND ss.role='primary')
                         AND NOT EXISTS (
                           SELECT 1 FROM accepted_session_participants participant
                           JOIN accepted_sessions active
                             ON active.id=participant.accepted_session_id
                            AND active.organization_id=participant.organization_id
                            AND active.event_id=participant.event_id
                            AND active.lifecycle_status='active'
                           JOIN event_speakers es ON es.id=participant.event_speaker_id
                           JOIN people p ON p.id=es.person_id
                           WHERE participant.organization_id=?2 AND participant.event_id=?3
                             AND p.user_id=?4)"""
                ).bind(now, organization_id, event_id, current["user_id"], current["id"])
            )
        if current["event_speaker_id"] is not None:
            event_speaker_id = str(current["event_speaker_id"])
            batch.add_statement(
                db.prepare(
                    """UPDATE speaker_tasks SET state='waived',waived_at_ms=?1,
                         updated_at_ms=?1,version=version+1
                       WHERE organization_id=?2 AND event_id=?3 AND event_speaker_id=?4
                         AND task_type IN ('profile','headshot') AND state='open'
                         AND NOT EXISTS (
                           SELECT 1 FROM submission_speakers linked
                           JOIN accepted_sessions active
                             ON active.organization_id=linked.organization_id
                            AND active.event_id=linked.event_id
                            AND active.submission_id=linked.submission_id
                            AND active.lifecycle_status='active'
                           WHERE linked.organization_id=?2 AND linked.event_id=?3
                             AND linked.event_speaker_id=?4)
                         AND NOT EXISTS (
                           SELECT 1 FROM accepted_session_participants participant
                           JOIN accepted_sessions active
                             ON active.organization_id=participant.organization_id
                            AND active.event_id=participant.event_id
                            AND active.id=participant.accepted_session_id
                            AND active.lifecycle_status='active'
                           WHERE participant.organization_id=?2 AND participant.event_id=?3
                             AND participant.event_speaker_id=?4)"""
                ).bind(now, organization_id, event_id, event_speaker_id)
            )
            batch.add_statement(
                db.prepare(
                    """UPDATE event_speakers SET
                         selection_status=CASE WHEN (EXISTS (
                           SELECT 1 FROM submission_speakers linked
                           JOIN accepted_sessions active
                             ON active.organization_id=linked.organization_id
                            AND active.event_id=linked.event_id
                            AND active.submission_id=linked.submission_id
                            AND active.lifecycle_status='active'
                           WHERE linked.organization_id=?2 AND linked.event_id=?3
                             AND linked.event_speaker_id=?4)
                           OR EXISTS (
                             SELECT 1 FROM accepted_session_participants participant
                             JOIN accepted_sessions active
                               ON active.organization_id=participant.organization_id
                              AND active.event_id=participant.event_id
                              AND active.id=participant.accepted_session_id
                              AND active.lifecycle_status='active'
                             WHERE participant.organization_id=?2 AND participant.event_id=?3
                               AND participant.event_speaker_id=?4))
                           THEN 'accepted' ELSE 'submitted' END,
                         status=CASE WHEN status='withdrawn' THEN status
                           WHEN EXISTS (SELECT 1 FROM speaker_tasks task
                             WHERE task.organization_id=?2 AND task.event_id=?3
                               AND task.event_speaker_id=?4 AND task.state='open')
                           THEN 'onboarding' ELSE 'complete' END,
                         last_activity_at_ms=?1,updated_at_ms=?1
                       WHERE organization_id=?2 AND event_id=?3 AND id=?4"""
                ).bind(now, organization_id, event_id, event_speaker_id)
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
        if normalized in active_existing_by_email:
            continue
        prior = all_existing_by_email.get(normalized)
        contributor_id = str(prior["id"]) if prior is not None else new_id()
        invitation_version = (
            int(prior["invitation_version"]) + 1 if prior is not None else 1
        )
        token, message_id = generate_token(), new_id()
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
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?9,'pending',
                          ?10,?11,?9,1)
                   ON CONFLICT(submission_id,normalized_email) DO UPDATE SET
                     display_name=excluded.display_name,email=excluded.email,role=excluded.role,
                     invitation_status='pending',invitation_token_hash=excluded.invitation_token_hash,
                     invitation_expires_at_ms=excluded.invitation_expires_at_ms,
                     invited_at_ms=excluded.invited_at_ms,declined_at_ms=NULL,removed_at_ms=NULL,
                     invitation_version=submission_contributors.invitation_version+1,
                     updated_at_ms=excluded.updated_at_ms"""
            ).bind(
                contributor_id, organization_id, event_id, submission_id,
                contributor.display_name, contributor.email, normalized, contributor.role, now,
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
                f"Invitation to join {proposal_title}",
                f'<p>{escape(primary_name)} invited you to join as '
                f'{escape(contributor_role_label(contributor.role).lower())}.</p>'
                f'<p><a href="{escape(invitation_url)}">Respond to the invitation</a>.</p>',
                f"co-speaker:{contributor_id}:v{invitation_version}", now,
            )
        )
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
    if idempotency_record is not None:
        batch.complete_idempotency(
            idempotency_record,
            status=200,
            resource_type="submission",
            resource_id=submission_id,
            completed_at_ms=now,
        )
    await _execute(request, batch)
    await publish_committed_messages(request, queued)


async def _timed_first(request: Request, statement, column: str | None = None):
    started = perf_counter()
    try:
        # D1 treats an explicit JavaScript null as a requested column named
        # "null". Omit the argument entirely when the caller wants the row.
        return await statement.first() if column is None else await statement.first(column)
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)


async def _timed_all(request: Request, statement):
    started = perf_counter()
    try:
        return await statement.all()
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
    await require_document_persona(request, Persona.ORGANIZER)
    await require_document_event(request, event_id)
    return _product_page(request, "admin_programs.html")


@cfp_router.get(
    "/admin/events/{event_id}/submissions",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_submissions_page(event_id: str, request: Request) -> HTMLResponse:
    await require_document_persona(request, Persona.ORGANIZER)
    await require_document_event(request, event_id)
    return _product_page(request, "admin_submissions.html")


def _public_event_key(event_id: str) -> str:
    return public_event_key(event_id)


@cfp_router.get("/calls", response_class=HTMLResponse, include_in_schema=False)
async def public_calls_page() -> HTMLResponse:
    return HTMLResponse(_asset("open_calls.html"), headers={"Cache-Control": "no-store"})


@cfp_router.get(
    "/cfp/{event_key}/{slug}", response_class=HTMLResponse, include_in_schema=False
)
async def public_cfp_page(event_key: str, slug: str, request: Request) -> HTMLResponse:
    event_id = await (
        _db(request)
        .prepare(
            """SELECT event_id FROM call_for_speaker_forms
               WHERE slug=?1 AND status='published' LIMIT 1"""
        )
        .bind(slug)
        .first("event_id")
    )
    if event_id is None or _public_event_key(str(event_id)) != event_key.casefold():
        raise HTTPException(status_code=404)
    return HTMLResponse(_asset("public_cfp.html"), headers={"Cache-Control": "no-store"})


@cfp_router.get("/cfp/{slug}", response_class=Response, include_in_schema=False)
async def legacy_public_cfp_page(slug: str, request: Request) -> Response:
    try:
        db = _db(request)
    except HTTPException as exc:
        if exc.status_code == 503:
            return HTMLResponse(_asset("public_cfp.html"), headers={"Cache-Control": "no-store"})
        raise
    event_id = await (
        db
        .prepare(
            """SELECT event_id FROM call_for_speaker_forms
               WHERE slug=?1 AND status='published' LIMIT 1"""
        )
        .bind(slug)
        .first("event_id")
    )
    if event_id is None:
        raise HTTPException(status_code=404)
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(
        f"/cfp/{_public_event_key(str(event_id))}/{slug}{query}", status_code=308
    )


@cfp_router.get("/product/assets/product.css", response_class=Response, include_in_schema=False)
async def product_css() -> Response:
    return Response(_asset("product.css"), media_type="text/css")


@cfp_router.get(
    "/product/assets/admin-programs.js", response_class=Response, include_in_schema=False
)
async def admin_programs_js(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("admin_programs.js"), media_type="text/javascript"
    )


@cfp_router.get("/product/assets/public-cfp.js", response_class=Response, include_in_schema=False)
async def public_cfp_js() -> Response:
    return Response(
        _asset("public_cfp.js"),
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@cfp_router.get("/product/assets/open-calls.js", response_class=Response, include_in_schema=False)
async def open_calls_js() -> Response:
    return Response(_asset("open_calls.js"), media_type="text/javascript")


@cfp_router.get(
    "/product/assets/admin-submissions.js",
    response_class=Response,
    include_in_schema=False,
)
async def admin_submissions_js(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("admin_submissions.js"), media_type="text/javascript"
    )


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
    response_model=AdminPublishedFormView,
    status_code=201,
    operation_id="publishCallForSpeakersForm",
    tags=["forms"],
)
async def publish_form(
    event_id: str,
    request: Request,
    body: FormPublish,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AdminPublishedFormView:
    await authenticate_request(request)
    key = _idempotency_key(idempotency_key)
    db = _db(request)
    event = row_mapping(
        await db.prepare(
            """SELECT organization_id,starts_at_ms,status FROM events
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
    if str(event["status"]) != "active":
        raise HTTPException(
            status_code=409,
            detail="Activate the event before publishing its CFP.",
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
                    "co_speaker_limit": body.co_speaker_limit,
                    "description_html": body.description_html,
                    "important_dates": [date.model_dump() for date in body.important_dates],
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
    response_model=AdminPublishedFormView,
    operation_id="updatePublishedCallForSpeakersForm",
    tags=["forms"],
)
async def update_published_form(
    event_id: str,
    request: Request,
    body: FormUpdate,
) -> AdminPublishedFormView:
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
        raise HTTPException(
            status_code=409,
            headers={"X-Conflict-Type": "stale"},
        )
    slug_owner = await (
        db.prepare(
            """SELECT id FROM call_for_speaker_forms
               WHERE slug=?1 AND id!=?2 LIMIT 1"""
        )
        .bind(body.slug, current["id"])
        .first("id")
    )
    if slug_owner is not None:
        raise HTTPException(
            status_code=409,
            headers={"X-Conflict-Type": "slug"},
        )
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
            "co_speaker_limit": body.co_speaker_limit,
            "description_html": body.description_html,
            "important_dates": [date.model_dump() for date in body.important_dates],
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
                   confirmation_subject=?10,confirmation_body=?11,updated_at_ms=?12
               WHERE id=?13 AND event_id=?14 AND status='published' AND version=?15"""
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
            body.confirmation_subject,
            body.confirmation_body,
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
                      e.name AS event_name,e.starts_at_ms AS event_starts_at_ms,
                      e.ends_at_ms AS event_ends_at_ms,e.time_zone AS event_time_zone,
                      e.location AS event_location,e.delivery_mode AS event_delivery_mode,
                      e.website_url AS event_website_url,e.accent_color,e.logo_url,
                      e.cover_image_url,
                      COUNT(s.id) AS submissions_received
               FROM call_for_speaker_forms f
               JOIN events e ON e.organization_id=f.organization_id AND e.id=f.event_id
               LEFT JOIN submissions s ON s.form_id=f.id AND s.status='submitted'
               WHERE f.slug = ?1 AND f.status = 'published' AND e.status = 'active'
               GROUP BY f.id"""
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
            """SELECT f.id,f.organization_id,f.event_id,f.schema_json,f.version,
                     f.opens_at_ms,f.closes_at_ms,f.submission_limit,f.confirmation_subject,
                     f.confirmation_body
               FROM call_for_speaker_forms f
               JOIN events e ON e.organization_id=f.organization_id AND e.id=f.event_id
               WHERE f.slug=?1 AND f.status='published' AND e.status='active' LIMIT 1"""
        )
        .bind(slug)
        .first()
    )


async def _workspace_form(request: Request, slug: str):
    """Authorize the speaker-only proposal workspace for one published CFP.

    The workspace deliberately reuses the public form and own-submission
    contracts.  It is a document route only; all reads and writes still pass
    through the existing scoped CFP endpoints.
    """
    authenticated = await authenticate_request(request)
    form = await _form_context(_db(request), slug)
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
    return authenticated, form


async def _require_workspace_submission(
    request: Request, slug: str, submission_id: str
) -> None:
    """Make opaque workspace URLs return 404 outside the speaker's scope."""
    authenticated, form = await _workspace_form(request, slug)
    normalized_email = await (
        _db(request)
        .prepare("SELECT normalized_email FROM users WHERE id=?1 AND status='active' LIMIT 1")
        .bind(authenticated.actor.user_id)
        .first("normalized_email")
    )
    found = await (
        _db(request)
        .prepare(
            """SELECT 1 AS found FROM submissions s
               WHERE s.id=?1 AND s.form_id=?2 AND (
                 s.submitter_user_id=?3 OR EXISTS (
                   SELECT 1 FROM submission_contributors c
                   WHERE c.submission_id=s.id AND c.normalized_email=?4
                     AND c.invitation_status='accepted'
                 )
               ) LIMIT 1"""
        )
        .bind(submission_id, form["id"], authenticated.actor.user_id, normalized_email)
        .first("found")
    )
    if found is None:
        raise HTTPException(status_code=404)


@cfp_router.get(
    "/speaker/proposals/{slug}", response_class=RedirectResponse, include_in_schema=False
)
async def speaker_proposals_page(slug: str, request: Request) -> RedirectResponse:
    await _workspace_form(request, slug)
    return RedirectResponse("/speaker", status_code=303)


@cfp_router.get(
    "/speaker/proposals/{slug}/new", response_class=RedirectResponse, include_in_schema=False
)
async def new_speaker_proposal_page(slug: str, request: Request) -> RedirectResponse:
    await _workspace_form(request, slug)
    return RedirectResponse("/speaker", status_code=303)


@cfp_router.get(
    "/speaker/proposals/{slug}/{submission_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def speaker_proposal_editor_page(
    slug: str, submission_id: str, request: Request
) -> HTMLResponse:
    await _require_workspace_submission(request, slug, submission_id)
    return HTMLResponse(_asset("public_cfp.html"), headers={"Cache-Control": "no-store"})


@cfp_router.get(
    "/api/v1/speaker/proposal-drafts",
    response_model=SpeakerProposalDraftList,
    operation_id="listMyProposalDrafts",
    tags=["submissions"],
)
async def list_my_proposal_drafts(request: Request) -> SpeakerProposalDraftList:
    authenticated = await authenticate_request(request)
    db = _db(request)
    rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT d.id,d.form_id,d.event_id,d.answers_json,d.updated_at_ms,
                          f.slug AS form_slug,e.name AS event_name
                   FROM submission_drafts d
                   JOIN call_for_speaker_forms f
                     ON f.organization_id=d.organization_id AND f.event_id=d.event_id
                    AND f.id=d.form_id
                   JOIN events e
                     ON e.organization_id=d.organization_id AND e.id=d.event_id
                   WHERE d.user_id=?1
                   ORDER BY d.updated_at_ms DESC,d.id DESC LIMIT 100"""
            ).bind(authenticated.actor.user_id),
        )
    )
    drafts = []
    for row in rows:
        answers = json.loads(str(row["answers_json"]))
        event_id = str(row["event_id"])
        slug = str(row["form_slug"])
        drafts.append(
            SpeakerProposalDraftSummary(
                id=str(row["id"]),
                form_id=str(row["form_id"]),
                event_id=event_id,
                event_name=str(row["event_name"]),
                form_slug=slug,
                proposal_title=str(answers.get("proposal_title") or "Untitled proposal"),
                updated_at_ms=int(row["updated_at_ms"]),
                edit_path=f"/cfp/{public_event_key(event_id)}/{slug}",
            )
        )
    return SpeakerProposalDraftList(data=drafts)


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
                      COALESCE((SELECT c.corrected_decision
                        FROM submission_decision_corrections c
                        WHERE c.submission_id=s.id
                        ORDER BY c.corrected_at_ms DESC,c.id DESC LIMIT 1),
                        d.decision,s.status) AS status,s.submitted_at_ms,s.version,
                      s.routed_category,s.routed_track,s.routed_review_queue,
                      CASE WHEN s.submitter_user_id=?2
                                  AND s.status='submitted'
                                  AND d.submission_id IS NULL
                           THEN 1 ELSE 0 END AS editable,
                      CASE WHEN s.submitter_user_id=?2 THEN 1 ELSE 0 END
                        AS can_manage_participants
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
                    "can_manage_participants": bool(values["can_manage_participants"]),
                    "answers": answers,
                    "co_speakers": contributors,
                }
            )
        )
    return OwnedSubmissionList(data=data)


@cfp_router.get(
    "/api/v1/forms/{slug}/submissions/title-match",
    response_model=SubmissionTitleMatch | None,
    operation_id="findMyCallForSpeakersSubmissionByTitle",
    tags=["submissions"],
)
async def find_my_submission_by_title(
    slug: str, request: Request
) -> SubmissionTitleMatch | None:
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
    title = str(request.query_params.get("title") or "").strip()
    exclude_id = str(request.query_params.get("exclude_id") or "").strip()
    if not 1 <= len(title) <= 200:
        raise HTTPException(status_code=422)
    row = row_mapping(
        await db.prepare(
            """SELECT s.id,s.proposal_title,s.submitted_at_ms,
                      COALESCE((SELECT c.corrected_decision
                        FROM submission_decision_corrections c
                        WHERE c.submission_id=s.id
                        ORDER BY c.corrected_at_ms DESC,c.id DESC LIMIT 1),
                        d.decision,s.status) AS status
               FROM submissions s
               LEFT JOIN submission_decisions d ON d.submission_id=s.id
               WHERE s.form_id=?1 AND s.submitter_user_id=?2
                 AND lower(trim(s.proposal_title))=lower(trim(?3))
                 AND (?4='' OR s.id!=?4)
               ORDER BY s.updated_at_ms DESC,s.id DESC LIMIT 1"""
        )
        .bind(form["id"], authenticated.actor.user_id, title, exclude_id)
        .first()
    )
    return SubmissionTitleMatch.model_validate(row) if row is not None else None


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
                      c.invitation_expires_at_ms,s.proposal_title,e.name AS event_name,
                      COALESCE((SELECT correction.corrected_decision
                        FROM submission_decision_corrections correction
                        WHERE correction.organization_id=s.organization_id
                          AND correction.event_id=s.event_id
                          AND correction.submission_id=s.id
                        ORDER BY correction.corrected_at_ms DESC,correction.id DESC LIMIT 1),
                        (SELECT decision.decision FROM submission_decisions decision
                         WHERE decision.organization_id=s.organization_id
                           AND decision.event_id=s.event_id
                           AND decision.submission_id=s.id LIMIT 1)) AS effective_decision
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
            await db.prepare(
                """SELECT u.id,u.description,
                          EXISTS(SELECT 1 FROM user_headshots headshot
                                 WHERE headshot.user_id=u.id) AS has_account_headshot
                   FROM users u WHERE u.normalized_email=?1 LIMIT 1"""
            )
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
                     revoked_at_ms=NULL,
                     role=CASE WHEN organization_memberships.status='revoked'
                          THEN 'member' ELSE organization_memberships.role END,
                     version=version+1,updated_at_ms=excluded.updated_at_ms"""
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
                """SELECT id,biography FROM people
                   WHERE organization_id=?1 AND user_id=?2 LIMIT 1"""
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
                """SELECT es.id,es.status,
                          EXISTS(SELECT 1 FROM speaker_assets asset
                            JOIN speaker_asset_versions version ON version.asset_id=asset.id
                              AND version.is_current=1 AND version.scan_state='clean'
                            WHERE asset.organization_id=es.organization_id
                              AND asset.event_id=es.event_id
                              AND asset.event_speaker_id=es.id AND asset.kind='headshot')
                            AS has_event_headshot,
                          EXISTS(SELECT 1 FROM speaker_tasks task
                            WHERE task.organization_id=es.organization_id
                              AND task.event_id=es.event_id AND task.event_speaker_id=es.id
                              AND task.task_type='profile' AND task.state='open')
                            AS has_profile_task,
                          EXISTS(SELECT 1 FROM speaker_tasks task
                            WHERE task.organization_id=es.organization_id
                              AND task.event_id=es.event_id AND task.event_speaker_id=es.id
                              AND task.task_type='headshot' AND task.state='open')
                            AS has_headshot_task
                   FROM event_speakers es
                   WHERE es.organization_id=?1 AND es.event_id=?2 AND es.person_id=?3 LIMIT 1"""
            )
            .bind(row["organization_id"], row["event_id"], person_id)
            .first()
        )
        speaker_id = str(speaker["id"]) if speaker is not None else new_id()
        accepted_submission = str(row["effective_decision"] or "") == "accepted"
        if speaker is None:
            batch.add_statement(
                db.prepare(
                    """INSERT INTO event_speakers
                       (id,organization_id,event_id,person_id,status,accepted_at_ms,
                        last_activity_at_ms,created_at_ms,updated_at_ms,selection_status)
                       VALUES(?1,?2,?3,?4,'onboarding',?5,?5,?5,?5,?6)"""
                ).bind(
                    speaker_id,
                    row["organization_id"],
                    row["event_id"],
                    person_id,
                    now,
                    "accepted" if accepted_submission else "submitted",
                )
            )
        elif accepted_submission:
            batch.add_statement(
                db.prepare(
                    """UPDATE event_speakers SET selection_status='accepted',
                         accepted_at_ms=COALESCE(accepted_at_ms,?1),last_activity_at_ms=?1,
                         updated_at_ms=?1 WHERE id=?2"""
                ).bind(now, speaker_id)
            )
        batch.add_statement(
            db.prepare(
                """INSERT INTO submission_speakers
                   (id,organization_id,event_id,submission_id,event_speaker_id,role,
                    snapshot_name,created_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8)
                   ON CONFLICT(organization_id,event_id,submission_id,event_speaker_id)
                   DO UPDATE SET role=excluded.role,snapshot_name=excluded.snapshot_name"""
            ).bind(
                new_id(), row["organization_id"], row["event_id"], row["submission_id"],
                speaker_id, row["role"], row["display_name"], now,
            )
        )
        if accepted_submission and str((speaker or {}).get("status") or "") != "withdrawn":
            task_context = {
                "biography": (
                    (person or {}).get("biography")
                    or (existing_user or {}).get("description")
                    or ""
                ),
                "has_account_headshot": bool(
                    (existing_user or {}).get("has_account_headshot")
                ),
                "has_event_headshot": bool((speaker or {}).get("has_event_headshot")),
                "has_profile_task": bool((speaker or {}).get("has_profile_task")),
                "has_headshot_task": bool((speaker or {}).get("has_headshot_task")),
                "has_slides_task": False,
            }
            tasks = acceptance_speaker_tasks(task_context, include_slides=False)
            batch.add_statement(
                db.prepare(
                    """UPDATE event_speakers SET status=?1,updated_at_ms=?2
                       WHERE id=?3 AND status!='withdrawn'"""
                ).bind("onboarding" if tasks else "complete", now, speaker_id)
            )
            append_acceptance_speaker_tasks(
                batch,
                db,
                task_context,
                organization_id=str(row["organization_id"]),
                event_id=str(row["event_id"]),
                submission_id=str(row["submission_id"]),
                event_speaker_id=speaker_id,
                now=now,
                include_slides=False,
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
    if response_status == "accepted":
        try:
            await reconcile_accepted_submission_speakers(
                db,
                organization_id=str(row["organization_id"]),
                event_id=str(row["event_id"]),
                submission_id=str(row["submission_id"]),
                now=now,
                execute_batch=lambda followup: _execute(request, followup),
            )
        except HTTPException:
            # The invitation response is already committed. Do not claim it
            # failed merely because the defensive convergence pass did; record
            # the repair gap for operators and preserve the truthful outcome.
            record_degradation(request, "accepted_participant_reconciliation_failed")
    return CoSpeakerInvitationView(
        id=str(row["id"]),
        submission_id=str(row["submission_id"]),
        display_name=str(row["display_name"]),
        email=str(row["email"]),
        role=str(row["role"]),
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
    await enforce_rate_limit(
        request,
        binding_name="PUBLIC_RATE_LIMITER",
        policy=RateLimitPolicy("cfp.co_speaker.email", limit=20, window_seconds=3_600),
        subject=(
            f"{authenticated.actor.user_id}:{row['event_id']}:{_request_source(request)}"
        ),
    )
    db, now = _db(request), utc_now_ms()
    token, message_id = generate_token(), new_id()
    invitation_deadline = (
        None if str(row["effective_decision"] or "") == "accepted" else row["closes_at_ms"]
    )
    expires_at = _co_speaker_expiry(now, invitation_deadline)
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
            f"Invitation to join {row['proposal_title']}",
            "<p>You were invited to join as "
            + escape(contributor_role_label(str(row["role"])).lower())
            + " "
            + f'at {escape(str(row["event_name"]))}.</p>'
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
    await publish_committed_messages(request, [message_id])
    view = CoSpeakerView(
        id=co_speaker_id,
        display_name=str(row["display_name"]),
        email=str(row["email"]),
        role=str(row["role"]),
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
    if str(row["effective_decision"] or "") == "accepted":
        raise HTTPException(
            status_code=409,
            detail=(
                "Use the accepted proposal participant editor so participant "
                "removal and onboarding are reconciled together."
            ),
            headers={"X-Conflict-Type": "decision"},
        )
    db, now = _db(request), utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """DELETE FROM submission_speakers
               WHERE submission_id=?1 AND role!='primary' AND event_speaker_id IN (
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


def _bucket(request: Request):
    bucket = getattr(request.scope.get("env"), "ASSETS", None)
    if bucket is None:
        raise HTTPException(status_code=503)
    return bucket


async def _open_staged_form(db, form_id: str, now: int):
    form = row_mapping(
        await db.prepare(
            """SELECT id, organization_id, event_id FROM call_for_speaker_forms
               WHERE id=?1 AND status='published'
                 AND (opens_at_ms IS NULL OR opens_at_ms<=?2)
                 AND (closes_at_ms IS NULL OR closes_at_ms>?2) LIMIT 1"""
        )
        .bind(form_id, now)
        .first()
    )
    if form is None:
        raise HTTPException(status_code=404)
    return form


async def _staged_authorization_view(
    request: Request, staged_id: str, token: str
) -> StagedUploadAuthorizationView:
    row = row_mapping(
        await _db(request)
        .prepare(
            """SELECT id, object_key, content_type, byte_size, created_at_ms
               FROM cfp_staged_assets WHERE id=?1 LIMIT 1"""
        )
        .bind(staged_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    environment = _env(request)
    content_type = str(row["content_type"])
    upload_expires = utc_now_ms() + STAGED_UPLOAD_URL_TTL_MS
    if getattr(environment, "APP_ENV", "production") == "local":
        upload_url = f"/api/v1/cfp/uploads/{staged_id}/content?token={token}"
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
            content_length=int(row["byte_size"]),
            now=datetime.now(UTC),
            expires_seconds=STAGED_UPLOAD_URL_TTL_MS // 1000,
        )
    return StagedUploadAuthorizationView(
        staged_id=staged_id,
        upload_url=upload_url,
        headers=headers,
        expires_at_ms=upload_expires,
    )


@cfp_router.post(
    "/api/v1/cfp/forms/{form_id}/upload-authorizations",
    response_model=StagedUploadAuthorizationView,
    status_code=201,
    operation_id="authorizeCfpStagedUpload",
    tags=["submissions"],
)
async def authorize_cfp_staged_upload(
    form_id: str,
    request: Request,
    body: StagedUploadCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StagedUploadAuthorizationView:
    """Stage a CFP file answer before the submission (and speaker graph) exist.

    Requires only an authenticated session: no person, speaker, or membership
    rows are read or written here, so a revoked or brand-new user gains no
    access through this route.
    """
    key = _idempotency_key(idempotency_key)
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    await enforce_rate_limit(
        request,
        binding_name="CFP_UPLOAD_AUTH_RATE_LIMITER",
        policy=RateLimitPolicy("cfp.staged_upload.authorize", limit=10, window_seconds=60),
        subject=f"{authenticated.actor.user_id}:{form_id}:{_request_source(request)}",
    )
    db = _db(request)
    now = utc_now_ms()
    form = await _open_staged_form(db, form_id, now)
    allowed_types, max_bytes = STAGED_ASSET_RULES[body.kind]
    if body.content_type not in allowed_types or body.byte_size > max_bytes:
        raise HTTPException(status_code=400)
    route = "POST /api/v1/cfp/forms/{form_id}/upload-authorizations"
    fingerprint = _fingerprint(body)
    replay = await _find_replay(db, authenticated.actor.user_id, route, key)
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        staged_id = str(replay["response_resource_id"])
        return await _staged_authorization_view(
            request, staged_id, staged_upload_token(secret(request, "UPLOAD_HMAC_KEY"), staged_id)
        )
    # Claimed rows leave the active-usage quota below, so also cap how many
    # authorizations a user can create per form per hour regardless of status —
    # a submit-and-restage loop cannot grow R2 storage unboundedly.
    created_in_window = int(
        await db.prepare(
            """SELECT COUNT(*) AS created_count FROM cfp_staged_assets
               WHERE form_id=?1 AND user_id=?2 AND created_at_ms>?3"""
        )
        .bind(form_id, authenticated.actor.user_id, now - STAGED_AUTHORIZATION_WINDOW_MS)
        .first("created_count")
        or 0
    )
    if created_in_window >= MAX_STAGED_AUTHORIZATIONS_PER_HOUR:
        raise HTTPException(status_code=429, headers={"Retry-After": "3600"})
    usage = row_mapping(
        await db.prepare(
            """SELECT COUNT(*) AS staged_count, COALESCE(SUM(byte_size), 0) AS staged_bytes
               FROM cfp_staged_assets
               WHERE form_id=?1 AND user_id=?2 AND expires_at_ms>?3
                 AND status IN ('pending_upload', 'uploaded', 'scanning', 'staged')"""
        )
        .bind(form_id, authenticated.actor.user_id, now)
        .first()
    )
    if usage is not None and (
        int(usage["staged_count"]) >= MAX_ACTIVE_STAGED_FILES
        or int(usage["staged_bytes"]) + body.byte_size > MAX_ACTIVE_STAGED_BYTES
    ):
        raise HTTPException(status_code=429)
    staged_id = new_id()
    token = staged_upload_token(secret(request, "UPLOAD_HMAC_KEY"), staged_id)
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        organization_id=str(form["organization_id"]),
        event_id=str(form["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO cfp_staged_assets
               (id, organization_id, event_id, form_id, user_id, kind, object_key,
                original_filename, content_type, byte_size, checksum_sha256,
                upload_token_hash, status, expires_at_ms, created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12,
                       'pending_upload', ?13, ?14, ?14)"""
        ).bind(
            staged_id,
            form["organization_id"],
            form["event_id"],
            form_id,
            authenticated.actor.user_id,
            body.kind,
            f"staged/{staged_id}/{new_id()}",
            body.filename,
            body.content_type,
            body.byte_size,
            bytes.fromhex(body.checksum_sha256),
            hash_token(token),
            now + STAGED_UPLOAD_TTL_MS,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            organization_id=str(form["organization_id"]),
            event_id=str(form["event_id"]),
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="cfp.staged_upload.authorize",
            target_type="cfp_staged_asset",
            target_id=staged_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={"kind": body.kind},
        )
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="cfp_staged_asset",
        resource_id=staged_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return await _staged_authorization_view(request, staged_id, token)


@cfp_router.put(
    "/api/v1/cfp/uploads/{staged_id}/content",
    status_code=204,
    include_in_schema=False,
)
async def local_staged_upload_content(staged_id: str, request: Request, token: str) -> None:
    if getattr(_env(request), "APP_ENV", "production") != "local":
        raise HTTPException(status_code=404)
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT object_key, content_type, byte_size, checksum_sha256,
                      upload_token_hash, status, expires_at_ms
               FROM cfp_staged_assets WHERE id=?1 LIMIT 1"""
        )
        .bind(staged_id)
        .first()
    )
    if (
        row is None
        or str(row["status"]) != "pending_upload"
        or int(row["expires_at_ms"]) < utc_now_ms()
        or not hmac.compare_digest(_blob(row["upload_token_hash"]), hash_token(token))
    ):
        raise HTTPException(status_code=404)
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    content_length = request.headers.get("content-length")
    if content_type != str(row["content_type"]):
        raise HTTPException(status_code=400)
    if content_length is not None and int(content_length) != int(row["byte_size"]):
        raise HTTPException(status_code=400)
    body = await request.body()
    if len(body) != int(row["byte_size"]):
        raise HTTPException(status_code=400)
    if not hmac.compare_digest(hashlib.sha256(body).digest(), _blob(row["checksum_sha256"])):
        raise HTTPException(status_code=400)
    await _bucket(request).put(str(row["object_key"]), body)


@cfp_router.post(
    "/api/v1/cfp/forms/{form_id}/upload-authorizations/{staged_id}/complete",
    response_model=StagedUploadCompletionView,
    operation_id="completeCfpStagedUpload",
    tags=["submissions"],
)
async def complete_cfp_staged_upload(
    form_id: str,
    staged_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StagedUploadCompletionView:
    _idempotency_key(idempotency_key)
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    # Completion is polled (~1/s per in-flight upload while a scan runs), so it
    # gets its own, much more generous bucket than authorization.
    await enforce_rate_limit(
        request,
        binding_name="CFP_UPLOAD_POLL_RATE_LIMITER",
        policy=RateLimitPolicy("cfp.staged_upload.complete", limit=240, window_seconds=60),
        subject=f"{authenticated.actor.user_id}:{form_id}:{_request_source(request)}",
    )
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT id, organization_id, event_id, object_key, content_type, byte_size,
                      checksum_sha256, status, expires_at_ms
               FROM cfp_staged_assets
               WHERE id=?1 AND form_id=?2 AND user_id=?3 LIMIT 1"""
        )
        .bind(staged_id, form_id, authenticated.actor.user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    status = str(row["status"])
    if status in {"staged", "rejected", "claimed", "scanning"}:
        return StagedUploadCompletionView(staged_id=staged_id, state=status)
    now = utc_now_ms()
    if status == "uploaded":
        await _claim_and_enqueue_staged_scan(request, row)
        return StagedUploadCompletionView(staged_id=staged_id, state="uploaded")
    if int(row["expires_at_ms"]) < now:
        raise HTTPException(status_code=404)
    stored = await _bucket(request).head(str(row["object_key"]))
    if stored is None or int(stored.size) != int(row["byte_size"]):
        raise HTTPException(status_code=409)
    environment = _env(request)
    local = getattr(environment, "APP_ENV", "production") == "local"
    scan_bypassed = malware_scan_disabled(environment)
    if scan_bypassed:
        state, code = "staged", "development_bypass"
    elif local:
        stored_body = await _bucket(request).get(str(row["object_key"]))
        if stored_body is None:
            raise HTTPException(status_code=409)
        job = _staged_scan_job(row)
        try:
            provider_result = await SignedScannerAdapter(environment).scan(stored_body, job=job)
        except Exception as exc:
            raise HTTPException(status_code=503) from exc
        if provider_result.verdict == "error":
            raise HTTPException(status_code=503)
        if provider_result.verdict == "clean":
            state, code = "staged", "clamav_clean"
        else:
            state, code = "rejected", provider_result.signature_code or "malware_detected"
    else:
        state, code = "uploaded", None
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE cfp_staged_assets SET status=?1, scan_result_code=?2, updated_at_ms=?3
               WHERE id=?4 AND status='pending_upload'"""
        ).bind(state, code, now, staged_id)
    )
    batch.audit(
        AuditEvent(
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            actor_user_id=authenticated.actor.user_id,
            actor_type="user",
            action="cfp.staged_upload.complete",
            target_type="cfp_staged_asset",
            target_id=staged_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            metadata={
                "state": state,
                "malware_scan_bypassed": int(scan_bypassed),
                "local_scan": int(local and not scan_bypassed),
            },
        )
    )
    await _execute(request, batch)
    if state == "uploaded":
        await _claim_and_enqueue_staged_scan(request, row)
    return StagedUploadCompletionView(staged_id=staged_id, state=state)


def _staged_scan_job(row) -> "ScanJob":
    return ScanJob(
        schema_version=1,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        asset_version_id=str(row["id"]),
        generation=1,
        checksum_sha256=_blob(row["checksum_sha256"]),
        job_id=str(row["id"]),
    )


async def _claim_and_enqueue_staged_scan(request: Request, row) -> None:
    """Atomically let one completion request publish the scan job.

    The short-lived claim is cleared only when queue publication fails, making
    a later poll retryable while preventing concurrent polls from duplicating
    a successfully published job.

    Completion is a ~1/s polling endpoint, so the poll loop itself is the
    retry driver for a transient publish failure: release the claim, flag the
    degradation, and answer with the true committed state ('uploaded') instead
    of a 503 the browser would surface as a failed upload. A missing queue
    binding is a deployment misconfiguration no amount of polling can heal, so
    that still fails loudly.
    """
    queue = getattr(_env(request), "ASSET_SCAN_QUEUE", None)
    if queue is None:
        raise HTTPException(status_code=503)
    claim = f"enqueue:{new_id()}"
    now = utc_now_ms()
    stale_before = now - 5 * 60 * 1000
    claimed = row_mapping(
        await _db(request)
        .prepare(
            """UPDATE cfp_staged_assets SET scan_result_code=?1, updated_at_ms=?2
               WHERE id=?3 AND status='uploaded' AND (
                 scan_result_code IS NULL OR
                 (scan_result_code LIKE 'enqueue:%' AND updated_at_ms < ?4)
               )
               RETURNING id"""
        )
        .bind(claim, now, row["id"], stale_before)
        .first()
    )
    if claimed is None:
        return
    try:
        await queue.send(_staged_scan_job(row).to_message())
    except Exception:
        await (
            _db(request)
            .prepare(
                """UPDATE cfp_staged_assets SET scan_result_code=NULL
                   WHERE id=?1 AND status='uploaded' AND scan_result_code=?2"""
            )
            .bind(row["id"], claim)
            .run()
        )
        record_degradation(request, "asset_scan_queue_publish_failed")


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
            """SELECT s.organization_id,s.event_id,s.form_id,s.status,s.version,s.proposal_title,
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
    if int(row["version"]) != body.version:
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
        raise HTTPException(
            status_code=409,
            detail="A final decision has been recorded, so this proposal is read-only.",
            headers={"X-Conflict-Type": "decision"},
        )
    normalized_email = (
        await db.prepare("SELECT normalized_email FROM users WHERE id=?1")
        .bind(authenticated.actor.user_id)
        .first("normalized_email")
    )
    if normalized_email is None or normalize_email(body.speaker_email) != str(normalized_email):
        raise HTTPException(status_code=422)
    await _guard_new_co_speaker_invitations(
        request,
        actor_user_id=authenticated.actor.user_id,
        event_id=str(row["event_id"]),
        desired=body.co_speakers,
        submission_id=submission_id,
    )
    schema = json.loads(str(row["schema_json"]))
    # Preserve previously uploaded file answers: an edit that does not
    # re-attach a file must never clobber the stored upload reference.
    stored_answers = json.loads(
        str(
            await db.prepare("SELECT answers_json FROM submissions WHERE id=?1")
            .bind(submission_id)
            .first("answers_json")
            or "{}"
        )
    )
    for field in schema.get("fields", []):
        if isinstance(field, dict) and field.get("type") in {"file", "image"}:
            key = str(field.get("key", ""))
            if key and not body.answers.get(key) and stored_answers.get(key):
                body.answers[key] = stored_answers[key]
    _validate_submission_schema(schema, body)
    await _validate_upload_answers(
        db,
        schema,
        body.answers,
        form_id=str(row["form_id"]),
        event_id=str(row["event_id"]),
        user_id=authenticated.actor.user_id,
        canonical={
            "speaker_name": body.speaker_name,
            "speaker_email": body.speaker_email,
            "proposal_title": body.proposal_title,
            "proposal_abstract": body.proposal_abstract,
        },
    )
    staged_ids = staged_references(schema, body.answers)
    staged_claim = None
    if staged_ids:
        primary_speaker_id = (
            await db.prepare(
                """SELECT event_speaker_id FROM submission_speakers
                   WHERE submission_id=?1 AND role='primary' LIMIT 1"""
            )
            .bind(submission_id)
            .first("event_speaker_id")
        )
        if primary_speaker_id is None:
            raise HTTPException(status_code=409)
        staged_claim = await build_staged_claim(
            db,
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            event_speaker_id=str(primary_speaker_id),
            submission_id=submission_id,
            form_id=str(row["form_id"]),
            user_id=authenticated.actor.user_id,
            staged_ids=staged_ids,
            now=now,
        )
        body.answers = {
            key: staged_claim.answer_rewrites.get(value, value)
            if isinstance(value, str)
            else value
            for key, value in body.answers.items()
        }
    routing = _route_submission(schema, body.answers)
    await _validate_routed_track(
        db,
        organization_id=str(row["organization_id"]),
        event_id=str(row["event_id"]),
        routing=routing,
    )
    update_statement = db.prepare(
        """UPDATE submissions SET proposal_title=?1,proposal_abstract=?2,
              speaker_name=?3,speaker_email=?4,answers_json=?5,
              routed_category=?6,routed_track=?7,routed_review_queue=?8,
              version=version+1,updated_at_ms=?9
       WHERE id=?10 AND submitter_user_id=?11 AND status='submitted' AND version=?12"""
    ).bind(
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
    # Two tabs editing the same proposal can race: the loser's optimistic
    # UPDATE matches zero rows, yet any statements after it in the batch would
    # still run — claiming a staged file against answers that were never
    # stored. The write guard records changes() from the UPDATE immediately
    # after it, and its CHECK (applied_changes = 1) aborts the whole D1 batch
    # when the UPDATE did not win, rolling every later statement back with it.
    # The guard runs in BOTH branches so a lost race is always detected inside
    # the transaction itself, not by a read-back that a third concurrent
    # request could skew.
    write_guard = db.prepare(
        """INSERT INTO submission_write_guards
           (id,submission_id,applied_changes,created_at_ms)
           VALUES(?1,?2,changes(),?3)"""
    ).bind(new_id(), submission_id, now)
    claim_statements = staged_claim.statements if staged_claim is not None else []
    try:
        await execute_batch(db, [update_statement, write_guard, *claim_statements])
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
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
    "/api/v1/forms/{slug}/submissions/{submission_id}/participants",
    response_model=PrivateSubmissionView,
    operation_id="updateAcceptedSubmissionParticipants",
    tags=["submissions"],
)
async def update_accepted_submission_participants(
    slug: str,
    submission_id: str,
    body: AcceptedSubmissionParticipantsUpdate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> PrivateSubmissionView:
    """Correct participants on an accepted proposal without reopening its answers."""
    authenticated = await authenticate_request(request)
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT s.organization_id,s.event_id,s.proposal_title,s.speaker_name,
                      s.speaker_email,s.submitter_user_id,s.version,f.schema_json,
                      COALESCE((SELECT c.corrected_decision
                        FROM submission_decision_corrections c
                        WHERE c.organization_id=s.organization_id AND c.event_id=s.event_id
                          AND c.submission_id=s.id
                        ORDER BY c.corrected_at_ms DESC,c.id DESC LIMIT 1),d.decision)
                        AS effective_decision
               FROM submissions s
               JOIN call_for_speaker_forms f ON f.id=s.form_id AND f.slug=?2
               LEFT JOIN submission_decisions d ON d.organization_id=s.organization_id
                 AND d.event_id=s.event_id AND d.submission_id=s.id
               WHERE s.id=?1 LIMIT 1"""
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
    if str(row["effective_decision"] or "") != "accepted":
        raise HTTPException(
            status_code=409,
            detail="Participants can be changed only after this proposal is accepted.",
            headers={"X-Conflict-Type": "decision"},
        )
    key = _idempotency_key(idempotency_key)
    route = "PATCH /api/v1/forms/{slug}/submissions/{submission_id}/participants"
    fingerprint = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), separators=(",", ":"), sort_keys=True).encode()
    ).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND state='completed' LIMIT 1"""
        )
        .bind(
            authenticated.actor.user_id,
            route,
            hashlib.sha256(key.encode()).digest(),
        )
        .first()
    )
    if replay is not None:
        if bytes(to_python(replay["request_fingerprint"])) != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="This retry key was already used for a different participant list.",
            )
        return await _private_submission_by_id(db, submission_id, editable=False)
    if int(row["version"]) != body.version:
        raise HTTPException(
            status_code=409,
            detail="This participant list changed. Reload it and try again.",
        )
    schema = json.loads(str(row["schema_json"]))
    limit = int(schema.get("co_speaker_limit", 3))
    if len(body.co_speakers) > limit:
        raise HTTPException(
            status_code=422,
            detail=f"This form allows up to {limit} additional participants.",
        )
    primary_email = normalize_email(str(row["speaker_email"]))
    participant_emails = [normalize_email(item.email) for item in body.co_speakers]
    if primary_email in participant_emails or len(participant_emails) != len(
        set(participant_emails)
    ):
        raise HTTPException(
            status_code=422, detail="Participant email addresses must be unique."
        )
    await _guard_new_co_speaker_invitations(
        request,
        actor_user_id=authenticated.actor.user_id,
        event_id=str(row["event_id"]),
        desired=body.co_speakers,
        submission_id=submission_id,
    )
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
    try:
        await _reconcile_co_speakers(
            request,
            submission_id=submission_id,
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            invitation_deadline_ms=None,
            proposal_title=str(row["proposal_title"]),
            primary_name=str(row["speaker_name"]),
            desired=body.co_speakers,
            actor_user_id=authenticated.actor.user_id,
            expected_submission_version=body.version,
            idempotency_record=record,
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        completed = row_mapping(
            await db.prepare(
                """SELECT request_fingerprint FROM idempotency_records
                   WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                     AND state='completed' LIMIT 1"""
            )
            .bind(
                authenticated.actor.user_id,
                route,
                hashlib.sha256(key.encode()).digest(),
            )
            .first()
        )
        if (
            completed is not None
            and bytes(to_python(completed["request_fingerprint"])) == fingerprint
        ):
            return await _private_submission_by_id(db, submission_id, editable=False)
        raise HTTPException(
            status_code=409,
            detail="This participant list changed. Reload it and try again.",
        ) from exc
    return await _private_submission_by_id(db, submission_id, editable=False)


@cfp_router.post(
    "/api/v1/forms/{slug}/submissions/{submission_id}/withdraw",
    response_model=PrivateSubmissionView,
    operation_id="withdrawMyCallForSpeakersSubmission",
    tags=["submissions"],
)
async def withdraw_submission(
    slug: str,
    submission_id: str,
    request: Request,
) -> PrivateSubmissionView:
    authenticated = await authenticate_request(request)
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT s.organization_id,s.event_id,s.status,s.submitter_user_id
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
    if row["status"] == "withdrawn":
        return await _private_submission_by_id(db, submission_id, editable=False)
    if row["status"] != "submitted":
        raise HTTPException(status_code=409, detail="Only a submitted proposal can be withdrawn.")
    blocked = await db.prepare(
        """SELECT 1 AS found
           WHERE EXISTS(SELECT 1 FROM evaluation_assignments WHERE submission_id=?1)
              OR EXISTS(SELECT 1 FROM evaluations e JOIN evaluation_assignments a
                        ON a.id=e.assignment_id WHERE a.submission_id=?1)
              OR EXISTS(SELECT 1 FROM submission_decisions WHERE submission_id=?1)
           LIMIT 1"""
    ).bind(submission_id).first("found")
    if blocked is not None:
        raise HTTPException(
            status_code=409,
            detail="This proposal can no longer be withdrawn because review has started.",
        )
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE submissions SET status='withdrawn',version=version+1,updated_at_ms=?1
               WHERE id=?2 AND submitter_user_id=?3 AND status='submitted'
                 AND NOT EXISTS(SELECT 1 FROM evaluation_assignments
                                WHERE submission_id=?2)
                 AND NOT EXISTS(SELECT 1 FROM submission_decisions
                                WHERE submission_id=?2)"""
        ).bind(now, submission_id, authenticated.actor.user_id)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO submission_write_guards
               (id,submission_id,applied_changes,created_at_ms)
               VALUES(?1,?2,changes(),?3)"""
        ).bind(new_id(), submission_id, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action="submission.withdraw",
            target_type="submission",
            target_id=submission_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            metadata={},
        )
    )
    try:
        await _execute(request, batch)
    except PersistenceError as exc:
        raise HTTPException(
            status_code=409,
            detail="This proposal can no longer be withdrawn because review has started.",
        ) from exc
    return await _private_submission_by_id(db, submission_id, editable=False)


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
    authenticated = await authenticate_request(request)
    guard_mutation(request, authenticated.session_id)
    await enforce_rate_limit(
        request,
        binding_name="PUBLIC_RATE_LIMITER",
        policy=RateLimitPolicy("public.submit", limit=50, window_seconds=60),
        subject=f"{authenticated.actor.user_id}:{_request_source(request)}",
    )
    db = _db(request)
    form = row_mapping(
        await db.prepare(
            """SELECT f.id, f.organization_id, f.event_id, f.slug, f.version, f.schema_json,
                      f.opens_at_ms, f.closes_at_ms, f.submission_limit,
                      f.confirmation_subject, f.confirmation_body
               FROM call_for_speaker_forms f
               JOIN events e ON e.organization_id=f.organization_id AND e.id=f.event_id
               WHERE f.slug = ?1 AND f.status = 'published' AND e.status = 'active'"""
        )
        .bind(slug)
        .first()
    )
    if form is None:
        raise HTTPException(status_code=404)
    # Scope the idempotency namespace to the concrete form. A client may use
    # the same generated key for independent proposals without replaying a
    # response from another event.
    principal_key = authenticated.actor.user_id
    fingerprint = _fingerprint(body)
    route_key = f"POST /api/v1/forms/{form['id']}/submissions"
    replay = await _find_replay(db, principal_key, route_key, key)
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _editable_submission_by_id(db, str(replay["response_resource_id"]))
    await _guard_new_co_speaker_invitations(
        request,
        actor_user_id=authenticated.actor.user_id,
        event_id=str(form["event_id"]),
        desired=body.co_speakers,
    )
    now = utc_now_ms()
    availability = _form_availability(form, 0, now)
    if not availability.accepting:
        headers = {"X-CFP-Availability-State": availability.state}
        if availability.boundary_at_ms is not None:
            headers["X-CFP-Availability-Boundary-At-Ms"] = str(
                availability.boundary_at_ms
            )
        if availability.boundary_kind is not None:
            headers["X-CFP-Availability-Boundary-Kind"] = availability.boundary_kind
        raise HTTPException(
            status_code=409,
            detail=availability.message,
            headers=headers,
        )
    submitter_user_id = authenticated.actor.user_id
    submission_limit = (
        int(form["submission_limit"])
        if form.get("submission_limit") is not None
        else None
    )
    if submission_limit is not None:
        speaker_submissions = int(
            await db.prepare(
                """SELECT COUNT(*) AS count_value FROM submissions
                   WHERE form_id=?1 AND submitter_user_id=?2 AND status='submitted'"""
            )
            .bind(form["id"], submitter_user_id)
            .first("count_value")
            or 0
        )
        if speaker_submissions >= submission_limit:
            raise HTTPException(
                status_code=409,
                detail="You have reached the proposal limit for this Call for Proposals.",
            )
    schema = json.loads(str(form["schema_json"]))
    _validate_submission_schema(schema, body)
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
        form_id=str(form["id"]),
        event_id=str(form["event_id"]),
        user_id=authenticated.actor.user_id,
        canonical={
            "speaker_name": body.speaker_name,
            "speaker_email": body.speaker_email,
            "proposal_title": body.proposal_title,
            "proposal_abstract": body.proposal_abstract,
        },
    )
    submission_id = new_id()
    routing = _route_submission(schema, body.answers)
    await _validate_routed_track(
        db,
        organization_id=str(form["organization_id"]),
        event_id=str(form["event_id"]),
        routing=routing,
    )
    person = row_mapping(
        await db.prepare("SELECT id FROM people WHERE organization_id=?1 AND user_id=?2 LIMIT 1")
        .bind(form["organization_id"], submitter_user_id)
        .first()
    )
    person_id = str(person["id"]) if person is not None else new_id()
    speaker = row_mapping(
        await db.prepare(
            """SELECT id FROM event_speakers
               WHERE organization_id=?1 AND event_id=?2 AND person_id=?3 LIMIT 1"""
        )
        .bind(form["organization_id"], form["event_id"], person_id)
        .first()
    )
    event_speaker_id = str(speaker["id"]) if speaker is not None else new_id()
    legacy_upload_references = _upload_references(schema, body.answers)
    staged_claim = await build_staged_claim(
        db,
        organization_id=str(form["organization_id"]),
        event_id=str(form["event_id"]),
        event_speaker_id=event_speaker_id,
        submission_id=submission_id,
        form_id=str(form["id"]),
        user_id=submitter_user_id,
        staged_ids=staged_references(schema, body.answers),
        now=now,
    )
    if staged_claim.answer_rewrites:
        body.answers = {
            key: staged_claim.answer_rewrites.get(value, value)
            if isinstance(value, str)
            else value
            for key, value in body.answers.items()
        }
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
    # Provision the speaker graph only as part of a successful submission, and
    # never resurrect a revoked membership: insert where absent, otherwise
    # leave the existing row (including its status) untouched. These run before
    # the submission insert because the submission-owner integrity trigger
    # requires an active organization membership to exist at insert time — a
    # still-revoked membership therefore aborts the whole batch.
    batch.add_statement(
        db.prepare(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,'member','active',?4,?4)
               ON CONFLICT(organization_id,user_id) DO NOTHING"""
        ).bind(new_id(), form["organization_id"], submitter_user_id, now)
    )
    batch.add_statement(
        db.prepare(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'speaker','active',?5,?5)
               ON CONFLICT(organization_id,event_id,user_id,role) DO NOTHING"""
        ).bind(
            new_id(), form["organization_id"], form["event_id"], submitter_user_id, now
        )
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
        ).bind(submitter_user_id, now)
    )
    if person is None:
        batch.add_statement(
            db.prepare(
                """INSERT INTO people
                   (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?5)"""
            ).bind(person_id, form["organization_id"], submitter_user_id, body.speaker_name, now)
        )
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
            """INSERT INTO submissions
               (id, organization_id, event_id, form_id, public_session_id,
                proposal_title, proposal_abstract, speaker_name, speaker_email, answers_json,
                submitter_user_id, status, submitted_at_ms, created_at_ms, updated_at_ms,
                routed_category, routed_track, routed_review_queue)
               SELECT ?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10,
                      ?11, 'submitted', ?12, ?12, ?12, ?13, ?14, ?15
               WHERE (SELECT COUNT(*) FROM submissions
                      WHERE form_id=?4 AND submitter_user_id=?11 AND status='submitted')
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
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?9,'pending',
                          ?10,?11,?9,1)"""
            ).bind(
                contributor_id,
                form["organization_id"],
                form["event_id"],
                submission_id,
                contributor.display_name,
                contributor.email,
                normalize_email(contributor.email),
                contributor.role,
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
                f"Invitation to join {body.proposal_title}",
                f'<p>{escape(body.speaker_name)} invited you to join as '
                f'{escape(contributor_role_label(contributor.role).lower())}.</p>'
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
    for answer in legacy_upload_references:
        batch.add_statement(
            db.prepare(
                """UPDATE speaker_assets SET submission_id=?1,updated_at_ms=?2
                       WHERE id=(SELECT av.asset_id FROM upload_intents ui
                         JOIN speaker_asset_versions av ON av.id=ui.asset_version_id
                         WHERE ui.id=?3 LIMIT 1)"""
            ).bind(submission_id, now, answer)
        )
    for statement in staged_claim.statements:
        batch.add_statement(statement)
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
        f"{str(request.base_url).rstrip('/')}/cfp/"
        f"{_public_event_key(str(form['event_id']))}/{escape(str(form['slug']))}"
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
    await publish_committed_messages(request, [message_id, *co_speaker_message_ids])
    return await _editable_submission_by_id(db, submission_id)


SUBMISSIONS_PAGE_LIMIT = 100
_SUBMISSIONS_CURSOR = SignedCursorContract(
    "cfp_submissions", {"id": BOUNDED_ID, "sub": STRICT_INT}
)


def _submissions_cursor(
    request: Request, value: str | None, *, event_id: str
) -> tuple[int, str] | None:
    """Decode and verify a signed keyset cursor; 400 on tamper or expiry."""
    decoded = _SUBMISSIONS_CURSOR.decode(request, value, scope={"event": event_id})
    if decoded is None:
        return None
    submitted_at, row_id = decoded["sub"], decoded["id"]
    return submitted_at, row_id


def _submissions_next_cursor(
    request: Request, *, event_id: str, submitted_at_ms: int, row_id: str
) -> str | None:
    return _SUBMISSIONS_CURSOR.encode(
        request,
        scope={"event": event_id},
        position={"id": row_id, "sub": submitted_at_ms},
    )


def _submission_answer_labels(
    schema_json: str, answers: dict[str, object]
) -> dict[str, str]:
    """Return configured form labels for answer keys present in a submission."""
    try:
        schema = json.loads(schema_json)
    except (TypeError, ValueError):
        return {}
    fields = schema.get("fields", []) if isinstance(schema, dict) else []
    labels: dict[str, str] = {}
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = field.get("key")
        label = field.get("label")
        if (
            isinstance(key, str)
            and key in answers
            and isinstance(label, str)
            and label.strip()
        ):
            labels[key] = label.strip()
    return labels


@cfp_router.get(
    "/api/v1/admin/events/{event_id}/submissions",
    response_model=SubmissionList,
    operation_id="listEventSubmissions",
    tags=["submissions"],
)
async def list_submissions(
    event_id: str,
    request: Request,
    cursor: str | None = None,
    limit: int = SUBMISSIONS_PAGE_LIMIT,
) -> SubmissionList:
    if not 1 <= limit <= SUBMISSIONS_PAGE_LIMIT:
        raise HTTPException(status_code=422)
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
    window = _submissions_cursor(request, cursor, event_id=event_id)
    columns = """SELECT s.id,s.speaker_name,s.speaker_email,s.proposal_title,
                  s.proposal_abstract,s.answers_json,f.schema_json AS form_schema_json,
                  COALESCE((SELECT c.corrected_decision
                    FROM submission_decision_corrections c
                    WHERE c.submission_id=s.id
                    ORDER BY c.corrected_at_ms DESC,c.id DESC LIMIT 1),
                    d.decision,s.status) AS status,s.submitted_at_ms,s.version,
                  s.routed_category,s.routed_track,s.routed_review_queue,
                  er.id AS evaluation_round_id,er.name AS evaluation_round_name,
                  CASE WHEN d.id IS NOT NULL AND er.status='open'
                       THEN 'under_review' END AS reassessment_state,
                  -- Prefer what the speaker says about themselves on their account and
                  -- fall back to the person record the organizer curates for this
                  -- organization. Both are scoped to this submission's tenant: `people`
                  -- is unique per (organization_id, user_id), so the join adds no rows,
                  -- and a person record belonging to another organization is never read.
                  COALESCE(
                    NULLIF(TRIM(author.company),''),
                    NULLIF(TRIM(person.company),'')
                  ) AS speaker_company
               FROM submissions s
               JOIN call_for_speaker_forms f ON f.id=s.form_id
               LEFT JOIN users author ON author.id=s.submitter_user_id
               LEFT JOIN people person ON person.user_id=s.submitter_user_id
                 AND person.organization_id=s.organization_id
                 AND person.archived_at_ms IS NULL
               LEFT JOIN submission_decisions d
                 ON d.organization_id=s.organization_id AND d.event_id=s.event_id
                AND d.submission_id=s.id
               LEFT JOIN evaluation_rounds er ON er.id=(
                 SELECT candidate.id FROM evaluation_round_submissions membership
                 JOIN evaluation_rounds candidate ON candidate.id=membership.round_id
                 WHERE membership.organization_id=s.organization_id
                   AND membership.event_id=s.event_id
                   AND membership.submission_id=s.id AND membership.status='active'
                   AND candidate.status='open'
                 ORDER BY candidate.updated_at_ms DESC,candidate.id DESC LIMIT 1
               )
               WHERE s.organization_id=?1 AND s.event_id=?2"""
    if window is None:
        statement = (
            _db(request)
            .prepare(
                columns
                + """
               ORDER BY s.submitted_at_ms DESC,s.id DESC LIMIT ?3"""  # noqa: S608
            )
            .bind(event["organization_id"], event_id, limit + 1)
        )
    else:
        statement = (
            _db(request)
            .prepare(
                columns
                + """
                 AND (s.submitted_at_ms<?3 OR (s.submitted_at_ms=?3 AND s.id<?4))
               ORDER BY s.submitted_at_ms DESC,s.id DESC LIMIT ?5"""  # noqa: S608
            )
            .bind(event["organization_id"], event_id, window[0], window[1], limit + 1)
        )
    rows = result_rows(await statement.all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    data = []
    for row in rows:
        answers = json.loads(str(row.pop("answers_json")))
        answer_labels = _submission_answer_labels(
            str(row.pop("form_schema_json")), answers
        )
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
            SubmissionView.model_validate(
                {
                    **row,
                    "answers": answers,
                    "answer_labels": answer_labels,
                    "co_speakers": contributors,
                }
            )
        )
    total_row = (
        await _db(request)
        .prepare(
            """SELECT COUNT(*) AS total FROM submissions
               WHERE organization_id=?1 AND event_id=?2"""
        )
        .bind(event["organization_id"], event_id)
        .first("total")
    )
    next_cursor = (
        _submissions_next_cursor(
            request,
            event_id=event_id,
            submitted_at_ms=int(rows[-1]["submitted_at_ms"]),
            row_id=str(rows[-1]["id"]),
        )
        if has_more and rows
        else None
    )
    return SubmissionList(
        organization_id=str(event["organization_id"]),
        event_id=event_id,
        data=data,
        total=int(total_row or 0),
        next_cursor=next_cursor,
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


async def _form_by_id(db, form_id: str) -> AdminPublishedFormView:
    row = row_mapping(
        await db.prepare(
            """SELECT f.id,f.event_id,f.version,f.slug,f.welcome_text,
                      f.schema_json,f.opens_at_ms,f.closes_at_ms,f.submission_limit,
                      f.success_title,f.success_message,f.redirect_to_portal,
                      f.confirmation_subject,f.confirmation_body,
                      e.name AS event_name,e.starts_at_ms AS event_starts_at_ms,
                      e.ends_at_ms AS event_ends_at_ms,e.time_zone AS event_time_zone,
                      e.location AS event_location,e.delivery_mode AS event_delivery_mode,
                      e.website_url AS event_website_url,e.accent_color,e.logo_url,
                      e.cover_image_url,
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
    confirmation_subject = row.pop("confirmation_subject")
    confirmation_body = row.pop("confirmation_body")
    return AdminPublishedFormView.model_validate(
        _published_form_view(row, utc_now_ms()).model_dump()
        | {
            "confirmation_subject": confirmation_subject,
            "confirmation_body": confirmation_body,
        }
    )


async def _submission_by_id(db, submission_id: str) -> SubmissionView:
    row = row_mapping(
        await db.prepare(
            """SELECT s.id,s.speaker_name,s.speaker_email,s.proposal_title,
                      s.proposal_abstract,s.answers_json,
                      COALESCE((SELECT c.corrected_decision
                        FROM submission_decision_corrections c
                        WHERE c.organization_id=s.organization_id AND c.event_id=s.event_id
                          AND c.submission_id=s.id
                        ORDER BY c.corrected_at_ms DESC,c.id DESC LIMIT 1),
                        d.decision,s.status) AS status,
                      s.submitted_at_ms,s.version,s.routed_category,s.routed_track,
                      s.routed_review_queue
               FROM submissions s
               LEFT JOIN submission_decisions d ON d.organization_id=s.organization_id
                 AND d.event_id=s.event_id AND d.submission_id=s.id
               WHERE s.id=?1"""
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
    return await _private_submission_by_id(db, submission_id, editable=True)


async def _private_submission_by_id(
    db, submission_id: str, *, editable: bool
) -> PrivateSubmissionView:
    submission = await _submission_by_id(db, submission_id)
    return PrivateSubmissionView.model_validate(
        {
            **submission.model_dump(
                exclude={"co_speakers": {"__all__": {"role_label"}}}
            ),
            "editable": editable,
            # Every caller of this private resolver has already established
            # submitter ownership. Contributor-visible list responses are
            # built separately and keep this false.
            "can_manage_participants": True,
        }
    )


def _availability_boundaries(row) -> tuple[int | None, int | None]:
    opens_at = int(row["opens_at_ms"]) if row.get("opens_at_ms") is not None else None
    closes_at = int(row["closes_at_ms"]) if row.get("closes_at_ms") is not None else None
    return opens_at, closes_at


def _form_availability(
    row, submissions_received: int, now_ms: int
) -> FormAvailability:
    # Kept in the signature because callers also use the total as a public
    # activity metric. The configured limit is per authenticated speaker and
    # is therefore enforced by create_submission, not on this public view.
    del submissions_received
    return form_availability(*_availability_boundaries(row), now_ms)


def _published_form_view(row, now_ms: int) -> PublishedFormView:
    schema = json.loads(str(row.pop("schema_json")))
    submissions_received = int(row.get("submissions_received") or 0)
    availability = _form_availability(row, submissions_received, now_ms)
    return PublishedFormView.model_validate(
        {
            **row,
            **schema,
            "accent_color": row.get("accent_color") or "#3159d9",
            "redirect_to_portal": bool(row["redirect_to_portal"]),
            "submissions_received": submissions_received,
            "accepting_submissions": availability.accepting,
            # Organizer and public surfaces render this state directly, so the
            # deadline is decided once, here, from the stored boundaries.
            "availability_state": availability.state,
            "availability_message": availability.message,
            "availability_boundary_at_ms": availability.boundary_at_ms,
            "availability_boundary_kind": availability.boundary_kind,
        }
    )


def _condition_matches(operator: object, actual: object, expected: str) -> bool:
    if operator == "contains":
        return expected in actual if isinstance(actual, list) else expected in str(actual or "")
    if isinstance(actual, list):
        comparable = [str(item) for item in actual]
    elif isinstance(actual, bool):
        comparable = ["true" if actual else "false"]
    else:
        comparable = [str(actual if actual is not None else "")]
    matches = expected in comparable
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
    form_id: str,
    event_id: str,
    user_id: str,
    canonical: dict[str, object] | None = None,
) -> None:
    raw_fields = schema.get("fields", [])
    if not isinstance(raw_fields, list):
        raise HTTPException(status_code=409)
    values = {**answers, **(canonical or {})}
    inactive_targets: set[str] = set()
    raw_conditions = schema.get("conditions", [])
    if not isinstance(raw_conditions, list):
        raise HTTPException(status_code=409)
    for condition in raw_conditions:
        if not isinstance(condition, dict):
            raise HTTPException(status_code=409)
        source_value = values.get(str(condition.get("source_key", "")), "")
        expected = str(condition.get("value", ""))
        if not _condition_matches(condition.get("operator"), source_value, expected):
            inactive_targets.add(str(condition.get("target_key", "")))
    now = utc_now_ms()
    for field in raw_fields:
        if not isinstance(field, dict) or field.get("type") not in {"file", "image"}:
            continue
        field_key = str(field.get("key", ""))
        if field_key in inactive_targets:
            continue
        value = answers.get(field_key)
        if value in (None, "") and not field.get("required"):
            continue
        if not isinstance(value, str):
            raise HTTPException(
                status_code=422,
                detail={"field": field_key, "message": "A file upload is required."},
            )
        expected_kind = "headshot" if field.get("type") == "image" else "supporting_document"
        if value.startswith("staged:"):
            staged_id = value.removeprefix("staged:")
            found = (
                await db.prepare(
                    """SELECT 1 AS found FROM cfp_staged_assets
                       WHERE id=?1 AND form_id=?2 AND user_id=?3 AND kind=?4
                         AND status='staged' AND expires_at_ms>?5 LIMIT 1"""
                )
                .bind(staged_id, form_id, user_id, expected_kind, now)
                .first("found")
            )
            if found is None:
                raise HTTPException(
                    status_code=422,
                    detail={"field": field_key, "message": "The uploaded file is unavailable."},
                )
            continue
        if not value.startswith("upload:"):
            raise HTTPException(
                status_code=422,
                detail={"field": field_key, "message": "Upload this file before submitting."},
            )
        intent_id = value.removeprefix("upload:")
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
            raise HTTPException(
                status_code=422,
                detail={"field": field_key, "message": "The uploaded file is unavailable."},
            )


def _request_source(request: Request) -> str:
    connecting_ip = request.headers.get("cf-connecting-ip")
    if connecting_ip:
        return connecting_ip
    return request.client.host if request.client is not None else "unknown"


def _validate_submission_schema(schema: dict[str, object], body: SubmissionCreate) -> None:
    co_speaker_limit = schema.get("co_speaker_limit", 3)
    if not isinstance(co_speaker_limit, int) or not 0 <= co_speaker_limit <= 10:
        raise HTTPException(status_code=409)
    if len(body.co_speakers) > co_speaker_limit:
        raise HTTPException(
            status_code=422,
            detail=f"This form allows up to {co_speaker_limit} co-speakers.",
        )
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
    elif field_type in {"file", "image"} and not (
        str(value).startswith("upload:") or str(value).startswith("staged:")
    ):
        raise HTTPException(status_code=422)

import hashlib
import json
from csv import writer
from datetime import UTC, datetime
from html import escape
from io import StringIO
from time import perf_counter

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from sessionbuddy.console import embedded_assets
from sessionbuddy.observability import record_timing
from sessionbuddy.platform.auth.http import (
    authenticate_request,
    require_document_persona,
    require_permission,
)
from sessionbuddy.platform.auth.tokens import normalize_email
from sessionbuddy.platform.authorization import Permission, Persona, ResourceContext
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch, IdempotencyRecord
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.signed_cursors import decode_signed_cursor, encode_signed_cursor

from .models import (
    AiTriageView,
    AssignmentReassign,
    ConflictDeclaration,
    ConflictProgress,
    ConflictView,
    EvaluationAssignmentList,
    EvaluationAssignmentView,
    EvaluationDetail,
    EvaluationRoundClosed,
    EvaluationRoundCloseRequest,
    EvaluationRoundCreate,
    EvaluationRoundList,
    EvaluationRoundResults,
    EvaluationRoundView,
    EvaluationSave,
    EvaluationView,
    EvaluatorList,
    EvaluatorProgress,
    EvaluatorReminderQueued,
    EvaluatorView,
    ReassignmentView,
    RoundEvaluatorAdd,
    RoundEvaluatorChange,
    RoundSubmissionAdd,
    RoundSubmissionChange,
    SubmissionAnswerView,
    SubmissionDecisionCreate,
    SubmissionDecisionView,
    SubmissionEvaluationResult,
)

evaluation_router = APIRouter()

AI_TRIAGE_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast"
EVALUATION_PAGE_LIMIT = 50


def _acceptance_speaker_tasks(speaker: dict[str, object]) -> list[tuple[str, str, str, int]]:
    """Return only actionable work still missing when a proposal is accepted."""
    tasks: list[tuple[str, str, str, int]] = []
    if not str(speaker.get("biography") or "").strip() and not bool(
        speaker.get("has_profile_task")
    ):
        tasks.append(
            (
                "profile",
                "Add your speaker biography",
                "Your registration is complete; add the missing biography for the program.",
                7,
            )
        )
    if (
        not bool(speaker.get("has_account_headshot"))
        and not bool(speaker.get("has_event_headshot"))
        and not bool(speaker.get("has_headshot_task"))
    ):
        tasks.append(
            (
                "headshot",
                "Upload your headshot",
                "Add a program-ready profile photo.",
                10,
            )
        )
    tasks.append(
        (
            "slides",
            "Upload your presentation",
            "Share the final slide deck with the event team.",
            21,
        )
    )
    return tasks


def _evaluation_cursor(
    request: Request,
    value: str | None,
    *,
    kind: str,
    scope_id: str,
) -> tuple[int, str] | None:
    """Decode a signed keyset cursor scoped to one list and identity."""
    decoded = decode_signed_cursor(
        request,
        value,
        scope={"kind": kind, "scope": scope_id},
        position_fields={"id", "ts"},
    )
    if decoded is None:
        return None
    timestamp, row_id = decoded["ts"], decoded["id"]
    if type(timestamp) is not int or not isinstance(row_id, str) or not 1 <= len(row_id) <= 100:
        raise HTTPException(status_code=400, detail="Invalid or expired cursor")
    return timestamp, row_id


def _evaluation_next_cursor(
    request: Request,
    *,
    kind: str,
    scope_id: str,
    timestamp: int,
    row_id: str,
) -> str:
    return encode_signed_cursor(
        request,
        scope={"kind": kind, "scope": scope_id},
        position={"id": row_id, "ts": timestamp},
    )


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


@evaluation_router.get("/reviews", response_class=HTMLResponse, include_in_schema=False)
async def reviews_page(request: Request) -> HTMLResponse:
    await require_document_persona(request, Persona.REVIEWER)
    return HTMLResponse(_asset("app/index.html"), headers={"Cache-Control": "no-store"})


@evaluation_router.get(
    "/admin/evaluation-rounds/{round_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_round_page(round_id: str, request: Request) -> HTMLResponse:
    await require_document_persona(request, Persona.ORGANIZER)
    return HTMLResponse(_asset("app/index.html"), headers={"Cache-Control": "no-store"})


@evaluation_router.get("/app/assets/reviews.css", response_class=Response, include_in_schema=False)
async def reviews_css() -> Response:
    return Response(_asset("app/assets/reviews.css"), media_type="text/css")


@evaluation_router.get("/app/assets/reviews.js", response_class=Response, include_in_schema=False)
async def reviews_js() -> Response:
    return Response(_asset("app/assets/reviews.js"), media_type="text/javascript")


def _db(request: Request):
    db = getattr(request.scope.get("env"), "DB", None)
    if db is None:
        raise HTTPException(status_code=503)
    return db


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


def _assignment_pairs(
    submission_ids: list[str], evaluator_ids: list[str], strategy: str
) -> list[tuple[str, str]]:
    if strategy == "all":
        return [
            (submission_id, evaluator_id)
            for submission_id in submission_ids
            for evaluator_id in evaluator_ids
        ]
    return [
        (submission_id, evaluator_ids[index % len(evaluator_ids)])
        for index, submission_id in enumerate(submission_ids)
    ]


def _weighted_mean(values: list[tuple[float, int]]) -> float | None:
    count = sum(weight for _, weight in values)
    if count == 0:
        return None
    return round(sum(value * weight for value, weight in values) / count, 2)


async def _evaluator_emails(db, user_ids: list[str]) -> dict[str, str]:
    if not user_ids:
        return {}
    placeholders = ",".join(f"?{index + 1}" for index in range(len(user_ids)))
    rows = result_rows(
        await db.prepare(
            f"SELECT id,email FROM users WHERE id IN ({placeholders})"  # noqa: S608
        )
        .bind(*user_ids)
        .all()
    )
    return {str(row["id"]): str(row["email"]) for row in rows}


def _queue_assignment_notifications(
    batch: CommandBatch,
    db,
    *,
    emails_by_user: dict[str, str],
    assignment_counts: dict[str, int],
    organization_id: str,
    event_id: str,
    round_id: str,
    round_name: str,
    review_closes_at_ms: int | None,
    base_url: str,
    now_ms: int,
    dedup_suffix: str,
) -> list[str]:
    """Queue one assignment-notification email per evaluator, atomically with
    the assignments themselves. Returns queued message ids for best-effort
    publish after commit; the scheduled dispatcher recovers anything missed."""
    message_ids: list[str] = []
    deadline = ""
    if review_closes_at_ms is not None:
        closes = datetime.fromtimestamp(review_closes_at_ms / 1000, UTC)
        deadline = f"<p>Reviews close {closes.strftime('%B %d, %Y at %H:%M UTC')}.</p>"
    link = (
        f'<p><a href="{escape(base_url.rstrip("/"))}/reviews">Open your reviews</a></p>'
        if base_url
        else "<p>Open SessionBuddy and visit Reviews to get started.</p>"
    )
    for evaluator_id in sorted(assignment_counts):
        count = assignment_counts[evaluator_id]
        email = emails_by_user.get(evaluator_id, "")
        if not email or count <= 0:
            continue
        message_id = new_id()
        message_ids.append(message_id)
        html_body = (
            f"<p>You have been assigned {count} proposal"
            f"{'s' if count != 1 else ''} to review in "
            f"{escape(round_name)}.</p>{deadline}{link}"
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO communication_messages
                   (id,organization_id,event_id,recipient_user_id,recipient_email,subject,
                    html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9)"""
            ).bind(
                message_id,
                organization_id,
                event_id,
                evaluator_id,
                email,
                f"New review assignments: {round_name}",
                html_body,
                f"evaluation-assignment:{round_id}:{evaluator_id}:{dedup_suffix}",
                now_ms,
            )
        )
    return message_ids


async def _publish_queued_messages(request: Request, message_ids: list[str]) -> None:
    queue = getattr(request.scope.get("env"), "COMMUNICATION_QUEUE", None)
    if queue is None:
        return
    for message_id in message_ids:
        try:
            await queue.send({"schema_version": 1, "message_id": message_id})
        except Exception:
            # Durable rows stay 'queued'; the scheduled dispatcher republishes.
            record_timing(request, "domain", 0)


async def _event_organization_id(db, event_id: str) -> str:
    event = row_mapping(
        await db.prepare(
            """SELECT organization_id FROM events
               WHERE id = ?1 AND status != 'archived' LIMIT 1"""
        )
        .bind(event_id)
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404)
    return str(event["organization_id"])


@evaluation_router.post(
    "/api/v1/admin/events/{event_id}/evaluation-rounds",
    response_model=EvaluationRoundView,
    status_code=201,
    operation_id="createEvaluationRound",
    tags=["evaluations"],
)
async def create_evaluation_round(
    event_id: str,
    request: Request,
    body: EvaluationRoundCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EvaluationRoundView:
    db = _db(request)
    organization_id = await _event_organization_id(db, event_id)
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(organization_id, event_id),
        mutation=True,
    )
    key = _key(idempotency_key)
    route = "POST /api/v1/admin/events/{event_id}/evaluation-rounds"
    fingerprint = _fingerprint(body)
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint, response_resource_id FROM idempotency_records
               WHERE principal_key = ?1 AND route_key = ?2 AND idempotency_key_hash = ?3
                 AND state = 'completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(key.encode()).digest())
        .first()
    )
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _round_view(db, str(replay["response_resource_id"]))

    active_round = (
        await db.prepare(
            """SELECT 1 AS found FROM evaluation_rounds
           WHERE organization_id = ?1 AND event_id = ?2
             AND status = 'open' LIMIT 1"""
        )
        .bind(organization_id, event_id)
        .first("found")
    )
    if active_round is not None:
        raise HTTPException(status_code=409)

    evaluator_placeholders = ",".join(
        f"?{index + 1}" for index in range(len(body.evaluator_user_ids))
    )
    evaluators = result_rows(
        await db.prepare(
            f"""SELECT u.id AS user_id FROM users u
            JOIN user_roles ur ON ur.user_id=u.id
            JOIN identity_invitations i
              ON i.organization_id=?{len(body.evaluator_user_ids) + 1}
             AND i.event_id=?{len(body.evaluator_user_ids) + 2}
             AND i.normalized_email=u.normalized_email
             AND i.role='evaluator' AND i.status='accepted'
            WHERE u.id IN ({evaluator_placeholders})
              AND u.status='active' AND ur.role='reviewer' AND ur.status='active'"""  # noqa: S608
        )
        .bind(*body.evaluator_user_ids, organization_id, event_id)
        .all()
    )
    if {str(row["user_id"]) for row in evaluators} != set(body.evaluator_user_ids):
        raise HTTPException(status_code=400)
    placeholders = ",".join(f"?{index + 3}" for index in range(len(body.submission_ids)))
    submissions = result_rows(
        await db.prepare(
            f"""SELECT id FROM submissions WHERE organization_id = ?1 AND event_id = ?2
                  AND status='submitted'
                  AND id IN ({placeholders})"""  # noqa: S608
        )
        .bind(organization_id, event_id, *body.submission_ids)
        .all()
    )
    if {str(row["id"]) for row in submissions} != set(body.submission_ids):
        raise HTTPException(status_code=400)

    now = utc_now_ms()
    round_id = new_id()
    rubric = {
        "rating": {"min": body.rating_min, "max": body.rating_max, "required": True},
        "recommendation": {"choices": body.recommendations, "required": True},
        "internal_comment": {"required": body.comment_required},
        "guidance": body.evaluator_guidance,
        "criteria": [criterion.model_dump() for criterion in body.criteria],
        "blind_review": body.blind_review,
        "assignment_strategy": body.assignment_strategy,
    }
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=organization_id,
        event_id=event_id,
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO evaluation_rounds
               (id, organization_id, event_id, name, rubric_json, status,
                review_opens_at_ms,review_closes_at_ms,created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, 'open', ?6, ?7, ?8, ?8)"""
        ).bind(
            round_id,
            organization_id,
            event_id,
            body.name,
            json.dumps(rubric, separators=(",", ":"), sort_keys=True),
            body.review_opens_at_ms,
            body.review_closes_at_ms,
            now,
        )
    )
    assignment_pairs = _assignment_pairs(
        body.submission_ids, body.evaluator_user_ids, body.assignment_strategy
    )
    for submission_id, evaluator_id in assignment_pairs:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_assignments
                   (id, organization_id, event_id, round_id, submission_id,
                    evaluator_user_id, status, created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'assigned', ?7, ?7)"""
            ).bind(new_id(), organization_id, event_id, round_id, submission_id, evaluator_id, now)
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation_round.create",
            target_type="evaluation_round",
            target_id=round_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={
                "assignment_count": len(assignment_pairs),
                "evaluator_count": len(body.evaluator_user_ids),
                "assignment_strategy": body.assignment_strategy,
            },
        )
    )
    assignment_counts: dict[str, int] = {}
    for _, evaluator_id in assignment_pairs:
        assignment_counts[evaluator_id] = assignment_counts.get(evaluator_id, 0) + 1
    notification_ids = _queue_assignment_notifications(
        batch,
        db,
        emails_by_user=await _evaluator_emails(db, body.evaluator_user_ids),
        assignment_counts=assignment_counts,
        organization_id=organization_id,
        event_id=event_id,
        round_id=round_id,
        round_name=body.name,
        review_closes_at_ms=body.review_closes_at_ms,
        base_url=str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "") or ""),
        now_ms=now,
        dedup_suffix=str(now),
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="evaluation_round",
        resource_id=round_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    await _publish_queued_messages(request, notification_ids)
    return EvaluationRoundView(
        id=round_id,
        event_id=event_id,
        name=body.name,
        status="open",
        assignment_count=len(assignment_pairs),
        evaluator_count=len(body.evaluator_user_ids),
    )


@evaluation_router.get(
    "/api/v1/admin/events/{event_id}/evaluation-rounds/current",
    response_model=EvaluationRoundView | None,
    operation_id="getCurrentEvaluationRound",
    tags=["evaluations"],
)
async def get_current_evaluation_round(
    event_id: str, request: Request
) -> EvaluationRoundView | None:
    db = _db(request)
    organization_id = await _event_organization_id(db, event_id)
    await require_permission(
        request,
        Permission.EVALUATION_RESULTS_READ,
        ResourceContext(organization_id, event_id),
        mutation=False,
    )
    row = row_mapping(
        await db.prepare(
            """SELECT r.id, r.event_id, r.name, r.status, COUNT(a.id) AS assignment_count,
                  COUNT(DISTINCT a.evaluator_user_id) AS evaluator_count
           FROM evaluation_rounds r LEFT JOIN evaluation_assignments a ON a.round_id = r.id
           WHERE r.organization_id = ?1 AND r.event_id = ?2 AND r.status = 'open'
           GROUP BY r.id ORDER BY r.created_at_ms DESC LIMIT 1"""
        )
        .bind(organization_id, event_id)
        .first()
    )
    return EvaluationRoundView.model_validate(row) if row is not None else None


@evaluation_router.get(
    "/api/v1/admin/events/{event_id}/evaluation-rounds",
    response_model=EvaluationRoundList,
    operation_id="listEvaluationRounds",
    tags=["evaluations"],
)
async def list_evaluation_rounds(event_id: str, request: Request) -> EvaluationRoundList:
    db = _db(request)
    organization_id = await _event_organization_id(db, event_id)
    await require_permission(
        request,
        Permission.EVALUATION_RESULTS_READ,
        ResourceContext(organization_id, event_id),
        mutation=False,
    )
    rows = result_rows(
        await db.prepare(
            """SELECT r.id,r.event_id,r.name,r.status,COUNT(a.id) AS assignment_count,
                      COUNT(DISTINCT a.evaluator_user_id) AS evaluator_count
               FROM evaluation_rounds r
               LEFT JOIN evaluation_assignments a ON a.round_id=r.id
               WHERE r.organization_id=?1 AND r.event_id=?2 GROUP BY r.id
               ORDER BY r.created_at_ms DESC,r.id DESC LIMIT 50"""
        )
        .bind(organization_id, event_id)
        .all()
    )
    round_ids = [str(row["id"]) for row in rows]
    proposals_by_round: dict[str, list[dict[str, str]]] = {round_id: [] for round_id in round_ids}
    if round_ids:
        placeholders = ",".join(f"?{index + 1}" for index in range(len(round_ids)))
        proposal_rows = result_rows(
            await db.prepare(
                f"""SELECT DISTINCT a.round_id,s.id AS submission_id,s.proposal_title
                     FROM evaluation_assignments a
                     JOIN submissions s ON s.id=a.submission_id
                     WHERE a.round_id IN ({placeholders}) AND a.status!='revoked'
                     ORDER BY a.round_id,s.proposal_title,s.id"""  # noqa: S608
            )
            .bind(*round_ids)
            .all()
        )
        for proposal in proposal_rows:
            proposals_by_round[str(proposal["round_id"])].append(
                {"submission_id": str(proposal["submission_id"]), "proposal_title": str(proposal["proposal_title"])}
            )
    return EvaluationRoundList(data=[
        EvaluationRoundView.model_validate({**row, "proposals": proposals_by_round[str(row["id"])]})
        for row in rows
    ])


@evaluation_router.get(
    "/api/v1/admin/events/{event_id}/evaluators",
    response_model=EvaluatorList,
    operation_id="listEventEvaluators",
    tags=["evaluations"],
)
async def list_event_evaluators(
    event_id: str,
    request: Request,
    email: str | None = Query(default=None, min_length=3, max_length=320),
) -> EvaluatorList:
    db = _db(request)
    organization_id = await _event_organization_id(db, event_id)
    await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(organization_id, event_id),
        mutation=False,
    )
    if email is None:
        return EvaluatorList(data=[])
    normalized = normalize_email(email)
    rows = result_rows(
        await db.prepare(
            """SELECT u.id AS user_id, COALESCE(u.display_name,u.email) AS display_name
               FROM users u JOIN user_roles ur ON ur.user_id=u.id
               JOIN identity_invitations i
                 ON i.organization_id=?2 AND i.event_id=?3
                AND i.normalized_email=u.normalized_email
                AND i.role='evaluator' AND i.status='accepted'
               WHERE u.normalized_email=?1 AND u.status='active'
                 AND ur.role='reviewer' AND ur.status='active'
               LIMIT 1"""
        )
        .bind(normalized, organization_id, event_id)
        .all()
    )
    return EvaluatorList(data=[EvaluatorView.model_validate(row) for row in rows])


@evaluation_router.post(
    "/api/v1/admin/submissions/{submission_id}/ai-triage",
    response_model=AiTriageView,
    operation_id="triageSubmissionWithWorkersAi",
    tags=["evaluations", "ai"],
)
async def triage_submission(submission_id: str, request: Request) -> AiTriageView:
    db = _db(request)
    submission = row_mapping(
        await db.prepare(
            """SELECT organization_id,event_id,proposal_title,proposal_abstract
               FROM submissions WHERE id=?1 LIMIT 1"""
        ).bind(submission_id).first()
    )
    if submission is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request, Permission.EVALUATION_RESULTS_READ,
        ResourceContext(str(submission["organization_id"]), str(submission["event_id"])),
        mutation=True,
    )
    ai = getattr(request.scope.get("env"), "AI", None)
    if ai is None:
        raise HTTPException(status_code=503)
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a conference proposal triage assistant. Evaluate only the proposal "
                    "content. Return a 0-10 score, one recommendation, and a specific rationale. "
                    "This is advisory; a human makes the final decision. Do not infer protected "
                    "traits or score the identity, reputation, employer, or demographic profile "
                    "of any speaker."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Title: {submission['proposal_title']}\n\n"
                    f"Abstract: {submission['proposal_abstract']}"
                ),
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "type": "object",
                "properties": {
                    "score": {"type": "integer", "minimum": 0, "maximum": 10},
                    "recommendation": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["score", "recommendation", "rationale"],
            },
        },
    }
    try:
        raw = await ai.run(AI_TRIAGE_MODEL, payload)
        converted = to_python(raw)
        result = converted.get("response", converted) if isinstance(converted, dict) else None
        if isinstance(result, str):
            result = json.loads(result)
        if not isinstance(result, dict):
            raise ValueError("Workers AI returned no structured response")
        view = AiTriageView(
            submission_id=submission_id, model=AI_TRIAGE_MODEL,
            score=int(result["score"]), recommendation=str(result["recommendation"]),
            rationale=str(result["rationale"]), generated_at_ms=utc_now_ms(),
        )
        await db.prepare(
            """INSERT INTO ai_triage_results
               (id,organization_id,event_id,submission_id,model,score,recommendation,
                rationale,generated_by_user_id,generated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)"""
        ).bind(
            new_id(), submission["organization_id"], submission["event_id"], submission_id,
            view.model, view.score, view.recommendation, view.rationale,
            auth.actor.user_id, view.generated_at_ms,
        ).run()
        return view
    except Exception as exc:
        raise HTTPException(status_code=502) from exc


@evaluation_router.post(
    "/api/v1/admin/evaluation-rounds/{round_id}/evaluators",
    response_model=RoundEvaluatorChange,
    operation_id="addEvaluationRoundEvaluator",
    tags=["evaluations"],
)
async def add_round_evaluator(
    round_id: str,
    body: RoundEvaluatorAdd,
    request: Request,
) -> RoundEvaluatorChange:
    db = _db(request)
    round_row = row_mapping(
        await db.prepare(
            """SELECT organization_id,event_id,status FROM evaluation_rounds
               WHERE id=?1 LIMIT 1"""
        )
        .bind(round_id)
        .first()
    )
    if round_row is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(round_row["organization_id"]), str(round_row["event_id"])),
        mutation=True,
    )
    if round_row["status"] != "open":
        raise HTTPException(status_code=409)
    reviewer = await db.prepare(
        """SELECT 1 AS found FROM users u JOIN user_roles ur ON ur.user_id=u.id
           JOIN identity_invitations i
             ON i.organization_id=?2 AND i.event_id=?3
            AND i.normalized_email=u.normalized_email
            AND i.role='evaluator' AND i.status='accepted'
           WHERE u.id=?1 AND u.status='active'
             AND ur.role='reviewer' AND ur.status='active' LIMIT 1"""
    ).bind(
        body.evaluator_user_id, round_row["organization_id"], round_row["event_id"]
    ).first("found")
    if reviewer is None:
        raise HTTPException(status_code=400)
    submission_ids = [
        str(row["submission_id"])
        for row in result_rows(
            await db.prepare(
                """SELECT DISTINCT submission_id FROM evaluation_assignments
                   WHERE round_id=?1 ORDER BY submission_id"""
            )
            .bind(round_id)
            .all()
        )
    ]
    existing_rows = result_rows(
        await db.prepare(
            """SELECT a.id, a.submission_id, a.status,
                      EXISTS(SELECT 1 FROM evaluation_conflicts c
                             WHERE c.assignment_id=a.id) AS has_conflict
               FROM evaluation_assignments a
               WHERE a.round_id=?1 AND a.evaluator_user_id=?2"""
        )
        .bind(round_id, body.evaluator_user_id)
        .all()
    )
    existing_ids = {str(row["submission_id"]) for row in existing_rows}
    # Revoked assignments are revived rather than re-inserted (UNIQUE constraint),
    # except conflict-declared ones: a recorded conflict of interest stays removed.
    revive_ids = [
        str(row["id"])
        for row in existing_rows
        if row["status"] == "revoked" and not row["has_conflict"]
    ]
    missing_ids = [
        submission_id for submission_id in submission_ids if submission_id not in existing_ids
    ]
    now = utc_now_ms()
    batch = CommandBatch(db)
    for assignment_row_id in revive_ids:
        batch.add_statement(
            db.prepare(
                """UPDATE evaluation_assignments SET status='assigned', updated_at_ms=?2
                   WHERE id=?1 AND status='revoked'"""
            ).bind(assignment_row_id, now)
        )
    for submission_id in missing_ids:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_assignments
                   (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                    status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,'assigned',?7,?7)"""
            ).bind(
                new_id(), round_row["organization_id"], round_row["event_id"], round_id,
                submission_id, body.evaluator_user_id, now,
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user", actor_user_id=auth.actor.user_id,
            action="evaluation_round.evaluator.add", target_type="evaluation_round",
            target_id=round_id, result="succeeded", correlation_id=request.state.request_id,
            occurred_at_ms=now, organization_id=str(round_row["organization_id"]),
            event_id=str(round_row["event_id"]),
            metadata={
                "evaluator_user_id": body.evaluator_user_id,
                "assignment_count": len(missing_ids) + len(revive_ids),
            },
        )
    )
    round_detail = row_mapping(
        await db.prepare(
            "SELECT name,review_closes_at_ms FROM evaluation_rounds WHERE id=?1 LIMIT 1"
        )
        .bind(round_id)
        .first()
    )
    notification_ids = _queue_assignment_notifications(
        batch,
        db,
        emails_by_user=await _evaluator_emails(db, [body.evaluator_user_id]),
        assignment_counts={body.evaluator_user_id: len(missing_ids) + len(revive_ids)},
        organization_id=str(round_row["organization_id"]),
        event_id=str(round_row["event_id"]),
        round_id=round_id,
        round_name=str(round_detail["name"]) if round_detail else "",
        review_closes_at_ms=(
            int(round_detail["review_closes_at_ms"])
            if round_detail and round_detail["review_closes_at_ms"] is not None
            else None
        ),
        base_url=str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "") or ""),
        now_ms=now,
        dedup_suffix=str(now),
    )
    await batch.execute()
    await _publish_queued_messages(request, notification_ids)
    return RoundEvaluatorChange(
        round_id=round_id, evaluator_user_id=body.evaluator_user_id,
        assignment_count=len(missing_ids) + len(revive_ids),
    )


@evaluation_router.post(
    "/api/v1/admin/evaluation-rounds/{round_id}/submissions",
    response_model=RoundSubmissionChange,
    operation_id="addEvaluationRoundSubmissions",
    tags=["evaluations"],
)
async def add_round_submissions(
    round_id: str,
    body: RoundSubmissionAdd,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> RoundSubmissionChange:
    db = _db(request)
    round_row = row_mapping(
        await db.prepare(
            """SELECT organization_id,event_id,rubric_json,status,name,review_closes_at_ms
               FROM evaluation_rounds WHERE id=?1 LIMIT 1"""
        )
        .bind(round_id)
        .first()
    )
    if round_row is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(round_row["organization_id"]), str(round_row["event_id"])),
        mutation=True,
    )
    key = _key(idempotency_key)
    route = "POST /api/v1/admin/evaluation-rounds/{round_id}/submissions"
    fingerprint = hashlib.sha256(
        json.dumps(
            {"round_id": round_id, "body": body.model_dump()},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id
               FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND organization_id=?4 AND event_id=?5 AND state='completed'"""
        )
        .bind(
            auth.actor.user_id,
            route,
            hashlib.sha256(key.encode()).digest(),
            round_row["organization_id"],
            round_row["event_id"],
        )
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        try:
            result = RoundSubmissionChange.model_validate_json(
                str(replay["response_resource_id"])
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=409) from exc
        if result.round_id != round_id:
            raise HTTPException(status_code=409)
        return result
    if round_row["status"] != "open":
        raise HTTPException(status_code=409)
    placeholders = ",".join(f"?{index + 3}" for index in range(len(body.submission_ids)))
    submissions = result_rows(
        await db.prepare(
            f"""SELECT id FROM submissions WHERE organization_id=?1 AND event_id=?2
                  AND status='submitted'
                  AND id IN ({placeholders})"""  # noqa: S608
        )
        .bind(
            round_row["organization_id"],
            round_row["event_id"],
            *body.submission_ids,
        )
        .all()
    )
    if {str(row["id"]) for row in submissions} != set(body.submission_ids):
        raise HTTPException(status_code=400)
    existing_ids = {
        str(row["submission_id"])
        for row in result_rows(
            await db.prepare(
                """SELECT DISTINCT submission_id FROM evaluation_assignments
                   WHERE round_id=?1"""
            )
            .bind(round_id)
            .all()
        )
    }
    new_submission_ids = [
        submission_id
        for submission_id in body.submission_ids
        if submission_id not in existing_ids
    ]
    evaluator_ids = [
        str(row["evaluator_user_id"])
        for row in result_rows(
            await db.prepare(
                """SELECT evaluator_user_id,COUNT(*) AS assignment_count
                   FROM evaluation_assignments
                   WHERE round_id=?1 AND status!='revoked'
                   GROUP BY evaluator_user_id
                   ORDER BY assignment_count,evaluator_user_id"""
            )
            .bind(round_id)
            .all()
        )
    ]
    if not evaluator_ids:
        raise HTTPException(status_code=409, detail="Add a reviewer before adding submissions.")
    rubric = json.loads(str(round_row["rubric_json"]))
    strategy = str(rubric.get("assignment_strategy", "balanced"))
    if strategy not in {"all", "balanced"}:
        strategy = "balanced"
    assignment_pairs = _assignment_pairs(new_submission_ids, evaluator_ids, strategy)
    now = utc_now_ms()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(round_row["organization_id"]),
        event_id=str(round_row["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    for submission_id, evaluator_id in assignment_pairs:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_assignments
                   (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                    status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,'assigned',?7,?7)"""
            ).bind(
                new_id(),
                round_row["organization_id"],
                round_row["event_id"],
                round_id,
                submission_id,
                evaluator_id,
                now,
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation_round.submissions.add",
            target_type="evaluation_round",
            target_id=round_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(round_row["organization_id"]),
            event_id=str(round_row["event_id"]),
            metadata={
                "submission_count": len(new_submission_ids),
                "assignment_count": len(assignment_pairs),
            },
        )
    )
    assignment_counts: dict[str, int] = {}
    for _, evaluator_id in assignment_pairs:
        assignment_counts[evaluator_id] = assignment_counts.get(evaluator_id, 0) + 1
    added_digest = hashlib.sha256(
        ":".join(sorted(new_submission_ids)).encode()
    ).hexdigest()[:16]
    notification_ids = _queue_assignment_notifications(
        batch,
        db,
        emails_by_user=await _evaluator_emails(db, list(assignment_counts)),
        assignment_counts=assignment_counts,
        organization_id=str(round_row["organization_id"]),
        event_id=str(round_row["event_id"]),
        round_id=round_id,
        round_name=str(round_row["name"]),
        review_closes_at_ms=(
            int(round_row["review_closes_at_ms"])
            if round_row["review_closes_at_ms"] is not None
            else None
        ),
        base_url=str(getattr(request.scope.get("env"), "PUBLIC_BASE_URL", "") or ""),
        now_ms=now,
        dedup_suffix=(
            f"{added_digest}:{hashlib.sha256(key.encode()).hexdigest()[:16]}"
        ),
    )
    result = RoundSubmissionChange(
        round_id=round_id,
        submission_count=len(new_submission_ids),
        assignment_count=len(assignment_pairs),
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="evaluation_round_submission_change",
        resource_id=result.model_dump_json(),
        completed_at_ms=now,
    )
    await _execute(request, batch)
    await _publish_queued_messages(request, notification_ids)
    return result


@evaluation_router.post(
    "/api/v1/admin/evaluation-rounds/{round_id}/evaluators/{evaluator_user_id}/remove",
    response_model=RoundEvaluatorChange,
    operation_id="removeEvaluationRoundEvaluator",
    tags=["evaluations"],
)
async def remove_round_evaluator(
    round_id: str,
    evaluator_user_id: str,
    request: Request,
) -> RoundEvaluatorChange:
    db = _db(request)
    round_row = row_mapping(
        await db.prepare(
            """SELECT organization_id,event_id,status FROM evaluation_rounds
               WHERE id=?1 LIMIT 1"""
        ).bind(round_id).first()
    )
    if round_row is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request, Permission.SUBMISSION_MANAGE,
        ResourceContext(str(round_row["organization_id"]), str(round_row["event_id"])),
        mutation=True,
    )
    if round_row["status"] != "open":
        raise HTTPException(status_code=409)
    saved = int(
        await db.prepare(
            """SELECT COUNT(*) AS count_value FROM evaluations e
               JOIN evaluation_assignments a ON a.id=e.assignment_id
               WHERE a.round_id=?1 AND a.evaluator_user_id=?2"""
        ).bind(round_id, evaluator_user_id).first("count_value") or 0
    )
    if saved:
        raise HTTPException(status_code=409)
    uncovered = int(
        await db.prepare(
            """SELECT COUNT(*) AS count_value FROM evaluation_assignments target
               WHERE target.round_id=?1 AND target.evaluator_user_id=?2
                 AND target.status!='revoked' AND NOT EXISTS (
                   SELECT 1 FROM evaluation_assignments replacement
                   WHERE replacement.round_id=target.round_id
                     AND replacement.submission_id=target.submission_id
                     AND replacement.evaluator_user_id!=target.evaluator_user_id
                     AND replacement.status!='revoked'
                 )"""
        ).bind(round_id, evaluator_user_id).first("count_value") or 0
    )
    if uncovered:
        raise HTTPException(status_code=409)
    active_count = int(
        await db.prepare(
            """SELECT COUNT(*) AS count_value FROM evaluation_assignments
               WHERE round_id=?1 AND evaluator_user_id=?2 AND status!='revoked'"""
        ).bind(round_id, evaluator_user_id).first("count_value") or 0
    )
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE evaluation_assignments SET status='revoked', updated_at_ms=?3
               WHERE round_id=?1 AND evaluator_user_id=?2 AND status!='revoked'"""
        ).bind(round_id, evaluator_user_id, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user", actor_user_id=auth.actor.user_id,
            action="evaluation_round.evaluator.remove", target_type="evaluation_round",
            target_id=round_id, result="succeeded", correlation_id=request.state.request_id,
            occurred_at_ms=now, organization_id=str(round_row["organization_id"]),
            event_id=str(round_row["event_id"]),
            metadata={"evaluator_user_id": evaluator_user_id, "assignment_count": active_count},
        )
    )
    await batch.execute()
    return RoundEvaluatorChange(
        round_id=round_id, evaluator_user_id=evaluator_user_id, assignment_count=active_count
    )


@evaluation_router.post(
    "/api/v1/admin/evaluation-rounds/{round_id}/evaluators/{evaluator_user_id}/reminder",
    response_model=EvaluatorReminderQueued,
    operation_id="remindEvaluationRoundEvaluator",
    tags=["evaluations", "communications"],
)
async def remind_round_evaluator(
    round_id: str, evaluator_user_id: str, request: Request
) -> EvaluatorReminderQueued:
    db = _db(request)
    row = row_mapping(
        await db.prepare(
            """SELECT r.organization_id,r.event_id,r.name,u.email,
                      COUNT(a.id) AS assigned_count,
                      SUM(CASE WHEN e.state='final' THEN 1 ELSE 0 END) AS completed_count
               FROM evaluation_rounds r
               JOIN evaluation_assignments a ON a.round_id=r.id AND a.status!='revoked'
               JOIN users u ON u.id=a.evaluator_user_id
               LEFT JOIN evaluations e ON e.assignment_id=a.id
               WHERE r.id=?1 AND a.evaluator_user_id=?2 AND r.status='open'
               GROUP BY r.id,u.id LIMIT 1"""
        ).bind(round_id, evaluator_user_id).first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request, Permission.COMMUNICATION_SEND,
        ResourceContext(str(row["organization_id"]), str(row["event_id"])), mutation=True,
    )
    outstanding = int(row["assigned_count"] or 0) - int(row["completed_count"] or 0)
    if outstanding <= 0:
        raise HTTPException(status_code=409)
    now, message_id = utc_now_ms(), new_id()
    deterministic = f"evaluation-reminder:{round_id}:{evaluator_user_id}:{now // 3_600_000}"
    html_body = (
        f"<p>You have {outstanding} outstanding review"
        f"{'s' if outstanding != 1 else ''} in {escape(str(row['name']))}.</p>"
        "<p>Open SessionBuddy and finish your assigned reviews.</p>"
    )
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_user_id,recipient_email,subject,
                html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9)"""
        ).bind(
            message_id, row["organization_id"], row["event_id"], evaluator_user_id,
            row["email"], f"Review reminder: {row['name']}", html_body, deterministic, now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user", actor_user_id=auth.actor.user_id,
            action="evaluation_round.evaluator.remind", target_type="evaluation_round",
            target_id=round_id, result="succeeded", correlation_id=request.state.request_id,
            occurred_at_ms=now, organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]), metadata={"outstanding_count": outstanding},
        )
    )
    await batch.execute()
    queue = getattr(request.scope.get("env"), "COMMUNICATION_QUEUE", None)
    if queue is not None:
        try:
            await queue.send({"schema_version": 1, "message_id": message_id})
        except Exception:
            # The durable queued message remains visible for operator replay.
            record_timing(request, "domain", 0)
    return EvaluatorReminderQueued(message_id=message_id)


_ANSWER_EXCLUDED_KEYS = frozenset(
    {"speaker_name", "speaker_email", "proposal_title", "proposal_abstract"}
)


def _answer_text(value: object, field_type: str) -> str:
    if field_type in {"file", "image"}:
        return "(uploaded file)" if value else ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if str(item).strip())
    if value is None:
        return ""
    return str(value).strip()


def _reviewer_answers(
    answers_json: str, schema_json: str, *, blind_review: bool
) -> tuple[list[SubmissionAnswerView], int]:
    """Project custom CFP answers for reviewers, in form order with labels.

    Core identity and proposal fields are always excluded: title and abstract
    render separately and speaker identity is masked upstream. Blind rounds
    fail closed: only fields the organizer explicitly marked ``blind_visible``
    are returned, and everything withheld is counted so the reviewer UI can
    say how many answers were hidden. Returns ``(answers, hidden_count)``.
    """
    try:
        answers = json.loads(answers_json)
        schema = json.loads(schema_json)
    except ValueError:
        return [], 0
    if not isinstance(answers, dict):
        return [], 0
    fields = schema.get("fields", []) if isinstance(schema, dict) else []
    views: list[SubmissionAnswerView] = []
    hidden = 0
    seen: set[str] = set()
    for field in fields:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key", ""))
        if not key or key in _ANSWER_EXCLUDED_KEYS or key in seen:
            continue
        seen.add(key)
        if key not in answers:
            continue
        value = _answer_text(answers[key], str(field.get("type", "")))
        if not value:
            continue
        if blind_review and field.get("blind_visible") is not True:
            hidden += 1
            continue
        label = str(field.get("label", "")) or key.replace("_", " ")
        views.append(SubmissionAnswerView(label=label, value=value))
    for key in sorted(answers):
        if key in seen or key in _ANSWER_EXCLUDED_KEYS:
            continue
        value = _answer_text(answers[key], "")
        if not value:
            continue
        if blind_review:
            # Answers with no schema definition have no blind_visible opt-in.
            hidden += 1
            continue
        views.append(SubmissionAnswerView(label=key.replace("_", " "), value=value))
    return views[:100], hidden


@evaluation_router.get(
    "/api/v1/evaluator/assignments",
    response_model=EvaluationAssignmentList,
    operation_id="listMyEvaluationAssignments",
    tags=["evaluations"],
)
async def list_my_assignments(request: Request) -> EvaluationAssignmentList:
    authenticated = await authenticate_request(request)
    now = utc_now_ms()
    cursor = request.query_params.get("cursor")
    try:
        requested_limit = int(request.query_params.get("limit", EVALUATION_PAGE_LIMIT))
    except ValueError as exc:
        raise HTTPException(status_code=422) from exc
    page_limit = max(1, min(EVALUATION_PAGE_LIMIT, requested_limit))
    decoded_cursor = _evaluation_cursor(
        request,
        cursor,
        kind="reviewer-assignments",
        scope_id=authenticated.actor.user_id,
    )
    after_created_at_ms, after_id = decoded_cursor if decoded_cursor else (None, None)
    assignment_query = """SELECT a.id, a.round_id, a.evaluator_user_id, a.status,
                  r.name AS round_name, a.submission_id,
                  s.proposal_title, s.proposal_abstract,
                  CASE WHEN COALESCE(json_extract(r.rubric_json,'$.blind_review'),0)=1
                       THEN 'Hidden for blind review' ELSE s.speaker_name END AS speaker_name,
                  r.organization_id, r.event_id, r.rubric_json,r.review_closes_at_ms,
                  COALESCE(e.state, 'not_started') AS evaluation_state,
                  e.rating, e.recommendation,
                  COALESCE(e.internal_comment, '') AS internal_comment,
                  COALESCE(e.criterion_scores_json, '{}') AS criterion_scores_json,
                  COALESCE(s.answers_json, '{}') AS answers_json,
                  COALESCE(f.schema_json, '{}') AS form_schema_json,
                  a.created_at_ms
           FROM evaluation_assignments a
           JOIN evaluation_rounds r ON r.id = a.round_id AND r.status = 'open'
           JOIN submissions s ON s.id = a.submission_id
           LEFT JOIN call_for_speaker_forms f ON f.id = s.form_id
           LEFT JOIN evaluations e ON e.assignment_id = a.id
           WHERE a.evaluator_user_id = ?1 AND a.status != 'revoked'
             AND (r.review_opens_at_ms IS NULL OR r.review_opens_at_ms<=?2)
             AND (r.review_closes_at_ms IS NULL OR r.review_closes_at_ms>?2)
             AND (?3 IS NULL OR a.created_at_ms<?3 OR (a.created_at_ms=?3 AND a.id<?4))
           ORDER BY a.created_at_ms DESC, a.id DESC LIMIT ?5"""
    rows = result_rows(
        await _timed_all(
            request,
            _db(request)
            .prepare(assignment_query)
            .bind(
                authenticated.actor.user_id,
                now,
                after_created_at_ms,
                after_id,
                page_limit + 1,
            ),
        )
    )
    page_rows = rows[:page_limit]
    data: list[EvaluationAssignmentView] = []
    for row in page_rows:
        await require_permission(
            request,
            Permission.SUBMISSION_READ_FOR_EVALUATION,
            ResourceContext(
                str(row["organization_id"]),
                str(row["event_id"]),
                evaluator_user_id=str(row["evaluator_user_id"]),
                evaluator_assignment_status=str(row["status"]),
                evaluation_round_open=True,
            ),
            mutation=False,
        )
        rubric = json.loads(str(row["rubric_json"]))
        answer_views, hidden_count = _reviewer_answers(
            str(row["answers_json"]),
            str(row["form_schema_json"]),
            blind_review=bool(rubric.get("blind_review", False)),
        )
        data.append(
            EvaluationAssignmentView(
                id=str(row["id"]),
                round_id=str(row["round_id"]),
                round_name=str(row["round_name"]),
                submission_id=str(row["submission_id"]),
                proposal_title=str(row["proposal_title"]),
                proposal_abstract=str(row["proposal_abstract"]),
                speaker_name=str(row["speaker_name"]),
                rating_min=int(rubric["rating"]["min"]),
                rating_max=int(rubric["rating"]["max"]),
                recommendations=list(rubric["recommendation"]["choices"]),
                evaluator_guidance=str(rubric.get("guidance", "")),
                comment_required=bool(
                    rubric.get("internal_comment", {}).get("required", False)
                ),
                criteria=list(rubric.get("criteria", [])),
                criterion_scores=json.loads(str(row["criterion_scores_json"])),
                blind_review=bool(rubric.get("blind_review", False)),
                review_closes_at_ms=(
                    int(row["review_closes_at_ms"])
                    if row["review_closes_at_ms"] is not None
                    else None
                ),
                evaluation_state=str(row["evaluation_state"]),
                rating=int(row["rating"]) if row["rating"] is not None else None,
                recommendation=(
                    str(row["recommendation"]) if row["recommendation"] is not None else None
                ),
                internal_comment=str(row["internal_comment"]),
                answers=answer_views,
                hidden_answer_count=hidden_count,
            )
        )
    counts = row_mapping(
        await _timed_first(
            request,
            _db(request)
            .prepare(
                """SELECT COUNT(a.id) AS total,
                          SUM(CASE WHEN e.state='final' THEN 1 ELSE 0 END) AS completed_count
                   FROM evaluation_assignments a
                   JOIN evaluation_rounds r ON r.id=a.round_id AND r.status='open'
                   LEFT JOIN evaluations e ON e.assignment_id=a.id
                   WHERE a.evaluator_user_id=?1 AND a.status!='revoked'
                     AND (r.review_opens_at_ms IS NULL OR r.review_opens_at_ms<=?2)
                     AND (r.review_closes_at_ms IS NULL OR r.review_closes_at_ms>?2)"""
            )
            .bind(authenticated.actor.user_id, now),
        )
    ) or {"total": 0, "completed_count": 0}
    next_cursor = None
    if len(rows) > page_limit and page_rows:
        last = page_rows[-1]
        next_cursor = _evaluation_next_cursor(
            request,
            kind="reviewer-assignments",
            scope_id=authenticated.actor.user_id,
            timestamp=int(last["created_at_ms"]),
            row_id=str(last["id"]),
        )
    return EvaluationAssignmentList(
        data=data,
        next_cursor=next_cursor,
        total=int(counts["total"] or 0),
        completed_count=int(counts["completed_count"] or 0),
    )


@evaluation_router.put(
    "/api/v1/evaluator/assignments/{assignment_id}/evaluation",
    response_model=EvaluationView,
    operation_id="saveMyEvaluation",
    tags=["evaluations"],
)
async def save_evaluation(
    assignment_id: str,
    request: Request,
    body: EvaluationSave,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EvaluationView:
    db = _db(request)
    assignment = row_mapping(
        await db.prepare(
            """SELECT a.id, a.organization_id, a.event_id, a.round_id, a.evaluator_user_id,
                  a.status, r.status AS round_status, r.rubric_json, e.id AS evaluation_id,
                  r.review_opens_at_ms,r.review_closes_at_ms,e.state AS existing_state,
                  COALESCE(e.version, 0) AS existing_version
           FROM evaluation_assignments a JOIN evaluation_rounds r ON r.id = a.round_id
           LEFT JOIN evaluations e ON e.assignment_id = a.id WHERE a.id = ?1 LIMIT 1"""
        )
        .bind(assignment_id)
        .first()
    )
    if assignment is None:
        raise HTTPException(status_code=404)
    now = utc_now_ms()
    within_window = (
        (assignment["review_opens_at_ms"] is None or int(assignment["review_opens_at_ms"]) <= now)
        and (
            assignment["review_closes_at_ms"] is None
            or int(assignment["review_closes_at_ms"]) > now
        )
    )
    authenticated = await require_permission(
        request,
        Permission.EVALUATION_SAVE,
        ResourceContext(
            str(assignment["organization_id"]),
            str(assignment["event_id"]),
            evaluator_user_id=str(assignment["evaluator_user_id"]),
            evaluator_assignment_status=str(assignment["status"]),
            evaluation_round_open=assignment["round_status"] == "open" and within_window,
        ),
        mutation=True,
    )
    if assignment["status"] == "revoked" or assignment["existing_state"] == "final":
        raise HTTPException(status_code=409)
    rubric = json.loads(str(assignment["rubric_json"]))
    rating_min, rating_max = int(rubric["rating"]["min"]), int(rubric["rating"]["max"])
    criteria = list(rubric.get("criteria", []))
    criterion_keys = {str(criterion["key"]) for criterion in criteria}
    if not set(body.criterion_scores) <= criterion_keys:
        raise HTTPException(status_code=422)
    if body.state == "final" and criteria and set(body.criterion_scores) != criterion_keys:
        raise HTTPException(status_code=422)
    if any(not rating_min <= score <= rating_max for score in body.criterion_scores.values()):
        raise HTTPException(status_code=422)
    rating = body.rating
    if criteria and body.criterion_scores:
        rating = round(
            sum(
                body.criterion_scores[str(criterion["key"])] * int(criterion["weight"])
                for criterion in criteria
                if str(criterion["key"]) in body.criterion_scores
            )
            / sum(
                int(criterion["weight"])
                for criterion in criteria
                if str(criterion["key"]) in body.criterion_scores
            )
        )
    if body.state == "final" and rating is None:
        raise HTTPException(status_code=422)
    if rating is not None and not rating_min <= rating <= rating_max:
        raise HTTPException(status_code=422)
    if (
        body.recommendation is not None
        and body.recommendation not in rubric["recommendation"]["choices"]
    ):
        raise HTTPException(status_code=422)
    if body.state == "final" and body.recommendation is None:
        raise HTTPException(status_code=422)
    if (
        body.state == "final"
        and rubric.get("internal_comment", {}).get("required")
        and not body.internal_comment
    ):
        raise HTTPException(status_code=422)

    key = _key(idempotency_key)
    route = "PUT /api/v1/evaluator/assignments/{assignment_id}/evaluation"
    fingerprint = hashlib.sha256(
        json.dumps(body.model_dump(), separators=(",", ":"), sort_keys=True).encode()
    ).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint, response_resource_id FROM idempotency_records
           WHERE principal_key = ?1 AND route_key = ?2 AND idempotency_key_hash = ?3
             AND state = 'completed'"""
        )
        .bind(authenticated.actor.user_id, route, hashlib.sha256(key.encode()).digest())
        .first()
    )
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _evaluation_view(db, str(replay["response_resource_id"]))

    evaluation_id = str(assignment["evaluation_id"] or new_id())
    version = int(assignment["existing_version"]) + 1
    finalized_at = now if body.state == "final" else None
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        organization_id=str(assignment["organization_id"]),
        event_id=str(assignment["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO evaluations
           (id, organization_id, event_id, round_id, assignment_id, evaluator_user_id,
            rating, recommendation, internal_comment,criterion_scores_json,state, version,
            created_at_ms,updated_at_ms, finalized_at_ms)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?13, ?14)
           ON CONFLICT(assignment_id) DO UPDATE SET rating = excluded.rating,
             recommendation = excluded.recommendation,
             internal_comment = excluded.internal_comment,
             criterion_scores_json=excluded.criterion_scores_json,state = excluded.state,
             version = excluded.version, updated_at_ms = excluded.updated_at_ms,
             finalized_at_ms = excluded.finalized_at_ms
           WHERE evaluations.state = 'draft'"""
        ).bind(
            evaluation_id,
            assignment["organization_id"],
            assignment["event_id"],
            assignment["round_id"],
            assignment_id,
            authenticated.actor.user_id,
            rating,
            body.recommendation,
            body.internal_comment,
            json.dumps(body.criterion_scores, separators=(",", ":"), sort_keys=True),
            body.state,
            version,
            now,
            finalized_at,
        )
    )
    if body.state == "final":
        batch.add_statement(
            db.prepare(
                """UPDATE evaluation_assignments SET status = 'completed', updated_at_ms = ?1
               WHERE id = ?2 AND evaluator_user_id = ?3 AND status = 'assigned'"""
            ).bind(now, assignment_id, authenticated.actor.user_id)
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=authenticated.actor.user_id,
            action=f"evaluation.{body.state}",
            target_type="evaluation",
            target_id=evaluation_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(assignment["organization_id"]),
            event_id=str(assignment["event_id"]),
            metadata={"state": body.state, "version": version},
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="evaluation",
        resource_id=evaluation_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return EvaluationView(
        id=evaluation_id,
        assignment_id=assignment_id,
        rating=rating,
        recommendation=body.recommendation,
        internal_comment=body.internal_comment,
        criterion_scores=body.criterion_scores,
        state=body.state,
        version=version,
    )


@evaluation_router.post(
    "/api/v1/evaluator/assignments/{assignment_id}/conflict",
    response_model=ConflictView,
    operation_id="declareEvaluationConflict",
    tags=["evaluations"],
)
async def declare_conflict(
    assignment_id: str,
    request: Request,
    body: ConflictDeclaration,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ConflictView:
    db = _db(request)
    assignment = row_mapping(
        await db.prepare(
            """SELECT a.organization_id, a.event_id, a.round_id, a.evaluator_user_id,
                  a.status, r.status AS round_status, e.state AS evaluation_state
           FROM evaluation_assignments a JOIN evaluation_rounds r ON r.id = a.round_id
           LEFT JOIN evaluations e ON e.assignment_id = a.id
           WHERE a.id = ?1 LIMIT 1"""
        )
        .bind(assignment_id)
        .first()
    )
    if assignment is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.EVALUATION_SAVE,
        ResourceContext(
            str(assignment["organization_id"]),
            str(assignment["event_id"]),
            evaluator_user_id=str(assignment["evaluator_user_id"]),
            evaluator_assignment_status=str(assignment["status"]),
            evaluation_round_open=assignment["round_status"] == "open",
        ),
        mutation=True,
    )
    if assignment["status"] != "assigned" or assignment["evaluation_state"] == "final":
        raise HTTPException(status_code=409)
    key = _key(idempotency_key)
    route = "POST /api/v1/evaluator/assignments/{assignment_id}/conflict"
    fingerprint = _fingerprint(body)
    replay = await _idempotency_replay(db, auth.actor.user_id, route, key, fingerprint)
    if replay is not None:
        return await _conflict_view(db, replay)
    now = utc_now_ms()
    conflict_id = new_id()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(assignment["organization_id"]),
        event_id=str(assignment["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO evaluation_conflicts
           (id, organization_id, event_id, round_id, assignment_id, evaluator_user_id,
            conflict_type, explanation, declared_at_ms)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)"""
        ).bind(
            conflict_id,
            assignment["organization_id"],
            assignment["event_id"],
            assignment["round_id"],
            assignment_id,
            auth.actor.user_id,
            body.conflict_type,
            body.explanation,
            now,
        )
    )
    batch.add_statement(
        db.prepare(
            """UPDATE evaluation_assignments SET status = 'revoked', updated_at_ms = ?1
           WHERE id = ?2 AND evaluator_user_id = ?3 AND status = 'assigned'"""
        ).bind(now, assignment_id, auth.actor.user_id)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation.conflict.declare",
            target_type="evaluation_assignment",
            target_id=assignment_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(assignment["organization_id"]),
            event_id=str(assignment["event_id"]),
            metadata={"conflict_type": body.conflict_type},
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="evaluation_conflict",
        resource_id=conflict_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return ConflictView(id=conflict_id, assignment_id=assignment_id, **body.model_dump())


@evaluation_router.post(
    "/api/v1/admin/evaluation-assignments/{assignment_id}/reassign",
    response_model=ReassignmentView,
    operation_id="reassignConflictedEvaluation",
    tags=["evaluations"],
)
async def reassign_conflict(
    assignment_id: str,
    request: Request,
    body: AssignmentReassign,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ReassignmentView:
    db = _db(request)
    assignment = row_mapping(
        await db.prepare(
            """SELECT a.organization_id, a.event_id, a.round_id, a.submission_id,
                  a.status, r.status AS round_status
           FROM evaluation_assignments a JOIN evaluation_rounds r ON r.id = a.round_id
           JOIN evaluation_conflicts c ON c.assignment_id = a.id
           WHERE a.id = ?1 LIMIT 1"""
        )
        .bind(assignment_id)
        .first()
    )
    if assignment is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(assignment["organization_id"]), str(assignment["event_id"])),
        mutation=True,
    )
    if assignment["status"] != "revoked" or assignment["round_status"] != "open":
        raise HTTPException(status_code=409)
    evaluator = (
        await db.prepare(
            """SELECT 1 AS found FROM users u JOIN user_roles ur ON ur.user_id=u.id
               JOIN identity_invitations i
                 ON i.organization_id=?2 AND i.event_id=?3
                AND i.normalized_email=u.normalized_email
                AND i.role='evaluator' AND i.status='accepted'
               WHERE u.id=?1 AND u.status='active'
                 AND ur.role='reviewer' AND ur.status='active' LIMIT 1"""
        )
        .bind(body.evaluator_user_id, assignment["organization_id"], assignment["event_id"])
        .first("found")
    )
    if evaluator is None:
        raise HTTPException(status_code=400)
    key = _key(idempotency_key)
    route = "POST /api/v1/admin/evaluation-assignments/{assignment_id}/reassign"
    fingerprint = _fingerprint(body)
    replay = await _idempotency_replay(db, auth.actor.user_id, route, key, fingerprint)
    if replay is not None:
        row = row_mapping(
            await db.prepare(
                "SELECT id, evaluator_user_id FROM evaluation_assignments WHERE id = ?1"
            )
            .bind(replay)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404)
        return ReassignmentView(
            assignment_id=str(row["id"]), evaluator_user_id=str(row["evaluator_user_id"])
        )
    now = utc_now_ms()
    new_assignment_id = new_id()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(assignment["organization_id"]),
        event_id=str(assignment["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO evaluation_assignments
           (id, organization_id, event_id, round_id, submission_id, evaluator_user_id,
            status, created_at_ms, updated_at_ms)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'assigned', ?7, ?7)"""
        ).bind(
            new_assignment_id,
            assignment["organization_id"],
            assignment["event_id"],
            assignment["round_id"],
            assignment["submission_id"],
            body.evaluator_user_id,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation.assignment.reassign",
            target_type="evaluation_assignment",
            target_id=new_assignment_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(assignment["organization_id"]),
            event_id=str(assignment["event_id"]),
            metadata={"replaced_assignment_id": assignment_id},
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="evaluation_assignment",
        resource_id=new_assignment_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return ReassignmentView(
        assignment_id=new_assignment_id, evaluator_user_id=body.evaluator_user_id
    )


@evaluation_router.get(
    "/api/v1/admin/evaluation-rounds/{round_id}/results",
    response_model=EvaluationRoundResults,
    operation_id="getEvaluationRoundResults",
    tags=["evaluations"],
)
async def get_round_results(
    round_id: str,
    request: Request,
    cursor: str | None = None,
    limit: int = EVALUATION_PAGE_LIMIT,
) -> EvaluationRoundResults:
    db = _db(request)
    round_row = row_mapping(
        await _timed_first(
            request,
            db.prepare(
            """SELECT id, organization_id, event_id, name, status
           FROM evaluation_rounds WHERE id = ?1 LIMIT 1"""
            ).bind(round_id),
        )
    )
    if round_row is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.EVALUATION_RESULTS_READ,
        ResourceContext(str(round_row["organization_id"]), str(round_row["event_id"])),
        mutation=False,
    )
    page_limit = max(1, min(EVALUATION_PAGE_LIMIT, limit))
    decoded_cursor = _evaluation_cursor(request, cursor, kind="round-results", scope_id=round_id)
    after_submitted_at_ms, after_id = decoded_cursor if decoded_cursor else (None, None)
    results_query = """SELECT s.id AS submission_id, s.speaker_name, s.proposal_title,
              s.submitted_at_ms,
              COUNT(a.id) AS assigned_count,
              SUM(CASE WHEN e.state = 'final' THEN 1 ELSE 0 END) AS completed_count,
              AVG(CASE WHEN e.state = 'final' THEN e.rating END) AS average_rating,
              d.decision,d.internal_reason
       FROM evaluation_assignments a
       JOIN submissions s ON s.id = a.submission_id
       LEFT JOIN evaluations e ON e.assignment_id = a.id
       LEFT JOIN submission_decisions d
         ON d.round_id = a.round_id AND d.submission_id = a.submission_id
       WHERE a.round_id = ?1 AND a.status != 'revoked'
         AND (?2 IS NULL OR s.submitted_at_ms<?2 OR (s.submitted_at_ms=?2 AND s.id<?3))
       GROUP BY s.id, s.speaker_name, s.proposal_title, d.decision,d.internal_reason
       ORDER BY s.submitted_at_ms DESC, s.id DESC LIMIT ?4"""
    fetched_rows = result_rows(
        await _timed_all(
            request,
            db.prepare(results_query).bind(
                round_id, after_submitted_at_ms, after_id, page_limit + 1
            ),
        )
    )
    rows = fetched_rows[:page_limit]
    submission_ids = [str(row["submission_id"]) for row in rows]
    review_rows: list[dict] = []
    if submission_ids:
        review_query = """SELECT a.submission_id,COALESCE(u.display_name,u.email) AS evaluator_name,
                      COALESCE(e.state,'not_started') AS state,e.rating,e.recommendation,
                      COALESCE(e.internal_comment,'') AS internal_comment
               FROM evaluation_assignments a JOIN users u ON u.id=a.evaluator_user_id
               LEFT JOIN evaluations e ON e.assignment_id=a.id
               WHERE a.round_id=?1 AND a.status!='revoked'
                 AND a.submission_id IN (SELECT value FROM json_each(?2))
               ORDER BY a.submission_id,u.normalized_email,a.id"""
        review_rows = result_rows(
            await _timed_all(
                request,
                db.prepare(review_query).bind(round_id, json.dumps(submission_ids)),
            )
        )
    reviews_by_submission: dict[str, list[EvaluationDetail]] = {}
    for review in review_rows:
        reviews_by_submission.setdefault(str(review["submission_id"]), []).append(
            EvaluationDetail(
                evaluator_name=str(review["evaluator_name"]),
                state=str(review["state"]),
                rating=int(review["rating"]) if review["rating"] is not None else None,
                recommendation=(
                    str(review["recommendation"])
                    if review["recommendation"] is not None
                    else None
                ),
                internal_comment=str(review["internal_comment"]),
            )
        )
    submissions = [
        SubmissionEvaluationResult(
            submission_id=str(row["submission_id"]),
            speaker_name=str(row["speaker_name"]),
            proposal_title=str(row["proposal_title"]),
            assigned_count=int(row["assigned_count"]),
            completed_count=int(row["completed_count"] or 0),
            average_rating=(
                round(float(row["average_rating"]), 2)
                if row["average_rating"] is not None
                else None
            ),
            decision=(str(row["decision"]) if row["decision"] is not None else None),
            internal_reason=str(row["internal_reason"] or ""),
            reviews=reviews_by_submission.get(str(row["submission_id"]), []),
        )
        for row in rows
    ]
    aggregate = row_mapping(
        await _timed_first(
            request,
            db.prepare(
                """SELECT COUNT(a.id) AS assigned_count,
                          SUM(CASE WHEN e.state='final' THEN 1 ELSE 0 END) AS completed_count,
                          AVG(CASE WHEN e.state='final' THEN e.rating END) AS average_rating,
                          COUNT(DISTINCT a.submission_id) AS submission_count
                   FROM evaluation_assignments a
                   LEFT JOIN evaluations e ON e.assignment_id=a.id
                   WHERE a.round_id=?1 AND a.status!='revoked'"""
            ).bind(round_id),
        )
    ) or {
        "assigned_count": 0,
        "completed_count": 0,
        "average_rating": None,
        "submission_count": 0,
    }
    evaluator_rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT a.evaluator_user_id, COALESCE(u.display_name,u.email) AS display_name,
                  SUM(CASE WHEN a.status != 'revoked' THEN 1 ELSE 0 END) AS assigned_count,
                  SUM(CASE WHEN e.state = 'final' THEN 1 ELSE 0 END) AS completed_count,
                  COUNT(c.id) AS conflict_count
           FROM evaluation_assignments a JOIN users u ON u.id = a.evaluator_user_id
           LEFT JOIN evaluations e ON e.assignment_id = a.id
           LEFT JOIN evaluation_conflicts c ON c.assignment_id = a.id
           WHERE a.round_id = ?1 GROUP BY a.evaluator_user_id, u.email
           ORDER BY u.normalized_email LIMIT 100"""
            ).bind(round_id),
        )
    )
    evaluator_progress = [
        EvaluatorProgress(
            evaluator_user_id=str(row["evaluator_user_id"]),
            display_name=str(row["display_name"]),
            assigned_count=int(row["assigned_count"] or 0),
            completed_count=int(row["completed_count"] or 0),
            conflict_count=int(row["conflict_count"] or 0),
        )
        for row in evaluator_rows
    ]
    available_evaluator_rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT DISTINCT u.id AS user_id,
                          COALESCE(u.display_name,u.email) AS display_name
                   FROM evaluation_assignments a JOIN users u ON u.id=a.evaluator_user_id
                   JOIN user_roles ur ON ur.user_id=u.id
                   WHERE a.organization_id=?1 AND a.event_id=?2
                     AND a.status!='revoked' AND u.status='active'
                     AND ur.role='reviewer' AND ur.status='active'
                   ORDER BY u.normalized_email LIMIT 100"""
            ).bind(round_row["organization_id"], round_row["event_id"]),
        )
    )
    available_evaluators = [
        EvaluatorView.model_validate(row) for row in available_evaluator_rows
    ]
    conflict_rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT c.assignment_id, c.evaluator_user_id,
                  COALESCE(u.display_name,u.email) AS evaluator_name,
                  s.proposal_title, c.conflict_type,
                  NOT EXISTS (
                    SELECT 1 FROM evaluation_assignments replacement
                    WHERE replacement.round_id = a.round_id
                      AND replacement.submission_id = a.submission_id
                      AND replacement.status != 'revoked'
                  ) AS replacement_required
           FROM evaluation_conflicts c
           JOIN evaluation_assignments a ON a.id = c.assignment_id
           JOIN users u ON u.id = c.evaluator_user_id
           JOIN submissions s ON s.id = a.submission_id
           WHERE c.round_id = ?1 AND a.status = 'revoked'
           ORDER BY c.declared_at_ms DESC LIMIT 100"""
            ).bind(round_id),
        )
    )
    conflicts = [ConflictProgress.model_validate(row) for row in conflict_rows]
    next_cursor = None
    if len(fetched_rows) > page_limit and rows:
        last = rows[-1]
        next_cursor = _evaluation_next_cursor(
            request,
            kind="round-results",
            scope_id=round_id,
            timestamp=int(last["submitted_at_ms"]),
            row_id=str(last["submission_id"]),
        )
    return EvaluationRoundResults(
        round_id=round_id,
        event_id=str(round_row["event_id"]),
        round_name=str(round_row["name"]),
        status=str(round_row["status"]),
        assigned_count=int(aggregate["assigned_count"] or 0),
        completed_count=int(aggregate["completed_count"] or 0),
        average_rating=(
            round(float(aggregate["average_rating"]), 2)
            if aggregate["average_rating"] is not None
            else None
        ),
        submissions=submissions,
        submission_count=int(aggregate["submission_count"] or 0),
        next_cursor=next_cursor,
        evaluators=evaluator_progress,
        available_evaluators=available_evaluators,
        conflicts=conflicts,
    )


@evaluation_router.get(
    "/api/v1/admin/evaluation-rounds/{round_id}/export.csv",
    response_class=Response,
    operation_id="exportEvaluationRoundResults",
    tags=["evaluations"],
)
async def export_round_results(round_id: str, request: Request) -> Response:
    results = await get_round_results(round_id, request)
    output = StringIO(newline="")
    csv = writer(output)
    csv.writerow(
        [
            "submission_id",
            "proposal_title",
            "speaker_name",
            "assigned_reviews",
            "completed_reviews",
            "average_rating",
            "decision",
        ]
    )

    def safe(value: object) -> object:
        if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(
            ("=", "+", "-", "@")
        ):
            return f"'{value}"
        return value

    for submission in results.submissions:
        csv.writerow(
            [
                submission.submission_id,
                safe(submission.proposal_title),
                safe(submission.speaker_name),
                submission.assigned_count,
                submission.completed_count,
                submission.average_rating if submission.average_rating is not None else "",
                submission.decision or "",
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="evaluation-round-{round_id}.csv"',
        },
    )


@evaluation_router.post(
    "/api/v1/admin/evaluation-rounds/{round_id}/close",
    response_model=EvaluationRoundClosed,
    operation_id="closeEvaluationRound",
    tags=["evaluations"],
)
async def close_evaluation_round(
    round_id: str,
    request: Request,
    body: EvaluationRoundCloseRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EvaluationRoundClosed:
    db = _db(request)
    round_row = row_mapping(
        await db.prepare(
            """SELECT id, organization_id, event_id, status
           FROM evaluation_rounds WHERE id = ?1 LIMIT 1"""
        )
        .bind(round_id)
        .first()
    )
    if round_row is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(round_row["organization_id"]), str(round_row["event_id"])),
        mutation=True,
    )
    key = _key(idempotency_key)
    if round_row["status"] == "closed":
        return EvaluationRoundClosed(round_id=round_id)
    route = "POST /api/v1/admin/evaluation-rounds/{round_id}/close"
    fingerprint = _fingerprint(body)
    replay = await _idempotency_replay(db, auth.actor.user_id, route, key, fingerprint)
    if replay is not None:
        return EvaluationRoundClosed(round_id=round_id)
    counts = row_mapping(
        await db.prepare(
            """SELECT COUNT(DISTINCT submission_id) AS total_submissions,
                  COUNT(DISTINCT CASE WHEN status != 'revoked' THEN submission_id END)
                    AS covered_submissions,
                  SUM(CASE WHEN status != 'revoked' AND
                    NOT EXISTS (SELECT 1 FROM evaluations e
                                WHERE e.assignment_id = evaluation_assignments.id
                                  AND e.state = 'final') THEN 1 ELSE 0 END) AS outstanding
           FROM evaluation_assignments WHERE round_id = ?1"""
        )
        .bind(round_id)
        .first()
    )
    incomplete = (
        counts is None
        or int(counts["total_submissions"] or 0) == 0
        or int(counts["covered_submissions"] or 0) < int(counts["total_submissions"] or 0)
        or int(counts["outstanding"] or 0) > 0
    )
    if incomplete and not body.force:
        raise HTTPException(status_code=409)
    now = utc_now_ms()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(round_row["organization_id"]),
        event_id=str(round_row["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """UPDATE evaluation_rounds SET status = 'closed', closed_at_ms = ?1,
             updated_at_ms = ?1 WHERE id = ?2 AND status = 'open'"""
        ).bind(now, round_id)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation_round.close",
            target_type="evaluation_round",
            target_id=round_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(round_row["organization_id"]),
            event_id=str(round_row["event_id"]),
            metadata={
                "evaluations_read_only": True,
                "forced": body.force,
                "outstanding_count": int(counts["outstanding"] or 0) if counts else 0,
                "reason_length": len(body.reason),
            },
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="evaluation_round",
        resource_id=round_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return EvaluationRoundClosed(round_id=round_id)


@evaluation_router.post(
    "/api/v1/admin/evaluation-rounds/{round_id}/submissions/{submission_id}/decision",
    response_model=SubmissionDecisionView,
    operation_id="recordSubmissionDecision",
    tags=["evaluations"],
)
async def record_submission_decision(
    round_id: str,
    submission_id: str,
    request: Request,
    body: SubmissionDecisionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SubmissionDecisionView:
    db = _db(request)
    context = row_mapping(
        await db.prepare(
            """SELECT r.organization_id, r.event_id, COUNT(a.id) AS assigned_count,
                  SUM(CASE WHEN e.state = 'final' THEN 1 ELSE 0 END) AS completed_count
           FROM evaluation_rounds r
           JOIN evaluation_assignments a ON a.round_id = r.id AND a.status != 'revoked'
           LEFT JOIN evaluations e ON e.assignment_id = a.id
           WHERE r.id = ?1 AND a.submission_id = ?2
           GROUP BY r.organization_id, r.event_id"""
        )
        .bind(round_id, submission_id)
        .first()
    )
    if context is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(context["organization_id"]), str(context["event_id"])),
        mutation=True,
    )
    incomplete_reviews = int(context["completed_count"] or 0) < int(context["assigned_count"])
    if incomplete_reviews and not body.override_incomplete_reviews:
        raise HTTPException(status_code=409)
    speaker = row_mapping(
        await db.prepare(
            """SELECT s.speaker_email,s.submitter_user_id,s.proposal_title,e.name AS event_name,
                      ss.event_speaker_id,p.biography,
                      EXISTS(
                        SELECT 1 FROM user_headshots uh
                         WHERE uh.user_id=p.user_id
                      ) AS has_account_headshot,
                      EXISTS(
                        SELECT 1 FROM speaker_assets sa
                        JOIN asset_versions av ON av.asset_id=sa.id
                          AND av.is_current=1 AND av.scan_state='clean'
                         WHERE sa.organization_id=s.organization_id
                           AND sa.event_id=s.event_id
                           AND sa.event_speaker_id=ss.event_speaker_id
                           AND sa.kind='headshot'
                      ) AS has_event_headshot,
                      EXISTS(
                        SELECT 1 FROM speaker_tasks st
                         WHERE st.organization_id=s.organization_id
                           AND st.event_id=s.event_id
                           AND st.event_speaker_id=ss.event_speaker_id
                           AND st.task_type='profile' AND st.state='open'
                      ) AS has_profile_task,
                      EXISTS(
                        SELECT 1 FROM speaker_tasks st
                         WHERE st.organization_id=s.organization_id
                           AND st.event_id=s.event_id
                           AND st.event_speaker_id=ss.event_speaker_id
                           AND st.task_type='headshot' AND st.state='open'
                      ) AS has_headshot_task
               FROM submissions s
               JOIN events e ON e.organization_id=s.organization_id AND e.id=s.event_id
               LEFT JOIN submission_speakers ss ON ss.organization_id=s.organization_id
                 AND ss.event_id=s.event_id AND ss.submission_id=s.id AND ss.role='primary'
               LEFT JOIN event_speakers es ON es.organization_id=s.organization_id
                 AND es.event_id=s.event_id AND es.id=ss.event_speaker_id
               LEFT JOIN people p ON p.organization_id=s.organization_id AND p.id=es.person_id
               WHERE s.id=?1 AND s.organization_id=?2 AND s.event_id=?3 LIMIT 1"""
        )
        .bind(submission_id, context["organization_id"], context["event_id"])
        .first()
    )
    if speaker is None:
        raise HTTPException(status_code=404)
    if body.send_email and not str(speaker["speaker_email"] or "").strip():
        raise HTTPException(status_code=409)
    key = _key(idempotency_key)
    route = "POST /api/v1/admin/evaluation-rounds/{round_id}/submissions/{submission_id}/decision"
    fingerprint = _fingerprint(body)
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint, response_resource_id FROM idempotency_records
           WHERE principal_key = ?1 AND route_key = ?2 AND idempotency_key_hash = ?3
             AND state = 'completed'"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(key.encode()).digest())
        .first()
    )
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(status_code=409)
        return await _decision_view(db, str(replay["response_resource_id"]))
    existing = row_mapping(
        await db.prepare(
            """SELECT id, version FROM submission_decisions
               WHERE organization_id=?1 AND event_id=?2 AND submission_id=?3"""
        )
        .bind(context["organization_id"], context["event_id"], submission_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409)
    decision_id = new_id()
    communication_id = new_id() if body.send_email else None
    version = 1
    now = utc_now_ms()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(context["organization_id"]),
        event_id=str(context["event_id"]),
        route_key=route,
        idempotency_key=key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO submission_decisions
           (id, organization_id, event_id, round_id, submission_id, decision,
            internal_reason, version, decided_by_user_id, decided_at_ms, updated_at_ms)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?10)
           """
        ).bind(
            decision_id,
            context["organization_id"],
            context["event_id"],
            round_id,
            submission_id,
            body.decision,
            body.internal_reason,
            version,
            auth.actor.user_id,
            now,
        )
    )
    if body.decision == "accepted":
        batch.add_statement(
            db.prepare(
                """INSERT INTO accepted_sessions
                   (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6)"""
            ).bind(
                new_id(),
                context["organization_id"],
                context["event_id"],
                submission_id,
                decision_id,
                now,
            )
        )
        if speaker["event_speaker_id"] is not None:
            batch.add_statement(
                db.prepare(
                    """UPDATE event_speakers SET selection_status='accepted',status='onboarding',
                              accepted_at_ms=?1,last_activity_at_ms=?1,updated_at_ms=?1
                       WHERE organization_id=?2 AND event_id=?3 AND id=?4"""
                ).bind(
                    now,
                    context["organization_id"],
                    context["event_id"],
                    speaker["event_speaker_id"],
                )
            )
            # Registration has already established the speaker's identity. Acceptance
            # therefore creates work only for information or assets that are actually
            # missing; a generic supporting-document request has no actionable meaning
            # and must be created later as an explicit, contextual request if needed.
            tasks = _acceptance_speaker_tasks(speaker)
            for task_type, title, help_text, days in tasks:
                batch.add_statement(
                    db.prepare(
                        """INSERT INTO speaker_tasks
                           (id,organization_id,event_id,event_speaker_id,submission_id,task_type,
                            title,help_text,destination_type,state,due_at_ms,created_at_ms,updated_at_ms)
                           VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?6,'open',?9,?10,?10)"""
                    ).bind(
                        new_id(),
                        context["organization_id"],
                        context["event_id"],
                        speaker["event_speaker_id"],
                        submission_id,
                        task_type,
                        title,
                        help_text,
                        now + days * 86_400_000,
                        now,
                    )
                )
    elif speaker["event_speaker_id"] is not None:
        # Downgrade selection status only when no OTHER submission of this
        # speaker has an accepted decision — a rejection of proposal B must not
        # clobber the accepted status earned by proposal A.
        batch.add_statement(
            db.prepare(
                """UPDATE event_speakers SET selection_status='rejected',last_activity_at_ms=?1,
                          updated_at_ms=?1 WHERE organization_id=?2 AND event_id=?3 AND id=?4
                     AND NOT EXISTS (
                       SELECT 1 FROM submission_decisions d
                       JOIN submission_speakers ss ON ss.submission_id=d.submission_id
                       WHERE ss.organization_id=?2 AND ss.event_id=?3
                         AND ss.event_speaker_id=?4 AND d.decision='accepted'
                         AND d.submission_id!=?5
                     )"""
            ).bind(
                now,
                context["organization_id"],
                context["event_id"],
                speaker["event_speaker_id"],
                submission_id,
            )
        )
        # Waive only the rejected submission's tasks, never tasks that belong
        # to another (accepted) submission of the same speaker.
        batch.add_statement(
            db.prepare(
                """UPDATE speaker_tasks SET state='waived',waived_at_ms=?1,updated_at_ms=?1,
                          version=version+1 WHERE organization_id=?2 AND event_id=?3
                          AND event_speaker_id=?4 AND state='open'
                          AND COALESCE(submission_id,'')=?5"""
            ).bind(
                now,
                context["organization_id"],
                context["event_id"],
                speaker["event_speaker_id"],
                submission_id,
            )
        )
    if communication_id is not None:
        outcome = "accepted" if body.decision == "accepted" else "not selected"
        message = body.speaker_message or (
            "Congratulations — your session has been accepted. "
            "Open your speaker portal for next steps."
            if body.decision == "accepted"
            else "Thank you for your proposal. It was not selected for this event."
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO communication_messages
                   (id,organization_id,event_id,recipient_user_id,recipient_email,subject,
                    html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9)"""
            ).bind(
                communication_id,
                context["organization_id"],
                context["event_id"],
                speaker["submitter_user_id"],
                speaker["speaker_email"],
                f"{speaker['event_name']}: proposal {outcome}",
                f"<p>{escape(message)}</p><p><strong>{escape(str(speaker['proposal_title']))}</strong></p>",
                f"submission-decision:{decision_id}:v1",
                now,
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="submission.decision.record",
            target_type="submission",
            target_id=submission_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(context["organization_id"]),
            event_id=str(context["event_id"]),
            metadata={
                "decision": body.decision,
                "version": version,
                "communication_queued": bool(communication_id),
                "onboarding_created": body.decision == "accepted",
                "review_override": incomplete_reviews,
            },
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="submission_decision",
        resource_id=decision_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    if communication_id is not None:
        queue = getattr(request.scope.get("env"), "COMMUNICATION_QUEUE", None)
        if queue is not None:
            try:
                await queue.send({"schema_version": 1, "message_id": communication_id})
            except Exception:
                record_timing(request, "domain", 0)
    return SubmissionDecisionView(
        id=decision_id,
        submission_id=submission_id,
        round_id=round_id,
        version=version,
        communication_queued=communication_id is not None,
        **body.model_dump(),
    )


async def _round_view(db, round_id: str) -> EvaluationRoundView:
    row = row_mapping(
        await db.prepare(
            """SELECT r.id, r.event_id, r.name, r.status, COUNT(a.id) AS assignment_count,
                  COUNT(DISTINCT a.evaluator_user_id) AS evaluator_count
           FROM evaluation_rounds r LEFT JOIN evaluation_assignments a ON a.round_id = r.id
           WHERE r.id = ?1 GROUP BY r.id"""
        )
        .bind(round_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return EvaluationRoundView.model_validate(row)


async def _evaluation_view(db, evaluation_id: str) -> EvaluationView:
    row = row_mapping(
        await db.prepare(
            """SELECT id, assignment_id, rating, recommendation, internal_comment,
                      criterion_scores_json,state, version
           FROM evaluations WHERE id = ?1"""
        )
        .bind(evaluation_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    row["criterion_scores"] = json.loads(str(row.pop("criterion_scores_json")))
    return EvaluationView.model_validate(row)


async def _decision_view(db, decision_id: str) -> SubmissionDecisionView:
    row = row_mapping(
        await db.prepare(
            """SELECT d.id,d.submission_id,d.round_id,d.decision,d.internal_reason,d.version,
                      EXISTS(SELECT 1 FROM communication_messages cm
                        WHERE cm.deterministic_key='submission-decision:' || d.id || ':v1')
                        AS send_email,
                      EXISTS(SELECT 1 FROM communication_messages cm
                        WHERE cm.deterministic_key='submission-decision:' || d.id || ':v1')
                        AS communication_queued,
                      '' AS speaker_message
               FROM submission_decisions d WHERE d.id = ?1"""
        )
        .bind(decision_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return SubmissionDecisionView.model_validate(row)


async def _conflict_view(db, conflict_id: str) -> ConflictView:
    row = row_mapping(
        await db.prepare(
            """SELECT id, assignment_id, conflict_type, explanation
           FROM evaluation_conflicts WHERE id = ?1"""
        )
        .bind(conflict_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    return ConflictView.model_validate(row)


async def _idempotency_replay(
    db, principal: str, route: str, key: str, fingerprint: bytes
) -> str | None:
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint, response_resource_id FROM idempotency_records
           WHERE principal_key = ?1 AND route_key = ?2 AND idempotency_key_hash = ?3
             AND state = 'completed'"""
        )
        .bind(principal, route, hashlib.sha256(key.encode()).digest())
        .first()
    )
    if replay is None:
        return None
    if _blob(replay["request_fingerprint"]) != fingerprint:
        raise HTTPException(status_code=409)
    return str(replay["response_resource_id"])


async def _execute(request: Request, batch: CommandBatch) -> None:
    started = perf_counter()
    try:
        await batch.execute()
    except PersistenceError as exc:
        raise HTTPException(status_code=409) from exc
    finally:
        record_timing(request, "db", (perf_counter() - started) * 1000)


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

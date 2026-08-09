import hashlib
import json
from html import escape
from time import perf_counter

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from sessionbuddy.console import embedded_assets
from sessionbuddy.observability import record_timing
from sessionbuddy.platform.auth.http import authenticate_request, require_permission
from sessionbuddy.platform.authorization import Permission, ResourceContext
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch, IdempotencyRecord
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms

from .models import (
    AssignmentReassign,
    ConflictDeclaration,
    ConflictProgress,
    ConflictView,
    EvaluationAssignmentList,
    EvaluationAssignmentView,
    EvaluationRoundClosed,
    EvaluationRoundCreate,
    EvaluationRoundResults,
    EvaluationRoundView,
    EvaluationSave,
    EvaluationView,
    EvaluatorList,
    EvaluatorProgress,
    EvaluatorView,
    ReassignmentView,
    SubmissionDecisionCreate,
    SubmissionDecisionView,
    SubmissionEvaluationResult,
)

evaluation_router = APIRouter()


def _asset(name: str) -> str:
    return getattr(embedded_assets, embedded_assets.ASSETS[name])


@evaluation_router.get("/reviews", response_class=HTMLResponse, include_in_schema=False)
async def reviews_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_asset("app/index.html"), headers={"Cache-Control": "no-store"})


@evaluation_router.get(
    "/admin/evaluation-rounds/{round_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def admin_round_page(round_id: str, request: Request) -> HTMLResponse:
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


@evaluation_router.post(
    "/api/v1/admin/programs/{program_id}/evaluation-rounds",
    response_model=EvaluationRoundView,
    status_code=201,
    operation_id="createEvaluationRound",
    tags=["evaluations"],
)
async def create_evaluation_round(
    program_id: str,
    request: Request,
    body: EvaluationRoundCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EvaluationRoundView:
    db = _db(request)
    program = row_mapping(
        await db.prepare("SELECT organization_id, event_id FROM programs WHERE id = ?1")
        .bind(program_id)
        .first()
    )
    if program is None:
        raise HTTPException(status_code=404)
    organization_id = str(program["organization_id"])
    event_id = str(program["event_id"])
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(organization_id, event_id),
        mutation=True,
    )
    key = _key(idempotency_key)
    route = "POST /api/v1/admin/programs/{program_id}/evaluation-rounds"
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
           WHERE organization_id = ?1 AND event_id = ?2 AND program_id = ?3
             AND status = 'open' LIMIT 1"""
        )
        .bind(organization_id, event_id, program_id)
        .first("found")
    )
    if active_round is not None:
        raise HTTPException(status_code=409)

    evaluator_placeholders = ",".join(
        f"?{index + 3}" for index in range(len(body.evaluator_user_ids))
    )
    evaluators = result_rows(
        await db.prepare(
            f"""SELECT user_id FROM event_memberships
            WHERE organization_id = ?1 AND event_id = ?2
              AND user_id IN ({evaluator_placeholders})
              AND role = 'evaluator' AND status = 'active'"""  # noqa: S608
        )
        .bind(organization_id, event_id, *body.evaluator_user_ids)
        .all()
    )
    if {str(row["user_id"]) for row in evaluators} != set(body.evaluator_user_ids):
        raise HTTPException(status_code=400)
    placeholders = ",".join(f"?{index + 4}" for index in range(len(body.submission_ids)))
    submissions = result_rows(
        await db.prepare(
            f"""SELECT id FROM submissions WHERE organization_id = ?1 AND event_id = ?2
                  AND program_id = ?3 AND id IN ({placeholders})"""  # noqa: S608
        )
        .bind(organization_id, event_id, program_id, *body.submission_ids)
        .all()
    )
    if {str(row["id"]) for row in submissions} != set(body.submission_ids):
        raise HTTPException(status_code=400)

    now = utc_now_ms()
    round_id = new_id()
    rubric = {
        "rating": {"min": body.rating_min, "max": body.rating_max, "required": True},
        "recommendation": {"choices": body.recommendations, "required": True},
        "internal_comment": {"required": False},
        "guidance": body.evaluator_guidance,
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
               (id, organization_id, event_id, program_id, name, rubric_json, status,
                created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?5, ?6, 'open', ?7, ?7)"""
        ).bind(
            round_id,
            organization_id,
            event_id,
            program_id,
            body.name,
            json.dumps(rubric, separators=(",", ":"), sort_keys=True),
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
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="evaluation_round",
        resource_id=round_id,
        completed_at_ms=now,
    )
    await _execute(request, batch)
    return EvaluationRoundView(
        id=round_id,
        program_id=program_id,
        name=body.name,
        status="open",
        assignment_count=len(assignment_pairs),
        evaluator_count=len(body.evaluator_user_ids),
    )


@evaluation_router.get(
    "/api/v1/admin/programs/{program_id}/evaluation-rounds/current",
    response_model=EvaluationRoundView | None,
    operation_id="getCurrentEvaluationRound",
    tags=["evaluations"],
)
async def get_current_evaluation_round(
    program_id: str, request: Request
) -> EvaluationRoundView | None:
    db = _db(request)
    program = row_mapping(
        await db.prepare("SELECT organization_id, event_id FROM programs WHERE id = ?1")
        .bind(program_id)
        .first()
    )
    if program is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.EVALUATION_RESULTS_READ,
        ResourceContext(str(program["organization_id"]), str(program["event_id"])),
        mutation=False,
    )
    row = row_mapping(
        await db.prepare(
            """SELECT r.id, r.program_id, r.name, r.status, COUNT(a.id) AS assignment_count,
                  COUNT(DISTINCT a.evaluator_user_id) AS evaluator_count
           FROM evaluation_rounds r LEFT JOIN evaluation_assignments a ON a.round_id = r.id
           WHERE r.program_id = ?1 AND r.status = 'open'
           GROUP BY r.id ORDER BY r.created_at_ms DESC LIMIT 1"""
        )
        .bind(program_id)
        .first()
    )
    return EvaluationRoundView.model_validate(row) if row is not None else None


@evaluation_router.get(
    "/api/v1/admin/programs/{program_id}/evaluators",
    response_model=EvaluatorList,
    operation_id="listProgramEvaluators",
    tags=["evaluations"],
)
async def list_program_evaluators(program_id: str, request: Request) -> EvaluatorList:
    db = _db(request)
    program = row_mapping(
        await db.prepare("SELECT organization_id, event_id FROM programs WHERE id = ?1")
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
    rows = result_rows(
        await db.prepare(
            """SELECT u.id AS user_id, u.email AS display_name
           FROM event_memberships em JOIN users u ON u.id = em.user_id
           WHERE em.organization_id = ?1 AND em.event_id = ?2
             AND em.role = 'evaluator' AND em.status = 'active' AND u.status = 'active'
           ORDER BY u.normalized_email LIMIT 100"""
        )
        .bind(program["organization_id"], program["event_id"])
        .all()
    )
    return EvaluatorList(data=[EvaluatorView.model_validate(row) for row in rows])


@evaluation_router.get(
    "/api/v1/evaluator/assignments",
    response_model=EvaluationAssignmentList,
    operation_id="listMyEvaluationAssignments",
    tags=["evaluations"],
)
async def list_my_assignments(request: Request) -> EvaluationAssignmentList:
    authenticated = await authenticate_request(request)
    rows = result_rows(
        await _timed_all(
            request,
            _db(request)
            .prepare(
                """SELECT a.id, a.round_id, r.name AS round_name, a.submission_id,
                      s.proposal_title, s.proposal_abstract, s.speaker_name,
                      r.organization_id, r.event_id, r.rubric_json,
                      COALESCE(e.state, 'not_started') AS evaluation_state,
                      e.rating, e.recommendation,
                      COALESCE(e.internal_comment, '') AS internal_comment
               FROM evaluation_assignments a
               JOIN evaluation_rounds r ON r.id = a.round_id AND r.status = 'open'
               JOIN submissions s ON s.id = a.submission_id
               LEFT JOIN evaluations e ON e.assignment_id = a.id
               WHERE a.evaluator_user_id = ?1 AND a.status != 'revoked'
               ORDER BY a.created_at_ms DESC, a.id DESC LIMIT 100"""
            )
            .bind(authenticated.actor.user_id),
        )
    )
    data: list[EvaluationAssignmentView] = []
    for row in rows:
        await require_permission(
            request,
            Permission.SUBMISSION_READ_FOR_EVALUATION,
            ResourceContext(
                str(row["organization_id"]),
                str(row["event_id"]),
                evaluator_assigned=True,
                evaluation_round_open=True,
            ),
            mutation=False,
        )
        rubric = json.loads(str(row["rubric_json"]))
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
                evaluation_state=str(row["evaluation_state"]),
                rating=int(row["rating"]) if row["rating"] is not None else None,
                recommendation=(
                    str(row["recommendation"]) if row["recommendation"] is not None else None
                ),
                internal_comment=str(row["internal_comment"]),
            )
        )
    return EvaluationAssignmentList(data=data)


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
                  e.state AS existing_state, COALESCE(e.version, 0) AS existing_version
           FROM evaluation_assignments a JOIN evaluation_rounds r ON r.id = a.round_id
           LEFT JOIN evaluations e ON e.assignment_id = a.id WHERE a.id = ?1 LIMIT 1"""
        )
        .bind(assignment_id)
        .first()
    )
    if assignment is None:
        raise HTTPException(status_code=404)
    authenticated = await require_permission(
        request,
        Permission.EVALUATION_SAVE,
        ResourceContext(
            str(assignment["organization_id"]),
            str(assignment["event_id"]),
            evaluator_assigned=True,
            evaluation_round_open=assignment["round_status"] == "open",
        ),
        mutation=True,
    )
    if str(assignment["evaluator_user_id"]) != authenticated.actor.user_id:
        raise HTTPException(status_code=404)
    if assignment["status"] == "revoked" or assignment["existing_state"] == "final":
        raise HTTPException(status_code=409)
    rubric = json.loads(str(assignment["rubric_json"]))
    if not rubric["rating"]["min"] <= body.rating <= rubric["rating"]["max"]:
        raise HTTPException(status_code=422)
    if body.recommendation not in rubric["recommendation"]["choices"]:
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

    now = utc_now_ms()
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
            rating, recommendation, internal_comment, state, version, created_at_ms,
            updated_at_ms, finalized_at_ms)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?12, ?13)
           ON CONFLICT(assignment_id) DO UPDATE SET rating = excluded.rating,
             recommendation = excluded.recommendation,
             internal_comment = excluded.internal_comment, state = excluded.state,
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
            body.rating,
            body.recommendation,
            body.internal_comment,
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
        id=evaluation_id, assignment_id=assignment_id, version=version, **body.model_dump()
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
            evaluator_assigned=True,
            evaluation_round_open=assignment["round_status"] == "open",
        ),
        mutation=True,
    )
    if str(assignment["evaluator_user_id"]) != auth.actor.user_id:
        raise HTTPException(status_code=404)
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
            """SELECT 1 AS found FROM event_memberships WHERE organization_id = ?1
           AND event_id = ?2 AND user_id = ?3 AND role = 'evaluator'
           AND status = 'active' LIMIT 1"""
        )
        .bind(assignment["organization_id"], assignment["event_id"], body.evaluator_user_id)
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
async def get_round_results(round_id: str, request: Request) -> EvaluationRoundResults:
    db = _db(request)
    round_row = row_mapping(
        await _timed_first(
            request,
            db.prepare(
            """SELECT id, organization_id, event_id, program_id, name, status
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
    rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT s.id AS submission_id, s.speaker_name, s.proposal_title,
                  COUNT(a.id) AS assigned_count,
                  SUM(CASE WHEN e.state = 'final' THEN 1 ELSE 0 END) AS completed_count,
                  AVG(CASE WHEN e.state = 'final' THEN e.rating END) AS average_rating,
                  d.decision
           FROM evaluation_assignments a
           JOIN submissions s ON s.id = a.submission_id
           LEFT JOIN evaluations e ON e.assignment_id = a.id
           LEFT JOIN submission_decisions d
             ON d.round_id = a.round_id AND d.submission_id = a.submission_id
           WHERE a.round_id = ?1 AND a.status != 'revoked'
           GROUP BY s.id, s.speaker_name, s.proposal_title, d.decision
           ORDER BY s.submitted_at_ms DESC, s.id DESC LIMIT 100"""
            ).bind(round_id),
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
        )
        for row in rows
    ]
    assigned_count = sum(item.assigned_count for item in submissions)
    completed_count = sum(item.completed_count for item in submissions)
    evaluator_rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT a.evaluator_user_id, u.email AS display_name,
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
    conflict_rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT c.assignment_id, c.evaluator_user_id, u.email AS evaluator_name,
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
    weighted_ratings = [
        (item.average_rating, item.completed_count)
        for item in submissions
        if item.average_rating is not None
    ]
    return EvaluationRoundResults(
        round_id=round_id,
        event_id=str(round_row["event_id"]),
        program_id=str(round_row["program_id"]),
        round_name=str(round_row["name"]),
        status=str(round_row["status"]),
        assigned_count=assigned_count,
        completed_count=completed_count,
        average_rating=_weighted_mean(weighted_ratings),
        submissions=submissions,
        evaluators=evaluator_progress,
        conflicts=conflicts,
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
    fingerprint = hashlib.sha256(round_id.encode()).digest()
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
    if (
        counts is None
        or int(counts["total_submissions"] or 0) == 0
        or int(counts["covered_submissions"] or 0) < int(counts["total_submissions"] or 0)
        or int(counts["outstanding"] or 0) > 0
    ):
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
            metadata={"evaluations_read_only": True},
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
           JOIN evaluation_assignments a ON a.round_id = r.id
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
    if int(context["completed_count"] or 0) < int(context["assigned_count"]):
        raise HTTPException(status_code=409)
    speaker = row_mapping(
        await db.prepare(
            """SELECT s.speaker_email,s.submitter_user_id,s.proposal_title,e.name AS event_name,
                      ss.event_speaker_id
               FROM submissions s
               JOIN events e ON e.organization_id=s.organization_id AND e.id=s.event_id
               LEFT JOIN submission_speakers ss ON ss.organization_id=s.organization_id
                 AND ss.event_id=s.event_id AND ss.submission_id=s.id AND ss.role='primary'
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
            tasks = (
                (
                    "profile",
                    "Complete your speaker profile",
                    "Add your biography and public details.",
                    7,
                ),
                ("headshot", "Upload your headshot", "Add a program-ready profile photo.", 10),
                (
                    "slides",
                    "Upload your presentation",
                    "Share the final slide deck with the event team.",
                    21,
                ),
                (
                    "supporting_document",
                    "Upload supporting material",
                    "Share any final handout or supporting PDF.",
                    21,
                ),
            )
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
        batch.add_statement(
            db.prepare(
                """UPDATE event_speakers SET selection_status='rejected',last_activity_at_ms=?1,
                          updated_at_ms=?1 WHERE organization_id=?2 AND event_id=?3 AND id=?4"""
            ).bind(
                now,
                context["organization_id"],
                context["event_id"],
                speaker["event_speaker_id"],
            )
        )
        batch.add_statement(
            db.prepare(
                """UPDATE speaker_tasks SET state='waived',waived_at_ms=?1,updated_at_ms=?1,
                          version=version+1 WHERE organization_id=?2 AND event_id=?3
                          AND event_speaker_id=?4 AND state='open'"""
            ).bind(
                now,
                context["organization_id"],
                context["event_id"],
                speaker["event_speaker_id"],
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
            """SELECT r.id, r.program_id, r.name, r.status, COUNT(a.id) AS assignment_count,
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
            """SELECT id, assignment_id, rating, recommendation, internal_comment, state, version
           FROM evaluations WHERE id = ?1"""
        )
        .bind(evaluation_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
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

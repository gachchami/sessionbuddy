import hashlib
import json
import re
import unicodedata
from csv import writer
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from html import escape, unescape
from io import StringIO
from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response

from sessionbuddy.communications.queue_publish import publish_committed_messages
from sessionbuddy.console import embedded_assets
from sessionbuddy.console.asset_response import content_addressed_asset
from sessionbuddy.observability import record_degradation, record_timing
from sessionbuddy.platform.auth.http import (
    authenticate_request,
    require_document_persona,
    require_document_round,
    require_permission,
)
from sessionbuddy.platform.auth.tokens import normalize_email
from sessionbuddy.platform.authorization import Permission, Persona, ResourceContext
from sessionbuddy.platform.db.commands import AuditEvent, CommandBatch, IdempotencyRecord
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping, to_python
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.http import attachment_header
from sessionbuddy.platform.signed_cursors import BOUNDED_ID, STRICT_INT, SignedCursorContract
from sessionbuddy.speaker_operations.acceptance_tasks import (
    SPEAKER_TASK_FLAGS_SQL,
    acceptance_requires_onboarding,
    append_acceptance_speaker_tasks,
    reconcile_accepted_submission_speakers,
)

from .models import (
    AssignmentReassign,
    ConflictDeclaration,
    ConflictProgress,
    ConflictView,
    EvaluationAssignmentList,
    EvaluationAssignmentView,
    EvaluationCriterion,
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
    PendingEvaluatorView,
    ReassignmentView,
    RoundAssignment,
    RoundEvaluatorAdd,
    RoundEvaluatorChange,
    RoundSubmissionAdd,
    RoundSubmissionChange,
    SubmissionAnswerView,
    SubmissionDecisionCorrectionCreate,
    SubmissionDecisionCorrectionView,
    SubmissionDecisionCreate,
    SubmissionDecisionMessagePreview,
    SubmissionDecisionMessagePreviewRequest,
    SubmissionDecisionView,
    SubmissionEvaluationResult,
)

evaluation_router = APIRouter()

EVALUATION_PAGE_LIMIT = 50

# Upper bound on pages walked by the CSV export: 200 pages x 50 rows = 10,000 proposals,
# far above any real round, but a hard stop against a cursor that never terminates.
# A full 50-proposal page with reviews executes eight D1 statements. The
# instrumented large-page test pins that measured cost. Budget only half of the
# Workers 1,000-subrequest ceiling for the pagination walk so authentication,
# telemetry, and future conditional reads retain substantial headroom.
ROUND_RESULTS_D1_CALLS_PER_FULL_PAGE = 8
EXPORT_D1_SUBREQUEST_BUDGET = 500
EXPORT_MAX_PAGES = (
    EXPORT_D1_SUBREQUEST_BUDGET // ROUND_RESULTS_D1_CALLS_PER_FULL_PAGE
)
EXPORT_RESPONSE_HEADERS = {
    "X-Export-Row-Count": {
        "description": "Number of CSV data records, excluding the header row.",
        "schema": {"type": "integer", "minimum": 0},
    }
}

# This projection is safe only for the decision write path: immediate success,
# <=24-hour idempotent replay, or a concurrent loser reading the winner it just raced.
# Do not reuse it for historical GETs. Older installations can lack review_override audit
# metadata, so a read surface must model that value as nullable/unknown rather than false.
DECISION_VIEW_SQL = """SELECT d.id,d.submission_id,d.round_id,d.decision,
          d.internal_reason,d.version,
          COALESCE((SELECT json_extract(a.metadata_json,'$.review_override')
            FROM audit_events a
            WHERE a.organization_id=d.organization_id
              AND a.action='submission.decision.record'
              AND a.target_type='submission' AND a.target_id=d.submission_id
            ORDER BY a.occurred_at_ms DESC,a.id DESC LIMIT 1),0)
            AS override_incomplete_reviews,
          cm.deterministic_key IS NOT NULL AS send_email,
          COALESCE(cm.status IN ('queued','sending','delivered'),0) AS communication_queued,
          CASE WHEN cm.subject_source='override' THEN cm.subject ELSE '' END AS speaker_subject,
          COALESCE(cm.html_body,'') AS speaker_message_html
   FROM submission_decisions d
   LEFT JOIN communication_messages cm
     ON cm.organization_id=d.organization_id AND cm.event_id=d.event_id
    AND cm.deterministic_key='submission-decision:' || d.id || ':v1'
   WHERE d.id = ?1"""


def _decision_speaker_message(html_body: object) -> str:
    """Recover the caller-supplied decision message from stored delivery HTML."""
    value = str(html_body or "")
    default_prefix = '<p data-message-source="default">'
    custom_prefix = '<p data-message-source="custom">'
    if value.startswith(default_prefix):
        return ""
    if value.startswith(custom_prefix):
        stored = value[len(custom_prefix) :].split("</p>", 1)[0]
        return unescape(stored.replace("<br>", "\n"))
    # Rows written before provenance was recorded cannot be classified safely.
    return ""


def _decision_message_html(value: str) -> str:
    """Escape organizer text while preserving the line breaks shown in preview."""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return escape(normalized).replace("\n", "<br>")


@dataclass(frozen=True)
class _DecisionComposition:
    subject: str
    body: str
    subject_source: str
    message_source: str


def _decision_composition(
    *,
    event_name: object,
    speaker_name: object,
    proposal_title: object,
    decision: str,
    correction: bool,
    subject_override: str,
    message_override: str,
) -> _DecisionComposition:
    tokens = {
        "event.name": str(event_name),
        "speaker.name": str(speaker_name),
        "submission.title": str(proposal_title),
    }
    legacy_tokens = {
        "{event_name}": tokens["event.name"],
        "{speaker_name}": tokens["speaker.name"],
        "{talk_title}": tokens["submission.title"],
    }

    def resolve(value: str) -> str:
        canonical_pattern = r"{{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\s*}}"
        unknown = sorted(set(re.findall(canonical_pattern, value)) - tokens.keys())
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown decision-message token: {{{{{unknown[0]}}}}}.",
            )
        pattern = canonical_pattern + r"|({(?:event_name|speaker_name|talk_title)})"
        return re.sub(
            pattern,
            lambda match: (
                tokens[match.group(1)] if match.group(1) else legacy_tokens[match.group(2)]
            ),
            value,
        )

    outcome = "accepted" if decision == "accepted" else "not selected"
    default_subject = (
        f"{event_name}: corrected proposal result — {outcome}"
        if correction
        else f"{event_name}: proposal {outcome}"
    )
    if correction:
        default_body = (
            "Congratulations — an audited correction has accepted your session. "
            "Open your speaker portal for next steps."
            if decision == "accepted"
            else "An audited program correction means your proposal is no longer selected."
        )
    else:
        default_body = (
            "Congratulations — your session has been accepted. "
            "Open your speaker portal for next steps."
            if decision == "accepted"
            else "Thank you for your proposal. It was not selected for this event."
        )
    return _DecisionComposition(
        subject=resolve(subject_override or default_subject),
        body=resolve(message_override or default_body),
        subject_source="override" if subject_override else "builtin",
        message_source="custom" if message_override else "default",
    )


_EVALUATION_CURSOR = SignedCursorContract(
    "evaluation_keyset", {"id": BOUNDED_ID, "ts": STRICT_INT}
)


def _evaluation_cursor(
    request: Request,
    value: str | None,
    *,
    kind: str,
    scope_id: str,
) -> tuple[int, str] | None:
    """Decode a signed keyset cursor scoped to one list and identity."""
    decoded = _EVALUATION_CURSOR.decode(
        request,
        value,
        scope={"kind": kind, "scope": scope_id},
    )
    if decoded is None:
        return None
    timestamp, row_id = decoded["ts"], decoded["id"]
    return timestamp, row_id


def _evaluation_next_cursor(
    request: Request,
    *,
    kind: str,
    scope_id: str,
    timestamp: int,
    row_id: str,
) -> str | None:
    return _EVALUATION_CURSOR.encode(
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
    await require_document_round(request, round_id)
    return HTMLResponse(_asset("app/index.html"), headers={"Cache-Control": "no-store"})


@evaluation_router.get("/app/assets/reviews.css", response_class=Response, include_in_schema=False)
async def reviews_css(request: Request) -> Response:
    return content_addressed_asset(request, _asset("app/assets/reviews.css"), media_type="text/css")


@evaluation_router.get("/app/assets/reviews.js", response_class=Response, include_in_schema=False)
async def reviews_js(request: Request) -> Response:
    return content_addressed_asset(
        request, _asset("app/assets/reviews.js"), media_type="text/javascript"
    )


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


async def _additional_acceptance_speakers(
    db,
    *,
    organization_id: str,
    event_id: str,
    submission_id: str,
) -> list[dict[str, object]]:
    """Load accepted-task context for non-primary participants on one proposal."""
    query = SPEAKER_TASK_FLAGS_SQL.join(
        (
            """SELECT ss.event_speaker_id,es.status AS event_speaker_status,
                      COALESCE(NULLIF(p.biography,''),u.description,'') AS biography,
                      EXISTS(SELECT 1 FROM user_headshots uh WHERE uh.user_id=p.user_id)
                        AS has_account_headshot,
                      EXISTS(SELECT 1 FROM speaker_assets sa
                        JOIN speaker_asset_versions av ON av.asset_id=sa.id
                          AND av.is_current=1 AND av.scan_state='clean'
                        WHERE sa.organization_id=s.organization_id AND sa.event_id=s.event_id
                          AND sa.event_speaker_id=ss.event_speaker_id AND sa.kind='headshot')
                        AS has_event_headshot,
""",
            """
               FROM submissions s
               JOIN submission_speakers ss ON ss.organization_id=s.organization_id
                 AND ss.event_id=s.event_id AND ss.submission_id=s.id AND ss.role!='primary'
               JOIN event_speakers es ON es.organization_id=s.organization_id
                 AND es.event_id=s.event_id AND es.id=ss.event_speaker_id
               JOIN people p ON p.organization_id=s.organization_id AND p.id=es.person_id
               JOIN users u ON u.id=p.user_id
               WHERE s.id=?1 AND s.organization_id=?2 AND s.event_id=?3""",
        )
    )
    return result_rows(
        await db.prepare(query).bind(submission_id, organization_id, event_id).all()
    )


def _append_rejected_participant_cleanup(
    batch,
    db,
    *,
    organization_id: str,
    event_id: str,
    submission_id: str,
    now: int,
) -> None:
    """Waive rejected work and derive every linked speaker's remaining lifecycle."""
    batch.add_statement(
        db.prepare(
            """UPDATE speaker_tasks AS task
               SET state='waived',waived_at_ms=?1,updated_at_ms=?1,version=version+1
               WHERE task.organization_id=?2 AND task.event_id=?3 AND task.state='open'
                 AND task.event_speaker_id IN (SELECT linked.event_speaker_id
                   FROM submission_speakers linked
                   WHERE linked.organization_id=?2 AND linked.event_id=?3
                     AND linked.submission_id=?4)
                 AND ((task.task_type IN ('profile','headshot') AND NOT (
                   EXISTS (SELECT 1 FROM submission_speakers linked
                     JOIN accepted_sessions active
                       ON active.organization_id=linked.organization_id
                      AND active.event_id=linked.event_id
                      AND active.submission_id=linked.submission_id
                      AND active.lifecycle_status='active'
                     WHERE linked.organization_id=?2 AND linked.event_id=?3
                       AND linked.event_speaker_id=task.event_speaker_id)
                   OR EXISTS (SELECT 1 FROM accepted_session_participants participant
                     JOIN accepted_sessions active
                       ON active.organization_id=participant.organization_id
                      AND active.event_id=participant.event_id
                      AND active.id=participant.accepted_session_id
                      AND active.lifecycle_status='active'
                     WHERE participant.organization_id=?2 AND participant.event_id=?3
                       AND participant.event_speaker_id=task.event_speaker_id)))
                   OR (task.task_type NOT IN ('profile','headshot')
                       AND task.submission_id=?4))"""
        ).bind(now, organization_id, event_id, submission_id)
    )
    batch.add_statement(
        db.prepare(
            """UPDATE event_speakers AS speaker SET
                 selection_status=CASE WHEN (
                   EXISTS (SELECT 1 FROM submission_speakers linked
                     JOIN accepted_sessions active
                       ON active.organization_id=linked.organization_id
                      AND active.event_id=linked.event_id
                      AND active.submission_id=linked.submission_id
                      AND active.lifecycle_status='active'
                     WHERE linked.organization_id=?2 AND linked.event_id=?3
                       AND linked.event_speaker_id=speaker.id)
                   OR EXISTS (SELECT 1 FROM accepted_session_participants participant
                     JOIN accepted_sessions active
                       ON active.organization_id=participant.organization_id
                      AND active.event_id=participant.event_id
                      AND active.id=participant.accepted_session_id
                      AND active.lifecycle_status='active'
                     WHERE participant.organization_id=?2 AND participant.event_id=?3
                       AND participant.event_speaker_id=speaker.id))
                   THEN 'accepted' ELSE 'rejected' END,
                 status=CASE WHEN speaker.status='withdrawn' THEN speaker.status
                   WHEN EXISTS (SELECT 1 FROM speaker_tasks task
                     WHERE task.organization_id=?2 AND task.event_id=?3
                       AND task.event_speaker_id=speaker.id AND task.state='open')
                   THEN 'onboarding' ELSE 'complete' END,
                 last_activity_at_ms=?1,updated_at_ms=?1
               WHERE speaker.organization_id=?2 AND speaker.event_id=?3
                 AND speaker.id IN (SELECT linked.event_speaker_id
                   FROM submission_speakers linked
                   WHERE linked.organization_id=?2 AND linked.event_id=?3
                     AND linked.submission_id=?4)"""
        ).bind(now, organization_id, event_id, submission_id)
    )


def _assignment_pairs(
    submission_ids: list[str], evaluator_ids: list[str], strategy: str
) -> list[tuple[str, str]]:
    # Draft rounds may be prepared before their reviewer pool is populated. In that
    # state there are intentionally no assignments yet, regardless of strategy.
    if not evaluator_ids:
        return []
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


# --- explicit per-submission assignment -------------------------------------------------
# Round membership and the assignment relation are separate concerns. _assignment_pairs
# above is now only a GENERATOR for the initial matrix; once an organizer edits the pairs,
# plan_round_diff is what decides the writes, and the strategy never overrides them.


@dataclass(frozen=True)
class AssignmentState:
    """What the database currently knows about one pair."""

    assignment_id: str
    status: str  # assigned | completed | revoked
    has_conflict: bool = False  # a reviewer declared a conflict of interest on it
    has_final_evaluation: bool = False
    draft_summary: dict | None = None  # whitelisted metadata, never evaluation text


@dataclass
class RoundDiff:
    """The six ordered steps. Order is load-bearing: assignments reference membership, so
    membership must exist before an assignment does and outlive it on the way out."""

    activate_submissions: list[str] = dataclass_field(default_factory=list)
    activate_evaluators: list[str] = dataclass_field(default_factory=list)
    add_assignments: list[tuple[str, str]] = dataclass_field(default_factory=list)
    revive_assignments: list[str] = dataclass_field(default_factory=list)
    revoke_assignments: list[tuple[str, str, dict | None]] = dataclass_field(default_factory=list)
    deactivate_submissions: list[str] = dataclass_field(default_factory=list)
    deactivate_evaluators: list[str] = dataclass_field(default_factory=list)
    refused: list[str] = dataclass_field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(
            (
                self.activate_submissions,
                self.activate_evaluators,
                self.add_assignments,
                self.revive_assignments,
                self.revoke_assignments,
                self.deactivate_submissions,
                self.deactivate_evaluators,
            )
        )


def plan_round_diff(
    *,
    current_submissions: dict[str, str],
    current_evaluators: dict[str, str],
    current_assignments: dict[tuple[str, str], AssignmentState],
    desired_submissions: list[str],
    desired_evaluators: list[str],
    desired_pairs: list[tuple[str, str]],
) -> RoundDiff:
    """Reduce desired round state to the minimum set of writes.

    Pure so the rules can be tested without a database. The rules that matter:

    * A pair may only be added or revived when BOTH memberships end up active. The schema
      enforces that membership EXISTS, never that it is ACTIVE, so this is the only place
      that check happens.
    * A conflict-revoked pair is never revived. The reviewer recused themselves; an
      organizer resubmitting the same desired state must not undo that silently.
    * An assignment carrying a finalized evaluation is never revoked. Final means final.
    * Identical desired state produces an empty diff, so the caller can skip the batch, the
      version bump and the audit record entirely.
    """
    diff = RoundDiff()
    desired_submission_set = set(desired_submissions)
    desired_evaluator_set = set(desired_evaluators)

    for submission_id in desired_submissions:
        if current_submissions.get(submission_id) != "active":
            diff.activate_submissions.append(submission_id)
    for evaluator_id in desired_evaluators:
        if current_evaluators.get(evaluator_id) != "active":
            diff.activate_evaluators.append(evaluator_id)

    for pair in desired_pairs:
        submission_id, evaluator_id = pair
        if submission_id not in desired_submission_set or evaluator_id not in desired_evaluator_set:
            # Membership would not be active after this save, so the pair cannot exist.
            diff.refused.append(f"{submission_id}->{evaluator_id}: membership not active")
            continue
        existing = current_assignments.get(pair)
        if existing is None:
            diff.add_assignments.append(pair)
        elif existing.status == "revoked":
            if existing.has_conflict:
                diff.refused.append(f"{submission_id}->{evaluator_id}: conflict-revoked")
            else:
                diff.revive_assignments.append(existing.assignment_id)

    desired_pair_set = set(desired_pairs)
    for pair, existing in sorted(current_assignments.items()):
        if pair in desired_pair_set or existing.status == "revoked":
            continue
        if existing.has_final_evaluation:
            diff.refused.append(f"{pair[0]}->{pair[1]}: finalized evaluation")
            continue
        diff.revoke_assignments.append(
            (existing.assignment_id, "organizer_removed", existing.draft_summary)
        )

    for submission_id, status in sorted(current_submissions.items()):
        if submission_id not in desired_submission_set and status == "active":
            diff.deactivate_submissions.append(submission_id)
    for evaluator_id, status in sorted(current_evaluators.items()):
        if evaluator_id not in desired_evaluator_set and status == "active":
            diff.deactivate_evaluators.append(evaluator_id)
    return diff


def _round_criteria_for_historical_scores(rubric_json: object) -> list[dict]:
    """Read the stored scoring inputs without applying today's authoring validators.

    Historical averages are recomputed from the rubric stored on the round. Tightening
    EvaluationCriterion later must not retroactively remove an old weighted criterion and
    change those averages; organizer-facing metadata is validated separately.
    """
    if rubric_json is None:
        return []
    try:
        rubric = json.loads(str(rubric_json))
    except (TypeError, ValueError):
        return []
    criteria = rubric.get("criteria") or []
    return [
        criterion
        for criterion in criteria
        if isinstance(criterion, dict) and criterion.get("key") and criterion.get("weight")
    ]


def _full_round_criteria(rubric_json: object) -> list[dict]:
    """Return every model-valid criterion in rubric order, including non-scored responses."""
    if rubric_json is None:
        return []
    try:
        rubric = json.loads(str(rubric_json))
    except (TypeError, ValueError):
        return []
    criteria = rubric.get("criteria") or []
    valid: list[dict] = []
    for criterion in criteria:
        if not isinstance(criterion, dict):
            continue
        try:
            normalized = EvaluationCriterion.model_validate(criterion)
        except ValueError:
            # A malformed historical criterion must not take down the results dashboard
            # or either export. Valid siblings remain visible in stored order.
            continue
        valid.append(normalized.model_dump(exclude_none=True))
    return valid


def _criterion_response_mapping(value: object) -> dict[str, int | str]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): response
        for key, response in parsed.items()
        if isinstance(response, (int, str)) and not isinstance(response, bool)
    }


def _build_round_rubric(body: EvaluationRoundCreate) -> dict:
    """Build the stored rubric identically for create and draft-update paths.

    A purpose-designated criterion is canonical. The legacy fields remain populated during
    compatibility because existing consumers and finalized-evaluation validation still read
    them, but their configuration is derived here rather than trusted as a second source.
    """
    criteria = [criterion.model_dump(exclude_none=True) for criterion in body.criteria]
    recommendation = next(
        (criterion for criterion in body.criteria if criterion.purpose == "recommendation"),
        None,
    )
    comment = next(
        (criterion for criterion in body.criteria if criterion.purpose == "comment"),
        None,
    )
    recommendation_choices = (
        list(recommendation.options) if recommendation is not None else body.recommendations
    )
    return {
        "rating": {"min": body.rating_min, "max": body.rating_max, "required": True},
        "recommendation": {"choices": recommendation_choices, "required": True},
        "internal_comment": {
            "required": comment.required if comment is not None else body.comment_required
        },
        "guidance": body.evaluator_guidance,
        "criteria": criteria,
        "blind_review": body.blind_review,
        "assignment_strategy": body.assignment_strategy,
    }


def _canonical_evaluation_fields(
    rubric: dict, body: EvaluationSave
) -> tuple[str | None, str]:
    """Resolve purpose-designated criterion responses and their legacy mirrors."""
    criteria = list(rubric.get("criteria", []))
    recommendation_criterion = next(
        (criterion for criterion in criteria if criterion.get("purpose") == "recommendation"),
        None,
    )
    comment_criterion = next(
        (criterion for criterion in criteria if criterion.get("purpose") == "comment"),
        None,
    )
    recommendation = body.recommendation
    if recommendation_criterion is not None:
        canonical = body.criterion_responses.get(str(recommendation_criterion["key"]))
        recommendation = str(canonical) if canonical is not None else None
    internal_comment = body.internal_comment
    if comment_criterion is not None:
        canonical = body.criterion_responses.get(str(comment_criterion["key"]))
        internal_comment = str(canonical) if canonical is not None else ""
    return recommendation, internal_comment


def _weighted_review_score(
    criteria: list[dict], criterion_responses_json: object, stored_rating: object
) -> float | None:
    """Weighted score for one finalized review, at full precision.

    ``evaluations.rating`` stores the reviewer-facing overall rating as an integer, so
    the criterion weighting is lost to rounding the moment it is saved. Recomputing here
    from the raw per-criterion scores keeps the weighting intact in the aggregate: with
    Originality 4 at weight 67 and Relevance 2 at weight 33 the mean is 3.34, not 3.
    Rounds with no scorecard criteria fall back to the stored rating.
    """
    scores: dict = {}
    if criterion_responses_json is not None:
        try:
            parsed = json.loads(str(criterion_responses_json))
        except (TypeError, ValueError):
            parsed = {}
        if isinstance(parsed, dict):
            scores = parsed
    if criteria and scores:
        total_weight = 0
        total = 0.0
        for criterion in criteria:
            key = str(criterion["key"])
            if key not in scores:
                continue
            try:
                weight = int(criterion["weight"])
                value = float(scores[key])
            except (TypeError, ValueError):
                continue
            total += value * weight
            total_weight += weight
        if total_weight:
            return total / total_weight
    if stored_rating is None:
        return None
    try:
        return float(stored_rating)
    except (TypeError, ValueError):
        return None


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
    summarize_changes: bool = False,
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
        deterministic_key = f"evaluation-assignment:{round_id}:{evaluator_id}:{dedup_suffix}"
        # Incremental assignment controls can be used several times in succession. A
        # stable id plus the deterministic-key conflict guard makes those clicks one
        # immediate queue notification per window, and duplicate queue envelopes remain
        # harmless under the consumer's existing atomic claim.
        message_id = (
            str(uuid5(NAMESPACE_URL, deterministic_key)) if summarize_changes else new_id()
        )
        message_ids.append(message_id)
        html_body = (
            f"<p>New review work was added to your queue in {escape(round_name)}.</p>"
            f"{deadline}{link}"
            if summarize_changes
            else (
                f"<p>You have been assigned {count} proposal"
                f"{'s' if count != 1 else ''} to review in "
                f"{escape(round_name)}.</p>{deadline}{link}"
            )
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO communication_messages
                   (id,organization_id,event_id,recipient_user_id,recipient_email,subject,
                    html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9)
                   ON CONFLICT(organization_id,event_id,deterministic_key) DO NOTHING"""
            ).bind(
                message_id,
                organization_id,
                event_id,
                evaluator_id,
                email,
                f"New review assignments: {round_name}",
                html_body,
                deterministic_key,
                now_ms,
            )
        )
    return message_ids


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


_REVIEWER_ELIGIBILITY_HELP = (
    "A reviewer becomes assignable only after accepting their invitation to this event, "
    "which happens when they open their invitation link and sign in."
)


async def _ineligible_evaluator_detail(
    db,
    organization_id: str,
    event_id: str,
    evaluator_user_ids: list[str],
) -> str:
    """Explain why a reviewer selection was rejected.

    Every eligibility check in this module joins `identity_invitations` on
    `status='accepted'`, so a reviewer who was invited but has not accepted yet fails
    them in exactly the same way a stranger does. The two need different answers:
    a stranger has to be invited, an unaccepted invitee only has to accept. Returning
    the same bare 400 for both is what sent organizers back to the invite form to
    re-invite someone who was already invited -- which reissues the pending row and
    leaves them just as blocked.

    Only the invited person can move an invitation to accepted, so the detail names who
    is being waited on rather than offering the organizer an action they do not have.
    """
    if not evaluator_user_ids:
        return f"Choose a reviewer for this round. {_REVIEWER_ELIGIBILITY_HELP}"
    placeholders = ",".join(f"?{index + 3}" for index in range(len(evaluator_user_ids)))
    names = [
        str(row["display_name"])
        for row in result_rows(
            await db.prepare(
                f"""SELECT DISTINCT
                           COALESCE(NULLIF(TRIM(i.display_name),''),i.email) AS display_name
                    FROM identity_invitations i
                    JOIN users u ON u.normalized_email=i.normalized_email
                    WHERE i.organization_id=?1 AND i.event_id=?2
                      AND i.role='evaluator' AND i.status='pending'
                      AND u.id IN ({placeholders})
                    ORDER BY display_name"""  # noqa: S608
            )
            .bind(organization_id, event_id, *evaluator_user_ids)
            .all()
        )
    ]
    if not names:
        return (
            "That reviewer is not eligible for this event. Invite them as a reviewer from "
            f"the Reviewers page. {_REVIEWER_ELIGIBILITY_HELP}"
        )
    waiting = (
        f"{names[0]} has not accepted"
        if len(names) == 1
        else f"{', '.join(names[:-1])} and {names[-1]} have not accepted"
    )
    return (
        f"{waiting} the reviewer invitation for this event yet, so they cannot be assigned. "
        f"{_REVIEWER_ELIGIBILITY_HELP} Resend the invitation from the Reviewers page if it "
        "has expired."
    )


def _round_name_key(name: str) -> str:
    """The comparison key for "is this the same round name?".

    A round name is a label, not an identifier, so it collides the way labels collide.
    "Round 1", "round 1" and "Round  1" are one label to the organizer scanning the round
    history and to the reviewer reading the assignment email, and comparing them raw let a
    second round be created beside the first -- same proposals, same reviewers, and only one
    of the two ever opened. Casefold rather than lower(): the key has to hold for non-ASCII
    names too, which SQLite's ASCII-only lower() would not, and the split/join collapses the
    stray double space that a paste leaves behind.
    """
    return " ".join(name.split()).casefold()


async def _reject_duplicate_round_name(
    db,
    organization_id: str,
    event_id: str,
    name: str,
    *,
    exclude_round_id: str | None = None,
) -> None:
    """Refuse a name already carried by a draft or open round in the same event.

    Scoped to rounds still in play. A closed round keeps its name in the history, but that
    name is free again -- an event that ran "Screening" last cycle may run it again, and
    blocking that would be a new bug rather than a fix for this one. `exclude_round_id`
    keeps a draft from colliding with itself when a save leaves the name untouched.

    Compared in Python, not in SQL: the key normalizes whitespace and casefolds, and the
    row set here is bounded by the one-open-round rule plus the handful of drafts an event
    accumulates, read through idx_evaluation_rounds_event.
    """
    key = _round_name_key(name)
    for row in result_rows(
        await db.prepare(
            """SELECT id, name FROM evaluation_rounds
               WHERE organization_id = ?1 AND event_id = ?2
                 AND status IN ('draft', 'open')"""
        )
        .bind(organization_id, event_id)
        .all()
    ):
        if str(row["id"]) == exclude_round_id or _round_name_key(str(row["name"])) != key:
            continue
        raise HTTPException(
            status_code=409,
            detail=(
                f"An evaluation round named {str(row['name'])!r} already exists for this "
                "event. Rename this round, or edit the existing one."
            ),
        )


async def _execute_round_write(
    request: Request,
    batch: CommandBatch,
    db,
    organization_id: str,
    event_id: str,
    name: str,
    *,
    exclude_round_id: str | None = None,
) -> None:
    """Run the batch, and let the name guard have the last word on a conflict.

    uq_evaluation_rounds_live_name is what actually stops the concurrent case, but its
    violation reaches _execute as an opaque PersistenceError -- execute_batch deliberately
    hides provider strings, so there is nothing safe to match on, and the caller would see
    only "the request conflicts with current state". Re-running the guard after a refusal
    costs one indexed read on a request that has already failed, and hands the loser of the
    race the same sentence the winner's rival would have got a millisecond earlier.
    """
    try:
        await _execute(request, batch)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        await _reject_duplicate_round_name(
            db, organization_id, event_id, name, exclude_round_id=exclude_round_id
        )
        raise


async def _execute_decision_correction(request: Request, batch: CommandBatch) -> None:
    """Execute a correction batch and turn an opaque D1 refusal into safe guidance.

    D1 provider messages are deliberately hidden at the persistence boundary. A failed
    correction therefore gets a degradation marker and actionable retry guidance rather
    than the bare ``Conflict Reference`` that trapped the organizer.
    """
    try:
        await _execute(request, batch)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        record_degradation(request, "decision_correction_conflict")
        raise HTTPException(
            status_code=409,
            detail=(
                "The proposal, speaker, or session changed while this correction was "
                "being recorded. Reload the proposal and try again; no correction was saved."
            ),
            headers={"X-Conflict-Type": "decision-correction"},
        ) from exc


@evaluation_router.post(
    "/api/v1/admin/events/{event_id}/submissions/{submission_id}/reject",
    response_model=SubmissionDecisionView,
    operation_id="rejectUnreviewedSubmission",
    tags=["evaluations"],
)
async def reject_unreviewed_submission(
    event_id: str,
    submission_id: str,
    request: Request,
    body: SubmissionDecisionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SubmissionDecisionView:
    if body.decision != "rejected" or body.override_incomplete_reviews:
        raise HTTPException(status_code=422, detail="This action only rejects unreviewed proposals")
    if not body.internal_reason:
        raise HTTPException(status_code=422, detail="An internal rejection reason is required")
    return await record_submission_decision(
        None, submission_id, request, body, idempotency_key, direct_event_id=event_id
    )


@evaluation_router.post(
    "/api/v1/admin/events/{event_id}/submissions/{submission_id}/accept",
    response_model=SubmissionDecisionView,
    operation_id="acceptUnreviewedSubmission",
    tags=["evaluations"],
)
async def accept_unreviewed_submission(
    event_id: str,
    submission_id: str,
    request: Request,
    body: SubmissionDecisionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SubmissionDecisionView:
    if body.decision != "accepted" or body.override_incomplete_reviews:
        raise HTTPException(status_code=422, detail="This action only accepts unreviewed proposals")
    if not body.internal_reason:
        raise HTTPException(status_code=422, detail="An internal acceptance reason is required")
    return await record_submission_decision(
        None, submission_id, request, body, idempotency_key, direct_event_id=event_id
    )


@evaluation_router.post(
    "/api/v1/admin/events/{event_id}/submissions/{submission_id}/decision-message-preview",
    response_model=SubmissionDecisionMessagePreview,
    operation_id="previewSubmissionDecisionMessage",
    tags=["evaluations"],
)
async def preview_submission_decision_message(
    event_id: str,
    submission_id: str,
    request: Request,
    body: SubmissionDecisionMessagePreviewRequest,
) -> SubmissionDecisionMessagePreview:
    """Resolve exactly what a decision email would say without recording a decision."""
    context = row_mapping(
        await _db(request)
        .prepare(
            """SELECT s.organization_id,s.proposal_title,s.speaker_name,s.speaker_email,
                      e.name AS event_name
               FROM submissions s
               JOIN events e ON e.organization_id=s.organization_id AND e.id=s.event_id
               WHERE s.id=?1 AND s.event_id=?2 LIMIT 1"""
        )
        .bind(submission_id, event_id)
        .first()
    )
    if context is None:
        raise HTTPException(status_code=404)
    await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(context["organization_id"]), event_id),
        mutation=True,
    )
    composition = _decision_composition(
        event_name=context["event_name"],
        speaker_name=context["speaker_name"],
        proposal_title=context["proposal_title"],
        decision=body.decision,
        correction=body.correction,
        subject_override=body.speaker_subject,
        message_override=body.speaker_message,
    )
    return SubmissionDecisionMessagePreview(
        resolved_subject=composition.subject,
        resolved_body=composition.body,
        proposal_title=str(context["proposal_title"]),
        recipient_available=bool(str(context["speaker_email"] or "").strip()),
    )


@evaluation_router.post(
    "/api/v1/admin/events/{event_id}/submissions/{submission_id}/decision-corrections",
    response_model=SubmissionDecisionCorrectionView,
    operation_id="correctFinalSubmissionDecision",
    tags=["evaluations"],
)
async def correct_final_submission_decision(
    event_id: str,
    submission_id: str,
    request: Request,
    body: SubmissionDecisionCorrectionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SubmissionDecisionCorrectionView:
    """Append an audited correction while preserving the original final decision."""
    db = _db(request)
    context_query = SPEAKER_TASK_FLAGS_SQL.join(
        (
            """SELECT s.organization_id,s.event_id,s.speaker_email,s.submitter_user_id,
                      s.speaker_name,s.proposal_title,e.name AS event_name,
                      d.id AS original_decision_id,
                      COALESCE((SELECT c.corrected_decision
                        FROM submission_decision_corrections c
                        WHERE c.organization_id=s.organization_id AND c.event_id=s.event_id
                          AND c.submission_id=s.id
                        ORDER BY c.corrected_at_ms DESC,c.id DESC LIMIT 1),d.decision)
                        AS effective_decision,
                      ss.event_speaker_id,es.status AS event_speaker_status,
                      es.withdrawn_at_ms AS event_speaker_withdrawn_at_ms,p.biography,
                      EXISTS(SELECT 1 FROM user_headshots uh WHERE uh.user_id=p.user_id)
                        AS has_account_headshot,
                      EXISTS(SELECT 1 FROM speaker_assets sa
                        JOIN speaker_asset_versions av ON av.asset_id=sa.id
                          AND av.is_current=1 AND av.scan_state='clean'
                        WHERE sa.organization_id=s.organization_id AND sa.event_id=s.event_id
                          AND sa.event_speaker_id=ss.event_speaker_id AND sa.kind='headshot')
                        AS has_event_headshot,
""",
            """
               FROM submissions s
               JOIN submission_decisions d ON d.organization_id=s.organization_id
                 AND d.event_id=s.event_id AND d.submission_id=s.id
               JOIN events e ON e.organization_id=s.organization_id AND e.id=s.event_id
               LEFT JOIN submission_speakers ss ON ss.organization_id=s.organization_id
                 AND ss.event_id=s.event_id AND ss.submission_id=s.id AND ss.role='primary'
               LEFT JOIN event_speakers es ON es.organization_id=s.organization_id
                 AND es.event_id=s.event_id AND es.id=ss.event_speaker_id
               LEFT JOIN people p ON p.organization_id=s.organization_id AND p.id=es.person_id
               WHERE s.id=?1 AND s.event_id=?2 LIMIT 1""",
        )
    )
    context = row_mapping(await db.prepare(context_query).bind(submission_id, event_id).first())
    if context is None:
        raise HTTPException(
            status_code=409,
            detail="Only a finalized proposal decision can be corrected.",
        )
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(str(context["organization_id"]), str(context["event_id"])),
        mutation=True,
    )
    previous = str(context["effective_decision"])
    key = _key(idempotency_key)
    route = "POST /api/v1/admin/events/{event_id}/submissions/{submission_id}/decision-corrections"
    fingerprint = _fingerprint(body)
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint,response_resource_id FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND state='completed' LIMIT 1"""
        )
        .bind(auth.actor.user_id, route, hashlib.sha256(key.encode()).digest())
        .first()
    )
    if replay is not None:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This correction request changed after an earlier attempt used the same "
                    "retry key. Reload the proposal to review the recorded outcome."
                ),
                headers={"X-Conflict-Type": "decision-correction"},
            )
        saved = row_mapping(
            await db.prepare(
                """SELECT c.id,c.submission_id,c.original_decision_id,c.previous_decision,
                          c.corrected_decision,c.reason,c.corrected_at_ms,
                          ac.id AS accepted_session_id,
                          ac.lifecycle_status AS accepted_session_lifecycle_status,
                          EXISTS(SELECT 1 FROM communication_messages message
                            WHERE message.deterministic_key='submission-decision-correction:'
                              || c.id || ':v1'
                              AND message.status IN ('queued','sending','delivered'))
                            AS communication_queued,
                          EXISTS(SELECT 1 FROM communication_messages message
                            WHERE message.deterministic_key='submission-decision-correction:'
                              || c.id || ':v1') AS send_email,
                          COALESCE((SELECT CASE WHEN message.subject_source='override'
                                               THEN message.subject ELSE '' END
                            FROM communication_messages message
                            WHERE message.deterministic_key='submission-decision-correction:'
                              || c.id || ':v1' LIMIT 1),'') AS speaker_subject,
                          -- Requested delivery and current queue state are distinct:
                          -- cancelled or failed messages still prove send_email was true.
                          COALESCE((SELECT message.html_body
                            FROM communication_messages message
                            WHERE message.deterministic_key='submission-decision-correction:'
                              || c.id || ':v1' LIMIT 1),'') AS speaker_message_html
                   FROM submission_decision_corrections c
                   LEFT JOIN accepted_sessions ac ON ac.decision_correction_id=c.id
                   WHERE c.id=?1 LIMIT 1"""
            )
            .bind(replay["response_resource_id"])
            .first()
        )
        if saved is None:
            raise HTTPException(status_code=409)
        saved["speaker_message"] = _decision_speaker_message(
            saved.pop("speaker_message_html", "")
        )
        return SubmissionDecisionCorrectionView.model_validate(saved)

    if previous == body.corrected_decision:
        raise HTTPException(
            status_code=409,
            detail=f"The effective decision is already {previous}.",
        )
    if body.send_email and not str(context["speaker_email"] or "").strip():
        raise HTTPException(
            status_code=409,
            detail="This correction cannot be emailed because the proposal has no speaker email.",
        )

    now = utc_now_ms()
    additional_speakers = (
        await _additional_acceptance_speakers(
            db,
            organization_id=str(context["organization_id"]),
            event_id=event_id,
            submission_id=submission_id,
        )
        if body.corrected_decision == "accepted"
        else []
    )
    correction_id = new_id()
    communication_id = new_id() if body.send_email else None
    speaker_withdrawn = context["event_speaker_status"] == "withdrawn"
    session_lifecycle = "withdrawn" if speaker_withdrawn else "active"
    session_withdrawn_at_ms = (
        context["event_speaker_withdrawn_at_ms"] if speaker_withdrawn else None
    )
    existing_session = row_mapping(
        await db.prepare(
            """SELECT id FROM accepted_sessions
               WHERE organization_id=?1 AND event_id=?2 AND submission_id=?3 LIMIT 1"""
        )
        .bind(context["organization_id"], event_id, submission_id)
        .first()
    )
    session_id = str(existing_session["id"]) if existing_session else new_id()
    record = IdempotencyRecord(
        principal_key=auth.actor.user_id,
        organization_id=str(context["organization_id"]),
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
            """INSERT INTO submission_decision_corrections
               (id,organization_id,event_id,submission_id,original_decision_id,
                previous_decision,corrected_decision,reason,corrected_by_user_id,corrected_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)"""
        ).bind(
            correction_id,
            context["organization_id"],
            event_id,
            submission_id,
            context["original_decision_id"],
            previous,
            body.corrected_decision,
            body.reason,
            auth.actor.user_id,
            now,
        )
    )
    if body.corrected_decision == "accepted":
        if existing_session:
            batch.add_statement(
                db.prepare(
                    """UPDATE accepted_sessions SET decision_id=?1,
                              decision_correction_id=?2,lifecycle_status=?3,
                              withdrawn_at_ms=?4,version=version+1
                       WHERE id=?5 AND organization_id=?6 AND event_id=?7"""
                ).bind(
                    context["original_decision_id"],
                    correction_id,
                    session_lifecycle,
                    session_withdrawn_at_ms,
                    session_id,
                    context["organization_id"],
                    event_id,
                )
            )
        else:
            batch.add_statement(
                db.prepare(
                    """INSERT INTO accepted_sessions
                       (id,organization_id,event_id,submission_id,decision_id,
                        decision_correction_id,lifecycle_status,withdrawn_at_ms,created_at_ms)
                       VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9)"""
                ).bind(
                    session_id,
                    context["organization_id"],
                    event_id,
                    submission_id,
                    context["original_decision_id"],
                    correction_id,
                    session_lifecycle,
                    session_withdrawn_at_ms,
                    now,
                )
            )
        if context["event_speaker_id"] is not None:
            primary_onboarding = acceptance_requires_onboarding(context)
            batch.add_statement(
                db.prepare(
                    """UPDATE event_speakers SET selection_status='accepted',
                              status=CASE WHEN status='withdrawn' THEN status
                                WHEN ?5=1 THEN 'onboarding' ELSE 'complete' END,
                              accepted_at_ms=COALESCE(accepted_at_ms,?1),
                              last_activity_at_ms=?1,updated_at_ms=?1
                       WHERE organization_id=?2 AND event_id=?3 AND id=?4"""
                ).bind(
                    now,
                    context["organization_id"],
                    event_id,
                    context["event_speaker_id"],
                    1 if primary_onboarding else 0,
                )
            )
            if not speaker_withdrawn:
                append_acceptance_speaker_tasks(
                    batch,
                    db,
                    context,
                    organization_id=str(context["organization_id"]),
                    event_id=event_id,
                    submission_id=submission_id,
                    event_speaker_id=str(context["event_speaker_id"]),
                    now=now,
                )
        for additional in additional_speakers:
            if str(additional["event_speaker_status"]) == "withdrawn":
                continue
            additional_id = str(additional["event_speaker_id"])
            additional_onboarding = acceptance_requires_onboarding(
                additional, include_slides=False
            )
            batch.add_statement(
                db.prepare(
                    """UPDATE event_speakers SET selection_status='accepted',
                              status=CASE WHEN ?5=1 THEN 'onboarding' ELSE 'complete' END,
                              accepted_at_ms=COALESCE(accepted_at_ms,?1),
                              last_activity_at_ms=?1,updated_at_ms=?1
                       WHERE organization_id=?2 AND event_id=?3 AND id=?4
                         AND status!='withdrawn'"""
                ).bind(
                    now, context["organization_id"], event_id, additional_id,
                    1 if additional_onboarding else 0,
                )
            )
            append_acceptance_speaker_tasks(
                batch,
                db,
                additional,
                organization_id=str(context["organization_id"]),
                event_id=event_id,
                submission_id=submission_id,
                event_speaker_id=additional_id,
                now=now,
                include_slides=False,
            )
    else:
        # Keep the accepted-session record and its content history, but remove it from
        # active scheduling. Draft agenda placements are disposable; published revisions
        # remain immutable historical evidence and are hidden by the session lifecycle.
        batch.add_statement(
            db.prepare(
                """DELETE FROM agenda_item_speakers WHERE agenda_item_id IN (
                     SELECT ai.id FROM agenda_items ai JOIN schedule_revisions r
                       ON r.id=ai.revision_id
                     WHERE ai.accepted_session_id=?1 AND r.status='draft')"""
            ).bind(session_id)
        )
        batch.add_statement(
            db.prepare(
                """DELETE FROM agenda_items WHERE accepted_session_id=?1
                   AND revision_id IN (SELECT id FROM schedule_revisions WHERE status='draft')"""
            ).bind(session_id)
        )
        batch.add_statement(
            db.prepare(
                """UPDATE accepted_sessions SET lifecycle_status='withdrawn',withdrawn_at_ms=?1,
                          version=version+1 WHERE id=?2 AND organization_id=?3 AND event_id=?4"""
            ).bind(now, session_id, context["organization_id"], event_id)
        )
        _append_rejected_participant_cleanup(
            batch,
            db,
            organization_id=str(context["organization_id"]),
            event_id=event_id,
            submission_id=submission_id,
            now=now,
        )
    if communication_id is not None:
        composition = _decision_composition(
            event_name=context["event_name"],
            speaker_name=context["speaker_name"],
            proposal_title=context["proposal_title"],
            decision=body.corrected_decision,
            correction=True,
            subject_override=body.speaker_subject,
            message_override=body.speaker_message,
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO communication_messages
                   (id,organization_id,event_id,recipient_user_id,recipient_email,subject,
                    html_body,deterministic_key,status,queued_at_ms,updated_at_ms,subject_source)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9,?10)"""
            ).bind(
                communication_id,
                context["organization_id"],
                event_id,
                context["submitter_user_id"],
                context["speaker_email"],
                composition.subject,
                f'<p data-message-source="{composition.message_source}">'
                f"{_decision_message_html(composition.body)}</p>"
                f"<p><strong>{escape(str(context['proposal_title']))}</strong></p>",
                f"submission-decision-correction:{correction_id}:v1",
                now,
                composition.subject_source,
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="submission.decision.correct",
            target_type="submission",
            target_id=submission_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(context["organization_id"]),
            event_id=event_id,
            metadata={
                "original_decision_id": str(context["original_decision_id"]),
                "previous_decision": previous,
                "corrected_decision": body.corrected_decision,
                "accepted_session_id": session_id,
                "reason_recorded": True,
                "communication_queued": bool(communication_id),
            },
        )
    )
    batch.complete_idempotency(
        record,
        status=200,
        resource_type="submission_decision_correction",
        resource_id=correction_id,
        completed_at_ms=now,
    )
    await _execute_decision_correction(request, batch)
    if body.corrected_decision == "accepted":
        try:
            await reconcile_accepted_submission_speakers(
                db,
                organization_id=str(context["organization_id"]),
                event_id=event_id,
                submission_id=submission_id,
                now=now,
                execute_batch=lambda followup: _execute(request, followup),
            )
        except HTTPException:
            record_degradation(request, "accepted_participant_reconciliation_failed")
    if communication_id is not None:
        await publish_committed_messages(request, [communication_id])
    return SubmissionDecisionCorrectionView(
        id=correction_id,
        submission_id=submission_id,
        original_decision_id=str(context["original_decision_id"]),
        previous_decision=previous,
        corrected_decision=body.corrected_decision,
        reason=body.reason,
        corrected_at_ms=now,
        accepted_session_id=session_id,
        accepted_session_lifecycle_status=session_lifecycle
        if body.corrected_decision == "accepted"
        else "withdrawn",
        communication_queued=communication_id is not None,
        send_email=body.send_email,
        speaker_subject=body.speaker_subject if body.send_email else "",
        speaker_message=body.speaker_message if body.send_email else "",
    )


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

    if body.status == "open":
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
            raise HTTPException(
                status_code=409,
                detail="An evaluation round is already open for this event. "
                "Close it first, or save this round as a draft.",
            )

    # After the replay check, never before it: a retry of this very request must return the
    # round it already created rather than collide with it. After the open-round check too,
    # because renaming does not clear that one -- being told to rename first would cost the
    # organizer a round trip and still leave them blocked.
    await _reject_duplicate_round_name(db, organization_id, event_id, body.name)

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
        raise HTTPException(
            status_code=400,
            detail=await _ineligible_evaluator_detail(
                db, organization_id, event_id, list(body.evaluator_user_ids)
            ),
        )
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
    rubric = _build_round_rubric(body)
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
               (id, organization_id, event_id, name, name_key, rubric_json, status,
                review_opens_at_ms,review_closes_at_ms,created_at_ms, updated_at_ms)
               VALUES (?1, ?2, ?3, ?4, ?10, ?5, ?9, ?6, ?7, ?8, ?8)"""
        ).bind(
            round_id,
            organization_id,
            event_id,
            body.name,
            json.dumps(rubric, separators=(",", ":"), sort_keys=True),
            body.review_opens_at_ms,
            body.review_closes_at_ms,
            now,
            body.status,
            # uq_evaluation_rounds_live_name compares this, not name. The guard above
            # reports the conflict in words; this is what makes the refusal atomic when
            # two organizers submit at the same instant and both guards read "free".
            _round_name_key(body.name),
        )
    )
    # assignment_strategy is a GENERATOR for the opening matrix. If the payload carries
    # explicit pairs they are authoritative and the strategy is not consulted.
    assignment_pairs = (
        [item.pair() for item in body.assignments]
        if body.assignments is not None
        else _assignment_pairs(
            body.submission_ids, body.evaluator_user_ids, body.assignment_strategy
        )
    )
    # Membership first: the assignment foreign keys are composite against these tables, so
    # an assignment cannot be written before the round knows its proposal and its reviewer.
    for submission_id in body.submission_ids:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_round_submissions
                   (round_id, submission_id, organization_id, event_id, status,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 'active', ?5, ?5)"""
            ).bind(round_id, submission_id, organization_id, event_id, now)
        )
    for evaluator_id in body.evaluator_user_ids:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_round_evaluators
                   (round_id, evaluator_user_id, organization_id, event_id, status,
                    created_at_ms, updated_at_ms)
                   VALUES (?1, ?2, ?3, ?4, 'active', ?5, ?5)"""
            ).bind(round_id, evaluator_id, organization_id, event_id, now)
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
    # A draft round is invisible to its reviewers, so it must also be silent. The email
    # links to /reviews, where a draft assignment does not appear, so sending it here
    # announces work nobody can see. open_evaluation_round sends it instead, once the
    # assignments are actually reachable.
    notification_ids = (
        _queue_assignment_notifications(
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
        if body.status == "open"
        else []
    )
    batch.complete_idempotency(
        record,
        status=201,
        resource_type="evaluation_round",
        resource_id=round_id,
        completed_at_ms=now,
    )
    await _execute_round_write(request, batch, db, organization_id, event_id, body.name)
    await publish_committed_messages(request, notification_ids)
    return EvaluationRoundView(
        id=round_id,
        event_id=event_id,
        name=body.name,
        status=body.status,
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
            """SELECT r.id, r.event_id, r.name, r.status,
                  (SELECT COUNT(*) FROM evaluation_assignments a
                    WHERE a.round_id=r.id AND a.status!='revoked') AS assignment_count,
                  (SELECT COUNT(*) FROM evaluation_round_evaluators e
                    WHERE e.round_id=r.id AND e.status='active') AS evaluator_count
           FROM evaluation_rounds r
           WHERE r.organization_id = ?1 AND r.event_id = ?2 AND r.status = 'open'
           ORDER BY r.created_at_ms DESC LIMIT 1"""
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
            # Reviewers are counted from their own membership table, not from the
            # assignments. Deriving them from assignments made a round that has a reviewer
            # pool but no pair yet report "0 reviewers" -- the same round the draft view
            # then reopens with its reviewers intact, which reads as data that was saved
            # and then lost. Revoked assignments are excluded so a removed reviewer stops
            # inflating the count the organizer is shown.
            """SELECT r.id,r.event_id,r.name,r.status,
                      r.review_opens_at_ms,r.review_closes_at_ms,
                      (SELECT COUNT(*) FROM evaluation_assignments a
                        WHERE a.round_id=r.id AND a.status!='revoked') AS assignment_count,
                      (SELECT COUNT(*) FROM evaluation_round_evaluators e
                        WHERE e.round_id=r.id AND e.status='active') AS evaluator_count
               FROM evaluation_rounds r
               WHERE r.organization_id=?1 AND r.event_id=?2
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
                # Membership, for the same reason as the counts above: a proposal that is
                # in the round but not yet assigned to anyone is still in the round, and
                # listing it is what tells the organizer their selection was saved.
                f"""SELECT m.round_id,s.id AS submission_id,s.proposal_title
                     FROM evaluation_round_submissions m
                     JOIN submissions s ON s.id=m.submission_id
                     WHERE m.round_id IN ({placeholders}) AND m.status='active'
                     ORDER BY m.round_id,s.proposal_title,s.id"""  # noqa: S608
            )
            .bind(*round_ids)
            .all()
        )
        for proposal in proposal_rows:
            proposals_by_round[str(proposal["round_id"])].append(
                {
                    "submission_id": str(proposal["submission_id"]),
                    "proposal_title": str(proposal["proposal_title"]),
                }
            )
    return EvaluationRoundList(
        data=[
            EvaluationRoundView.model_validate(
                {**row, "proposals": proposals_by_round[str(row["id"])]}
            )
            for row in rows
        ]
    )


@evaluation_router.get(
    "/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft",
    response_model=EvaluationRoundCreate,
    tags=["evaluations"],
)
async def get_draft_evaluation_round(event_id: str, round_id: str, request: Request):
    db = _db(request)
    organization_id = await _event_organization_id(db, event_id)
    await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(organization_id, event_id),
        mutation=False,
    )
    row = row_mapping(
        await db.prepare(
            """SELECT name,rubric_json,review_opens_at_ms,review_closes_at_ms
           FROM evaluation_rounds WHERE id=?1 AND organization_id=?2
             AND event_id=?3 AND status='draft'"""
        )
        .bind(round_id, organization_id, event_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    rubric = json.loads(str(row["rubric_json"]))
    # Membership is read from its own tables, not inferred from the assignments. Deriving
    # it here is what made a proposal with no reviewer -- or a reviewer with no proposals --
    # vanish from the round entirely, and what made a tailored matrix unrecoverable.
    assignments = result_rows(
        await db.prepare(
            """SELECT submission_id,evaluator_user_id FROM evaluation_assignments
           WHERE round_id=?1 AND organization_id=?2 AND status!='revoked'"""
        )
        .bind(round_id, organization_id)
        .all()
    )
    submission_members = result_rows(
        await db.prepare(
            """SELECT submission_id FROM evaluation_round_submissions
           WHERE round_id=?1 AND organization_id=?2 AND status='active'
           ORDER BY submission_id"""
        )
        .bind(round_id, organization_id)
        .all()
    )
    evaluator_members = result_rows(
        await db.prepare(
            """SELECT evaluator_user_id FROM evaluation_round_evaluators
           WHERE round_id=?1 AND organization_id=?2 AND status='active'
           ORDER BY evaluator_user_id"""
        )
        .bind(round_id, organization_id)
        .all()
    )
    return EvaluationRoundCreate(
        name=str(row["name"]),
        rating_min=int(rubric["rating"]["min"]),
        rating_max=int(rubric["rating"]["max"]),
        recommendations=list(rubric["recommendation"]["choices"]),
        evaluator_guidance=str(rubric.get("guidance", "")),
        comment_required=bool(rubric.get("internal_comment", {}).get("required", False)),
        criteria=list(rubric.get("criteria", [])),
        blind_review=bool(rubric.get("blind_review", True)),
        review_opens_at_ms=row["review_opens_at_ms"],
        review_closes_at_ms=row["review_closes_at_ms"],
        submission_ids=[str(item["submission_id"]) for item in submission_members],
        evaluator_user_ids=[str(item["evaluator_user_id"]) for item in evaluator_members],
        assignments=[
            RoundAssignment(
                submission_id=str(item["submission_id"]),
                evaluator_user_id=str(item["evaluator_user_id"]),
            )
            for item in assignments
        ],
        assignment_strategy=str(rubric.get("assignment_strategy", "balanced")),
        status="draft",
    )


@evaluation_router.put(
    "/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft",
    response_model=EvaluationRoundView,
    tags=["evaluations"],
)
async def update_draft_evaluation_round(
    event_id: str, round_id: str, request: Request, body: EvaluationRoundCreate
):
    if body.status != "draft":
        raise HTTPException(status_code=422, detail="Draft updates must remain draft")
    db = _db(request)
    organization_id = await _event_organization_id(db, event_id)
    auth = await require_permission(
        request,
        Permission.SUBMISSION_MANAGE,
        ResourceContext(organization_id, event_id),
        mutation=True,
    )
    found = (
        await db.prepare(
            """SELECT 1 AS found FROM evaluation_rounds WHERE id=?1 AND organization_id=?2
           AND event_id=?3 AND status='draft'"""
        )
        .bind(round_id, organization_id, event_id)
        .first("found")
    )
    if found is None:
        raise HTTPException(status_code=404)
    # A rename is a name write like any other. Excluding this round keeps a save that leaves
    # the name alone -- the common case -- from colliding with itself.
    await _reject_duplicate_round_name(
        db, organization_id, event_id, body.name, exclude_round_id=round_id
    )
    if body.submission_ids:
        placeholders = ",".join(f"?{index + 3}" for index in range(len(body.submission_ids)))
        submissions = result_rows(
            await db.prepare(
                f"""SELECT id FROM submissions WHERE organization_id=?1 AND event_id=?2
                     AND status='submitted' AND id IN ({placeholders})"""  # noqa: S608
            )
            .bind(organization_id, event_id, *body.submission_ids)
            .all()
        )
        if {str(row["id"]) for row in submissions} != set(body.submission_ids):
            raise HTTPException(status_code=400)
    if body.evaluator_user_ids:
        placeholders = ",".join(f"?{index + 3}" for index in range(len(body.evaluator_user_ids)))
        evaluators = result_rows(
            await db.prepare(
                f"""SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id=u.id
                     JOIN identity_invitations i ON i.normalized_email=u.normalized_email
                       AND i.organization_id=?1 AND i.event_id=?2 AND i.role='evaluator'
                       AND i.status='accepted'
                     WHERE u.id IN ({placeholders}) AND u.status='active'
                       AND ur.role='reviewer' AND ur.status='active'"""  # noqa: S608
            )
            .bind(organization_id, event_id, *body.evaluator_user_ids)
            .all()
        )
        if {str(row["id"]) for row in evaluators} != set(body.evaluator_user_ids):
            raise HTTPException(
                status_code=400,
                detail=await _ineligible_evaluator_detail(
                    db, organization_id, event_id, list(body.evaluator_user_ids)
                ),
            )
    now = utc_now_ms()
    rubric = _build_round_rubric(body)
    # assignment_strategy only GENERATES a starting matrix. Once the organizer has edited
    # the pairs the payload carries them, and the strategy must not overwrite that -- which
    # is exactly what delete-and-regenerate used to do on every save.
    desired_pairs = (
        [item.pair() for item in body.assignments]
        if body.assignments is not None
        else _assignment_pairs(
            body.submission_ids, body.evaluator_user_ids, body.assignment_strategy
        )
    )
    current_submissions = {
        str(item["submission_id"]): str(item["status"])
        for item in result_rows(
            await db.prepare(
                """SELECT submission_id,status FROM evaluation_round_submissions
                   WHERE round_id=?1"""
            )
            .bind(round_id)
            .all()
        )
    }
    current_evaluators = {
        str(item["evaluator_user_id"]): str(item["status"])
        for item in result_rows(
            await db.prepare(
                """SELECT evaluator_user_id,status FROM evaluation_round_evaluators
                   WHERE round_id=?1"""
            )
            .bind(round_id)
            .all()
        )
    }
    current_assignments = {}
    for item in result_rows(
        await db.prepare(
            """SELECT a.id,a.submission_id,a.evaluator_user_id,a.status,a.updated_at_ms,
                      EXISTS(SELECT 1 FROM evaluation_conflicts c
                             WHERE c.assignment_id=a.id) AS has_conflict,
                      EXISTS(SELECT 1 FROM evaluations e
                             WHERE e.assignment_id=a.id AND e.state='final') AS has_final,
                      (SELECT e.criterion_responses_json FROM evaluations e
                        WHERE e.assignment_id=a.id LIMIT 1) AS draft_responses,
                      (SELECT e.internal_comment FROM evaluations e
                        WHERE e.assignment_id=a.id LIMIT 1) AS draft_comment
                 FROM evaluation_assignments a WHERE a.round_id=?1"""
        )
        .bind(round_id)
        .all()
    ):
        # Whitelist, constructed field by field. The evaluation row is never spread into
        # audit metadata: internal_comment sits right beside these counts.
        draft_summary = None
        if item["draft_responses"] is not None:
            draft_summary = {
                "criteria_answered": len(json.loads(str(item["draft_responses"]))),
                "has_comment": bool(str(item["draft_comment"] or "").strip()),
                "updated_at_ms": int(item["updated_at_ms"]),
            }
        current_assignments[(str(item["submission_id"]), str(item["evaluator_user_id"]))] = (
            AssignmentState(
                assignment_id=str(item["id"]),
                status=str(item["status"]),
                has_conflict=bool(item["has_conflict"]),
                has_final_evaluation=bool(item["has_final"]),
                draft_summary=draft_summary,
            )
        )
    diff = plan_round_diff(
        current_submissions=current_submissions,
        current_evaluators=current_evaluators,
        current_assignments=current_assignments,
        desired_submissions=body.submission_ids,
        desired_evaluators=body.evaluator_user_ids,
        desired_pairs=desired_pairs,
    )
    if diff.refused:
        # A refusal makes the requested state unattainable. Applying the remaining
        # operations would turn a conflict or finalized-review guard into a partial
        # save and could remove membership while its assignment remains active.
        raise HTTPException(
            status_code=409,
            detail="The requested assignment changes cannot be applied safely",
        )
    rubric_json = json.dumps(rubric, separators=(",", ":"), sort_keys=True)
    # Read the stored configuration explicitly. The guard above only proves the draft
    # exists, and `row` in this scope is a comprehension variable from the membership
    # validation -- not the round.
    stored = row_mapping(
        await db.prepare(
            """SELECT name,rubric_json,review_opens_at_ms,review_closes_at_ms
               FROM evaluation_rounds WHERE id=?1 LIMIT 1"""
        )
        .bind(round_id)
        .first()
    )
    configuration_changed = stored is None or (
        str(stored["name"]) != body.name
        or str(stored["rubric_json"]) != rubric_json
        or stored["review_opens_at_ms"] != body.review_opens_at_ms
        or stored["review_closes_at_ms"] != body.review_closes_at_ms
    )
    if not diff.changed and not configuration_changed:
        # A repeated PUT writes nothing at all: no batch, no version bump, no audit record.
        # Bumping updated_at_ms here would churn optimistic concurrency for every other
        # client holding a version, turning idempotency into a liveness bug.
        return await _round_view(db, round_id)

    batch = CommandBatch(db)
    if configuration_changed:
        batch.add_statement(
            db.prepare(
                """UPDATE evaluation_rounds SET name=?1,name_key=?7,rubric_json=?2,
               review_opens_at_ms=?3,review_closes_at_ms=?4,updated_at_ms=?5
               WHERE id=?6 AND status='draft'"""
            ).bind(
                body.name,
                rubric_json,
                body.review_opens_at_ms,
                body.review_closes_at_ms,
                now,
                round_id,
                # Rewritten on every configuration save, so a row the migration backfilled
                # with SQL's ASCII lower() picks up the exact key the moment it is edited.
                _round_name_key(body.name),
            )
        )
    # Ordered. Membership must exist before an assignment references it, and must outlive
    # the assignment on the way out -- the composite foreign keys make that mandatory.
    for submission_id in diff.activate_submissions:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_round_submissions
                   (round_id,submission_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,'active',?5,?5)
                   ON CONFLICT(round_id,submission_id)
                   DO UPDATE SET status='active',updated_at_ms=excluded.updated_at_ms"""
            ).bind(round_id, submission_id, organization_id, event_id, now)
        )
    for evaluator_id in diff.activate_evaluators:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_round_evaluators
                   (round_id,evaluator_user_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,'active',?5,?5)
                   ON CONFLICT(round_id,evaluator_user_id)
                   DO UPDATE SET status='active',updated_at_ms=excluded.updated_at_ms"""
            ).bind(round_id, evaluator_id, organization_id, event_id, now)
        )
    for submission_id, evaluator_id in diff.add_assignments:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,status,created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,?5,?6,'assigned',?7,?7)"""
            ).bind(new_id(), organization_id, event_id, round_id, submission_id, evaluator_id, now)
        )
    for assignment_id in diff.revive_assignments:
        batch.add_statement(
            db.prepare(
                """UPDATE evaluation_assignments SET status='assigned',updated_at_ms=?2
                   WHERE id=?1 AND status='revoked'"""
            ).bind(assignment_id, now)
        )
    for assignment_id, _reason, _summary in diff.revoke_assignments:
        batch.add_statement(
            db.prepare(
                """UPDATE evaluation_assignments SET status='revoked',updated_at_ms=?2
                   WHERE id=?1 AND status!='revoked'"""
            ).bind(assignment_id, now)
        )
    for submission_id in diff.deactivate_submissions:
        batch.add_statement(
            db.prepare(
                """UPDATE evaluation_round_submissions SET status='removed',updated_at_ms=?3
                   WHERE round_id=?1 AND submission_id=?2"""
            ).bind(round_id, submission_id, now)
        )
    for evaluator_id in diff.deactivate_evaluators:
        batch.add_statement(
            db.prepare(
                """UPDATE evaluation_round_evaluators SET status='removed',updated_at_ms=?3
                   WHERE round_id=?1 AND evaluator_user_id=?2"""
            ).bind(round_id, evaluator_id, now)
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation_round.update",
            target_type="evaluation_round",
            target_id=round_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=organization_id,
            event_id=event_id,
            metadata={
                "status": "draft",
                "assignments_added": len(diff.add_assignments),
                "assignments_revived": len(diff.revive_assignments),
                "assignments_revoked": [
                    {"assignment_id": assignment_id, "reason": reason, "discarded_draft": summary}
                    for assignment_id, reason, summary in diff.revoke_assignments
                ],
                "submissions_removed": len(diff.deactivate_submissions),
                "evaluators_removed": len(diff.deactivate_evaluators),
                "configuration_changed": configuration_changed,
            },
        )
    )
    await _execute_round_write(
        request, batch, db, organization_id, event_id, body.name, exclude_round_id=round_id
    )
    return await _round_view(db, round_id)


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
            """SELECT u.id AS user_id,
                      COALESCE(NULLIF(TRIM(u.display_name),''),u.email) AS display_name
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
    if rows:
        return EvaluatorList(data=[EvaluatorView.model_validate(row) for row in rows])
    # No eligible reviewer. Before answering "nobody", say whether an invitation is
    # already outstanding: an organizer told only "not found" re-invites, which merely
    # reissues the same pending row and leaves them exactly as blocked.
    #
    # State only, no identity. SUBMISSION_MANAGE (satisfied by an event grant of `edit`)
    # reaches this route; the invitation roster needs RESOURCE_ACCESS_MANAGE (`manage`).
    # Returning a name or an invitation id here would let an `edit` collaborator probe by
    # email for roster facts they are not entitled to read.
    pending_rows = result_rows(
        await db.prepare(
            """SELECT email, expires_at_ms<=?4 AS expired
               FROM identity_invitations
               WHERE organization_id=?2 AND event_id=?3 AND normalized_email=?1
                 AND role='evaluator' AND status='pending'
               ORDER BY created_at_ms DESC LIMIT 1"""
        )
        .bind(normalized, organization_id, event_id, utc_now_ms())
        .all()
    )
    return EvaluatorList(
        data=[],
        pending=[
            PendingEvaluatorView.model_validate({**row, "expired": bool(row["expired"])})
            for row in pending_rows
        ],
    )


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
    if round_row["status"] not in ("draft", "open"):
        raise HTTPException(status_code=409)
    reviewer = (
        await db.prepare(
            """SELECT 1 AS found FROM users u JOIN user_roles ur ON ur.user_id=u.id
           JOIN identity_invitations i
             ON i.organization_id=?2 AND i.event_id=?3
            AND i.normalized_email=u.normalized_email
            AND i.role='evaluator' AND i.status='accepted'
           WHERE u.id=?1 AND u.status='active'
             AND ur.role='reviewer' AND ur.status='active' LIMIT 1"""
        )
        .bind(body.evaluator_user_id, round_row["organization_id"], round_row["event_id"])
        .first("found")
    )
    if reviewer is None:
        raise HTTPException(
            status_code=400,
            detail=await _ineligible_evaluator_detail(
                db,
                str(round_row["organization_id"]),
                str(round_row["event_id"]),
                [body.evaluator_user_id],
            ),
        )
    # The proposals the organizer actually chose -- NOT every proposal in the round.
    # Fanning a new reviewer across the whole round is the behaviour this replaces: adding
    # Sam used to assign him all three proposals with no way to say "A and B, not C".
    # Each named proposal must already be an active member of the round.
    member_ids = {
        str(row["submission_id"])
        for row in result_rows(
            await db.prepare(
                """SELECT submission_id FROM evaluation_round_submissions
                   WHERE round_id=?1 AND status='active'"""
            )
            .bind(round_id)
            .all()
        )
    }
    submission_ids = sorted(set(body.submission_ids))
    if not set(submission_ids) <= member_ids:
        raise HTTPException(
            status_code=422,
            detail="Every proposal must already be in this round before it can be assigned.",
        )
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
    chosen = set(submission_ids)
    revive_ids = [
        str(row["id"])
        for row in existing_rows
        # Scoped to the chosen proposals. Reviving every revoked row for this reviewer
        # would silently re-add proposals the organizer did not ask for.
        if row["status"] == "revoked"
        and not row["has_conflict"]
        and str(row["submission_id"]) in chosen
    ]
    missing_ids = [
        submission_id for submission_id in submission_ids if submission_id not in existing_ids
    ]
    now = utc_now_ms()
    batch = CommandBatch(db)
    # Membership before assignments: the composite foreign keys require it, and re-adding a
    # previously removed reviewer must restore them to the pool.
    batch.add_statement(
        db.prepare(
            """INSERT INTO evaluation_round_evaluators
               (round_id,evaluator_user_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES(?1,?2,?3,?4,'active',?5,?5)
               ON CONFLICT(round_id,evaluator_user_id)
               DO UPDATE SET status='active',updated_at_ms=excluded.updated_at_ms"""
        ).bind(
            round_id,
            body.evaluator_user_id,
            round_row["organization_id"],
            round_row["event_id"],
            now,
        )
    )
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
                new_id(),
                round_row["organization_id"],
                round_row["event_id"],
                round_id,
                submission_id,
                body.evaluator_user_id,
                now,
            )
        )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation_round.evaluator.add",
            target_type="evaluation_round",
            target_id=round_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(round_row["organization_id"]),
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
    # Silent while the round is a draft: these assignments are not on /reviews yet.
    # Opening the round notifies everyone it holds, including this reviewer.
    notification_ids = (
        _queue_assignment_notifications(
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
            # One immediate notice per reviewer and round each hour. The body names a
            # queue change rather than a count, so later clicks in the window cannot make
            # the already-queued message stale.
            dedup_suffix=f"assignment-window:{now // 3_600_000}",
            summarize_changes=True,
        )
        if round_row["status"] == "open"
        else []
    )
    await batch.execute()
    await publish_committed_messages(request, notification_ids)
    return RoundEvaluatorChange(
        round_id=round_id,
        evaluator_user_id=body.evaluator_user_id,
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
            result = RoundSubmissionChange.model_validate_json(str(replay["response_resource_id"]))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=409) from exc
        if result.round_id != round_id:
            raise HTTPException(status_code=409)
        return result
    if round_row["status"] not in ("draft", "open"):
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
        submission_id for submission_id in body.submission_ids if submission_id not in existing_ids
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
    # Adding proposals to a live round adds them to its membership first -- the composite
    # foreign keys reject an assignment whose proposal the round does not yet know about.
    for submission_id in new_submission_ids:
        batch.add_statement(
            db.prepare(
                """INSERT INTO evaluation_round_submissions
                   (round_id,submission_id,organization_id,event_id,status,
                    created_at_ms,updated_at_ms)
                   VALUES(?1,?2,?3,?4,'active',?5,?5)
                   ON CONFLICT(round_id,submission_id)
                   DO UPDATE SET status='active',updated_at_ms=excluded.updated_at_ms"""
            ).bind(
                round_id,
                submission_id,
                round_row["organization_id"],
                round_row["event_id"],
                now,
            )
        )
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
    added_digest = hashlib.sha256(":".join(sorted(new_submission_ids)).encode()).hexdigest()[:16]
    # Silent while the round is a draft, for the same reason: nothing reached /reviews.
    notification_ids = (
        _queue_assignment_notifications(
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
            dedup_suffix=(f"{added_digest}:{hashlib.sha256(key.encode()).hexdigest()[:16]}"),
        )
        if round_row["status"] == "open"
        else []
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
    await publish_committed_messages(request, notification_ids)
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
    if round_row["status"] not in ("draft", "open"):
        raise HTTPException(status_code=409)
    saved = int(
        await db.prepare(
            """SELECT COUNT(*) AS count_value FROM evaluations e
               JOIN evaluation_assignments a ON a.id=e.assignment_id
               WHERE a.round_id=?1 AND a.evaluator_user_id=?2"""
        )
        .bind(round_id, evaluator_user_id)
        .first("count_value")
        or 0
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
        )
        .bind(round_id, evaluator_user_id)
        .first("count_value")
        or 0
    )
    if uncovered:
        raise HTTPException(status_code=409)
    active_count = int(
        await db.prepare(
            """SELECT COUNT(*) AS count_value FROM evaluation_assignments
               WHERE round_id=?1 AND evaluator_user_id=?2 AND status!='revoked'"""
        )
        .bind(round_id, evaluator_user_id)
        .first("count_value")
        or 0
    )
    now = utc_now_ms()
    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """UPDATE evaluation_assignments SET status='revoked', updated_at_ms=?3
               WHERE round_id=?1 AND evaluator_user_id=?2 AND status!='revoked'"""
        ).bind(round_id, evaluator_user_id, now)
    )
    # Progress is membership-driven, so removal must update the membership in the same
    # batch as its assignments. Leaving it active makes the reviewer reappear as an
    # attached reviewer with no proposals immediately after a successful removal.
    batch.add_statement(
        db.prepare(
            """UPDATE evaluation_round_evaluators SET status='removed', updated_at_ms=?3
               WHERE round_id=?1 AND evaluator_user_id=?2 AND status='active'"""
        ).bind(round_id, evaluator_user_id, now)
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation_round.evaluator.remove",
            target_type="evaluation_round",
            target_id=round_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(round_row["organization_id"]),
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
        )
        .bind(round_id, evaluator_user_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    auth = await require_permission(
        request,
        Permission.COMMUNICATION_SEND,
        ResourceContext(str(row["organization_id"]), str(row["event_id"])),
        mutation=True,
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
            message_id,
            row["organization_id"],
            row["event_id"],
            evaluator_user_id,
            row["email"],
            f"Review reminder: {row['name']}",
            html_body,
            deterministic,
            now,
        )
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation_round.evaluator.remind",
            target_type="evaluation_round",
            target_id=round_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            metadata={"outstanding_count": outstanding},
        )
    )
    try:
        await batch.execute()
    except PersistenceError:
        # The hourly deterministic key is what makes this endpoint safe to call twice; it
        # must not also be what makes the second call fail. The unique constraint on
        # (organization_id, event_id, deterministic_key) is the send already having
        # happened, so the honest answer is that message, not a 5xx the caller reads as a
        # reminder that never went out. A bulk nudge is the case that punished this: one
        # collision partway down the reviewer list aborted the loop and silently stranded
        # everyone after it.
        existing = (
            await db.prepare(
                """SELECT id FROM communication_messages
                   WHERE organization_id=?1 AND event_id=?2 AND deterministic_key=?3
                   LIMIT 1"""
            )
            .bind(row["organization_id"], row["event_id"], deterministic)
            .first("id")
        )
        # Anything else really did fail to write.
        if existing is None:
            raise
        # No republish: that row is already queued, and the scheduled dispatcher
        # republishes anything that stays queued.
        return EvaluatorReminderQueued(message_id=str(existing))
    await publish_committed_messages(request, [message_id])
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
                  COALESCE(e.criterion_responses_json, '{}') AS criterion_responses_json,
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
                comment_required=bool(rubric.get("internal_comment", {}).get("required", False)),
                criteria=list(rubric.get("criteria", [])),
                criterion_responses=json.loads(str(row["criterion_responses_json"])),
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
        assignment["review_opens_at_ms"] is None or int(assignment["review_opens_at_ms"]) <= now
    ) and (
        assignment["review_closes_at_ms"] is None or int(assignment["review_closes_at_ms"]) > now
    )
    resource = ResourceContext(
        str(assignment["organization_id"]),
        str(assignment["event_id"]),
        evaluator_user_id=str(assignment["evaluator_user_id"]),
        evaluator_assignment_status=str(assignment["status"]),
        evaluation_round_open=assignment["round_status"] == "open" and within_window,
    )
    # A completed replay is a read of the review that this reviewer already owns,
    # not a second save. Authorize ownership first so a lost response can be
    # recovered even after the review window closes.
    authenticated = await require_permission(
        request,
        Permission.EVALUATION_OWN_READ,
        resource,
        mutation=True,
    )
    # Authentication and the mutation guard run before header-shape validation, so
    # anonymous callers receive the route's normal 401 rather than a key-format 400.
    _key(idempotency_key)
    fingerprint = hashlib.sha256(
        json.dumps(body.model_dump(), separators=(",", ":"), sort_keys=True).encode()
    ).digest()
    route = "PUT /api/v1/evaluator/assignments/{assignment_id}/evaluation"
    # One version-scoped semantic operation serializes draft and final writes that
    # started from the same review state. A later intentional draft save receives
    # the next version key; a finalized review keeps its current key for replay.
    existing_version = int(assignment["existing_version"])
    target_version = (
        existing_version if assignment["existing_state"] == "final" else existing_version + 1
    )
    mutation_key = f"evaluation-save:{assignment_id}:v{target_version}"
    key_hash = hashlib.sha256(mutation_key.encode()).digest()
    replay = row_mapping(
        await db.prepare(
            """SELECT request_fingerprint FROM idempotency_records
           WHERE principal_key = ?1 AND route_key = ?2 AND idempotency_key_hash = ?3
             AND state = 'completed'"""
        )
        .bind(authenticated.actor.user_id, route, key_hash)
        .first()
    )
    if replay:
        if _blob(replay["request_fingerprint"]) != fingerprint:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This review version was already saved with different answers. "
                    "Reload the review before trying again."
                ),
            )
        return await _evaluation_view_by_assignment(db, assignment_id)

    authenticated = await require_permission(
        request,
        Permission.EVALUATION_SAVE,
        resource,
        mutation=True,
    )
    if assignment["existing_state"] == "final":
        raise HTTPException(
            status_code=409,
            detail="This review is already finalized. Reload to see the recorded evaluation.",
        )
    rubric = json.loads(str(assignment["rubric_json"]))
    rating_min, rating_max = int(rubric["rating"]["min"]), int(rubric["rating"]["max"])
    criteria = list(rubric.get("criteria", []))
    criteria_by_key = {str(criterion["key"]): criterion for criterion in criteria}
    criterion_keys = set(criteria_by_key)
    if not set(body.criterion_responses) <= criterion_keys:
        raise HTTPException(status_code=422)
    required_keys = {
        key for key, criterion in criteria_by_key.items() if criterion.get("required", True)
    }
    if body.state == "final" and not required_keys <= set(body.criterion_responses):
        missing = sorted(required_keys - set(body.criterion_responses))
        raise HTTPException(
            status_code=422,
            detail=f"Complete every required scorecard response: {', '.join(missing)}.",
        )
    for key, response in body.criterion_responses.items():
        criterion = criteria_by_key[key]
        response_type = criterion.get("response_type", "score")
        if response_type == "score":
            if type(response) is not int or not rating_min <= response <= rating_max:
                raise HTTPException(status_code=422)
        elif response_type == "select":
            if not isinstance(response, str) or response not in criterion.get("options", []):
                raise HTTPException(status_code=422)
        elif not isinstance(response, str) or len(response) > 5000:
            raise HTTPException(status_code=422)
        elif body.state == "final" and key in required_keys and not response.strip():
            raise HTTPException(status_code=422)
    recommendation, internal_comment = _canonical_evaluation_fields(rubric, body)
    rating = body.rating
    scored = [
        criterion for criterion in criteria if criterion.get("response_type", "score") == "score"
    ]
    scored_responses = {
        key: value
        for key, value in body.criterion_responses.items()
        if key in criteria_by_key and criteria_by_key[key].get("response_type", "score") == "score"
    }
    if scored and scored_responses:
        rating = round(
            sum(
                int(scored_responses[str(criterion["key"])]) * int(criterion["weight"])
                for criterion in scored
                if str(criterion["key"]) in scored_responses
            )
            / sum(
                int(criterion["weight"])
                for criterion in scored
                if str(criterion["key"]) in scored_responses
            )
        )
    if body.state == "final" and rating is None:
        raise HTTPException(status_code=422)
    if rating is not None and not rating_min <= rating <= rating_max:
        raise HTTPException(status_code=422)
    if (
        recommendation is not None
        and recommendation not in rubric["recommendation"]["choices"]
    ):
        raise HTTPException(status_code=422)
    if body.state == "final" and recommendation is None:
        raise HTTPException(
            status_code=422,
            detail="Choose a recommendation before finalizing this review.",
        )
    if (
        body.state == "final"
        and rubric.get("internal_comment", {}).get("required")
        and not internal_comment
    ):
        raise HTTPException(
            status_code=422,
            detail="Add the required reviewer comment before finalizing this review.",
        )

    evaluation_id = str(assignment["evaluation_id"] or new_id())
    version = target_version
    finalized_at = now if body.state == "final" else None
    record = IdempotencyRecord(
        principal_key=authenticated.actor.user_id,
        organization_id=str(assignment["organization_id"]),
        event_id=str(assignment["event_id"]),
        route_key=route,
        idempotency_key=mutation_key,
        request_fingerprint=fingerprint,
        expires_at_ms=now + 86_400_000,
    )
    batch = CommandBatch(db)
    batch.begin_idempotency(record, now)
    batch.add_statement(
        db.prepare(
            """INSERT INTO evaluations
           (id, organization_id, event_id, round_id, assignment_id, evaluator_user_id,
            rating, recommendation, internal_comment,criterion_responses_json,state, version,
            created_at_ms,updated_at_ms, finalized_at_ms)
           VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?13, ?14)
           ON CONFLICT(assignment_id) DO UPDATE SET rating = excluded.rating,
             recommendation = excluded.recommendation,
             internal_comment = excluded.internal_comment,
             criterion_responses_json=excluded.criterion_responses_json,state = excluded.state,
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
            recommendation,
            internal_comment,
            json.dumps(body.criterion_responses, separators=(",", ":"), sort_keys=True),
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
    try:
        await _execute(request, batch)
    except HTTPException as exc:
        # A same-key race may lose at begin_idempotency after the other request
        # commits. Recover only a matching completed record; unrelated conflicts
        # retain their original failure.
        if exc.status_code != 409:
            raise
        raced = row_mapping(
            await db.prepare(
                """SELECT request_fingerprint FROM idempotency_records
               WHERE principal_key = ?1 AND route_key = ?2 AND idempotency_key_hash = ?3
                 AND state = 'completed'"""
            )
            .bind(authenticated.actor.user_id, route, key_hash)
            .first()
        )
        if raced is None:
            raise
        if _blob(raced["request_fingerprint"]) != fingerprint:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This review version was already saved with different answers. "
                    "Reload the review before trying again."
                ),
            ) from exc
    return await _evaluation_view_by_assignment(db, assignment_id)


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
        raise HTTPException(
            status_code=400,
            detail=await _ineligible_evaluator_detail(
                db,
                str(assignment["organization_id"]),
                str(assignment["event_id"]),
                [body.evaluator_user_id],
            ),
        )
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
                """SELECT r.id, r.organization_id, r.event_id, r.name, r.status,
                          r.rubric_json, e.name AS event_name
                   FROM evaluation_rounds r
                   JOIN events e ON e.id=r.event_id AND e.organization_id=r.organization_id
                   WHERE r.id = ?1 LIMIT 1"""
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
    # Driven from round MEMBERSHIP, not from assignments. Starting at
    # evaluation_assignments meant a proposal whose last assignment was revoked -- a
    # conflict declared on the only reviewer, say -- dropped out of the results altogether,
    # so the one state an organizer most needs to see was the one state that was invisible.
    # It now appears with assigned_count 0 and needs_reassignment true.
    results_query = """SELECT s.id AS submission_id, s.speaker_name, s.proposal_title,
              s.submitted_at_ms,
              COUNT(a.id) AS assigned_count,
              CASE WHEN COUNT(a.id) = 0 THEN 1 ELSE 0 END AS needs_reassignment,
              SUM(CASE WHEN e.state = 'final' THEN 1 ELSE 0 END) AS completed_count,
              AVG(CASE WHEN e.state = 'final' THEN e.rating END) AS average_rating,
              COALESCE((SELECT c.corrected_decision
                FROM submission_decision_corrections c
                WHERE c.organization_id=m.organization_id AND c.event_id=m.event_id
                  AND c.submission_id=m.submission_id
                ORDER BY c.corrected_at_ms DESC,c.id DESC LIMIT 1),d.decision) AS decision,
              COALESCE(d.internal_reason,'') AS internal_reason,
              COALESCE((SELECT c.reason
                FROM submission_decision_corrections c
                WHERE c.organization_id=m.organization_id AND c.event_id=m.event_id
                  AND c.submission_id=m.submission_id
                ORDER BY c.corrected_at_ms DESC,c.id DESC LIMIT 1),'')
                AS correction_reason,
              d.round_id AS decision_round_id
       FROM evaluation_round_submissions m
       JOIN submissions s ON s.id = m.submission_id
       LEFT JOIN evaluation_assignments a
         ON a.round_id = m.round_id AND a.submission_id = m.submission_id
        AND a.status != 'revoked'
       LEFT JOIN evaluations e ON e.assignment_id = a.id
       LEFT JOIN submission_decisions d
         ON d.organization_id=m.organization_id AND d.event_id=m.event_id
        AND d.submission_id=m.submission_id
       WHERE m.round_id = ?1 AND m.status = 'active'
         AND (?2 IS NULL OR s.submitted_at_ms<?2 OR (s.submitted_at_ms=?2 AND s.id<?3))
       GROUP BY s.id, s.speaker_name, s.proposal_title, d.id,d.round_id,d.internal_reason
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
        review_query = """SELECT a.submission_id,a.evaluator_user_id,
                      COALESCE(NULLIF(TRIM(u.display_name),''),u.email) AS evaluator_name,
                      COALESCE(e.state,'not_started') AS state,e.rating,e.recommendation,
                      COALESCE(e.criterion_responses_json,'{}') AS criterion_responses_json,
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
    all_round_criteria = _full_round_criteria(round_row["rubric_json"])
    round_criteria = _round_criteria_for_historical_scores(round_row["rubric_json"])
    reviews_by_submission: dict[str, list[EvaluationDetail]] = {}
    weighted_by_submission: dict[str, list[tuple[float, int]]] = {}
    for review in review_rows:
        weighted_score = None
        if str(review["state"]) == "final":
            weighted_score = _weighted_review_score(
                round_criteria, review["criterion_responses_json"], review["rating"]
            )
            if weighted_score is not None:
                weighted_by_submission.setdefault(str(review["submission_id"]), []).append(
                    (weighted_score, 1)
                )
        reviews_by_submission.setdefault(str(review["submission_id"]), []).append(
            EvaluationDetail(
                evaluator_user_id=str(review["evaluator_user_id"]),
                evaluator_name=str(review["evaluator_name"]),
                state=str(review["state"]),
                rating=int(review["rating"]) if review["rating"] is not None else None,
                weighted_score=(
                    round(weighted_score, 2) if weighted_score is not None else None
                ),
                recommendation=(
                    str(review["recommendation"]) if review["recommendation"] is not None else None
                ),
                internal_comment=str(review["internal_comment"]),
                criterion_responses=(
                    _criterion_response_mapping(review["criterion_responses_json"])
                    if str(review["state"]) == "final"
                    else {}
                ),
            )
        )
    submissions = [
        SubmissionEvaluationResult(
            submission_id=str(row["submission_id"]),
            speaker_name=str(row["speaker_name"]),
            proposal_title=str(row["proposal_title"]),
            assigned_count=int(row["assigned_count"]),
            needs_reassignment=bool(row["needs_reassignment"]),
            completed_count=int(row["completed_count"] or 0),
            average_rating=(
                _weighted_mean(weighted_by_submission[str(row["submission_id"])])
                if weighted_by_submission.get(str(row["submission_id"]))
                else (
                    round(float(row["average_rating"]), 2)
                    if row["average_rating"] is not None
                    else None
                )
            ),
            decision=(str(row["decision"]) if row["decision"] is not None else None),
            decision_round_id=(
                str(row["decision_round_id"])
                if row["decision_round_id"] is not None
                else None
            ),
            internal_reason=str(row["internal_reason"] or ""),
            correction_reason=str(row["correction_reason"] or ""),
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
                # Keep this membership-driven for the same reason as the reviewer count in
                # list_evaluation_rounds: a draft can hold a reviewer before it has any
                # assignments, and that reviewer must still appear in the results read model.
                """SELECT m.evaluator_user_id,
                  COALESCE(NULLIF(TRIM(u.display_name),''),u.email) AS display_name,
                  COUNT(DISTINCT CASE WHEN a.status != 'revoked' THEN a.id END)
                    AS assigned_count,
                  COUNT(DISTINCT CASE WHEN a.status != 'revoked' AND e.state = 'final'
                                      THEN a.id END) AS completed_count,
                  COUNT(DISTINCT CASE WHEN a.status != 'revoked' THEN c.id END)
                    AS conflict_count
           FROM evaluation_round_evaluators m
           JOIN users u ON u.id = m.evaluator_user_id
           LEFT JOIN evaluation_assignments a
             ON a.round_id = m.round_id
            AND a.evaluator_user_id = m.evaluator_user_id
           LEFT JOIN evaluations e ON e.assignment_id = a.id
           LEFT JOIN evaluation_conflicts c ON c.assignment_id = a.id
           WHERE m.round_id = ?1 AND m.status = 'active'
           GROUP BY m.evaluator_user_id, u.email, u.display_name
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
                          COALESCE(NULLIF(TRIM(u.display_name),''),u.email) AS display_name
                   FROM users u
                   JOIN user_roles ur ON ur.user_id=u.id
                   JOIN identity_invitations i
                     ON i.organization_id=?1 AND i.event_id=?2
                    AND i.normalized_email=u.normalized_email
                    AND i.role='evaluator' AND i.status='accepted'
                   WHERE u.status='active'
                     AND ur.role='reviewer' AND ur.status='active'
                   ORDER BY u.normalized_email LIMIT 100"""
            ).bind(round_row["organization_id"], round_row["event_id"]),
        )
    )
    available_evaluators = [EvaluatorView.model_validate(row) for row in available_evaluator_rows]
    conflict_rows = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT c.assignment_id, a.submission_id, c.evaluator_user_id,
                  COALESCE(NULLIF(TRIM(u.display_name),''),u.email) AS evaluator_name,
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
    round_scores = result_rows(
        await _timed_all(
            request,
            db.prepare(
                """SELECT e.rating, e.criterion_responses_json
                   FROM evaluations e
                   JOIN evaluation_assignments a ON a.id = e.assignment_id
                   WHERE a.round_id = ?1 AND a.status != 'revoked' AND e.state = 'final'"""
            ).bind(round_id),
        )
    )
    round_weighted = [
        (score, 1)
        for score in (
            _weighted_review_score(round_criteria, row["criterion_responses_json"], row["rating"])
            for row in round_scores
        )
        if score is not None
    ]
    return EvaluationRoundResults(
        round_id=round_id,
        event_id=str(round_row["event_id"]),
        event_name=str(round_row["event_name"]),
        round_name=str(round_row["name"]),
        status=str(round_row["status"]),
        assigned_count=int(aggregate["assigned_count"] or 0),
        completed_count=int(aggregate["completed_count"] or 0),
        average_rating=(
            _weighted_mean(round_weighted)
            if round_weighted
            else (
                round(float(aggregate["average_rating"]), 2)
                if aggregate["average_rating"] is not None
                else None
            )
        ),
        criteria=all_round_criteria,
        submissions=submissions,
        submission_count=int(aggregate["submission_count"] or 0),
        next_cursor=next_cursor,
        evaluators=evaluator_progress,
        available_evaluators=available_evaluators,
        conflicts=conflicts,
    )


def _csv_safe(value: object) -> object:
    """Prevent spreadsheet formula execution without changing ordinary values."""
    if isinstance(value, str) and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


async def _collect_export_submissions(
    round_id: str,
    request: Request,
    first_page: EvaluationRoundResults,
    *,
    max_pages: int = EXPORT_MAX_PAGES,
) -> list[SubmissionEvaluationResult]:
    """Walk every results page and fail closed rather than return a partial file."""
    proposal_limit = max_pages * EVALUATION_PAGE_LIMIT
    if first_page.submission_count > proposal_limit:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This round exceeds the {proposal_limit:,}-proposal single-file "
                "export limit. Ask your SessionBuddy administrator for a paginated "
                "bulk export; no partial CSV was downloaded."
            ),
            headers={"X-Conflict-Type": "export-limit"},
        )
    exported = list(first_page.submissions)
    cursor = first_page.next_cursor
    pages = 1
    while cursor and pages < max_pages:
        page = await get_round_results(round_id, request, cursor=cursor)
        exported.extend(page.submissions)
        cursor = page.next_cursor
        pages += 1
    if cursor:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This round exceeds the {proposal_limit:,}-proposal "
                "single-file export limit. Ask your SessionBuddy administrator for a "
                "paginated bulk export; no partial CSV was downloaded."
            ),
            headers={"X-Conflict-Type": "export-limit"},
        )
    return exported


def _filename_component(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    component = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE)
    return re.sub(r"-+", "-", component).strip("-._")


def _truncate_utf8(value: str, byte_limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value
    return encoded[:byte_limit].decode("utf-8", errors="ignore").rstrip("-._")


def _evaluation_export_filename_parts(
    results: EvaluationRoundResults, *, review_details: bool
) -> tuple[str, str]:
    kind = "review-details" if review_details else "results"
    discriminator = _filename_component(results.round_id.split("-", 1)[0])[:8] or "round"
    tail = f"-{kind}-{discriminator}.csv"
    prefix = "-".join(
        part
        for part in (
            _filename_component(results.event_name),
            _filename_component(results.round_name),
        )
        if part
    ) or "evaluation-round"
    prefix = _truncate_utf8(prefix, max(1, 180 - len(tail.encode("utf-8"))))
    return f"{prefix}{tail}", tail


@evaluation_router.get(
    "/api/v1/admin/evaluation-rounds/{round_id}/export.csv",
    response_class=Response,
    operation_id="exportEvaluationRoundResults",
    tags=["evaluations"],
    responses={200: {"headers": EXPORT_RESPONSE_HEADERS}},
)
async def export_round_results(round_id: str, request: Request) -> Response:
    results = await get_round_results(round_id, request)
    exported = await _collect_export_submissions(round_id, request, results)
    filename, filename_suffix = _evaluation_export_filename_parts(
        results, review_details=False
    )
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

    for submission in exported:
        csv.writerow(
            [
                submission.submission_id,
                _csv_safe(submission.proposal_title),
                _csv_safe(submission.speaker_name),
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
            "Content-Disposition": attachment_header(
                filename,
                ascii_suffix=filename_suffix,
            ),
            "X-Export-Row-Count": str(len(exported)),
        },
    )


@evaluation_router.get(
    "/api/v1/admin/evaluation-rounds/{round_id}/reviews.csv",
    response_class=Response,
    operation_id="exportEvaluationRoundReviews",
    tags=["evaluations"],
    responses={200: {"headers": EXPORT_RESPONSE_HEADERS}},
)
async def export_round_reviews(round_id: str, request: Request) -> Response:
    """Export one row per evaluation with rubric-ordered criterion responses."""
    results = await get_round_results(round_id, request)
    exported = await _collect_export_submissions(round_id, request, results)
    filename, filename_suffix = _evaluation_export_filename_parts(
        results, review_details=True
    )

    output = StringIO(newline="")
    csv = writer(output)
    csv.writerow(
        [
            "submission_id",
            "proposal_title",
            "speaker_name",
            "evaluator_name",
            "review_state",
            "rating",
            "weighted_score",
            "legacy_recommendation",
            "legacy_internal_comment",
            *[
                f"{criterion.label} [{criterion.key}]"
                for criterion in results.criteria
            ],
        ]
    )
    for submission in exported:
        for review in submission.reviews:
            csv.writerow(
                [
                    submission.submission_id,
                    _csv_safe(submission.proposal_title),
                    _csv_safe(submission.speaker_name),
                    _csv_safe(review.evaluator_name),
                    review.state,
                    review.rating if review.rating is not None else "",
                    review.weighted_score if review.weighted_score is not None else "",
                    _csv_safe(review.recommendation or ""),
                    _csv_safe(review.internal_comment),
                    *[
                        _csv_safe(review.criterion_responses.get(criterion.key, ""))
                        for criterion in results.criteria
                    ],
                ]
            )
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": attachment_header(
                filename,
                ascii_suffix=filename_suffix,
            ),
            "X-Export-Row-Count": str(
                sum(len(submission.reviews) for submission in exported)
            ),
        },
    )


@evaluation_router.post(
    "/api/v1/admin/evaluation-rounds/{round_id}/open",
    response_model=EvaluationRoundView,
    operation_id="openEvaluationRound",
    tags=["evaluations"],
)
async def open_evaluation_round(
    round_id: str,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> EvaluationRoundView:
    """Promote a draft evaluation round to open.

    A draft round is fully configured -- name, review window, scorecard, reviewer pool
    and proposal set -- but invisible to reviewers, so an organizer can plan the next
    round while the current one is still collecting scores. Only one round per event may
    be open at a time; opening a draft while another round is open returns 409.

    Opening is also when the round's reviewers are told about it. Assignments written
    while the round was a draft queue no mail (they would link to an empty /reviews), so
    this is their one notification; assignments added to an already-open round were
    announced when they were added and are not announced again here.
    """
    db = _db(request)
    round_row = row_mapping(
        await db.prepare(
            """SELECT id, organization_id, event_id, status, name, review_closes_at_ms
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
    if round_row["status"] == "open":
        return await _round_view(db, round_id)
    if round_row["status"] != "draft":
        raise HTTPException(status_code=409, detail="Only a draft round can be opened.")
    key = _key(idempotency_key)
    route = "POST /api/v1/admin/evaluation-rounds/{round_id}/open"
    fingerprint = hashlib.sha256(f"open:{round_id}".encode()).digest()
    replay = await _idempotency_replay(db, auth.actor.user_id, route, key, fingerprint)
    if replay is not None:
        return await _round_view(db, round_id)
    active_round = (
        await db.prepare(
            """SELECT 1 AS found FROM evaluation_rounds
           WHERE organization_id = ?1 AND event_id = ?2
             AND status = 'open' AND id != ?3 LIMIT 1"""
        )
        .bind(str(round_row["organization_id"]), str(round_row["event_id"]), round_id)
        .first("found")
    )
    if active_round is not None:
        raise HTTPException(
            status_code=409,
            detail="Another evaluation round is already open for this event. "
            "Close it before opening this one.",
        )
    coverage = row_mapping(
        await db.prepare(
            """SELECT COUNT(*) AS assignment_count,
                      COUNT(DISTINCT evaluator_user_id) AS evaluator_count
               FROM evaluation_assignments WHERE round_id = ?1 AND status != 'revoked'"""
        )
        .bind(round_id)
        .first()
    )
    if coverage is None or int(coverage["assignment_count"] or 0) == 0:
        raise HTTPException(
            status_code=409,
            detail="Add at least one proposal and one reviewer before opening this round.",
        )
    # Every proposal in the round must have somebody reviewing it. With explicit pairs a
    # proposal can be a member with nobody assigned -- fine while drafting, but once review
    # starts that proposal can never be decided, so opening is refused and the offending
    # titles are named. A reviewer with no proposals is only a warning: harmless, and the
    # organizer may be about to assign them.
    unassigned = result_rows(
        await db.prepare(
            """SELECT s.proposal_title
                 FROM evaluation_round_submissions m
                 JOIN submissions s ON s.id = m.submission_id
                WHERE m.round_id = ?1 AND m.status = 'active'
                  AND NOT EXISTS (
                    SELECT 1 FROM evaluation_assignments a
                     WHERE a.round_id = m.round_id
                       AND a.submission_id = m.submission_id
                       AND a.status != 'revoked')
                ORDER BY s.proposal_title"""
        )
        .bind(round_id)
        .all()
    )
    if unassigned:
        titles = ", ".join(f"\u201c{str(item['proposal_title'])}\u201d" for item in unassigned[:5])
        more = "" if len(unassigned) <= 5 else f" and {len(unassigned) - 5} more"
        raise HTTPException(
            status_code=409,
            detail=(
                f"Assign a reviewer to every proposal before opening this round. "
                f"Still unassigned: {titles}{more}."
            ),
        )
    # Per-reviewer workload for the opening notification. Revoked assignments are left
    # out for the same reason they never reach /reviews: nobody has to act on them.
    assignment_counts = {
        str(row["evaluator_user_id"]): int(row["assignment_count"] or 0)
        for row in result_rows(
            await db.prepare(
                """SELECT evaluator_user_id, COUNT(*) AS assignment_count
                     FROM evaluation_assignments
                    WHERE round_id = ?1 AND status != 'revoked'
                    GROUP BY evaluator_user_id"""
            )
            .bind(round_id)
            .all()
        )
    }
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
            """UPDATE evaluation_rounds SET status = 'open', updated_at_ms = ?1
             WHERE id = ?2 AND status = 'draft'"""
        ).bind(now, round_id)
    )
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
        # Keyed to the round rather than the clock. A round leaves draft exactly once, so
        # this cannot collide with itself, and a retried open after the idempotency record
        # has expired is refused by the UNIQUE deterministic key rather than mailed twice.
        dedup_suffix="round-opened",
    )
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=auth.actor.user_id,
            action="evaluation_round.open",
            target_type="evaluation_round",
            target_id=round_id,
            result="succeeded",
            correlation_id=request.state.request_id,
            occurred_at_ms=now,
            organization_id=str(round_row["organization_id"]),
            event_id=str(round_row["event_id"]),
            metadata={
                "assignment_count": int(coverage["assignment_count"] or 0),
                "evaluator_count": int(coverage["evaluator_count"] or 0),
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
    await publish_committed_messages(request, notification_ids)
    return await _round_view(db, round_id)


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
             updated_at_ms = ?1 WHERE id = ?2 AND status IN ('draft','open')"""
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
    round_id: str | None,
    submission_id: str,
    request: Request,
    body: SubmissionDecisionCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    direct_event_id: str | None = None,
) -> SubmissionDecisionView:
    db = _db(request)
    if round_id is None:
        context = row_mapping(
            await db.prepare(
                """SELECT s.organization_id,s.event_id,0 AS assigned_count,0 AS completed_count
                   FROM submissions s
                   WHERE s.id=?1 AND s.event_id=?2 AND s.status='submitted'
                     AND NOT EXISTS (SELECT 1 FROM submission_decisions d
                                      WHERE d.submission_id=s.id)
                     AND NOT EXISTS (SELECT 1 FROM evaluation_assignments a
                                      JOIN evaluation_rounds r ON r.id=a.round_id
                                      WHERE a.submission_id=s.id AND a.status!='revoked'
                                        AND r.status!='draft')"""
            )
            .bind(submission_id, direct_event_id)
            .first()
        )
        if context is None:
            # That query folds four separate conditions into a single miss, and
            # answering all of them with 404 told an organizer their proposal
            # did not exist when in fact it had been pulled into a round and the
            # direct path had closed behind it. Re-probe to say which it is --
            # but only after the caller has proved they may manage submissions
            # here, so a 409 can never be used to test whether an id exists.
            blocked = row_mapping(
                await db.prepare(
                    """SELECT s.organization_id,s.event_id,
                          EXISTS(SELECT 1 FROM submission_decisions d
                                  WHERE d.submission_id=s.id) AS decided,
                          EXISTS(SELECT 1 FROM evaluation_assignments a
                                  JOIN evaluation_rounds r ON r.id=a.round_id
                                  WHERE a.submission_id=s.id AND a.status!='revoked'
                                    AND r.status!='draft') AS assigned
                       FROM submissions s
                       WHERE s.id=?1 AND s.event_id=?2 AND s.status='submitted'"""
                )
                .bind(submission_id, direct_event_id)
                .first()
            )
            if blocked is not None:
                await require_permission(
                    request,
                    Permission.SUBMISSION_MANAGE,
                    ResourceContext(str(blocked["organization_id"]), str(blocked["event_id"])),
                    mutation=True,
                )
                if int(blocked["decided"] or 0):
                    raise HTTPException(
                        status_code=409,
                        detail="This proposal already has a decision recorded.",
                    )
                if int(blocked["assigned"] or 0):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "This proposal is already in an evaluation round, so it can no longer "
                            "be rejected without review. Reject it from the round instead, where "
                            "the organizer override and its reason are recorded."
                        ),
                        headers={"X-Conflict-Type": "round"},
                    )
    else:
        context = row_mapping(
            await db.prepare(
                """SELECT r.organization_id, r.event_id, r.status AS round_status,
                  COUNT(a.id) AS assigned_count,
                  SUM(CASE WHEN e.state = 'final' THEN 1 ELSE 0 END) AS completed_count
           FROM evaluation_rounds r
           JOIN evaluation_assignments a ON a.round_id = r.id AND a.status != 'revoked'
           LEFT JOIN evaluations e ON e.assignment_id = a.id
           WHERE r.id = ?1 AND a.submission_id = ?2
           GROUP BY r.organization_id, r.event_id, r.status"""
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
    # A draft round has never been visible to a reviewer, so there is no review to
    # decide on. Recording a decision here would let the organizer bypass review entirely
    # while still appearing to have run a round.
    if context.get("round_status") == "draft":
        raise HTTPException(
            status_code=409,
            detail=(
                "This evaluation round is still a draft. Open it before recording "
                "decisions, or reject the proposal without review from the proposal inbox."
            ),
        )
    incomplete_reviews = int(context["completed_count"] or 0) < int(context["assigned_count"])
    if incomplete_reviews and not body.override_incomplete_reviews:
        raise HTTPException(status_code=409)
    speaker_query = SPEAKER_TASK_FLAGS_SQL.join(
        (
            """SELECT s.speaker_email,s.submitter_user_id,s.speaker_name,s.proposal_title,
                      e.name AS event_name,
                      ss.event_speaker_id,p.biography,
                      EXISTS(
                        SELECT 1 FROM user_headshots uh
                         WHERE uh.user_id=p.user_id
                      ) AS has_account_headshot,
                      EXISTS(
                        SELECT 1 FROM speaker_assets sa
                        JOIN speaker_asset_versions av ON av.asset_id=sa.id
                          AND av.is_current=1 AND av.scan_state='clean'
                         WHERE sa.organization_id=s.organization_id
                           AND sa.event_id=s.event_id
                           AND sa.event_speaker_id=ss.event_speaker_id
                           AND sa.kind='headshot'
                      ) AS has_event_headshot,
""",
            """
               FROM submissions s
               JOIN events e ON e.organization_id=s.organization_id AND e.id=s.event_id
               LEFT JOIN submission_speakers ss ON ss.organization_id=s.organization_id
                 AND ss.event_id=s.event_id AND ss.submission_id=s.id AND ss.role='primary'
               LEFT JOIN event_speakers es ON es.organization_id=s.organization_id
                 AND es.event_id=s.event_id AND es.id=ss.event_speaker_id
               LEFT JOIN people p ON p.organization_id=s.organization_id AND p.id=es.person_id
               WHERE s.id=?1 AND s.organization_id=?2 AND s.event_id=?3 LIMIT 1""",
        )
    )
    speaker = row_mapping(
        await db.prepare(speaker_query)
        .bind(submission_id, context["organization_id"], context["event_id"])
        .first()
    )
    if speaker is None:
        raise HTTPException(status_code=404)
    if body.send_email and not str(speaker["speaker_email"] or "").strip():
        raise HTTPException(status_code=409)
    key = _key(idempotency_key)
    route = (
        "POST /api/v1/admin/evaluation-rounds/{round_id}/submissions/{submission_id}/decision"
        if round_id is not None
        else (
            "POST /api/v1/admin/events/{event_id}/submissions/{submission_id}/accept"
            if body.decision == "accepted"
            else "POST /api/v1/admin/events/{event_id}/submissions/{submission_id}/reject"
        )
    )
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
            raise HTTPException(
                status_code=409,
                detail=(
                    "This decision request changed after an earlier attempt used the same "
                    "retry key. Reload the proposal to review the recorded outcome."
                ),
                headers={"X-Conflict-Type": "final-decision"},
            )
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
        raise HTTPException(
            status_code=409,
            detail=(
                "This proposal already has a final decision. Preserve it, or use the "
                "audited decision-correction workflow to change the effective result."
            ),
            headers={"X-Conflict-Type": "final-decision"},
        )
    decision_id = new_id()
    communication_id = new_id() if body.send_email else None
    version = 1
    now = utc_now_ms()
    additional_speakers = (
        await _additional_acceptance_speakers(
            db,
            organization_id=str(context["organization_id"]),
            event_id=str(context["event_id"]),
            submission_id=submission_id,
        )
        if body.decision == "accepted"
        else []
    )
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
            primary_onboarding = acceptance_requires_onboarding(speaker)
            batch.add_statement(
                db.prepare(
                    """UPDATE event_speakers SET selection_status='accepted',
                              status=CASE WHEN status='withdrawn' THEN status
                                WHEN ?5=1 THEN 'onboarding' ELSE 'complete' END,
                              accepted_at_ms=COALESCE(accepted_at_ms,?1),
                              last_activity_at_ms=?1,updated_at_ms=?1
                       WHERE organization_id=?2 AND event_id=?3 AND id=?4"""
                ).bind(
                    now,
                    context["organization_id"],
                    context["event_id"],
                    speaker["event_speaker_id"],
                    1 if primary_onboarding else 0,
                )
            )
            # Registration has already established the speaker's identity. Acceptance
            # therefore creates work only for information or assets that are actually
            # missing; a generic supporting-document request has no actionable meaning
            # and must be created later as an explicit, contextual request if needed.
            append_acceptance_speaker_tasks(
                batch,
                db,
                speaker,
                organization_id=str(context["organization_id"]),
                event_id=str(context["event_id"]),
                submission_id=submission_id,
                event_speaker_id=str(speaker["event_speaker_id"]),
                now=now,
            )
        for additional in additional_speakers:
            if str(additional["event_speaker_status"]) == "withdrawn":
                continue
            additional_id = str(additional["event_speaker_id"])
            additional_onboarding = acceptance_requires_onboarding(
                additional, include_slides=False
            )
            batch.add_statement(
                db.prepare(
                    """UPDATE event_speakers SET selection_status='accepted',
                              status=CASE WHEN ?5=1 THEN 'onboarding' ELSE 'complete' END,
                              accepted_at_ms=COALESCE(accepted_at_ms,?1),
                              last_activity_at_ms=?1,updated_at_ms=?1
                       WHERE organization_id=?2 AND event_id=?3 AND id=?4
                         AND status!='withdrawn'"""
                ).bind(
                    now,
                    context["organization_id"],
                    context["event_id"],
                    additional_id,
                    1 if additional_onboarding else 0,
                )
            )
            append_acceptance_speaker_tasks(
                batch,
                db,
                additional,
                organization_id=str(context["organization_id"]),
                event_id=str(context["event_id"]),
                submission_id=submission_id,
                event_speaker_id=additional_id,
                now=now,
                include_slides=False,
            )
    if body.decision == "rejected":
        # A permanent rejection immediately removes unfinished work from every
        # reviewer queue. Completed reviews remain immutable historical evidence.
        batch.add_statement(
            db.prepare(
                """UPDATE evaluation_assignments SET status='revoked',updated_at_ms=?1
                   WHERE organization_id=?2 AND event_id=?3 AND submission_id=?4
                     AND status='assigned'"""
            ).bind(now, context["organization_id"], context["event_id"], submission_id)
        )
        _append_rejected_participant_cleanup(
            batch,
            db,
            organization_id=str(context["organization_id"]),
            event_id=str(context["event_id"]),
            submission_id=submission_id,
            now=now,
        )
    if communication_id is not None:
        composition = _decision_composition(
            event_name=speaker["event_name"],
            speaker_name=speaker["speaker_name"],
            proposal_title=speaker["proposal_title"],
            decision=body.decision,
            correction=False,
            subject_override=body.speaker_subject,
            message_override=body.speaker_message,
        )
        batch.add_statement(
            db.prepare(
                """INSERT INTO communication_messages
                   (id,organization_id,event_id,recipient_user_id,recipient_email,subject,
                    html_body,deterministic_key,status,queued_at_ms,updated_at_ms,subject_source)
                   VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9,?10)"""
            ).bind(
                communication_id,
                context["organization_id"],
                context["event_id"],
                speaker["submitter_user_id"],
                speaker["speaker_email"],
                composition.subject,
                f'<p data-message-source="{composition.message_source}">'
                f"{_decision_message_html(composition.body)}</p>"
                f"<p><strong>{escape(str(speaker['proposal_title']))}</strong></p>",
                f"submission-decision:{decision_id}:v1",
                now,
                composition.subject_source,
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
                # _decision_view uses this immutable value for canonical success,
                # idempotent replay, and concurrent-reconciliation responses. Keep the
                # key stable unless the response projection is migrated with it.
                "review_override": incomplete_reviews,
                "direct_decision": round_id is None,
                "direct_rejection": round_id is None and body.decision == "rejected",
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
    try:
        await _execute(request, batch)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        # Two requests for the same final decision can both pass the preflight read.
        # The unique submission constraint chooses one winner. If that winner recorded
        # the same outcome, return its canonical representation instead of telling the
        # losing request that an operation which succeeded has failed.
        concurrent = row_mapping(
            await db.prepare(
                """SELECT id,decision FROM submission_decisions
                   WHERE organization_id=?1 AND event_id=?2 AND submission_id=?3"""
            )
            .bind(context["organization_id"], context["event_id"], submission_id)
            .first()
        )
        if concurrent is not None and str(concurrent["decision"]) == body.decision:
            record_degradation(request, "submission_decision_concurrent_reconciled")
            if body.decision == "accepted":
                try:
                    await reconcile_accepted_submission_speakers(
                        db,
                        organization_id=str(context["organization_id"]),
                        event_id=str(context["event_id"]),
                        submission_id=submission_id,
                        now=now,
                        execute_batch=lambda followup: _execute(request, followup),
                    )
                except HTTPException:
                    record_degradation(
                        request, "accepted_participant_reconciliation_failed"
                    )
            return await _decision_view(db, str(concurrent["id"]))
        if concurrent is None:
            raise
        raise HTTPException(
            status_code=409,
            detail=(
                "Another final decision was recorded while this request was being processed. "
                "Reload the proposal to review the recorded outcome."
            ),
            headers={"X-Conflict-Type": "final-decision"},
        ) from exc
    if body.decision == "accepted":
        try:
            await reconcile_accepted_submission_speakers(
                db,
                organization_id=str(context["organization_id"]),
                event_id=str(context["event_id"]),
                submission_id=submission_id,
                now=now,
                execute_batch=lambda followup: _execute(request, followup),
            )
        except HTTPException:
            record_degradation(request, "accepted_participant_reconciliation_failed")
    if communication_id is not None:
        await publish_committed_messages(request, [communication_id])
    # Return the same canonical projection used by idempotent and concurrent
    # replays. Write-only message content is deliberately not echoed.
    return await _decision_view(db, decision_id)


async def _round_view(db, round_id: str) -> EvaluationRoundView:
    row = row_mapping(
        await db.prepare(
            # Same rule as the round list: reviewers come from membership, assignments
            # exclude revoked pairs. Kept identical on purpose -- a round must not report
            # one set of numbers when it is created and another when it is listed.
            """SELECT r.id, r.event_id, r.name, r.status,
                  r.review_opens_at_ms, r.review_closes_at_ms,
                  (SELECT COUNT(*) FROM evaluation_assignments a
                    WHERE a.round_id=r.id AND a.status!='revoked') AS assignment_count,
                  (SELECT COUNT(*) FROM evaluation_round_evaluators e
                    WHERE e.round_id=r.id AND e.status='active') AS evaluator_count
           FROM evaluation_rounds r WHERE r.id = ?1"""
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
                      criterion_responses_json,state, version
           FROM evaluations WHERE id = ?1"""
        )
        .bind(evaluation_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    row["criterion_responses"] = json.loads(str(row.pop("criterion_responses_json")))
    return EvaluationView.model_validate(row)


async def _evaluation_view_by_assignment(db, assignment_id: str) -> EvaluationView:
    row = row_mapping(
        await db.prepare(
            """SELECT id, assignment_id, rating, recommendation, internal_comment,
                      criterion_responses_json,state, version
               FROM evaluations WHERE assignment_id = ?1"""
        )
        .bind(assignment_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    row["criterion_responses"] = json.loads(str(row.pop("criterion_responses_json")))
    return EvaluationView.model_validate(row)


async def _decision_view(db, decision_id: str) -> SubmissionDecisionView:
    row = row_mapping(
        await db.prepare(DECISION_VIEW_SQL)
        .bind(decision_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404)
    row["speaker_message"] = _decision_speaker_message(row.pop("speaker_message_html", ""))
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

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from sessionbuddy.api.app import app
from sessionbuddy.console.models import BrowserTelemetryPayload
from sessionbuddy.evaluation.models import (
    ConflictDeclaration,
    EvaluationCriterion,
    EvaluationRoundCreate,
    EvaluationSave,
    RoundSubmissionAdd,
    SubmissionDecisionCorrectionCreate,
    SubmissionDecisionCreate,
)
from sessionbuddy.evaluation.router import (
    EVALUATION_PAGE_LIMIT,
    _assignment_pairs,
    _build_round_rubric,
    _canonical_evaluation_fields,
    _evaluation_cursor,
    _evaluation_next_cursor,
    _full_round_criteria,
    _round_criteria_for_historical_scores,
    _weighted_mean,
    _weighted_review_score,
    remind_round_evaluator,
)
from sessionbuddy.speaker_operations.acceptance_tasks import acceptance_speaker_tasks


class _ReminderStatement:
    def __init__(self, database, sql: str) -> None:
        self.database = database
        self.sql = sql
        self.values: tuple[object, ...] = ()

    def bind(self, *values: object):
        self.values = values
        return self

    async def first(self, column: str | None = None):
        if "COUNT(a.id) AS assigned_count" in self.sql:
            return {
                "organization_id": "organization-1",
                "event_id": "event-1",
                "name": "Initial review",
                "email": "reviewer@example.test",
                "assigned_count": 2,
                "completed_count": 0,
            }
        if "FROM communication_messages" in self.sql:
            self.database.replay_lookup = self.values
            return "message-already-queued" if column == "id" else {"id": "message-already-queued"}
        raise AssertionError(f"unexpected read: {self.sql}")


class _ReminderCollisionDatabase:
    def __init__(self) -> None:
        self.replay_lookup: tuple[object, ...] | None = None

    def prepare(self, sql: str):
        return _ReminderStatement(self, sql)

    async def batch(self, _statements):
        raise RuntimeError("UNIQUE constraint failed: communication_messages")


async def test_repeated_reviewer_reminder_returns_the_existing_message(monkeypatch) -> None:
    """Exercise the replay branch rather than merely asserting its source exists."""
    database = _ReminderCollisionDatabase()
    request = SimpleNamespace(
        scope={"env": SimpleNamespace(DB=database)},
        state=SimpleNamespace(request_id="request-1"),
    )

    async def allow(*_args, **_kwargs):
        return SimpleNamespace(actor=SimpleNamespace(user_id="organizer-1"))

    async def should_not_publish(*_args, **_kwargs):
        raise AssertionError("an already queued message must not be republished")

    monkeypatch.setattr("sessionbuddy.evaluation.router.require_permission", allow)
    monkeypatch.setattr("sessionbuddy.evaluation.router.utc_now_ms", lambda: 7_200_001)
    monkeypatch.setattr(
        "sessionbuddy.evaluation.router.publish_committed_messages", should_not_publish
    )

    result = await remind_round_evaluator("round-1", "reviewer-1", request)

    assert result.message_id == "message-already-queued"
    assert database.replay_lookup == (
        "organization-1",
        "event-1",
        "evaluation-reminder:round-1:reviewer-1:2",
    )


def test_acceptance_creates_only_missing_speaker_onboarding_tasks() -> None:
    missing = acceptance_speaker_tasks(
        {
            "biography": "",
            "has_account_headshot": 0,
            "has_event_headshot": 0,
            "has_profile_task": 0,
            "has_headshot_task": 0,
        }
    )
    assert [task[0] for task in missing] == ["profile", "headshot", "slides"]
    assert missing[0][1] == "Add your speaker biography"
    assert all(task[0] != "supporting_document" for task in missing)

    complete_registration = acceptance_speaker_tasks(
        {
            "biography": "Already supplied",
            "has_account_headshot": 1,
            "has_event_headshot": 0,
            "has_profile_task": 0,
            "has_headshot_task": 0,
        }
    )
    assert [task[0] for task in complete_registration] == ["slides"]


def test_acceptance_does_not_duplicate_existing_profile_or_headshot_tasks() -> None:
    tasks = acceptance_speaker_tasks(
        {
            "biography": "",
            "has_account_headshot": 0,
            "has_event_headshot": 0,
            "has_profile_task": 1,
            "has_headshot_task": 1,
        }
    )
    assert [task[0] for task in tasks] == ["slides"]

    participant_tasks = acceptance_speaker_tasks(
        {
            "biography": "",
            "has_account_headshot": 0,
            "has_event_headshot": 0,
            "has_profile_task": 0,
            "has_headshot_task": 0,
        },
        include_slides=False,
    )
    assert [task[0] for task in participant_tasks] == ["profile", "headshot"]


def test_evaluation_contracts_are_strict_and_bounded() -> None:
    round_create = EvaluationRoundCreate(
        name="Initial review",
        rating_min=1,
        rating_max=5,
        recommendations=["accept", "reject"],
        submission_ids=["a" * 36],
        evaluator_user_ids=["b" * 36, "c" * 36],
        assignment_strategy="balanced",
    )
    assert round_create.rating_max == 5
    typed = EvaluationRoundCreate(
        name="Typed review", rating_min=1, rating_max=5,
        recommendations=["accept", "reject"], submission_ids=["a" * 36],
        evaluator_user_ids=["b" * 36], assignment_strategy="all",
        criteria=[
            EvaluationCriterion(key="quality", label="Quality", weight=100),
            EvaluationCriterion(
                key="track", label="Best track", response_type="select",
                weight=None, options=["Platform", "AI"],
            ),
            EvaluationCriterion(
                key="notes", label="Evidence", response_type="text",
                weight=None, required=False,
            ),
        ],
    )
    assert [criterion.response_type for criterion in typed.criteria] == ["score", "select", "text"]
    with pytest.raises(ValidationError):
        EvaluationRoundCreate(
            name="Invalid",
            rating_min=5,
            rating_max=5,
            recommendations=["accept", "reject"],
            submission_ids=["a" * 36],
            evaluator_user_ids=["b" * 36],
            assignment_strategy="all",
        )
    with pytest.raises(ValidationError):
        EvaluationSave(
            rating=4,
            recommendation="accept",
            internal_comment="safe",
            state="final",
            unexpected="private",
        )
    assert SubmissionDecisionCreate(decision="accepted").internal_reason == ""
    with pytest.raises(ValidationError):
        SubmissionDecisionCreate(decision="maybe")
    assert ConflictDeclaration(
        conflict_type="same_company", explanation="Current colleague"
    ).conflict_type == "same_company"
    with pytest.raises(ValidationError):
        ConflictDeclaration(conflict_type="other", explanation="")


@pytest.mark.parametrize("response_type", ["text", "select"])
def test_non_score_null_and_omitted_weights_share_serialized_roundtrip(response_type) -> None:
    criterion = {"key": "feedback", "label": "Feedback", "response_type": response_type}
    if response_type == "select":
        criterion["options"] = ["Accept", "Reject"]
    rubrics = []
    for optional_weight in ({}, {"weight": None}):
        body = EvaluationRoundCreate(
            name="Weight normalization",
            rating_min=1,
            rating_max=5,
            recommendations=["Accept", "Reject"],
            assignment_strategy="balanced",
            status="draft",
            criteria=[
                {"key": "quality", "label": "Quality", "weight": 100},
                {**criterion, **optional_weight},
            ],
        )
        rubric = _build_round_rubric(body)
        assert "weight" not in rubric["criteria"][1]
        assert rubric["criteria"][0]["weight"] == 100
        assert _full_round_criteria(json.dumps(rubric)) == rubric["criteria"]
        # Historical explicit JSON null normalizes to the same internal reader output.
        explicit_null = json.loads(json.dumps(rubric))
        explicit_null["criteria"][1]["weight"] = None
        assert _full_round_criteria(json.dumps(explicit_null)) == rubric["criteria"]
        rubrics.append(rubric)
    assert rubrics[0] == rubrics[1]


@pytest.mark.parametrize("response_type", ["text", "select"])
@pytest.mark.parametrize("weight", [0, 1, 100])
def test_non_score_numeric_weights_are_rejected(response_type, weight) -> None:
    with pytest.raises(ValidationError):
        EvaluationCriterion(
            key="feedback", label="Feedback", response_type=response_type, weight=weight,
            options=["Accept", "Reject"] if response_type == "select" else [],
        )


@pytest.mark.parametrize("optional_weight", [{}, {"weight": None}, {"weight": 0}])
def test_score_criterion_requires_positive_weight(optional_weight) -> None:
    with pytest.raises(ValidationError):
        EvaluationCriterion(key="quality", label="Quality", **optional_weight)


def test_criterion_purposes_are_typed_unique_and_drive_legacy_compatibility() -> None:
    recommendation = EvaluationCriterion(
        key="recommendation",
        label="Recommendation",
        response_type="select",
        options=["Accept", "Maybe", "Reject"],
        purpose="recommendation",
    )
    comment = EvaluationCriterion(
        key="comments",
        label="Comments",
        response_type="text",
        purpose="comment",
    )
    body = EvaluationRoundCreate(
        name="Purpose review",
        rating_min=1,
        rating_max=5,
        recommendations=["legacy_accept", "legacy_reject"],
        criteria=[
            EvaluationCriterion(key="quality", label="Quality", weight=100),
            recommendation,
            comment,
        ],
        assignment_strategy="balanced",
        status="draft",
    )
    rubric = _build_round_rubric(body)
    assert rubric["recommendation"]["choices"] == ["Accept", "Maybe", "Reject"]
    assert rubric["internal_comment"]["required"] is True
    assert rubric["criteria"][1]["purpose"] == "recommendation"

    saved = EvaluationSave(
        state="final",
        rating=4,
        criterion_responses={
            "quality": 4,
            "recommendation": "Maybe",
            "comments": "Useful evidence.",
        },
    )
    assert _canonical_evaluation_fields(rubric, saved) == ("Maybe", "Useful evidence.")

    with pytest.raises(ValidationError, match="criterion purposes must be unique"):
        EvaluationRoundCreate(
            name="Duplicate purpose",
            rating_min=1,
            rating_max=5,
            recommendations=["Accept", "Reject"],
            criteria=[
                EvaluationCriterion(key="quality", label="Quality", weight=100),
                recommendation,
                recommendation.model_copy(update={"key": "second_recommendation"}),
            ],
            assignment_strategy="balanced",
            status="draft",
        )

    with pytest.raises(ValidationError, match="recommendation-purpose"):
        EvaluationCriterion(
            key="wrong",
            label="Wrong",
            response_type="text",
            purpose="recommendation",
        )
    with pytest.raises(ValidationError, match="must be required"):
        EvaluationCriterion(
            key="optional_recommendation",
            label="Optional recommendation",
            response_type="select",
            required=False,
            options=["Accept", "Reject"],
            purpose="recommendation",
        )

    assert _full_round_criteria(
        '{"criteria":['
        '{"key":"valid","label":"Valid","weight":100},'
        '{"key":"malformed","label":"Malformed","weight":null}'
        "]}"
    ) == [
        {
            "key": "valid",
            "label": "Valid",
            "response_type": "score",
            "required": True,
            "weight": 100,
            "options": [],
        }
    ]
    assert _round_criteria_for_historical_scores(
        '{"criteria":['
        '{"key":"historical","label":"Historical","weight":150}'
        "]}"
    )[0]["weight"] == 150

    optional_comment = EvaluationCriterion(
        key="optional_comment",
        label="Optional comment",
        response_type="text",
        required=False,
        purpose="comment",
    )
    optional_body = body.model_copy(
        update={"criteria": [body.criteria[0], recommendation, optional_comment]}
    )
    optional_rubric = _build_round_rubric(optional_body)
    assert optional_rubric["internal_comment"]["required"] is False
    assert _canonical_evaluation_fields(
        optional_rubric,
        EvaluationSave(
            state="final",
            rating=4,
            criterion_responses={"quality": 4, "recommendation": "Accept"},
        ),
    ) == ("Accept", "")


def test_assignment_strategies_are_deterministic() -> None:
    assert _assignment_pairs(["s1", "s2"], [], "balanced") == []
    assert _assignment_pairs(["s1", "s2"], [], "all") == []
    assert _assignment_pairs(["s1", "s2", "s3"], ["e1", "e2"], "balanced") == [
        ("s1", "e1"),
        ("s2", "e2"),
        ("s3", "e1"),
    ]
    assert _assignment_pairs(["s1", "s2"], ["e1", "e2"], "all") == [
        ("s1", "e1"),
        ("s1", "e2"),
        ("s2", "e1"),
        ("s2", "e2"),
    ]
    assert RoundSubmissionAdd(submission_ids=["a" * 36]).submission_ids == ["a" * 36]


def test_source_wiring_round_workspaces_support_late_submissions_and_audited_force_close() -> None:
    root = Path(__file__).parents[2]
    submissions = (root / "src/sessionbuddy/static/admin_submissions.js").read_text()
    reviews = (root / "frontend/src/main.tsx").read_text()
    assert 'add.id = "add-selected-to-round"' in submissions
    assert '"Select proposals to add"' in submissions
    assert "Add ${count} selected proposal" in submissions
    assert "/submissions`" in submissions
    assert "Organizer closed the round before every review was final." in reviews


def test_source_wiring_evaluation_rounds_are_owned_by_events() -> None:
    root = Path(__file__).parents[2]
    router = (root / "src/sessionbuddy/evaluation/router.py").read_text()
    models = (root / "src/sessionbuddy/evaluation/models.py").read_text()
    reviews = (root / "frontend/src/main.tsx").read_text()

    for route in (
        '"/api/v1/admin/events/{event_id}/evaluation-rounds"',
        '"/api/v1/admin/events/{event_id}/evaluation-rounds/current"',
        '"/api/v1/admin/events/{event_id}/evaluators"',
    ):
        assert route in router
    assert "program_id" not in router
    assert "program_id" not in models
    assert "results.program_id" not in reviews
    assert "/admin/events/${encodeURIComponent(results.event_id)}/submissions" in reviews


def test_source_wiring_reviewers_use_exact_assignments_not_hidden_event_memberships() -> None:
    root = Path(__file__).parents[2]
    router = (root / "src/sessionbuddy/evaluation/router.py").read_text()
    access = (root / "src/sessionbuddy/platform/auth/access.py").read_text()
    baseline = (root / "migrations_baseline/0001_baseline.sql").read_text()

    assert "ur.role='reviewer'" in router
    assert "a.evaluator_user_id=NEW.user_id" in baseline
    event_membership = baseline.split("CREATE TABLE event_memberships", 1)[1].split(
        "CREATE TABLE", 1
    )[0]
    assert "'evaluator'" not in event_membership
    assert 'invitation["role"] != "evaluator"' in access


def test_source_wiring_withdrawn_submissions_cannot_enter_review_assignments() -> None:
    router = (
        Path(__file__).parents[2] / "src/sessionbuddy/evaluation/router.py"
    ).read_text()
    create_round = router.split("async def create_evaluation_round(", 1)[1].split(
        "async def", 1
    )[0]
    add_submissions = router.split("async def add_round_submissions(", 1)[1].split(
        "async def", 1
    )[0]

    assert "AND status='submitted'" in create_round
    assert "AND status='submitted'" in add_submissions


def test_source_wiring_decision_readiness_ignores_revoked_conflict_assignments() -> None:
    router = (
        Path(__file__).parents[2] / "src/sessionbuddy/evaluation/router.py"
    ).read_text()
    decision = router.split("async def record_submission_decision(", 1)[1].split(
        "async def", 1
    )[0]

    assert (
        "JOIN evaluation_assignments a ON a.round_id = r.id "
        "AND a.status != 'revoked'"
    ) in decision
    assert "JOIN speaker_asset_versions av ON av.asset_id=sa.id" in decision
    assert "JOIN asset_versions av" not in decision
    assert "AND status='assigned'" in decision


def test_source_wiring_direct_rejection_is_unreviewed_audited_and_round_independent() -> None:
    root = Path(__file__).parents[2]
    router = (root / "src/sessionbuddy/evaluation/router.py").read_text()
    baseline = (root / "migrations_baseline/0001_baseline.sql").read_text()
    direct = router.split("async def reject_unreviewed_submission(", 1)[1].split(
        "async def", 1
    )[0]

    assert "body.decision != \"rejected\"" in direct
    assert "if not body.internal_reason" in direct
    assert "direct_event_id=event_id" in direct
    decisions = baseline.split("CREATE TABLE submission_decisions", 1)[1].split(
        "CREATE TABLE", 1
    )[0]
    assert "round_id TEXT," in decisions
    assert "UNIQUE (submission_id)" in decisions


def test_acceptance_headshot_readiness_query_uses_the_canonical_asset_table() -> None:
    connection = sqlite3.connect(":memory:")
    baseline = Path(__file__).parents[2] / "migrations_baseline/0001_baseline.sql"
    connection.executescript(baseline.read_text())

    result = connection.execute(
        """SELECT EXISTS(
               SELECT 1 FROM speaker_assets sa
               JOIN speaker_asset_versions av ON av.asset_id=sa.id
                 AND av.is_current=1 AND av.scan_state='clean'
               WHERE sa.organization_id=?1 AND sa.event_id=?2
                 AND sa.event_speaker_id=?3 AND sa.kind='headshot'
             )""",
        ("organization", "event", "speaker"),
    ).fetchone()

    assert result == (0,)


def test_aggregate_is_weighted_across_individual_final_evaluations() -> None:
    assert _weighted_mean([(4.0, 1), (2.0, 3)]) == 2.5
    assert _weighted_mean([]) is None


@pytest.mark.parametrize(
    ("criteria", "responses", "expected"),
    [
        (
            [{"key": "originality", "weight": 67}, {"key": "relevance", "weight": 33}],
            '{"originality":4,"relevance":2}',
            3.34,
        ),
        (
            [{"key": "originality", "weight": 67}, {"key": "relevance", "weight": 33}],
            '{"originality":5,"relevance":1}',
            3.68,
        ),
        (
            [{"key": "originality", "weight": 50}, {"key": "relevance", "weight": 50}],
            '{"originality":3,"relevance":2}',
            2.5,
        ),
    ],
)
def test_final_review_score_keeps_weighted_precision(
    criteria: list[dict], responses: str, expected: float
) -> None:
    assert _weighted_review_score(criteria, responses, stored_rating=3) == expected


def test_evaluation_lists_use_scoped_signed_pagination() -> None:
    request = SimpleNamespace(
        scope={"env": SimpleNamespace(CSRF_HMAC_KEY="c" * 32)}
    )
    cursor = _evaluation_next_cursor(
        request,
        kind="round-results",
        scope_id="round-1",
        timestamp=1234,
        row_id="submission-1",
    )
    assert _evaluation_cursor(
        request, cursor, kind="round-results", scope_id="round-1"
    ) == (1234, "submission-1")
    with pytest.raises(HTTPException, match="400"):
        _evaluation_cursor(
            request, cursor, kind="round-results", scope_id="another-round"
        )


def test_evaluation_workspaces_expose_pages_beyond_the_first_100_records() -> None:
    root = Path(__file__).parents[2]
    router = (root / "src/sessionbuddy/evaluation/router.py").read_text()
    models = (root / "src/sessionbuddy/evaluation/models.py").read_text()
    reviews = (root / "frontend/src/main.tsx").read_text()

    assignments = router.split("async def list_my_assignments", 1)[1].split(
        "@evaluation_router.put", 1
    )[0]
    results = router.split("async def get_round_results", 1)[1].split(
        "@evaluation_router.get", 1
    )[0]
    submission_results = results.split("evaluator_rows =", 1)[0]
    assert EVALUATION_PAGE_LIMIT == 50
    assert "LIMIT 100" not in assignments
    assert "LIMIT 100" not in submission_results
    assert "next_cursor" in assignments and "next_cursor" in results
    assert "next_cursor" in models and "submission_count" in models
    assert "Load more reviews" in reviews
    assert "Load more results" in reviews


async def test_review_workspace_is_local_only_and_bundled() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        page = await client.get("/reviews")
        javascript = await client.get("/app/assets/reviews.js")
        css = await client.get("/app/assets/reviews.css")
        assert javascript.headers["cache-control"] == "no-store"
        assert css.headers["cache-control"] == "no-store"
        admin = await client.get(
            "/admin/evaluation-rounds/11111111-1111-4111-8111-111111111111"
        )

    assert page.status_code == javascript.status_code == css.status_code == admin.status_code == 200
    assert "SessionBuddy · Reviews" in page.text
    assert "__sessionbuddyTelemetryDraft" in javascript.text
    assert "/reviews" in javascript.text
    assert "/admin/evaluation-rounds/{round_id}" in javascript.text

    telemetry = BrowserTelemetryPayload(
        schema_version=1,
        page_template="/reviews",
        navigation_type="navigate",
        device_class="desktop",
        sampled=False,
    )
    assert telemetry.page_template == "/reviews"


async def test_review_workspace_deploys_but_api_requires_identity() -> None:
    environment = SimpleNamespace(
        APP_ENV="development",
        DB=object(),
        SESSION_HMAC_KEY="s" * 32,
        CSRF_HMAC_KEY="c" * 32,
    )

    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="http://test"
    ) as client:
        page = await client.get("/reviews")
        assignments = await client.get("/api/v1/evaluator/assignments")
        admin = await client.get(
            "/admin/evaluation-rounds/11111111-1111-4111-8111-111111111111"
        )

    assert page.status_code == 200
    assert admin.status_code == 200
    assert assignments.status_code == 401


def test_blind_rounds_hide_unmarked_answers_fail_closed() -> None:
    from sessionbuddy.evaluation.router import _reviewer_answers

    answers = (
        '{"company": "Acme Corp", "bio": "I am Jane",'
        ' "topic_area": "MLOps", "q_unknown": "text"}'
    )
    schema = (
        '{"fields": ['
        '{"key": "company", "type": "text", "label": "Company"},'
        '{"key": "bio", "type": "textarea", "label": "Bio"},'
        '{"key": "topic_area", "type": "select", "label": "Topic area", "blind_visible": true}'
        "]}"
    )

    visible, hidden = _reviewer_answers(answers, schema, blind_review=True)
    assert [(view.label, view.value) for view in visible] == [("Topic area", "MLOps")]
    # company, bio, and the schemaless q_unknown are all withheld.
    assert hidden == 3

    visible, hidden = _reviewer_answers(answers, schema, blind_review=False)
    assert {view.label for view in visible} == {"Company", "Bio", "Topic area", "q unknown"}
    assert hidden == 0


def test_core_identity_fields_never_reach_reviewers() -> None:
    from sessionbuddy.evaluation.router import _reviewer_answers

    answers = '{"speaker_name": "Jane", "speaker_email": "j@x.io", "topic_area": "MLOps"}'
    schema = (
        '{"fields": ['
        '{"key": "speaker_name", "type": "text", "label": "Speaker name", "blind_visible": true},'
        '{"key": "speaker_email", "type": "email", "label": "Email", "blind_visible": true},'
        '{"key": "topic_area", "type": "text", "label": "Topic area", "blind_visible": true}'
        "]}"
    )
    for blind in (True, False):
        visible, _ = _reviewer_answers(answers, schema, blind_review=blind)
        labels = {view.label for view in visible}
        assert "Speaker name" not in labels and "Email" not in labels
        assert "Topic area" in labels


def test_draft_evaluations_permit_partial_input() -> None:
    from pydantic import ValidationError

    from sessionbuddy.evaluation.models import EvaluationSave

    draft = EvaluationSave(state="draft")
    assert draft.rating is None and draft.recommendation is None

    partial = EvaluationSave(state="draft", criterion_responses={"depth": 4, "notes": "Clear"})
    assert partial.criterion_responses == {"depth": 4, "notes": "Clear"}

    with pytest.raises(ValidationError):
        EvaluationSave(state="final")
    # Recommendation completeness depends on the round: a purpose-designated criterion
    # may provide and mirror it, so the request model cannot decide this in isolation.
    assert EvaluationSave(state="final", rating=5).recommendation is None
    with pytest.raises(ValidationError):
        EvaluationSave(
            state="final", rating=5, recommendation="accept",
            criterion_responses={"comments": "   "},
        )
    complete = EvaluationSave(state="final", rating=5, recommendation="accept")
    assert complete.rating == 5


@pytest.mark.parametrize(
    "value", ["line one\nline two", "line one\rline two", "a\u2028b", "a\u2029b"]
)
def test_decision_email_subjects_are_single_line(value: str) -> None:
    with pytest.raises(ValidationError, match="single line"):
        SubmissionDecisionCreate(decision="accepted", speaker_subject=value)
    with pytest.raises(ValidationError, match="single line"):
        SubmissionDecisionCorrectionCreate(
            corrected_decision="rejected", reason="Correction", speaker_subject=value
        )


def test_whitespace_only_decision_subject_uses_the_default_sentinel() -> None:
    decision = SubmissionDecisionCreate(decision="accepted", speaker_subject="   ")
    assert decision.speaker_subject == ""

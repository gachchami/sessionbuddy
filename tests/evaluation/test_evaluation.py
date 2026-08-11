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
    EvaluationRoundCreate,
    EvaluationSave,
    RoundSubmissionAdd,
    SubmissionDecisionCreate,
)
from sessionbuddy.evaluation.router import (
    EVALUATION_PAGE_LIMIT,
    _assignment_pairs,
    _evaluation_cursor,
    _evaluation_next_cursor,
    _weighted_mean,
)


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


def test_assignment_strategies_are_deterministic() -> None:
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


def test_round_workspaces_support_late_submissions_and_audited_force_close() -> None:
    root = Path(__file__).parents[2]
    submissions = (root / "src/sessionbuddy/static/admin_submissions.js").read_text()
    reviews = (root / "frontend/src/main.tsx").read_text()
    assert "Add selected proposals to open round" in submissions
    assert "/submissions`" in submissions
    assert "Organizer closed the round before every review was final." in reviews


def test_evaluation_rounds_are_owned_by_events() -> None:
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


def test_reviewers_use_exact_assignments_not_hidden_event_memberships() -> None:
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


def test_withdrawn_submissions_cannot_enter_review_assignments() -> None:
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


def test_decision_readiness_ignores_revoked_conflict_assignments() -> None:
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


def test_aggregate_is_weighted_across_individual_final_evaluations() -> None:
    assert _weighted_mean([(4.0, 1), (2.0, 3)]) == 2.5
    assert _weighted_mean([]) is None


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

    partial = EvaluationSave(state="draft", criterion_scores={"depth": 4})
    assert partial.criterion_scores == {"depth": 4}

    with pytest.raises(ValidationError):
        EvaluationSave(state="final")
    with pytest.raises(ValidationError):
        EvaluationSave(state="final", rating=5)
    complete = EvaluationSave(state="final", rating=5, recommendation="accept")
    assert complete.rating == 5

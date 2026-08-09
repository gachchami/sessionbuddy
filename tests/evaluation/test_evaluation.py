from pathlib import Path
from types import SimpleNamespace

import pytest
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
from sessionbuddy.evaluation.router import _assignment_pairs, _weighted_mean


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
    assert "Add selected submissions to open round" in submissions
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


def test_aggregate_is_weighted_across_individual_final_evaluations() -> None:
    assert _weighted_mean([(4.0, 1), (2.0, 3)]) == 2.5
    assert _weighted_mean([]) is None


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

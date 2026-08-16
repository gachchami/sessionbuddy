import json
from types import SimpleNamespace

from fastapi import HTTPException

from sessionbuddy.evaluation import router
from sessionbuddy.evaluation.models import (
    EvaluationCriterion,
    EvaluationDetail,
    EvaluationRoundResults,
    SubmissionEvaluationResult,
)


class _MeasuredStatement:
    def __init__(self, sql: str) -> None:
        self.sql = sql

    def bind(self, *_values):
        return self

    async def first(self):
        if "FROM evaluation_rounds r" in self.sql:
            return {
                "id": "round-id",
                "organization_id": "org-id",
                "event_id": "event-id",
                "name": "Large review",
                "status": "closed",
                "rubric_json": json.dumps(
                    {"criteria": [{"key": "score", "label": "Score", "weight": 100}]}
                ),
                "event_name": "Measured Event",
            }
        if "COUNT(a.id) AS assigned_count" in self.sql:
            return {
                "assigned_count": 50,
                "completed_count": 50,
                "average_rating": 4,
                "submission_count": 50,
            }
        raise AssertionError(f"unexpected first query: {self.sql}")

    async def all(self):
        if "FROM evaluation_round_submissions m" in self.sql:
            return {
                "results": [
                    {
                        "submission_id": f"submission-{index:02d}",
                        "speaker_name": f"Speaker {index}",
                        "proposal_title": f"Proposal {index}",
                        "submitted_at_ms": 10_000 - index,
                        "assigned_count": 1,
                        "needs_reassignment": 0,
                        "completed_count": 1,
                        "average_rating": 4,
                        "decision": None,
                        "internal_reason": "",
                        "correction_reason": "",
                        "decision_round_id": None,
                    }
                    for index in range(50)
                ]
            }
        if "SELECT DISTINCT u.id AS user_id" in self.sql:
            return {"results": [{"user_id": "reviewer-id", "display_name": "Reviewer"}]}
        if "FROM evaluation_assignments a JOIN users u" in self.sql:
            return {
                "results": [
                    {
                        "submission_id": f"submission-{index:02d}",
                        "evaluator_user_id": "reviewer-id",
                        "evaluator_name": "Reviewer",
                        "state": "final",
                        "rating": 4,
                        "recommendation": "accept",
                        "criterion_responses_json": '{"score":4}',
                        "internal_comment": "",
                    }
                    for index in range(50)
                ]
            }
        if "FROM evaluation_round_evaluators m" in self.sql:
            return {
                "results": [
                    {
                        "evaluator_user_id": "reviewer-id",
                        "display_name": "Reviewer",
                        "assigned_count": 50,
                        "completed_count": 50,
                        "conflict_count": 0,
                    }
                ]
            }
        if "FROM evaluation_conflicts c" in self.sql:
            return {"results": []}
        if "FROM evaluations e" in self.sql:
            return {
                "results": [
                    {"rating": 4, "criterion_responses_json": '{"score":4}'}
                    for _ in range(50)
                ]
            }
        raise AssertionError(f"unexpected all query: {self.sql}")


class _MeasuredDatabase:
    def __init__(self) -> None:
        self.prepare_count = 0

    def prepare(self, sql: str) -> _MeasuredStatement:
        self.prepare_count += 1
        return _MeasuredStatement(sql)


async def test_full_round_results_page_measures_eight_d1_calls(monkeypatch) -> None:
    database = _MeasuredDatabase()
    request = SimpleNamespace(
        scope={"env": SimpleNamespace(DB=database)},
        state=SimpleNamespace(request_id="measure-round-results", timings={}),
    )

    async def allow(*_args, **_kwargs):
        return SimpleNamespace(actor=SimpleNamespace(user_id="organizer-id"))

    monkeypatch.setattr(router, "require_permission", allow)

    results = await router.get_round_results("round-id", request)

    assert len(results.submissions) == 50
    assert all(submission.reviews for submission in results.submissions)
    assert database.prepare_count == router.ROUND_RESULTS_D1_CALLS_PER_FULL_PAGE == 8
    assert (
        router.EXPORT_MAX_PAGES * database.prepare_count
        <= router.EXPORT_D1_SUBREQUEST_BUDGET
    )


async def test_evaluation_csv_quotes_formula_prefixes_after_whitespace(monkeypatch) -> None:
    results = EvaluationRoundResults(
        round_id="round-id",
        event_id="event-id",
        event_name="Example Event",
        round_name="Review",
        status="closed",
        assigned_count=1,
        completed_count=1,
        average_rating=8.0,
        submissions=[
            SubmissionEvaluationResult(
                submission_id="submission-id",
                speaker_name="\r+SUM(1,1)",
                proposal_title="\t=HYPERLINK(\"https://example.invalid\")",
                assigned_count=1,
                completed_count=1,
                average_rating=8.0,
                decision="accepted",
            )
        ],
        submission_count=1,
        evaluators=[],
        conflicts=[],
    )

    async def fake_results(_round_id, _request):
        return results

    monkeypatch.setattr(router, "get_round_results", fake_results)
    response = await router.export_round_results("round-id", object())
    body = response.body.decode()

    assert "'\t=HYPERLINK" in body
    assert "'\r+SUM" in body
    assert response.headers["x-export-row-count"] == "1"
    assert "example-event-review-results-round.csv" in response.headers["content-disposition"]


async def test_detailed_review_csv_keeps_rubric_order_and_all_response_types(
    monkeypatch,
) -> None:
    results = EvaluationRoundResults(
        round_id="round-id",
        event_id="event-id",
        event_name="Example Event",
        round_name="Review",
        status="closed",
        assigned_count=1,
        completed_count=1,
        average_rating=3.34,
        criteria=[
            EvaluationCriterion(key="originality", label="Originality", weight=67),
            EvaluationCriterion(
                key="recommendation",
                label="Recommendation",
                response_type="select",
                options=["Accept", "Maybe", "Reject"],
            ),
            EvaluationCriterion(
                key="comments", label="Recommendation", response_type="text"
            ),
        ],
        submissions=[
            SubmissionEvaluationResult(
                submission_id="submission-id",
                speaker_name="Speaker",
                proposal_title="Proposal",
                assigned_count=1,
                completed_count=1,
                average_rating=3.34,
                decision=None,
                reviews=[
                    EvaluationDetail(
                        evaluator_user_id="reviewer-id",
                        evaluator_name="Reviewer",
                        state="final",
                        rating=3,
                        weighted_score=3.34,
                        recommendation="accept",
                        criterion_responses={
                            "comments": "Detailed review",
                            "recommendation": "Accept",
                            "originality": 4,
                        },
                    )
                ],
            )
        ],
        submission_count=1,
        evaluators=[],
        conflicts=[],
    )

    async def fake_results(_round_id, _request):
        return results

    monkeypatch.setattr(router, "get_round_results", fake_results)
    response = await router.export_round_reviews("round-id", object())
    lines = response.body.decode().splitlines()
    assert lines[0].endswith(
        "legacy_internal_comment,Originality [originality],"
        "Recommendation [recommendation],Recommendation [comments]"
    )
    assert lines[1].endswith(",4,Accept,Detailed review")
    assert response.headers["x-export-row-count"] == "1"
    assert (
        "example-event-review-review-details-round.csv"
        in response.headers["content-disposition"]
    )


async def test_export_fails_closed_when_the_page_cap_is_reached() -> None:
    results = EvaluationRoundResults(
        round_id="round-id",
        event_id="event-id",
        event_name="Example Event",
        round_name="Review",
        status="closed",
        assigned_count=0,
        completed_count=0,
        average_rating=None,
        submissions=[],
        submission_count=51,
        next_cursor="more-results",
        evaluators=[],
        conflicts=[],
    )

    try:
        await router._collect_export_submissions(
            "round-id", object(), results, max_pages=1
        )
    except HTTPException as error:
        assert error.status_code == 409
        assert error.headers == {"X-Conflict-Type": "export-limit"}
        assert "50-proposal single-file export limit" in str(error.detail)
    else:
        raise AssertionError("a capped export must not return a partial file")


def test_export_filename_reserves_type_and_identity_suffixes() -> None:
    results = EvaluationRoundResults(
        round_id="f5358ef8-5682-4e66-9020-c30000000000",
        event_id="event-id",
        event_name="東 Conference " * 30,
        round_name="..Initial / Review\r\nInjected",
        status="closed",
        assigned_count=0,
        completed_count=0,
        average_rating=None,
        submissions=[],
        submission_count=0,
        evaluators=[],
        conflicts=[],
    )

    results_name, results_suffix = router._evaluation_export_filename_parts(
        results, review_details=False
    )
    reviews_name, reviews_suffix = router._evaluation_export_filename_parts(
        results, review_details=True
    )
    results_header = router.attachment_header(
        results_name,
        ascii_suffix=results_suffix,
    )
    reviews_header = router.attachment_header(
        reviews_name,
        ascii_suffix=reviews_suffix,
    )

    assert len(results_name.encode()) <= 180
    assert results_name.endswith("-results-f5358ef8.csv")
    assert reviews_name.endswith("-review-details-f5358ef8.csv")
    assert "\r" not in reviews_name and "\n" not in reviews_name
    assert 'filename="' in results_header
    assert "-results-f5358ef8.csv\"" in results_header
    assert "-review-details-f5358ef8.csv\"" in reviews_header
    assert results_header != reviews_header


def test_export_header_does_not_duplicate_suffix_for_non_ascii_names() -> None:
    results = EvaluationRoundResults(
        round_id="f5358ef8-5682-4e66-9020-c30000000000",
        event_id="event-id",
        event_name="東京",
        round_name="レビュー",
        status="closed",
        assigned_count=0,
        completed_count=0,
        average_rating=None,
        submissions=[],
        submission_count=0,
        evaluators=[],
        conflicts=[],
    )
    filename, suffix = router._evaluation_export_filename_parts(
        results, review_details=False
    )

    header = router.attachment_header(filename, ascii_suffix=suffix)

    assert 'filename="results-f5358ef8.csv"' in header
    assert "results-f5358ef8.csv-results" not in header


async def test_export_walks_every_page_before_returning(monkeypatch) -> None:
    first = EvaluationRoundResults(
        round_id="round-id",
        event_id="event-id",
        event_name="Example Event",
        round_name="Review",
        status="closed",
        assigned_count=0,
        completed_count=0,
        average_rating=None,
        submissions=[],
        submission_count=1,
        next_cursor="page-two",
        evaluators=[],
        conflicts=[],
    )
    second = first.model_copy(
        update={
            "submissions": [
                SubmissionEvaluationResult(
                    submission_id="submission-two",
                    speaker_name="Speaker",
                    proposal_title="Proposal",
                    assigned_count=0,
                    completed_count=0,
                    average_rating=None,
                    decision=None,
                )
            ],
            "next_cursor": None,
        }
    )
    cursors: list[str | None] = []

    async def fake_results(_round_id, _request, *, cursor=None):
        cursors.append(cursor)
        return second

    monkeypatch.setattr(router, "get_round_results", fake_results)
    exported = await router._collect_export_submissions("round-id", object(), first)

    assert [item.submission_id for item in exported] == ["submission-two"]
    assert cursors == ["page-two"]


async def test_export_fails_closed_when_cursor_outlives_page_cap(monkeypatch) -> None:
    first = EvaluationRoundResults(
        round_id="round-id",
        event_id="event-id",
        event_name="Example Event",
        round_name="Review",
        status="closed",
        assigned_count=0,
        completed_count=0,
        average_rating=None,
        submissions=[],
        submission_count=0,
        next_cursor="still-more",
        evaluators=[],
        conflicts=[],
    )

    async def fake_results(_round_id, _request, *, cursor=None):
        assert cursor == "still-more"
        return first

    monkeypatch.setattr(router, "get_round_results", fake_results)

    try:
        await router._collect_export_submissions(
            "round-id", object(), first, max_pages=2
        )
    except HTTPException as error:
        assert error.status_code == 409
        assert error.headers == {"X-Conflict-Type": "export-limit"}
        assert "100-proposal single-file export limit" in str(error.detail)
    else:
        raise AssertionError("a stale count must not permit a partial export")

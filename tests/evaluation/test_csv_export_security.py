from sessionbuddy.evaluation import router
from sessionbuddy.evaluation.models import (
    EvaluationCriterion,
    EvaluationDetail,
    EvaluationRoundResults,
    SubmissionEvaluationResult,
)


async def test_evaluation_csv_quotes_formula_prefixes_after_whitespace(monkeypatch) -> None:
    results = EvaluationRoundResults(
        round_id="round-id",
        event_id="event-id",
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


async def test_detailed_review_csv_keeps_rubric_order_and_all_response_types(
    monkeypatch,
) -> None:
    results = EvaluationRoundResults(
        round_id="round-id",
        event_id="event-id",
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

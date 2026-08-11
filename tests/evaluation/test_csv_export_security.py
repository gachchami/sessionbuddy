from sessionbuddy.evaluation import router
from sessionbuddy.evaluation.models import (
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

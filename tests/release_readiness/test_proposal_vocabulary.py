from pathlib import Path


def test_review_surfaces_use_proposal_until_acceptance() -> None:
    markup = Path("src/sessionbuddy/static/admin_submissions.html").read_text()
    javascript = Path("src/sessionbuddy/static/admin_submissions.js").read_text()
    reviewer = Path("frontend/src/main.tsx").read_text()

    assert "<title>Proposals · SessionBuddy</title>" in markup
    assert "<h1>Proposals</h1>" in markup
    assert "Proposal details" in markup
    assert "Submission details" not in markup
    assert "No proposals yet." in javascript
    assert "selected proposals" in javascript
    assert "decide which proposals move" in reviewer
    assert "Proposal results" in reviewer
    assert "decide which sessions move" not in reviewer

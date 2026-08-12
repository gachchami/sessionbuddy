from pathlib import Path

STATIC = Path("src/sessionbuddy/static")


def test_evaluation_round_selection_is_explicit_and_counted() -> None:
    markup = (STATIC / "admin_submissions.html").read_text()
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert 'id="select-eligible"' in markup
    assert 'id="clear-selection"' in markup
    assert 'id="selected-count" role="status"' in markup
    assert 'id="configure-round" type="button" disabled' in markup
    assert "Accepted and rejected proposals are already decided" in markup
    assert "selection.checked = false" in javascript
    assert "selection.checked = item.status === \"submitted\"" not in javascript
    assert 'selection.addEventListener("change", updateSelectedCount)' in javascript
    assert "item.evaluation_round_name" in javascript
    assert "`In ${item.evaluation_round_name}`" in javascript
    assert ': "Already decided"' in javascript
    assert 'byId("configure-round").disabled = count === 0' in javascript


def test_round_errors_open_the_disclosure_and_receive_focus() -> None:
    markup = (STATIC / "admin_submissions.html").read_text()
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert 'id="round-disclosure"' in markup
    assert 'id="round-status" class="status" role="alert" tabindex="-1"' in markup
    assert 'byId("round-disclosure").open = true' in javascript
    assert 'byId("round-status").focus()' in javascript

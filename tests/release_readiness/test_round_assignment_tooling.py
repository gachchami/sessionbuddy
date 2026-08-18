from pathlib import Path

STATIC = Path("src/sessionbuddy/static")


def test_source_wiring_reviewer_reminders_are_reachable_from_the_proposal_inbox() -> None:
    """The nudge exists on the round detail page; organizers work the inbox.

    ``POST .../evaluators/{id}/reminder`` and its per-reviewer button on the round
    workspace both predate this. Neither is visible from the ledger an organizer
    actually watches, so a round could stall with the remedy one page away and no
    sign that it existed. The ledger sends to every reviewer with outstanding work
    in one action; the server derives the outstanding count itself and folds each
    send into an hourly deterministic key, so a repeat click is not a repeat email.
    """
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert "async function remindOutstandingReviewers(round, button)" in javascript
    assert 'remind.textContent = "Remind reviewers"' in javascript
    # Only an open round has reviews to chase.
    assert 'if (round.status === "open") {\n        const remind' in javascript
    assert "/results`)" in javascript
    assert "item.completed_count < item.assigned_count" in javascript
    assert "/reminder`" in javascript
    assert '"x-csrf-token": state.csrf' in javascript
    # A reviewer who finishes between the progress read and the send is not a failure,
    # and no single reviewer aborts the rest of the list.
    assert "if (error.status === 409) finished += 1;" in javascript
    assert "else failures.push(window.SessionBuddyApi.message(error));" in javascript


def test_source_wiring_the_proposal_inbox_filters_by_routed_track() -> None:
    """Track already travels on every submission row; only the filter was missing.

    ``submissions.routed_track`` is written by the form's routing rules and already
    rendered in the proposal detail. Selecting a whole track for a round meant
    ticking boxes by hand and reading each one to know which track it was in.
    """
    markup = (STATIC / "admin_submissions.html").read_text()
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert 'id="track-filter"' in markup
    assert 'id="track-filter-row"' in markup
    assert "function applyTrackFilter()" in javascript
    assert 'row.dataset.track = String(item.routed_track || "").trim()' in javascript
    # An event whose form routes nothing has no tracks, and no control either.
    assert 'byId("track-filter-row").hidden = tracks.length === 0' in javascript
    # Selecting means what is on screen; clearing stays absolute.
    assert 'if (selected && input.closest("tr")?.hidden) return;' in javascript
    # A selection the filter hides is still in the round, and says so.
    assert "stay in the round" in javascript


def test_source_wiring_assignments_can_be_distributed_across_reviewers() -> None:
    """Auto-distribution seeds the matrix; it never becomes the saved payload.

    The API stores the explicit pair list verbatim, which is what lets a hand-edited
    matrix survive a save. Distribution therefore fills the checkboxes and stops
    there. The balanced generator on the server assigns exactly one reviewer per
    proposal; this is where "three reviewers each, nobody over twenty" is expressed.
    """
    markup = (STATIC / "admin_submissions.html").read_text()
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert 'id="reviewers-per-proposal"' in markup
    assert 'id="max-per-reviewer"' in markup
    assert 'id="distribute-assignments"' in markup
    assert "function distributeAssignments()" in javascript
    assert (
        'byId("distribute-assignments").addEventListener("click", distributeAssignments)'
        in javascript
    )
    # Clamped to the pool rather than promising reviewers the round does not have.
    assert "Math.min(requested, evaluators.length)" in javascript
    # An unsatisfiable cap is refused, not silently under-assigned: a proposal with no
    # reviewer cannot be decided, and open_evaluation_round would refuse it later with
    # less to go on.
    assert "cap * evaluators.length < perProposal * submissions.length" in javascript
    # Every renderable cell is written, because an unset key means "reviews everything".
    assert "state.pairs = {};" in javascript
    # Seeding must mark the form dirty or the save button stays disabled.
    assert "markRoundFormDirty();\n    renderEvaluatorChoices();" in javascript


def test_source_wiring_distribution_is_a_client_side_seed_not_a_new_api_contract() -> None:
    """Guards the boundary the round builder is built on.

    ``roundAssignments()`` returns an explicit matrix on every save, so the server's
    ``assignment_strategy`` generator is never consulted from this page. Distribution
    has to stay on this side of that line: a reviewers-per-proposal field added to the
    request model would be dead weight the UI never sends, and a second generator to
    keep in step with this one.
    """
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert "reviewers_per_submission" not in javascript
    assert "max_assignments_per_reviewer" not in javascript
    assert "roundAssignments()" in javascript

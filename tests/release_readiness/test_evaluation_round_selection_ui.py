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
    assert 'selection.checked = item.status === "submitted"' not in javascript
    assert 'selection.addEventListener("change", submissionSelectionChanged);' in javascript
    assert "item.evaluation_round_name" in javascript
    assert "`In ${item.evaluation_round_name}`" in javascript
    assert ': "Already decided"' in javascript
    assert 'byId("configure-round").disabled = count === 0' in javascript


def test_a_round_with_nothing_assigned_cannot_be_saved_from_the_form() -> None:
    """The matrix is the payload, so an empty one has to be refused before the POST.

    The API stores an explicit `assignments` list verbatim, so an empty list is saved as
    "nobody reviews anything" -- legal for the schema, invisible in every count, and
    impossible to open. The server refuses it now; this is the half that tells the
    organizer which box to tick instead of returning a validation code.

    String assertions can only prove the guard is still written. What it does is pinned
    behaviourally in harness/e2e/evaluation-round-matrix.spec.ts.
    """
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert "!roundAssignments().length" in javascript
    assert "Assign at least one proposal to a reviewer." in javascript
    # The guard is not conditional on the round being opened now: saving a draft is the
    # path where an empty matrix used to pass silently.
    assert (
        "if (submissions.length && evaluators.length && !roundAssignments().length) {"
        in javascript
    )


def test_editing_a_draft_keeps_proposals_the_table_cannot_show() -> None:
    """The proposal table pages at 100; a draft may hold proposals past the first page.

    Rebuilding the selection from rendered checkboxes alone dropped them from the payload,
    and the round diff then deactivated their membership and revoked their assignments.
    """
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert "hiddenSubmissionIds" in javascript
    assert "state.hiddenSubmissionIds = new Set(draft.submission_ids.filter" in javascript
    # Only while editing: "add selected to the open round" still means the on-screen boxes.
    assert (
        "if (!state.editingRoundId || !state.hiddenSubmissionIds.size) return rendered;"
        in javascript
    )
    # Carried visibly, not silently.
    assert "already in this draft, not on this page" in javascript


def test_round_errors_open_the_disclosure_and_receive_focus() -> None:
    markup = (STATIC / "admin_submissions.html").read_text()
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert 'id="round-disclosure"' in markup
    assert 'id="round-status" class="status" role="alert" tabindex="-1"' in markup
    assert 'byId("round-disclosure").open = true' in javascript
    assert 'byId("round-status").focus()' in javascript


def test_round_history_distinguishes_work_by_status() -> None:
    markup = (STATIC / "admin_submissions.html").read_text()
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert 'class="round-ledger"' in markup
    assert "Open my assigned reviews" not in markup
    assert "summary>Create an evaluation round</summary>" in markup
    assert 'round.status === "draft" ? "View draft"' in javascript
    assert 'round.status === "closed" ? "View results"' in javascript
    assert ': "Manage round"' in javascript
    assert 'openDraft.textContent = "Start review"' in javascript
    assert 'editDraft.textContent = "Edit draft"' in javascript
    assert "async function editDraftRound(round)" in javascript
    assert 'method: editingRoundId ? "PUT" : "POST"' in javascript
    assert 'eyebrow.textContent = "Current round"' in javascript
    assert 'link.textContent = "Manage decisions"' in javascript
    assert 'add.id = "add-selected-to-round"' in javascript
    assert ': "Select proposals to add"' in javascript
    assert "addToRound.disabled = count === 0" in javascript


def test_draft_round_configuration_has_read_and_update_contracts() -> None:
    router = Path("src/sessionbuddy/evaluation/router.py").read_text()

    assert '"/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft"' in router
    assert "async def get_draft_evaluation_round" in router
    assert "async def update_draft_evaluation_round" in router
    assert 'AND event_id=?3 AND status="draft"' not in router
    assert "AND event_id=?3 AND status='draft'" in router
    assert 'action="evaluation_round.update"' in router


def test_assignment_matrix_follows_the_proposal_selection() -> None:
    """The per-reviewer proposal checkboxes ARE the payload's assignment list.

    They are rendered from the current proposal selection, so every path that changes
    that selection has to rebuild them. When it did not, a reviewer added before the
    proposals were picked kept an empty matrix, roundAssignments() returned [], and the
    API -- which treats a present list as authoritative -- created the round with its
    proposals and its reviewers but no assignments at all.
    """
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert "function submissionSelectionChanged() {" in javascript
    # Both selection paths -- one checkbox, and the Select/Clear all buttons -- rebuild it.
    assert javascript.count("submissionSelectionChanged()") >= 1
    assert 'selection.addEventListener("change", submissionSelectionChanged);' in javascript
    matrix_rebuild = javascript.split("function submissionSelectionChanged() {", 1)[1]
    assert "renderEvaluatorChoices();" in matrix_rebuild.split("}", 1)[0]

    # A pair kept only in the DOM is lost on the next render, so the choice is stored.
    assert "state.pairs[pairKey] = box.checked;" in javascript
    assert "evaluator.in_round = input.checked;" in javascript
    assert 'box.addEventListener("change", markRoundFormDirty);' not in javascript


def test_round_payload_lists_cannot_contradict_each_other() -> None:
    """assignments is filtered by the membership lists sent alongside it.

    The API rejects an assignment naming a proposal or a reviewer that is not in the
    round, so a stale matrix row -- a reviewer unchecked after their row was drawn --
    would otherwise fail the entire save with a validation error.
    """
    javascript = (STATIC / "admin_submissions.js").read_text()

    assert "evaluators.has(box.dataset.pairEvaluator)" in javascript
    assert "submissions.has(box.dataset.pairSubmission)" in javascript

(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const main = document.querySelector("main");
  main.id = "main";
  main.tabIndex = -1;
  const skip = document.createElement("a");
  skip.className = "skip-link";
  skip.href = "#main";
  skip.textContent = "Skip to content";
  document.body.prepend(skip);
  document.querySelectorAll("th").forEach((heading) => heading.setAttribute("scope", "col"));
  byId("status").tabIndex = -1;
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/submissions$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const state = { csrf: "", userId: "", timeZone: "", submissions: [], evaluators: [], rounds: [], nextCursor: null, addRoundMutation: null, draftOnly: false, editingRoundId: null, pairs: {},
    // roundSubmitInFlight serialises submits so a double-click cannot issue two POSTs
    // even if the disabled attribute is bypassed (Enter key, programmatic submit).
    // roundSaved/roundFormDirty gate the button after a save: resetting the form makes a
    // repeat click harmless-looking, but it still files a round nobody asked for, so the
    // next save must follow a deliberate edit.
    roundSubmitInFlight: false, roundSaved: false, roundFormDirty: false,
    // Proposals that belong to the draft being edited but have no checkbox on this page.
    // The table pages at 100; a draft may hold proposals that sit past the first page.
    hiddenSubmissionIds: new Set() };
  function selectedSubmissionIds() {
    const rendered = [...document.querySelectorAll('input[name="submission_ids"]:checked')].map((input) => input.value);
    // Rebuilding the selection from the DOM alone dropped every draft proposal the table
    // had not rendered: the save then carried a shorter submission_ids, and the round diff
    // deactivated that membership and revoked its assignments. Silent, and on a form the
    // organizer never touched. Only while editing -- "add selected to the open round"
    // must keep meaning the boxes that are actually on screen.
    if (!state.editingRoundId || !state.hiddenSubmissionIds.size) return rendered;
    const seen = new Set(rendered);
    return rendered.concat([...state.hiddenSubmissionIds].filter((id) => !seen.has(id)));
  }
  function submissionLabel(submissionId) {
    const item = state.submissions.find((entry) => entry.id === submissionId);
    if (item) return item.proposal_title;
    return `Proposal ${submissionId.slice(0, 8)} · already in this draft, not on this page`;
  }
  function renderEvaluatorChoices() {
    const evaluatorChoices = byId("evaluators");
    evaluatorChoices.replaceChildren();
    if (!state.evaluators.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No reviewers added yet.";
      evaluatorChoices.append(empty);
      updatePrerequisites();
      return;
    }
    // Each reviewer owns a row listing the selected proposals, so the organizer can say
    // "Sam reviews A and B, not C". Adding a reviewer preselects every proposal -- the
    // common case stays one click -- and unchecking is how you narrow it. The checkboxes
    // ARE the assignment matrix; roundAssignments() reads them straight back out.
    const selected = selectedSubmissionIds();
    state.evaluators.forEach((evaluator) => {
      const row = document.createElement("div");
      row.className = "reviewer-row";
      const label = document.createElement("label");
      label.className = "check-label";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = "evaluator_user_ids";
      input.value = evaluator.user_id;
      input.checked = evaluator.in_round !== false;
      // The matrix is re-rendered whenever the proposal selection changes, so the
      // reviewer's own checkbox has to survive a render too -- keeping it only in the DOM
      // would resurrect a reviewer the organizer had just unchecked.
      input.addEventListener("change", () => {
        evaluator.in_round = input.checked;
        markRoundFormDirty();
      });
      label.append(input, evaluator.display_name);
      row.append(label);
      if (!selected.length) {
        const hint = document.createElement("p");
        hint.className = "help";
        hint.textContent = "Select proposals above to choose which ones this reviewer sees.";
        row.append(hint);
      } else {
        const proposals = document.createElement("div");
        proposals.className = "reviewer-row__proposals";
        selected.forEach((submissionId) => {
          const pair = document.createElement("label");
          pair.className = "check-label";
          const box = document.createElement("input");
          box.type = "checkbox";
          box.dataset.pairEvaluator = evaluator.user_id;
          box.dataset.pairSubmission = submissionId;
          // Default on: assigning a new reviewer to everything currently selected is the
          // behaviour organizers already expect, and unchecking is cheaper than picking.
          const pairKey = `${submissionId}:${evaluator.user_id}`;
          const known = state.pairs && state.pairs[pairKey];
          box.checked = known === undefined ? true : Boolean(known);
          // Write the choice back to state.pairs, which is what a re-render reads. A pair
          // that lived only in the DOM was reset to the "reviews everything" default the
          // next time the matrix was rebuilt, silently widening the round.
          box.addEventListener("change", () => {
            state.pairs = state.pairs || {};
            state.pairs[pairKey] = box.checked;
            markRoundFormDirty();
          });
          pair.append(box, submissionLabel(submissionId));
          proposals.append(pair);
        });
        row.append(proposals);
      }
      evaluatorChoices.append(row);
    });
    updatePrerequisites();
  }
  function installReviewerLookup() {
    const choices = byId("evaluators");
    const help = document.createElement("p");
    help.className = "help";
    help.textContent = "Add an existing Reviewer account by its exact email address. Accounts are never searchable or listed.";
    const row = document.createElement("div");
    row.className = "form-grid";
    const label = document.createElement("label");
    label.textContent = "Reviewer email";
    const input = document.createElement("input");
    input.id = "reviewer-email";
    // Deliberately type="text", not type="email". This lookup box sits INSIDE
    // #round-form, whose submit ends in form.reportValidity() -- so anything that
    // can make this field invalid silently blocks "Save draft round" for a field
    // that contributes nothing to the round payload. A half-typed address in a
    // box the organizer never submitted must not be able to veto the round.
    // The format is checked in the click handler instead, and reported on the
    // status line below, where it is visible to screen readers and automation.
    input.type = "text";
    input.inputMode = "email";
    input.autocomplete = "off";
    input.maxLength = 320;
    input.placeholder = "reviewer@example.com";
    input.required = false;
    label.append(input);
    const action = document.createElement("div");
    action.className = "field-action";
    const button = document.createElement("button");
    button.id = "find-reviewer";
    button.type = "button";
    button.className = "secondary";
    button.textContent = "Add reviewer";
    action.append(button);
    row.append(label, action);
    const status = document.createElement("p");
    status.id = "reviewer-lookup-status";
    status.className = "status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    choices.before(help, row, status);
    const reportLookup = (message) => {
      status.textContent = message;
      status.classList.add("error");
      input.focus();
    };
    button.addEventListener("click", async () => {
      // Never setCustomValidity() on this field. It is inside #round-form, and a
      // custom message set here is cleared only by a later successful click on
      // this same button -- not by typing, not by submitting. One click on "Add
      // reviewer" with an empty box (which is the state this handler leaves
      // behind after a successful add) therefore poisons the round form
      // permanently: every "Save draft round" afterwards fails reportValidity()
      // with no feedback but a native bubble, which is absent from the
      // accessibility tree. An eval agent spent 44 clicks and hit its turn cap
      // on exactly that.
      const email = input.value.trim();
      if (!email) {
        reportLookup("Enter the reviewer's exact email address.");
        return;
      }
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        reportLookup("Enter a valid email address, for example reviewer@example.com.");
        return;
      }
      status.classList.remove("error");
      status.textContent = "Checking that Reviewer account…";
      try {
        const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluators?email=${encodeURIComponent(input.value.trim())}`);
        const reviewer = result.data[0];
        if (!reviewer) {
          // "Not eligible" has two causes needing opposite actions. Telling an organizer
          // to invite someone they already invited sends them back to the invite form,
          // which only reissues the pending invitation and leaves the round as blocked.
          // Named by the address the organizer just typed: the lookup deliberately
          // returns no identity, because an event `edit` grant reaches it and the
          // invitation roster is `manage`-only.
          const pending = (result.pending || [])[0];
          status.textContent = pending
            ? (pending.expired
              ? `${email} was invited as a Reviewer, but the invitation expired before it was accepted, so they cannot be added to a round yet. Send a new invitation from the Reviewers page.`
              : `${email} was invited as a Reviewer but has not accepted yet, so they cannot be added to a round yet. They become available once they open their invitation link and sign in.`)
            : "No active Reviewer account matches that exact email. Invite them as a Reviewer first \u2014 they become available to add once they accept.";
          return;
        }
        if (!state.evaluators.some((item) => item.user_id === reviewer.user_id)) state.evaluators.push(reviewer);
        // Preselect every currently selected proposal for the new reviewer, matching the
        // API's now-required submission_ids rather than relying on a server-side fan-out.
        state.pairs = state.pairs || {};
        for (const submissionId of selectedSubmissionIds()) {
          state.pairs[`${submissionId}:${reviewer.user_id}`] = true;
        }
        renderEvaluatorChoices();
        markRoundFormDirty();
        status.textContent = `${reviewer.display_name} added to this round.`;
        input.value = "";
      } catch (error) {
        status.textContent = window.SessionBuddyApi.message(error, "The Reviewer account could not be checked.");
      }
    });
  }
  function distributionRotation(position, rotation, size) {
    return ((position - rotation) % size + size) % size;
  }
  function distributeAssignments() {
    // Seeds the checkbox matrix; it never becomes the thing that is saved. The API stores
    // the explicit pairs roundAssignments() reads back out, so an organizer can distribute
    // and then hand-correct, and the correction survives every later save.
    const summary = byId("distribution-summary");
    summary.classList.remove("error");
    const fail = (message) => { summary.textContent = message; summary.classList.add("error"); };
    const submissions = selectedSubmissionIds();
    const evaluators = state.evaluators.filter((item) => item.in_round !== false).map((item) => item.user_id);
    if (!submissions.length || !evaluators.length) {
      return fail("Select at least one proposal and one reviewer before distributing.");
    }
    const requested = Number(byId("reviewers-per-proposal").value || 1);
    const capValue = String(byId("max-per-reviewer").value || "").trim();
    const cap = capValue === "" ? null : Number(capValue);
    if (!Number.isInteger(requested) || requested < 1) {
      return fail("Reviewers per proposal must be a whole number of at least 1.");
    }
    if (cap !== null && (!Number.isInteger(cap) || cap < 1)) {
      return fail("Maximum proposals per reviewer must be a whole number of at least 1, or blank for no limit.");
    }
    const strategy = document.querySelector('[name="assignment_strategy"]')?.value || "balanced";
    const perProposal = strategy === "all" ? evaluators.length : Math.min(requested, evaluators.length);
    const clamped = strategy !== "all" && requested > evaluators.length;
    // Refuse rather than silently under-assign: a proposal nobody reviews cannot be
    // decided, and the round would be refused at open time with less to go on.
    if (cap !== null && cap * evaluators.length < perProposal * submissions.length) {
      return fail(`${evaluators.length} reviewer${evaluators.length === 1 ? "" : "s"} capped at ${cap} cannot cover ${submissions.length} proposal${submissions.length === 1 ? "" : "s"} at ${perProposal} review${perProposal === 1 ? "" : "s"} each. Raise the cap, add reviewers, or lower reviewers per proposal.`);
    }
    const loads = new Map(evaluators.map((id) => [id, 0]));
    const position = new Map(evaluators.map((id, index) => [id, index]));
    const chosen = new Set();
    submissions.forEach((submissionId, index) => {
      // Rotating the tie-break stops an even split from always starting at the same
      // reviewer; with one reviewer per proposal and no cap this is plain round-robin.
      const rotation = (index * perProposal) % evaluators.length;
      evaluators
        .filter((id) => cap === null || loads.get(id) < cap)
        .sort((left, right) => (loads.get(left) - loads.get(right))
          || distributionRotation(position.get(left), rotation, evaluators.length)
           - distributionRotation(position.get(right), rotation, evaluators.length))
        .slice(0, perProposal)
        .forEach((id) => {
          chosen.add(`${submissionId}:${id}`);
          loads.set(id, loads.get(id) + 1);
        });
    });
    // Every cell this matrix can render is decided here. A key left unset falls back to
    // the "reviews everything" default, which would quietly widen what was distributed.
    state.pairs = {};
    submissions.forEach((submissionId) => evaluators.forEach((id) => {
      state.pairs[`${submissionId}:${id}`] = chosen.has(`${submissionId}:${id}`);
    }));
    markRoundFormDirty();
    renderEvaluatorChoices();
    const counts = [...loads.values()];
    const low = Math.min(...counts);
    const high = Math.max(...counts);
    summary.textContent = `${chosen.size} review${chosen.size === 1 ? "" : "s"}: ${perProposal} reviewer${perProposal === 1 ? "" : "s"} per proposal, ${low === high ? `${low} each` : `${low} to ${high}`} per reviewer.`
      + (clamped ? ` Only ${evaluators.length} reviewer${evaluators.length === 1 ? " is" : "s are"} in this round, so ${requested} per proposal was not possible.` : "");
  }
  byId("distribute-assignments").addEventListener("click", distributeAssignments);
  function markRoundFormDirty() {
    if (state.roundFormDirty) return;
    state.roundFormDirty = true;
    updatePrerequisites();
  }
  function roundAssignments() {
    // The explicit pair list the API now stores verbatim. Returning it means
    // assignment_strategy is only ever used to seed a NEW round's matrix, never to
    // regenerate one the organizer has edited.
    //
    // Filtered against the two membership lists that travel in the same payload: the API
    // rejects an assignment naming a proposal or a reviewer the round does not contain,
    // so a stale row left over from an unchecked reviewer would fail the whole save.
    const evaluators = new Set(
      [...document.querySelectorAll('input[name="evaluator_user_ids"]:checked')]
        .map((input) => input.value),
    );
    const submissions = new Set(selectedSubmissionIds());
    return [...document.querySelectorAll("[data-pair-evaluator]")]
      .filter((box) => box.checked
        && evaluators.has(box.dataset.pairEvaluator)
        && submissions.has(box.dataset.pairSubmission))
      .map((box) => ({
        submission_id: box.dataset.pairSubmission,
        evaluator_user_id: box.dataset.pairEvaluator,
      }));
  }
  // The per-reviewer proposal checkboxes ARE the assignment matrix, and they are built
  // from the proposal selection, so every change to that selection has to rebuild them.
  // Without this a reviewer added BEFORE the proposals were picked kept a matrix with no
  // rows: roundAssignments() returned [], and the API treats a present list as
  // authoritative, so the round was created with its proposals and its reviewers but zero
  // assignments -- which the organizer then had to re-add through "Edit draft". A draft
  // never trips the server-side coverage check, so nothing surfaced the loss.
  function submissionSelectionChanged() {
    updateSelectedCount();
    renderEvaluatorChoices();
    markRoundFormDirty();
  }
  function updateSelectedCount() {
    const count = selectedSubmissionIds().length;
    byId("selected-count").textContent = `${count} selected`;
    byId("configure-round").disabled = count === 0;
    const addToRound = byId("add-selected-to-round");
    if (addToRound) {
      addToRound.disabled = count === 0;
      addToRound.textContent = count
        ? `Add ${count} selected proposal${count === 1 ? "" : "s"}`
        : "Select proposals to add";
    }
  }
  function setEligibleSelection(selected) {
    // Only ever reached from the Select/Clear buttons, so this is always a user action.
    document.querySelectorAll('input[name="submission_ids"]:not(:disabled)').forEach((input) => {
      // Selecting means the proposals the organizer can actually see: with a track filter
      // applied, "Select submitted" is how you say "this whole track". Clearing stays
      // absolute -- it has always meant none, and a hidden row left checked would travel
      // into the round without ever appearing on screen.
      if (selected && input.closest("tr")?.hidden) return;
      // Decided proposals remain individually eligible for an intentional advisory
      // reassessment, but the bulk action must not sweep the finalized program back into
      // review. The organizer opts those rows in one at a time.
      if (selected && input.dataset.finalDecision === "true") return;
      input.checked = selected;
    });
    // "Clear selection" means none -- including the draft proposals this page cannot show.
    if (!selected) state.hiddenSubmissionIds = new Set();
    submissionSelectionChanged();
  }
  function submissionTrack(item) {
    return String(item.routed_track || "").trim();
  }
  const NO_TRACK = "\u0000none";
  function renderTrackFilter() {
    // Tracks come from the form's routing rules, so an event that routes nothing has
    // nothing to filter by and the control stays out of the way entirely.
    const select = byId("track-filter");
    const tracks = [...new Set(state.submissions.map(submissionTrack).filter(Boolean))]
      .sort((left, right) => left.localeCompare(right));
    const untracked = state.submissions.some((item) => !submissionTrack(item));
    const previous = select.value;
    select.replaceChildren();
    select.append(new Option("All tracks", ""));
    tracks.forEach((track) => select.add(new Option(track, track)));
    if (untracked && tracks.length) select.add(new Option("No track", NO_TRACK));
    select.value = [...select.options].some((option) => option.value === previous) ? previous : "";
    byId("track-filter-row").hidden = tracks.length === 0;
    applyTrackFilter();
  }
  function applyTrackFilter() {
    const wanted = byId("track-filter").value;
    let visible = 0;
    let hiddenSelected = 0;
    for (const row of byId("submissions").querySelectorAll("tr[data-submission-id]")) {
      const track = row.dataset.track || "";
      const matches = !wanted || (wanted === NO_TRACK ? !track : track === wanted);
      row.hidden = !matches;
      if (matches) visible += 1;
      else if (row.querySelector('input[name="submission_ids"]:checked')) hiddenSelected += 1;
    }
    // A selection made before the filter narrowed the table is still part of the round.
    // Saying so is the difference between a filter and a trap.
    byId("track-filter-summary").textContent = !wanted
      ? ""
      : hiddenSelected
        ? `Showing ${visible} proposal${visible === 1 ? "" : "s"}. ${hiddenSelected} selected proposal${hiddenSelected === 1 ? " is" : "s are"} hidden by this filter and stay in the round.`
        : `Showing ${visible} proposal${visible === 1 ? "" : "s"}.`;
  }
  byId("track-filter").addEventListener("change", applyTrackFilter);
  function showRoundError(message) {
    byId("round-disclosure").open = true;
    byId("round-status").textContent = message;
    byId("round-status").classList.add("error");
    byId("round-status").focus();
  }
  byId("select-eligible").addEventListener("click", () => setEligibleSelection(true));
  byId("clear-selection").addEventListener("click", () => setEligibleSelection(false));
  byId("configure-round").addEventListener("click", () => {
    const disclosure = byId("round-disclosure");
    disclosure.open = true;
    disclosure.scrollIntoView({ behavior: "smooth", block: "start" });
    disclosure.querySelector("input, select, button")?.focus({ preventScroll: true });
  });
  const prerequisites = document.createElement("p");
  prerequisites.id = "round-prerequisites";
  prerequisites.className = "help";
  prerequisites.setAttribute("role", "status");
  byId("open-round").before(prerequisites);
  function updatePrerequisites() {
    const missing = [];
    if (!state.submissions.some((submission) => submission.status === "submitted")) missing.push("receive at least one submitted proposal awaiting a decision");
    if (!state.evaluators.length) missing.push("add at least one reviewer who has accepted their invitation to this event");
    const draftOnly = state.draftOnly;
    prerequisites.textContent = draftOnly
      ? "A round is already open, so this one will be saved as a draft. You can add proposals and reviewers now or later."
      : missing.length
        ? `Before opening a round: ${missing.join("; ")}. You can still save a draft.`
        : "Choose proposals and reviewers, then open the round.";
    // Drafting is always available; only opening needs a proposal and a reviewer.
    const blockedByPrerequisites =
      missing.length > 0 && !draftOnly && !isDraftSubmission(byId("round-form"));
    const awaitingIntent = state.roundSaved && !state.roundFormDirty;
    if (awaitingIntent) {
      prerequisites.textContent =
        "Saved. Change a setting, or select proposals or reviewers, to start another round.";
    }
    byId("open-round").disabled = blockedByPrerequisites || awaitingIntent;
  }
  // Custom validity outlives the edit that fixed it: the message is cleared only by an
  // explicit setCustomValidity(""), and while one is set the browser refuses to fire the
  // form's submit event -- so validateRound(), the only code that recomputes these
  // messages, never runs again. The form-level input listener clears just event.target,
  // which is enough when the invalid control is the one the organizer edits to fix it.
  // Neither scorecard message is that kind: a weight total is anchored to one row but
  // corrected on any row, and a choice-list message stays behind on an input that
  // switching the row to Score or Free text has just hidden. Both are therefore cleared
  // for the whole scorecard whenever anything in it changes.
  //
  // The weight half is currently also masked by api_client.js's validateRequiredText(),
  // which runs on a capture-phase click on any submit button and rewrites the validity of
  // every non-empty required input -- the weight inputs included. That is an accident of
  // another module's required-field handling, not a guarantee this form should lean on:
  // it does not cover the choice list (which drops `required` the moment the row stops
  // being a Dropdown), and it would replace the total message with "This field is
  // required." on an empty weight. Clearing here keeps the recovery local and intentional.
  function clearCriterionValidity() {
    byId("criteria")
      .querySelectorAll('input[name="criterion_weight"], input[name="criterion_options"]')
      .forEach((input) => input.setCustomValidity(""));
  }
  function scoredWeightTotal() {
    const rows = [...byId("criteria").querySelectorAll(".criterion-row")]
      .filter((row) => (row.querySelector('[name="criterion_type"]')?.value ?? "score") === "score");
    return {
      count: rows.length,
      total: rows.reduce((sum, row) => sum + (Number(row.querySelector('[name="criterion_weight"]')?.value) || 0), 0),
      rows,
    };
  }
  // The running total is the piece the bubble alone could never carry: "must total 100"
  // is only actionable next to what the Score weights currently add up to, recomputed
  // the moment a weight or a criterion type changes.
  function updateScorecardTotal() {
    const summary = byId("scorecard-total");
    if (!summary) return;
    const { count, total } = scoredWeightTotal();
    summary.textContent = count
      ? `Score weights total ${total} of 100.`
      : "No Score criteria — keep at least one so reviews produce a rating.";
    summary.classList.toggle("error", !count || total !== 100);
  }
  const duplicateRecommendationWarning =
    "This criterion may duplicate the built-in Recommendation field. Reviewers will see both "
    + "controls. Use the built-in field or designate this criterion after purpose-based fields "
    + "are available.";
  function updateCriterionWarning(row) {
    const warning = row.querySelector(".criterion-duplicate-warning");
    if (!warning) return;
    const label = String(row.querySelector('[name="criterion_label"]')?.value || "")
      .toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
    const purpose = row.querySelector('[name="criterion_purpose"]')?.value || "";
    const duplicateRecommendation = !purpose && label === "recommendation";
    const duplicateComment = !purpose && ["comment", "comments", "internal_comment"].includes(label);
    warning.hidden = !duplicateRecommendation && !duplicateComment;
    warning.textContent = duplicateRecommendation
      ? duplicateRecommendationWarning
      : "This criterion may duplicate the built-in Internal comment field. Reviewers will see "
        + "both controls. Use the built-in field or designate this criterion as the reviewer comment.";
  }
  function syncPurposeControls() {
    const form = byId("round-form");
    const rows = [...byId("criteria").querySelectorAll(".criterion-row")];
    const recommendationRow = rows.find((row) => row.querySelector('[name="criterion_purpose"]')?.value === "recommendation");
    const commentRow = rows.find((row) => row.querySelector('[name="criterion_purpose"]')?.value === "comment");
    const recommendationInput = form.elements.recommendations;
    const recommendationLabel = recommendationInput.closest("label");
    recommendationLabel.hidden = Boolean(recommendationRow);
    recommendationInput.required = !recommendationRow;
    if (recommendationRow) {
      recommendationInput.value = recommendationRow.querySelector('[name="criterion_options"]').value;
      recommendationInput.setCustomValidity("");
    }
    const commentRequiredInput = form.elements.comment_required;
    if (commentRequiredInput) {
      commentRequiredInput.closest("label").hidden = Boolean(commentRow);
      if (commentRow) {
        commentRequiredInput.checked = commentRow.querySelector('[name="criterion_required"]').checked;
      }
    }
  }
  function addRemoveButton(row) {
    if (!row.querySelector('[name="criterion_type"]')) {
      const typeLabel = document.createElement("label");
      typeLabel.textContent = "Response";
      const type = document.createElement("select");
      type.name = "criterion_type";
      [["score", "Score"], ["select", "Dropdown"], ["text", "Free text"]].forEach(([value, label]) => type.add(new Option(label, value)));
      typeLabel.append(type);
      const optionsLabel = document.createElement("label");
      optionsLabel.textContent = "Choices";
      optionsLabel.hidden = true;
      const options = document.createElement("input");
      options.name = "criterion_options";
      options.placeholder = "Excellent, Good, Needs work";
      optionsLabel.append(options);
      const requiredLabel = document.createElement("label");
      requiredLabel.className = "check-label";
      const required = document.createElement("input");
      required.type = "checkbox"; required.name = "criterion_required"; required.checked = true;
      requiredLabel.append(required, " Required");
      const purposeLabel = document.createElement("label");
      purposeLabel.textContent = "Use as";
      const purpose = document.createElement("select");
      purpose.name = "criterion_purpose";
      [["", "Additional scorecard field"], ["recommendation", "Recommendation"], ["comment", "Reviewer comment"]]
        .forEach(([value, label]) => purpose.add(new Option(label, value)));
      purposeLabel.append(purpose);
      const duplicateWarning = document.createElement("p");
      duplicateWarning.className = "help warning criterion-duplicate-warning";
      duplicateWarning.hidden = true;
      const weightLabel = row.querySelector('input[name="criterion_weight"]')?.closest("label");
      const updateType = () => {
        const scored = type.value === "score";
        weightLabel.hidden = !scored;
        weightLabel.querySelector("input").required = scored;
        optionsLabel.hidden = type.value !== "select";
        options.required = type.value === "select";
        // Before the input goes out of sight, and unconditionally: a choice-list message
        // left on a hidden input blocks every later submit with nothing on screen to fix,
        // and dropping `required` here is what removes this input from the only other
        // thing that would have cleared it.
        options.setCustomValidity("");
        // Covers the synthetic change events too (draft loading, row restore), which are
        // dispatched without bubbles and so never reach the container's listeners.
        clearCriterionValidity();
        updateScorecardTotal();
        updateCriterionWarning(row);
        syncPurposeControls();
      };
      const updatePurpose = () => {
        if (purpose.value === "recommendation") type.value = "select";
        if (purpose.value === "comment") type.value = "text";
        type.disabled = Boolean(purpose.value);
        if (purpose.value === "recommendation") required.checked = true;
        required.disabled = purpose.value === "recommendation";
        updateType();
      };
      type.addEventListener("change", updateType);
      purpose.addEventListener("change", updatePurpose);
      options.addEventListener("input", syncPurposeControls);
      required.addEventListener("change", syncPurposeControls);
      row.querySelector('[name="criterion_label"]')?.addEventListener("input", () => updateCriterionWarning(row));
      row.append(typeLabel, optionsLabel, requiredLabel, purposeLabel, duplicateWarning);
      updatePurpose();
    }
    if (row.querySelector("button")) return;
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "secondary"; remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      if (byId("criteria").querySelectorAll(".criterion-row").length <= 1) {
        byId("status").textContent = "Keep at least one scorecard criterion.";
        return;
      }
      row.remove();
      // Deleting a row is the third way to correct a weight total without touching the
      // row the message is anchored to, and the only one with no path through
      // updateType() -- a click on this button reaches neither the scorecard's
      // input/change listeners nor api_client's submit-button handler. Adding a row is
      // already covered: appendCriterionRow() ends in updateType().
      clearCriterionValidity();
      updateScorecardTotal();
      syncPurposeControls();
    });
    row.append(remove);
  }
  byId("criteria").querySelectorAll(".criterion-row").forEach(addRemoveButton);
  // Keep the visual total current while typing without announcing every
  // keystroke. The live region is temporarily muted until the committed change.
  byId("criteria").addEventListener("input", () => {
    const summary = byId("scorecard-total");
    summary?.setAttribute("aria-live", "off");
    clearCriterionValidity();
    updateScorecardTotal();
  });
  byId("criteria").addEventListener("change", () => {
    byId("scorecard-total")?.setAttribute("aria-live", "polite");
    clearCriterionValidity();
    updateScorecardTotal();
  });
  updateScorecardTotal();
  const guidance = byId("round-form").elements.evaluator_guidance.closest("label");
  const commentRequired = document.createElement("label");
  commentRequired.className = "check-label";
  const commentRequiredInput = document.createElement("input");
  commentRequiredInput.type = "checkbox";
  commentRequiredInput.name = "comment_required";
  commentRequired.append(commentRequiredInput, " Require a written reviewer comment");
  guidance.after(commentRequired);
  syncPurposeControls();
  function enhanceRoundExport(link, round, kind) {
    link.addEventListener("click", async (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      if (link.dataset.busy === "true") return;
      link.dataset.busy = "true";
      link.setAttribute("aria-disabled", "true");
      const label = link.textContent;
      link.textContent = "Preparing CSV…";
      byId("status").classList.remove("error");
      byId("status").textContent = `Preparing ${kind} for ${round.name}…`;
      try {
        const result = await window.SessionBuddyApi.download(link.href, {}, {
          accept: "text/csv",
          fallback: "The evaluation export could not be prepared.",
          fallbackFilename: kind === "review details" ? "review-details.csv" : "results.csv"
        });
        const count = result.rowCount === null ? "" : ` — ${result.rowCount} record${result.rowCount === 1 ? "" : "s"}`;
        byId("status").textContent = `Download started: ${result.filename}${count}. If it did not start, use the direct download link.`;
      } catch (error) {
        if (window.SessionBuddyApi.redirectIfSignedOut(error)) return;
        if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error) || window.SessionBuddyApi.redirectIfDocumentAccessChanged(error)) return;
        byId("status").textContent = window.SessionBuddyApi.message(error, "The evaluation export could not be prepared.");
        byId("status").classList.add("error");
        byId("status").focus();
      } finally {
        delete link.dataset.busy;
        link.removeAttribute("aria-disabled");
        link.textContent = label;
      }
    });
  }
  function renderRoundHistory(rounds) {
    state.rounds = rounds;
    const container = byId("round-history");
    container.replaceChildren();
    if (!rounds.length) {
      const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "No evaluation rounds yet."; container.append(empty); return;
    }
    const statusOrder = { open: 0, draft: 1, closed: 2 };
    const orderedRounds = [...rounds].sort((left, right) => (statusOrder[left.status] ?? 9) - (statusOrder[right.status] ?? 9));
    for (const round of orderedRounds) {
      const card = document.createElement("article"); card.className = `round-ledger__row round-ledger__row--${round.status}`;
      const stateMarker = document.createElement("span"); stateMarker.className = "round-ledger__marker"; stateMarker.setAttribute("aria-hidden", "true");
      const content = document.createElement("div"); content.className = "round-ledger__content";
      const heading = document.createElement("h3"); const link = document.createElement("a"); link.href = `/admin/evaluation-rounds/${encodeURIComponent(round.id)}`; link.textContent = round.name; heading.append(link);
      const status = document.createElement("span"); status.className = "round-ledger__status"; status.textContent = round.status === "open" ? "In review" : round.status;
      const summary = document.createElement("p"); summary.className = "result"; summary.textContent = `${round.assignment_count} assignment${round.assignment_count === 1 ? "" : "s"} · ${round.evaluator_count} reviewer${round.evaluator_count === 1 ? "" : "s"}`;
      content.append(status, heading, summary);
      const actions = document.createElement("div"); actions.className = "round-ledger__actions";
      const monitor = document.createElement("a"); monitor.className = round.status === "open" ? "button" : "button secondary"; monitor.href = link.href; monitor.textContent = round.status === "draft" ? "View draft" : round.status === "closed" ? "View results" : "Manage round";
      const exportLink = document.createElement("a"); exportLink.className = "round-ledger__export"; exportLink.href = `/api/v1/admin/evaluation-rounds/${encodeURIComponent(round.id)}/export.csv`; exportLink.textContent = "Export CSV";
      const reviewExportLink = document.createElement("a"); reviewExportLink.className = "round-ledger__export"; reviewExportLink.href = `/api/v1/admin/evaluation-rounds/${encodeURIComponent(round.id)}/reviews.csv`; reviewExportLink.textContent = "Export review details";
      const directFallbacks = document.createElement("p"); directFallbacks.className = "round-ledger__fallbacks"; directFallbacks.append("Download blocked? ");
      const directExport = document.createElement("a"); directExport.className = "round-ledger__direct"; directExport.href = exportLink.href; directExport.target = "_blank"; directExport.rel = "noopener"; directExport.textContent = "Direct results download";
      const directReviews = document.createElement("a"); directReviews.className = "round-ledger__direct"; directReviews.href = reviewExportLink.href; directReviews.target = "_blank"; directReviews.rel = "noopener"; directReviews.textContent = "Direct review-details download";
      directFallbacks.append(directExport, " · ", directReviews);
      content.append(directFallbacks);
      enhanceRoundExport(exportLink, round, "results");
      enhanceRoundExport(reviewExportLink, round, "review details");
      actions.append(monitor, exportLink, reviewExportLink);
      if (round.status === "open") {
        const remind = document.createElement("button");
        remind.type = "button"; remind.className = "secondary"; remind.textContent = "Remind reviewers";
        remind.addEventListener("click", () => remindOutstandingReviewers(round, remind));
        actions.append(remind);
      }
      if (round.status === "draft") {
        const editDraft = document.createElement("button");
        editDraft.type = "button"; editDraft.className = "secondary"; editDraft.textContent = "Edit draft";
        editDraft.addEventListener("click", () => editDraftRound(round).catch((error) => showRoundError(window.SessionBuddyApi.message(error))));
        actions.append(editDraft);
        const openDraft = document.createElement("button");
        openDraft.type = "button"; openDraft.textContent = "Start review";
        openDraft.addEventListener("click", () => openDraftRound(round, openDraft));
        actions.append(openDraft);
      }
      card.append(stateMarker, content, actions); container.append(card);
    }
  }
  async function remindOutstandingReviewers(round, button) {
    // The per-reviewer endpoint already existed and is already surfaced on the round
    // detail page. Organizers work the proposal inbox, so the nudge belongs here too.
    // The server derives the outstanding count itself and folds each reminder into an
    // hourly deterministic key, so a second click inside the hour re-sends nothing.
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Sending…";
    try {
      const results = await api(`/api/v1/admin/evaluation-rounds/${encodeURIComponent(round.id)}/results`);
      const outstanding = (results.evaluators || []).filter((item) => item.completed_count < item.assigned_count);
      if (!outstanding.length) {
        byId("status").classList.remove("error");
        byId("status").textContent = `Every reviewer in ${round.name} has finished their assigned reviews.`;
        return;
      }
      let sent = 0;
      let finished = 0;
      const failures = [];
      for (const evaluator of outstanding) {
        try {
          await api(`/api/v1/admin/evaluation-rounds/${encodeURIComponent(round.id)}/evaluators/${encodeURIComponent(evaluator.evaluator_user_id)}/reminder`, {
            method: "POST",
            headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
            body: "{}",
          });
          sent += 1;
        } catch (error) {
          // One reviewer must never strand the rest of the list -- that is precisely how
          // a bulk action turns into a partial send nobody can see. A 409 is this
          // reviewer finishing between the progress read and the send, which is the
          // reminder doing its job. Everything else is collected and reported once the
          // loop has been all the way through.
          if (error.status === 409) finished += 1;
          else failures.push(window.SessionBuddyApi.message(error));
        }
      }
      const parts = [];
      if (sent) parts.push(`Reminder queued for ${sent} reviewer${sent === 1 ? "" : "s"} with outstanding reviews in ${round.name}.`);
      if (finished) parts.push(`${finished} finished while sending.`);
      if (failures.length) parts.push(`${failures.length} could not be reminded: ${failures[0]}`);
      if (!parts.length) parts.push(`Every reviewer in ${round.name} finished before the reminders went out.`);
      byId("status").classList.toggle("error", failures.length > 0);
      byId("status").textContent = parts.join(" ");
    } catch (error) {
      showRoundError(window.SessionBuddyApi.message(error));
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  }
  async function editDraftRound(round) {
    const draft = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds/${encodeURIComponent(round.id)}/draft`);
    state.editingRoundId = round.id;
    const form = byId("round-form");
    for (const [name, value] of Object.entries({ name: draft.name, rating_min: draft.rating_min, rating_max: draft.rating_max, recommendations: draft.recommendations.join(", "), evaluator_guidance: draft.evaluator_guidance, assignment_strategy: draft.assignment_strategy })) form.elements[name].value = value;
    form.elements.blind_review.checked = draft.blind_review;
    form.elements.comment_required.checked = draft.comment_required;
    form.elements.round_status.value = "draft";
    form.elements.review_opens_at.value = millisInputValue(draft.review_opens_at_ms);
    form.elements.review_closes_at.value = millisInputValue(draft.review_closes_at_ms);
    const criteria = byId("criteria");
    criteria.replaceChildren();
    for (const criterion of draft.criteria) {
      const row = document.createElement("div"); row.className = "form-grid criterion-row";
      const label = document.createElement("label"); label.textContent = "Criterion";
      const labelInput = document.createElement("input"); labelInput.name = "criterion_label"; labelInput.required = true; labelInput.maxLength = 120; labelInput.value = criterion.label; label.append(labelInput);
      const weight = document.createElement("label"); weight.textContent = "Weight";
      const weightInput = document.createElement("input"); weightInput.name = "criterion_weight"; weightInput.type = "number"; weightInput.min = 1; weightInput.max = 100; weightInput.value = criterion.weight || 1; weight.append(weightInput);
      row.append(label, weight); addRemoveButton(row); criteria.append(row);
      row.querySelector('[name="criterion_type"]').value = criterion.response_type;
      row.querySelector('[name="criterion_options"]').value = (criterion.options || []).join(", ");
      row.querySelector('[name="criterion_required"]').checked = criterion.required;
      row.querySelector('[name="criterion_purpose"]').value = criterion.purpose || "";
      row.querySelector('[name="criterion_purpose"]').dispatchEvent(new Event("change"));
      row.querySelector('[name="criterion_type"]').dispatchEvent(new Event("change"));
    }
    const rendered = new Set();
    document.querySelectorAll('input[name="submission_ids"]').forEach((input) => {
      rendered.add(input.value);
      input.checked = draft.submission_ids.includes(input.value);
    });
    // Anything the draft holds that this page has no checkbox for -- a proposal past the
    // first 100, or one the table hides because it is already decided -- is carried in
    // state instead, so saving the draft cannot drop what the organizer never saw.
    state.hiddenSubmissionIds = new Set(draft.submission_ids.filter((id) => !rendered.has(id)));
    // Restore the stored pairs. Without this the checkboxes would default every reviewer
    // back to "reviews everything" and the next save would quietly widen the round.
    state.pairs = {};
    for (const submissionId of draft.submission_ids) {
      for (const evaluatorId of draft.evaluator_user_ids) {
        state.pairs[`${submissionId}:${evaluatorId}`] = false;
      }
    }
    for (const pair of draft.assignments || []) {
      state.pairs[`${pair.submission_id}:${pair.evaluator_user_id}`] = true;
    }
    for (const userId of draft.evaluator_user_ids) if (!state.evaluators.some((item) => item.user_id === userId)) state.evaluators.push({ user_id: userId, display_name: "Existing reviewer" });
    renderEvaluatorChoices();
    byId("round-disclosure").open = true;
    byId("open-round").textContent = "Save draft changes";
    byId("round-disclosure").scrollIntoView({ block: "start" });
    updateSelectedCount();
  }
  function showRound(round) {
    const label = document.createElement("div");
    label.className = "current-round-actions__label";
    const eyebrow = document.createElement("span"); eyebrow.textContent = "Current round";
    const name = document.createElement("strong"); name.textContent = round.name;
    label.append(eyebrow, name);
    const link = document.createElement("a");
    link.href = `/admin/evaluation-rounds/${round.id}`;
    link.textContent = "Manage decisions";
    link.className = "button secondary";
    const add = document.createElement("button");
    add.type = "button";
    add.className = "secondary";
    add.id = "add-selected-to-round";
    add.addEventListener("click", async () => {
      const submissionIds = selectedSubmissionIds();
      if (!submissionIds.length) {
        showRoundError("Select at least one proposal.");
        return;
      }
      add.disabled = true;
      try {
        const payload = JSON.stringify({ submission_ids: submissionIds });
        const fingerprint = `${round.id}:${payload}`;
        if (!state.addRoundMutation || state.addRoundMutation.fingerprint !== fingerprint) {
          state.addRoundMutation = {
            fingerprint,
            key: `${crypto.randomUUID()}-${crypto.randomUUID()}`,
          };
        }
        const result = await api(`/api/v1/admin/evaluation-rounds/${encodeURIComponent(round.id)}/submissions`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": state.addRoundMutation.key },
          body: payload
        });
        state.addRoundMutation = null;
        byId("status").textContent = result.submission_count
          ? `${result.submission_count} proposal${result.submission_count === 1 ? "" : "s"} added with ${result.assignment_count} review assignment${result.assignment_count === 1 ? "" : "s"}.`
          : "Every selected proposal is already in this round.";
      } catch (error) {
        byId("status").textContent = window.SessionBuddyApi.message(error);
        byId("status").classList.add("error");
      } finally {
        add.disabled = false;
      }
    });
    const actions = document.createElement("div");
    actions.className = "actions";
    actions.append(add, link);
    byId("round-result").replaceChildren(label, actions);
    updateSelectedCount();
    setDraftOnly(round.name);
  }
  function setDraftOnly(openRoundName) {
    // A round is already open, so a new round can only be prepared as a draft. The form
    // stays editable -- the organizer can still configure the next round's dates,
    // scorecard and reviewer pool while the current one collects scores.
    state.draftOnly = true;
    const status = byId("round-form").elements.round_status;
    if (!status) return;
    status.value = "draft";
    const openOption = status.querySelector('option[value="open"]');
    if (openOption) openOption.disabled = true;
    byId("round-status-help").textContent = openRoundName
      ? `“${openRoundName}” is open, so this round will be saved as a draft. Close the open round to start this one.`
      : "This round will be saved as a draft.";
    byId("open-round").textContent = "Save draft round";
  }
  async function openDraftRound(round, button) {
    button.disabled = true;
    try {
      const result = await api(`/api/v1/admin/evaluation-rounds/${encodeURIComponent(round.id)}/open`, {
        method: "POST",
        headers: { "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` }
      });
      byId("status").classList.remove("error");
      byId("status").textContent = `${result.name} is now open with ${result.assignment_count} assignments across ${result.evaluator_count} reviewers.`;
      const history = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds`);
      renderRoundHistory(history.data);
      const current = history.data.find((item) => item.status === "open") || null;
      if (current) showRound(current);
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
      button.disabled = false;
    }
  }
  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = { schema_version: 1, page_template: "/admin/events/{event_id}/submissions", navigation_type: navigation?.type || "unknown", device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop", sampled: false, lcp_ms: null, inp_ms: null, cls: null, ttfb_ms: navigation?.responseStart ?? null, fcp_ms: null, route_transition_ms: null, critical_api_ms: Math.max(0, performance.now() - started), api_request_id: response.headers.get("x-request-id") };
  }
  async function api(path, options = {}) {
    const started = performance.now();
    return window.SessionBuddyApi.request(path, options, {
      onResponse: (response) => recordTelemetry(started, response)
    });
  }
  async function loadEventTimeZone() {
    const event = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
    if (!event?.time_zone) throw new Error("The event time zone could not be loaded.");
    return event.time_zone;
  }
  function partsInTimeZone(value) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: state.timeZone,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23"
    }).formatToParts(new Date(value));
    return Object.fromEntries(parts
      .filter(({ type }) => type !== "literal")
      .map(({ type, value: part }) => [type, Number(part)]));
  }
  function millisInputValue(value) {
    if (!value) return "";
    const parts = partsInTimeZone(value);
    const pad = (number) => String(number).padStart(2, "0");
    return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
  }
  function inputMillis(value) {
    if (!value) return null;
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!match) return Number.NaN;
    const [, year, month, day, hour, minute] = match.map(Number);
    const intended = Date.UTC(year, month - 1, day, hour, minute);
    let timestamp = intended;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const actual = partsInTimeZone(timestamp);
      const actualAsUtc = Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute);
      const adjustment = intended - actualAsUtc;
      timestamp += adjustment;
      if (adjustment === 0) break;
    }
    const actual = partsInTimeZone(timestamp);
    return Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute) === intended
      ? timestamp
      : Number.NaN;
  }
  function detailRow(label, value) {
    const group = document.createElement("div");
    group.append(document.createElement("dt"), document.createElement("dd"));
    group.firstChild.textContent = label;
    group.lastChild.textContent = value === null || value === undefined || value === "" ? "Not provided" : String(value);
    return group;
  }
  function humanize(key) {
    return key.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
  }
  function answerText(value) {
    if (Array.isArray(value)) return value.length ? value.join(", ") : "Not provided";
    if (typeof value === "boolean") return value ? "Yes" : "No";
    return value;
  }
  function answerLabel(item, key) {
    return item.answer_labels?.[key] || humanize(key);
  }
  function decisionEmailComposer(item, decision, correction, actionLabel) {
    const wrap = document.createElement("div");
    wrap.className = "decision-composition";
    const notifyLabel = document.createElement("label");
    notifyLabel.className = "check-label";
    const notify = document.createElement("input");
    notify.type = "checkbox";
    notify.checked = true;
    notifyLabel.append(notify, document.createTextNode(` Email ${item.speaker_name || "the speaker"} about ${actionLabel}`));
    const fields = document.createElement("div");
    fields.className = "decision-composition__fields";
    const loading = document.createElement("p");
    loading.className = "help";
    loading.textContent = "Loading the decision email…";
    const defaultSubject = document.createElement("p");
    defaultSubject.className = "help";
    const subjectLabel = document.createElement("label");
    subjectLabel.append(document.createTextNode("Custom subject "));
    const subjectOptional = document.createElement("span");
    subjectOptional.className = "optional";
    subjectOptional.textContent = "Optional";
    subjectLabel.append(subjectOptional);
    const subject = document.createElement("input");
    subject.type = "text";
    subject.maxLength = 200;
    subject.placeholder = "Leave blank to use the default subject.";
    subjectLabel.append(subject);
    const defaultMessage = document.createElement("p");
    defaultMessage.className = "help";
    const messageLabel = document.createElement("label");
    messageLabel.append(document.createTextNode("Custom message "));
    const messageOptional = document.createElement("span");
    messageOptional.className = "optional";
    messageOptional.textContent = "Optional";
    messageLabel.append(messageOptional);
    const message = document.createElement("textarea");
    message.rows = 3;
    message.maxLength = 4000;
    message.placeholder = "Leave blank to use the default message.";
    messageLabel.append(message);
    const preview = document.createElement("div");
    preview.className = "decision-email-preview";
    preview.setAttribute("aria-label", "Decision email preview");
    const previewStatus = document.createElement("p");
    previewStatus.className = "help";
    let resolved = null;
    let defaults = null;
    let previewTimer = 0;
    function renderPreview() {
      if (!resolved) return;
      preview.replaceChildren();
      const heading = document.createElement("strong");
      heading.textContent = `Subject: ${resolved.resolved_subject}`;
      const body = document.createElement("p");
      body.textContent = resolved.resolved_body;
      const title = document.createElement("p");
      const titleStrong = document.createElement("strong");
      titleStrong.textContent = resolved.proposal_title;
      title.append(titleStrong);
      preview.append(heading, body, title);
    }
    async function refreshPreview() {
      try {
        const next = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions/${encodeURIComponent(item.id)}/decision-message-preview`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
          body: JSON.stringify({
            decision,
            correction,
            speaker_subject: subject.value.trim(),
            speaker_message: message.value.trim(),
          }),
        });
        resolved = next;
        defaults ||= next;
        previewStatus.textContent = "";
        previewStatus.classList.remove("warning");
        renderPreview();
        return next;
      } catch (error) {
        previewStatus.textContent = "The preview could not be refreshed. The standard decision email will be used if you confirm now.";
        previewStatus.classList.add("warning");
        throw error;
      }
    }
    function schedulePreview() {
      clearTimeout(previewTimer);
      previewTimer = window.setTimeout(() => { void refreshPreview().catch(() => {}); }, 250);
    }
    subject.addEventListener("input", schedulePreview);
    message.addEventListener("input", schedulePreview);
    fields.append(loading);
    wrap.append(notifyLabel, fields);
    notify.addEventListener("change", () => { fields.hidden = !notify.checked; });
    async function load() {
      try {
        await refreshPreview();
      } catch (error) {
        fields.replaceChildren(previewStatus);
        throw error;
      }
      fields.replaceChildren();
      if (!resolved.recipient_available) {
        const unavailable = document.createElement("p");
        unavailable.className = "status error";
        unavailable.setAttribute("role", "alert");
        unavailable.textContent = "This speaker has no email address. Add one before sending a decision email.";
        fields.append(unavailable);
        return;
      }
      const defaultSubjectLabel = document.createElement("strong");
      defaultSubjectLabel.textContent = "Default subject: ";
      defaultSubject.replaceChildren(defaultSubjectLabel, document.createTextNode(defaults.resolved_subject));
      const defaultMessageLabel = document.createElement("strong");
      defaultMessageLabel.textContent = "Default message: ";
      defaultMessage.replaceChildren(defaultMessageLabel, document.createTextNode(defaults.resolved_body));
      fields.append(defaultSubject, subjectLabel, defaultMessage, messageLabel, preview, previewStatus);
      renderPreview();
    }
    return {
      element: wrap,
      notify,
      subject,
      message,
      load,
      canSend: () => !notify.checked || resolved?.recipient_available !== false,
    };
  }
  // One reject affordance, shared by the inline row and the proposal dialog.
  // The reason is captured in a field rather than window.prompt: the dialog is
  // modal, and a prompt raised over an open <dialog> is both poor UX and
  // suppressible by the browser, which would silently remove the only way to
  // reject a proposal that never went to review.
  function rejectWithoutReviewControl(item) {
    const wrap = document.createElement("div");
    wrap.className = "reject-without-review";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "danger secondary";
    trigger.textContent = "Reject without review";
    const panel = document.createElement("div");
    panel.className = "reject-without-review__panel";
    panel.hidden = true;
    const label = document.createElement("label");
    label.textContent = "Internal reason";
    const reason = document.createElement("textarea");
    reason.id = `direct-rejection-reason-${item.id}`;
    label.htmlFor = reason.id;
    reason.rows = 3;
    reason.maxLength = 2000;
    const help = document.createElement("p");
    help.className = "help";
    help.textContent = "Recorded for organizers only, never shown to the speaker. This decision is permanent.";
    const composer = decisionEmailComposer(item, "rejected", false, "rejection");
    const message = document.createElement("p");
    message.className = "status";
    message.setAttribute("role", "alert");
    message.tabIndex = -1;
    const confirmButton = document.createElement("button");
    confirmButton.type = "button";
    confirmButton.className = "danger";
    confirmButton.textContent = "Confirm rejection";
    const cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.className = "secondary";
    cancelButton.textContent = "Cancel";
    const buttons = document.createElement("div");
    buttons.className = "actions decision-actions";
    // Keep the row clear of the dialog's bottom padding when it is scrolled into view.
    buttons.style.scrollMarginBottom = "1.5rem";
    buttons.append(confirmButton, cancelButton);
    panel.append(label, message, reason, help, composer.element, buttons);
    // The sticky action row remains visible while focus moves to the field that
    // needs correction, so validation never strands the organizer below the fold.
    function showPanelError(text, focusTarget) {
      message.textContent = text;
      message.classList.add("error");
      focusTarget.focus({ preventScroll: true });
      focusTarget.scrollIntoView({ block: "nearest" });
    }
    trigger.addEventListener("click", () => {
      trigger.hidden = true;
      panel.hidden = false;
      reason.focus();
      confirmButton.disabled = true;
      void composer.load()
        .catch(() => {})
        .finally(() => { confirmButton.disabled = false; });
    });
    cancelButton.addEventListener("click", () => {
      panel.hidden = true;
      trigger.hidden = false;
      message.textContent = "";
      message.classList.remove("error");
      trigger.focus();
    });
    confirmButton.addEventListener("click", async () => {
      const internalReason = reason.value.trim();
      if (!internalReason) {
        showPanelError("Add an internal reason before rejecting.", reason);
        return;
      }
      if (!composer.canSend()) {
        showPanelError("Add a speaker email address before sending this decision email.", reason);
        return;
      }
      confirmButton.disabled = true;
      cancelButton.disabled = true;
      try {
        const decision = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions/${encodeURIComponent(item.id)}/reject`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}`,
          },
          body: JSON.stringify({
            decision: "rejected",
            internal_reason: internalReason,
            send_email: composer.notify.checked,
            speaker_subject: composer.notify.checked ? composer.subject.value.trim() : "",
            speaker_message: composer.notify.checked ? composer.message.value.trim() : "",
            override_incomplete_reviews: false,
          }),
        });
        byId("status").textContent = `“${item.proposal_title}” was rejected without review.${decision.communication_queued ? " Speaker email queued — track delivery in the message log." : " No email sent."}`;
        location.reload();
      } catch (error) {
        // Chief among these is the 409 the server returns once the proposal is
        // in a round: it names the round path, so surface it in place rather
        // than on the page status line the dialog covers.
        confirmButton.disabled = false;
        cancelButton.disabled = false;
        showPanelError(
          error.code === "round_conflict"
            ? "This proposal is already being reviewed. Open its evaluation round to record the decision."
            : window.SessionBuddyApi.message(error),
          message
        );
      }
    });
    wrap.append(trigger, panel);
    return wrap;
  }
  function decisionCorrectionControl(item) {
    const wrap = document.createElement("div");
    wrap.className = "reject-without-review";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "secondary";
    trigger.textContent = "Correct decision";
    const panel = document.createElement("div");
    panel.className = "reject-without-review__panel";
    panel.hidden = true;
    const target = item.status === "accepted" ? "rejected" : "accepted";
    const explanation = document.createElement("p");
    explanation.textContent = target === "accepted"
      ? "This preserves the original rejection and creates or restores an accepted session."
      : "This preserves the original acceptance and withdraws the session from active scheduling.";
    const label = document.createElement("label");
    label.textContent = "Correction reason";
    const reason = document.createElement("textarea");
    reason.rows = 4;
    reason.maxLength = 2000;
    reason.required = true;
    label.append(reason);
    const composer = decisionEmailComposer(item, target, true, "this correction");
    const feedback = document.createElement("p");
    feedback.className = "status";
    feedback.setAttribute("role", "alert");
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = target === "rejected" ? "danger" : "";
    confirm.textContent = `Record correction to ${target}`;
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary";
    cancel.textContent = "Cancel";
    const actions = document.createElement("div");
    actions.className = "actions decision-actions";
    actions.append(cancel, confirm);
    panel.append(explanation, label, composer.element, feedback, actions);
    trigger.addEventListener("click", () => {
      trigger.hidden = true;
      panel.hidden = false;
      reason.focus();
      confirm.disabled = true;
      void composer.load()
        .catch(() => {})
        .finally(() => { confirm.disabled = false; });
    });
    cancel.addEventListener("click", () => {
      panel.hidden = true;
      trigger.hidden = false;
      feedback.textContent = "";
      trigger.focus();
    });
    confirm.addEventListener("click", async () => {
      if (!reason.value.trim()) {
        feedback.textContent = "Add the internal reason for this correction.";
        feedback.classList.add("error");
        reason.focus();
        return;
      }
      if (!composer.canSend()) {
        feedback.textContent = "Add a speaker email address before sending this correction email.";
        feedback.classList.add("error");
        reason.focus();
        return;
      }
      confirm.disabled = true;
      cancel.disabled = true;
      try {
        const corrected = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions/${encodeURIComponent(item.id)}/decision-corrections`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}`,
          },
          body: JSON.stringify({
            corrected_decision: target,
            reason: reason.value.trim(),
            send_email: composer.notify.checked,
            speaker_subject: composer.notify.checked ? composer.subject.value.trim() : "",
            speaker_message: composer.notify.checked ? composer.message.value.trim() : "",
          }),
        });
        const lifecycleNotice = corrected.accepted_session_lifecycle_status === "withdrawn" && target === "accepted"
          ? " The session remains withdrawn until speaker participation is restored."
          : "";
        byId("status").textContent = `Decision corrected to ${target}. The original decision remains in the audit history.${lifecycleNotice}`;
        location.reload();
      } catch (error) {
        confirm.disabled = false;
        cancel.disabled = false;
        feedback.textContent = window.SessionBuddyApi.message(error, "The correction could not be recorded.");
        feedback.classList.add("error");
        feedback.focus();
      }
    });
    wrap.append(trigger, panel);
    return wrap;
  }
  function acceptWithoutReviewControl(item) {
    const wrap = document.createElement("div");
    wrap.className = "reject-without-review";
    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.textContent = "Accept without review";
    const panel = document.createElement("div");
    panel.className = "reject-without-review__panel";
    panel.hidden = true;
    const explanation = document.createElement("p");
    explanation.className = "help";
    explanation.textContent = "Use this audited override only when review is intentionally unnecessary. It creates the accepted session and speaker onboarding work.";
    const label = document.createElement("label");
    label.textContent = "Internal acceptance reason";
    const reason = document.createElement("textarea");
    reason.rows = 3;
    reason.maxLength = 2000;
    label.append(reason);
    const composer = decisionEmailComposer(item, "accepted", false, "acceptance");
    const feedback = document.createElement("p");
    feedback.className = "status";
    feedback.setAttribute("role", "alert");
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.textContent = "Confirm acceptance";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "secondary";
    cancel.textContent = "Cancel";
    const actions = document.createElement("div");
    actions.className = "actions decision-actions";
    actions.append(cancel, confirm);
    panel.append(explanation, label, composer.element, feedback, actions);
    trigger.addEventListener("click", () => {
      trigger.hidden = true;
      panel.hidden = false;
      reason.focus();
      confirm.disabled = true;
      void composer.load()
        .catch(() => {})
        .finally(() => { confirm.disabled = false; });
    });
    cancel.addEventListener("click", () => {
      panel.hidden = true;
      trigger.hidden = false;
      feedback.textContent = "";
      trigger.focus();
    });
    confirm.addEventListener("click", async () => {
      if (!reason.value.trim()) {
        feedback.textContent = "Add an internal reason before accepting without review.";
        feedback.classList.add("error");
        reason.focus();
        return;
      }
      if (!composer.canSend()) {
        feedback.textContent = "Add a speaker email address before sending this decision email.";
        feedback.classList.add("error");
        reason.focus();
        return;
      }
      confirm.disabled = true;
      cancel.disabled = true;
      try {
        await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions/${encodeURIComponent(item.id)}/accept`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}`,
          },
          body: JSON.stringify({
            decision: "accepted",
            internal_reason: reason.value.trim(),
            send_email: composer.notify.checked,
            speaker_subject: composer.notify.checked ? composer.subject.value.trim() : "",
            speaker_message: composer.notify.checked ? composer.message.value.trim() : "",
            override_incomplete_reviews: false,
          }),
        });
        byId("status").textContent = `“${item.proposal_title}” was accepted without review.${composer.notify.checked ? " Speaker email queued — track delivery in the message log." : " No email sent."}`;
        location.reload();
      } catch (error) {
        confirm.disabled = false;
        cancel.disabled = false;
        feedback.textContent = window.SessionBuddyApi.message(error, "The proposal could not be accepted.");
        feedback.classList.add("error");
      }
    });
    wrap.append(trigger, panel);
    return wrap;
  }
  function showSubmission(item, trigger) {
    const details = byId("submission-detail-list");
    details.replaceChildren(
      detailRow("Speaker", item.speaker_name),
      detailRow("Company", item.speaker_company),
      detailRow("Email", item.speaker_email),
      detailRow("Title", item.proposal_title),
      detailRow("Full abstract", item.proposal_abstract),
      detailRow("Status", item.status),
      detailRow("Submitted", new Date(item.submitted_at_ms).toLocaleString()),
      detailRow("Routed category", item.routed_category),
      detailRow("Routed track", item.routed_track),
      detailRow("Review queue", item.routed_review_queue),
      ...(item.evaluation_round_name ? [detailRow("Evaluation round", item.evaluation_round_name)] : []),
      ...Object.entries(item.answers || {}).map(([key, value]) => detailRow(answerLabel(item, key), answerText(value)))
    );
    if (item.co_speakers?.length) {
      details.append(detailRow("Additional participants", item.co_speakers.map((person) => `${person.display_name} (${person.email}) · ${person.role_label}`).join(", ")));
    }
    const decisionActions = byId("submission-detail-actions");
    decisionActions.replaceChildren();
    if (item.status === "submitted" && item.evaluation_round_id) {
      const roundLink = document.createElement("a");
      roundLink.className = "button";
      roundLink.href = `/admin/evaluation-rounds/${encodeURIComponent(item.evaluation_round_id)}`;
      roundLink.textContent = `Open ${item.evaluation_round_name || "evaluation round"} to decide`;
      decisionActions.append(roundLink);
    } else if (item.status === "submitted") {
      decisionActions.append(
        acceptWithoutReviewControl(item),
        rejectWithoutReviewControl(item),
      );
    } else if (item.status === "accepted" || item.status === "rejected") {
      decisionActions.append(decisionCorrectionControl(item));
    }
    const dialog = byId("submission-detail");
    dialog.addEventListener("close", () => trigger.focus(), { once: true });
    dialog.showModal();
  }
  async function load() {
    try {
      if (!eventId) throw new Error("This event link is invalid.");
      const session = await api("/api/v1/auth/session");
      state.csrf = session.csrf_token;
      state.userId = session.user_id;
      state.timeZone = await loadEventTimeZone();
      byId("round-time-zone").textContent = state.timeZone;
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions`);
      state.submissions = result.data;
      document.body.dataset.eventId = eventId;
      window.dispatchEvent(new Event("sessionbuddy:event-context"));
      byId("cfp-workspace-link").href = `/admin/events/${encodeURIComponent(eventId)}/cfp`;
      byId("cfp-workspace-link").hidden = false;
      // Decisions queue speaker email from this page; the delivery record for
      // those emails lives on the messages page. Without this link an organizer
      // has no way from here to confirm what was actually sent.
      byId("message-log-link").href = `/admin/events/${encodeURIComponent(eventId)}/messages`;
      byId("message-log-link").hidden = false;
      const cfp = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/cfp`);
      if (cfp.published_form?.accepting_submissions) {
        const eventKey = eventId.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 6);
        byId("create-proposal-link").href = `/cfp/${eventKey}/${encodeURIComponent(cfp.published_form.slug)}`;
        byId("create-proposal-link").hidden = false;
      }
      renderEvaluatorChoices();
      state.nextCursor = result.next_cursor || null;
      const body = byId("submissions");
      body.replaceChildren();
      if (!result.data.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 5;
        cell.textContent = "No proposals yet.";
        row.append(cell);
        body.append(row);
      }
      appendSubmissionRows(result.data);
      document.body.classList.remove("is-loading");
      byId("submissions").closest("section").setAttribute("aria-busy", "false");
      renderLoadMore(Number(result.total ?? result.data.length));
      updatePrerequisites();
      const history = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds`);
      renderRoundHistory(history.data);
      const currentRound = history.data.find((round) => round.status === "open") || null;
      if (currentRound) showRound(currentRound);
    } catch (error) {
      document.body.classList.remove("is-loading");
      byId("submissions").closest("section").setAttribute("aria-busy", "false");
      if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error) || window.SessionBuddyApi.redirectIfDocumentAccessChanged(error)) return;
      byId("status").textContent = window.SessionBuddyApi.message(error, "Proposals could not be loaded. Return to the event and try again.");
      byId("status").classList.add("error");
    }
  }
  function appendSubmissionRows(items) {
    const body = byId("submissions");
    items.forEach((item) => {
        const row = document.createElement("tr");
        row.dataset.submissionId = item.id;
        row.dataset.track = String(item.routed_track || "").trim();
        const selectionCell = document.createElement("td");
        selectionCell.dataset.label = "Include";
        if (!item.evaluation_round_id && item.status !== "withdrawn") {
          const selection = document.createElement("input");
          selection.type = "checkbox";
          selection.name = "submission_ids";
          selection.value = item.id;
          selection.dataset.finalDecision = String(
            item.status === "accepted" || item.status === "rejected",
          );
          selection.checked = false;
          selection.setAttribute("aria-label", `Include ${item.proposal_title}`);
          selection.addEventListener("change", submissionSelectionChanged);
          selectionCell.append(selection);
        } else {
          const decided = document.createElement("span");
          decided.className = "proposal-selection-unavailable";
          decided.textContent = item.evaluation_round_name
            ? `Already in ${item.evaluation_round_name}`
            : "Unavailable";
          selectionCell.append(decided);
        }
        row.append(selectionCell);
        [["Speaker", item.speaker_name], ["Proposal", item.proposal_title], ["Status", item.status]].forEach(([label, value]) => {
          const cell = document.createElement("td");
          cell.dataset.label = label;
          if (label === "Proposal") {
            cell.className = "proposal-inbox__title";
            const identity = document.createElement("span");
            identity.className = "proposal-inbox__identity";
            identity.textContent = value;
            const metadata = document.createElement("small");
            metadata.textContent = `Submitted ${new Date(item.submitted_at_ms).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })} · Receipt ${item.id.slice(0, 8)}`;
            cell.append(identity, metadata);
          }
          if (label === "Status") {
            cell.className = `proposal-inbox__status proposal-inbox__status--${String(value).toLowerCase()}`;
            cell.append(document.createTextNode(value));
            if (item.reassessment_state === "under_review") {
              const reviewState = document.createElement("small");
              reviewState.textContent = `Under review in ${item.evaluation_round_name}`;
              cell.append(reviewState);
            }
          } else if (label !== "Proposal") cell.textContent = value;
          row.append(cell);
        });
        const detailCell = document.createElement("td");
        detailCell.dataset.label = "Actions";
        const detailButton = document.createElement("button");
        detailButton.type = "button";
        detailButton.className = "secondary";
        detailButton.textContent = "View proposal";
        detailButton.addEventListener("click", () => showSubmission(item, detailButton));
        detailCell.append(detailButton);
        row.append(detailCell);
        body.append(row);
      });
    renderTrackFilter();
    updateSelectedCount();
  }
  function renderLoadMore(total) {
    const shown = state.submissions.length;
    byId("status").textContent = state.nextCursor
      ? `Showing ${shown} of ${total} proposals.`
      : `${total} proposal${total === 1 ? "" : "s"}.`;
    let button = byId("load-more-submissions");
    if (!state.nextCursor) { if (button) button.remove(); return; }
    if (!button) {
      button = document.createElement("button");
      button.id = "load-more-submissions";
      button.type = "button";
      button.className = "secondary";
      byId("submissions").closest("table").after(button);
      button.addEventListener("click", () => loadMoreSubmissions(button));
    }
    button.textContent = `Load ${Math.min(100, total - shown)} more`;
    button.disabled = false;
  }
  async function loadMoreSubmissions(button) {
    button.disabled = true;
    button.textContent = "Loading…";
    try {
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions?cursor=${encodeURIComponent(state.nextCursor)}`);
      state.submissions = state.submissions.concat(result.data);
      state.nextCursor = result.next_cursor || null;
      appendSubmissionRows(result.data);
      renderLoadMore(Number(result.total ?? state.submissions.length));
      updatePrerequisites();
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error, "More submissions could not be loaded. Try again.");
      button.disabled = false;
      button.textContent = "Load more";
    }
  }
  function isDraftSubmission(form) {
    if (state.draftOnly) return true;
    return String(form.elements.round_status?.value || "open") === "draft";
  }
  function validateRound(form) {
    const minimum = Number(form.elements.rating_min.value);
    const maximum = Number(form.elements.rating_max.value);
    const recommendationInput = form.elements.recommendations;
    const recommendationPurposeRow = [...form.querySelectorAll(".criterion-row")]
      .find((row) => row.querySelector('[name="criterion_purpose"]')?.value === "recommendation");
    const recommendations = String(recommendationInput.value || "").split(",").map((choice) => choice.trim()).filter(Boolean);
    form.elements.rating_max.setCustomValidity(maximum > minimum ? "" : "Maximum rating must be greater than minimum rating.");
    const recommendationError = recommendations.length < 2 || recommendations.length > 8
      ? "Enter 2–8 recommendations."
      : recommendations.some((choice) => choice.length > 80)
        ? "Each recommendation must be at most 80 characters."
        : new Set(recommendations).size !== recommendations.length
          ? "Recommendations must be unique."
          : "";
    recommendationInput.setCustomValidity(recommendationPurposeRow ? "" : recommendationError);
    const opens = inputMillis(form.elements.review_opens_at.value);
    const closes = inputMillis(form.elements.review_closes_at.value);
    form.elements.review_opens_at.setCustomValidity(Number.isNaN(opens) ? `Choose a valid local time in ${state.timeZone}.` : "");
    form.elements.review_closes_at.setCustomValidity(Number.isNaN(closes)
      ? `Choose a valid local time in ${state.timeZone}.`
      : opens !== null && closes !== null && closes <= opens
        ? "Review close must be after review open."
        : "");
    // Weight-total invalidity must sit on a control the organizer can see. The first
    // weight input in the form is not that control when its row was switched to Dropdown
    // or Free text: updateType() hides it, reportValidity() then returns false on an
    // unfocusable input, and the submit dies with no bubble, no status text, and only a
    // "not focusable" warning in the console. Clear every weight input first, so a row
    // switched away from Score can never hold the form invalid from behind a hidden
    // label, then anchor the error to the first weight input that is still visible.
    form.querySelectorAll('input[name="criterion_weight"]').forEach((input) => input.setCustomValidity(""));
    const scorecard = scoredWeightTotal();
    if (!scorecard.count) {
      // Every weight input is hidden, so there is nothing to anchor to: this is a
      // scorecard-level problem and gets the scorecard-level error the server would
      // also raise ("a scorecard requires at least one scored criterion").
      showRoundError(
        "Keep at least one Score criterion. Dropdown and Free text collect answers, "
        + "but only Score criteria produce the round's weighted rating.",
      );
      return false;
    }
    if (scorecard.total !== 100) {
      scorecard.rows[0].querySelector('input[name="criterion_weight"]')
        .setCustomValidity(`Score criterion weights must total 100. They currently total ${scorecard.total}.`);
    }
    // Dropdown choices are checked before the payload leaves the browser: the server's
    // 422 for this names criteria[n].options, which no organizer can map back to a row.
    form.querySelectorAll(".criterion-row").forEach((row) => {
      const optionsInput = row.querySelector('[name="criterion_options"]');
      if (!optionsInput) return;
      if (row.querySelector('[name="criterion_type"]')?.value !== "select") {
        optionsInput.setCustomValidity("");
        return;
      }
      const options = optionsInput.value.split(",").map((option) => option.trim()).filter(Boolean);
      const isRecommendation = row.querySelector('[name="criterion_purpose"]')?.value === "recommendation";
      optionsInput.setCustomValidity(
        options.length < 2 || options.length > (isRecommendation ? 8 : 20)
          ? `Enter 2–${isRecommendation ? 8 : 20} comma-separated choices.`
          : options.some((option) => option.length > (isRecommendation ? 80 : 120))
            ? `Each choice must be at most ${isRecommendation ? 80 : 120} characters.`
            : new Set(options).size !== options.length
              ? "Choices must be unique."
              : "",
      );
    });
    const usedPurposes = new Set();
    for (const row of form.querySelectorAll(".criterion-row")) {
      const purpose = row.querySelector('[name="criterion_purpose"]');
      purpose.setCustomValidity("");
      if (!purpose.value) continue;
      if (usedPurposes.has(purpose.value)) {
        purpose.setCustomValidity(
          `Only one criterion can be the ${purpose.selectedOptions[0].text.toLowerCase()}.`,
        );
      }
      usedPurposes.add(purpose.value);
    }
    // A draft is a work in progress: it may be saved with no proposals and no reviewers
    // yet. The API applies the same rule, and refuses to OPEN a round with no
    // assignments, so the constraint lives at the point where it actually matters.
    const submissions = selectedSubmissionIds();
    const evaluators = [...form.querySelectorAll('input[name="evaluator_user_ids"]:checked')];
    if (!isDraftSubmission(form) && (!submissions.length || !evaluators.length)) {
      showRoundError(!submissions.length
        ? "Select at least one proposal."
        : "Select at least one reviewer.");
      return false;
    }
    // Drafts too, and this is the case that used to pass silently. The payload carries an
    // explicit pair list, so an empty one is stored as "nobody reviews anything" rather
    // than regenerated from the strategy: the round keeps its proposals and reviewers in
    // their membership tables, reports zero assignments everywhere the organizer can see
    // it, and cannot be opened. The API refuses this too; catching it here is what turns
    // a 422 into a sentence that says which box to tick.
    if (submissions.length && evaluators.length && !roundAssignments().length) {
      showRoundError(
        "Assign at least one proposal to a reviewer. Every proposal box under a reviewer "
        + "is currently unticked, so this round would be saved with nothing to review.",
      );
      return false;
    }
    return form.reportValidity();
  }
  byId("round-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  // Any deliberate edit inside the form, or any change to the proposal selection, counts
  // as intent to build the next round and lifts the post-save gate.
  byId("round-form").addEventListener("input", markRoundFormDirty);
  byId("round-form").addEventListener("change", markRoundFormDirty);
  byId("round-form").elements.round_status?.addEventListener("change", updatePrerequisites);
  byId("round-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.roundSubmitInFlight) return;
    // The post-save gate must hold HERE, not only via the button's disabled attribute.
    // Measured: an Enter keypress never reaches this handler (the disabled default button
    // absorbs it), but form.requestSubmit() and a dispatched submit event both do, with
    // roundSaved=true/dirty=false -- and were stopped only incidentally, by validation
    // failing against a form the reset had just emptied. Incidental is not a guard.
    if (state.roundSaved && !state.roundFormDirty) return;
    state.roundSubmitInFlight = true;
    const button = byId("open-round");
    button.disabled = true;
    try {
      if (!validateRound(event.currentTarget)) { button.disabled = false; return; }
      const values = new FormData(event.currentTarget);
      const recommendations = String(values.get("recommendations") || "").split(",").map((choice) => choice.trim()).filter(Boolean);
      const usedKeys = new Set();
      const criteria = [...event.currentTarget.querySelectorAll(".criterion-row")].map((row, index) => {
        const label = row.querySelector('[name="criterion_label"]').value.trim();
        const responseType = row.querySelector('[name="criterion_type"]').value;
        const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 32) || `criterion_${index + 1}`;
        let key = /^[a-z]/.test(base) ? base : `criterion_${base}`;
        while (usedKeys.has(key)) key = `${key.slice(0, 36)}_${index + 1}`;
        usedKeys.add(key);
        const options = row.querySelector('[name="criterion_options"]').value.split(",").map((option) => option.trim()).filter(Boolean);
        const criterion = { key, label, response_type: responseType, required: row.querySelector('[name="criterion_required"]').checked, weight: responseType === "score" ? Number(row.querySelector('[name="criterion_weight"]').value) : null, options: responseType === "select" ? options : [] };
        const purpose = row.querySelector('[name="criterion_purpose"]').value;
        if (purpose) criterion.purpose = purpose;
        return criterion;
      });
      const roundStatus = isDraftSubmission(event.currentTarget) ? "draft" : "open";
      const reviewOpens = inputMillis(String(values.get("review_opens_at") || ""));
      const reviewCloses = inputMillis(String(values.get("review_closes_at") || ""));
      const editingRoundId = state.editingRoundId;
      const endpoint = editingRoundId ? `/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds/${encodeURIComponent(editingRoundId)}/draft` : `/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds`;
      const headers = { "content-type": "application/json", "x-csrf-token": state.csrf };
      if (!editingRoundId) headers["idempotency-key"] = `${crypto.randomUUID()}-${crypto.randomUUID()}`;
      const round = await api(endpoint, {
        method: editingRoundId ? "PUT" : "POST",
        headers,
        body: JSON.stringify({ name: values.get("name"), rating_min: Number(values.get("rating_min")), rating_max: Number(values.get("rating_max")), recommendations, evaluator_guidance: values.get("evaluator_guidance"), comment_required: values.get("comment_required") === "on", criteria, blind_review: values.get("blind_review") === "on", review_opens_at_ms: reviewOpens, review_closes_at_ms: reviewCloses, assignment_strategy: values.get("assignment_strategy"), status: roundStatus, submission_ids: selectedSubmissionIds(), evaluator_user_ids: values.getAll("evaluator_user_ids"), assignments: roundAssignments() })
      });
      state.editingRoundId = null;
      byId("status").classList.remove("error");
      byId("status").textContent = round.status === "draft"
        ? `${round.name} saved as a draft with ${round.assignment_count} assignments across ${round.evaluator_count} reviewers. Open it when the current round closes.`
        : `${round.name} opened with ${round.assignment_count} assignments across ${round.evaluator_count} evaluators.`;
      renderRoundHistory([round, ...state.rounds.filter((item) => item.id !== round.id)]);
      if (round.status === "open") showRound(round);
      // Saving must leave the form usable for the NEXT round but empty of this one.
      // Usable, because preparing round two while round one runs is legal and the
      // organizer should not have to reload. Empty, because a form still holding the
      // round it just created is a duplicate one click away, and gives no way to tell
      // a save from a no-op. resetRoundForm() recomputes the button state too, so it
      // subsumes the updatePrerequisites() call this replaced.
      resetRoundForm();
      byId("status").focus();
    } catch (error) {
      showRoundError(window.SessionBuddyApi.message(error));
      button.disabled = false;
    } finally {
      state.roundSubmitInFlight = false;
    }
  });
  function appendCriterionRow(values = {}) {
    const container = byId("criteria");
    const row = document.createElement("div"); row.className = "form-grid criterion-row";
    const label = document.createElement("label"); label.textContent = "Criterion"; const name = document.createElement("input"); name.name = "criterion_label"; name.required = true; name.maxLength = 120; label.append(name);
    const weightLabel = document.createElement("label"); weightLabel.textContent = "Weight"; const weight = document.createElement("input"); weight.name = "criterion_weight"; weight.type = "number"; weight.min = "1"; weight.max = "100"; weight.required = true; weightLabel.append(weight);
    row.append(label, weightLabel); addRemoveButton(row); container.append(row);
    name.value = values.label ?? "";
    weight.value = values.weight ?? "";
    if (values.type) {
      row.querySelector('[name="criterion_type"]').value = values.type;
      row.querySelector('[name="criterion_options"]').value = values.options ?? "";
      row.querySelector('[name="criterion_required"]').checked = values.required !== false;
      row.querySelector('[name="criterion_purpose"]').value = values.purpose ?? "";
      row.querySelector('[name="criterion_purpose"]').dispatchEvent(new Event("change"));
      row.querySelector('[name="criterion_type"]').dispatchEvent(new Event("change"));
    }
    // The restored weight lands after addRemoveButton() already ran updateType(), so the
    // total shown would otherwise trail the row by one edit.
    updateScorecardTotal();
    return { row, name };
  }
  byId("add-criterion").addEventListener("click", () => {
    const container = byId("criteria");
    if (container.querySelectorAll(".criterion-row").length >= 8) { byId("status").textContent = "A scorecard can contain up to eight criteria."; return; }
    appendCriterionRow().name.focus();
  });
  // Captured once, after addRemoveButton() has augmented the markup rows, so a reset
  // restores the scorecard the organizer started from rather than whatever the last
  // round left behind.
  const defaultCriteria = [...byId("criteria").querySelectorAll(".criterion-row")].map((row) => ({
    label: row.querySelector('[name="criterion_label"]').value,
    weight: row.querySelector('[name="criterion_weight"]').value,
    type: row.querySelector('[name="criterion_type"]')?.value,
    options: row.querySelector('[name="criterion_options"]')?.value,
    required: row.querySelector('[name="criterion_required"]')?.checked,
    purpose: row.querySelector('[name="criterion_purpose"]')?.value,
  }));
  // A saved round must leave the form visibly spent. Without this the create form sits
  // there still holding the round it just created -- same name, same everything, button
  // enabled -- so one more click silently files a duplicate, and there is no way to tell
  // a successful save from a no-op because nothing on screen changed. Verified: two
  // clicks produced two identically named rounds.
  function resetRoundForm() {
    const form = byId("round-form");
    state.editingRoundId = null;
    state.roundSaved = true;
    state.roundFormDirty = false;
    form.reset();
    byId("criteria").replaceChildren();
    defaultCriteria.forEach((criterion) => appendCriterionRow(criterion));
    document.querySelectorAll('input[name="submission_ids"]').forEach((input) => { input.checked = false; });
    state.hiddenSubmissionIds = new Set();
    // The reviewer pool is per round. Carrying it over silently gives the next round a
    // pool the organizer never chose -- which is exactly what happened in the eval run.
    state.evaluators = [];
    state.pairs = {};
    renderEvaluatorChoices();
    const lookupStatus = byId("reviewer-lookup-status");
    if (lookupStatus) { lookupStatus.textContent = ""; lookupStatus.classList.remove("error"); }
    // form.reset() restores round_status to its markup default of "open", but
    // setDraftOnly() has disabled that option while another round runs -- leaving a
    // disabled option selected. Put the select back where setDraftOnly() had it.
    if (state.draftOnly && form.elements.round_status) form.elements.round_status.value = "draft";
    byId("open-round").textContent = state.draftOnly ? "Save draft round" : "Open evaluation round";
    updateSelectedCount();
    updatePrerequisites();
  }
  installReviewerLookup();
  load();
})();

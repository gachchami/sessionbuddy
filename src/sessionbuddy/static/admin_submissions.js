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
  const state = { csrf: "", userId: "", timeZone: "", submissions: [], evaluators: [], rounds: [], nextCursor: null, addRoundMutation: null, draftOnly: false };
  function selectedSubmissionIds() {
    return [...document.querySelectorAll('input[name="submission_ids"]:checked')].map((input) => input.value);
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
    state.evaluators.forEach((evaluator) => {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = "evaluator_user_ids";
      input.value = evaluator.user_id;
      input.checked = true;
      label.append(input, evaluator.display_name);
      evaluatorChoices.append(label);
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
    input.type = "email";
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
    button.addEventListener("click", async () => {
      if (!input.value.trim()) {
        input.setCustomValidity("Enter the reviewer's exact email address.");
      } else {
        input.setCustomValidity("");
      }
      if (!input.reportValidity()) return;
      status.textContent = "Checking that Reviewer account…";
      try {
        const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluators?email=${encodeURIComponent(input.value.trim())}`);
        const reviewer = result.data[0];
        if (!reviewer) {
          status.textContent = "No active Reviewer account matches that exact email. Invite them as a Reviewer first.";
          return;
        }
        if (!state.evaluators.some((item) => item.user_id === reviewer.user_id)) state.evaluators.push(reviewer);
        renderEvaluatorChoices();
        status.textContent = `${reviewer.display_name} added to this round.`;
        input.value = "";
      } catch (error) {
        status.textContent = window.SessionBuddyApi.message(error, "The Reviewer account could not be checked.");
      }
    });
  }
  function updateSelectedCount() {
    const count = selectedSubmissionIds().length;
    byId("selected-count").textContent = `${count} selected`;
    byId("configure-round").disabled = count === 0;
  }
  function setEligibleSelection(selected) {
    document.querySelectorAll('input[name="submission_ids"]:not(:disabled)').forEach((input) => {
      input.checked = selected;
    });
    updateSelectedCount();
  }
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
    if (!state.evaluators.length) missing.push("invite at least one reviewer");
    const draftOnly = state.draftOnly;
    prerequisites.textContent = draftOnly
      ? "A round is already open, so this one will be saved as a draft. You can add proposals and reviewers now or later."
      : missing.length
        ? `Before opening a round: ${missing.join("; ")}. You can still save a draft.`
        : "Choose proposals and reviewers, then open the round.";
    // Drafting is always available; only opening needs a proposal and a reviewer.
    byId("open-round").disabled = missing.length > 0 && !draftOnly && !isDraftSubmission(byId("round-form"));
  }
  function addRemoveButton(row) {
    if (row.querySelector("button")) return;
    const remove = document.createElement("button");
    remove.type = "button"; remove.className = "secondary"; remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      if (byId("criteria").querySelectorAll(".criterion-row").length <= 1) {
        byId("status").textContent = "Keep at least one scorecard criterion.";
        return;
      }
      row.remove();
    });
    row.append(remove);
  }
  byId("criteria").querySelectorAll(".criterion-row").forEach(addRemoveButton);
  const guidance = byId("round-form").elements.evaluator_guidance.closest("label");
  const commentRequired = document.createElement("label");
  commentRequired.className = "check-label";
  const commentRequiredInput = document.createElement("input");
  commentRequiredInput.type = "checkbox";
  commentRequiredInput.name = "comment_required";
  commentRequired.append(commentRequiredInput, " Require a written reviewer comment");
  guidance.after(commentRequired);
  function renderRoundHistory(rounds) {
    state.rounds = rounds;
    const container = byId("round-history");
    container.replaceChildren();
    if (!rounds.length) {
      const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "No evaluation rounds yet."; container.append(empty); return;
    }
    for (const round of rounds) {
      const card = document.createElement("article"); card.className = "entity-card";
      const heading = document.createElement("h3"); const link = document.createElement("a"); link.href = `/admin/evaluation-rounds/${encodeURIComponent(round.id)}`; link.textContent = round.name; heading.append(link);
      const summary = document.createElement("p"); summary.className = "result"; summary.textContent = `${round.status} · ${round.assignment_count} assignments · ${round.evaluator_count} reviewers`;
      const actions = document.createElement("div"); actions.className = "actions";
      const monitor = document.createElement("a"); monitor.className = "button secondary"; monitor.href = link.href; monitor.textContent = "Open";
      const exportLink = document.createElement("a"); exportLink.className = "button secondary"; exportLink.href = `/api/v1/admin/evaluation-rounds/${encodeURIComponent(round.id)}/export.csv`; exportLink.textContent = "Export CSV";
      actions.append(monitor, exportLink);
      if (round.status === "draft") {
        const openDraft = document.createElement("button");
        openDraft.type = "button"; openDraft.textContent = "Open round";
        openDraft.addEventListener("click", () => openDraftRound(round, openDraft));
        actions.append(openDraft);
      }
      card.append(heading, summary, actions); container.append(card);
    }
  }
  function showRound(round) {
    const link = document.createElement("a");
    link.href = `/admin/evaluation-rounds/${round.id}`;
    link.textContent = "Monitor round and record decisions";
    const add = document.createElement("button");
    add.type = "button";
    add.className = "secondary";
    add.textContent = "Add selected proposals to open round";
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
    actions.append(link, add);
    byId("round-result").replaceChildren(actions);
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
    label.append(document.createTextNode("Internal reason"));
    const reason = document.createElement("textarea");
    reason.rows = 3;
    reason.maxLength = 2000;
    label.append(reason);
    const help = document.createElement("p");
    help.className = "help";
    help.textContent = "Recorded for organizers only, never shown to the speaker. This decision is permanent.";
    const notifyLabel = document.createElement("label");
    notifyLabel.className = "check-label";
    const notify = document.createElement("input");
    notify.type = "checkbox";
    notify.checked = true;
    notifyLabel.append(notify, document.createTextNode(` Email ${item.speaker_name || "the speaker"}`));
    const speakerMessageLabel = document.createElement("label");
    speakerMessageLabel.append(document.createTextNode("Speaker message "));
    const optional = document.createElement("span");
    optional.className = "optional";
    optional.textContent = "Optional";
    speakerMessageLabel.append(optional);
    const speakerMessage = document.createElement("textarea");
    speakerMessage.rows = 3;
    speakerMessage.maxLength = 4000;
    speakerMessage.placeholder = "Leave blank to use the standard rejection message.";
    speakerMessageLabel.append(speakerMessage);
    notify.addEventListener("change", () => {
      speakerMessageLabel.hidden = !notify.checked;
    });
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
    buttons.className = "actions";
    // Keep the row clear of the dialog's bottom padding when it is scrolled into view.
    buttons.style.scrollMarginBottom = "1.5rem";
    buttons.append(confirmButton, cancelButton);
    panel.append(label, help, notifyLabel, speakerMessageLabel, message, buttons);
    // This panel sits at the bottom of a dialog that scrolls, and the alert sits
    // directly above the confirm/cancel row. Focusing the alert on its own parks its
    // bottom edge flush with the dialog's bottom edge, which leaves the buttons just
    // below the fold: still enabled, but invisible and not hit-testable, so the
    // organizer reads the guardrail as a dead end and escapes with Esc or a reload.
    // Scroll the action row into view instead -- it carries the alert with it.
    function showPanelError(text, focusTarget) {
      message.textContent = text;
      message.classList.add("error");
      focusTarget.focus({ preventScroll: true });
      buttons.scrollIntoView({ block: "nearest" });
    }
    trigger.addEventListener("click", () => {
      trigger.hidden = true;
      panel.hidden = false;
      reason.focus();
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
            send_email: notify.checked,
            speaker_message: notify.checked ? speakerMessage.value.trim() : "",
            override_incomplete_reviews: false,
          }),
        });
        byId("status").textContent = `“${item.proposal_title}” was rejected without review.${decision.communication_queued ? " Speaker email queued." : " No email sent."}`;
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
  function showSubmission(item, trigger) {
    const details = byId("submission-detail-list");
    details.replaceChildren(
      detailRow("Speaker", item.speaker_name),
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
      details.append(detailRow("Co-speakers", item.co_speakers.map((person) => `${person.display_name} (${person.email}) · ${person.role === "co_speaker" ? "Co-speaker" : person.role}`).join(", ")));
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
      decisionActions.append(rejectWithoutReviewControl(item));
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
      byId("status").textContent = window.SessionBuddyApi.message(error, "Proposals could not be loaded. Return to the event and try again.");
      byId("status").classList.add("error");
    }
  }
  function appendSubmissionRows(items) {
    const body = byId("submissions");
    items.forEach((item) => {
        const row = document.createElement("tr");
        const selectionCell = document.createElement("td");
        selectionCell.dataset.label = "Include";
        if (item.status === "submitted" && !item.evaluation_round_id) {
          const selection = document.createElement("input");
          selection.type = "checkbox";
          selection.name = "submission_ids";
          selection.value = item.id;
          selection.checked = false;
          selection.setAttribute("aria-label", `Include ${item.proposal_title}`);
          selection.addEventListener("change", updateSelectedCount);
          selectionCell.append(selection);
        } else {
          const decided = document.createElement("span");
          decided.className = "proposal-selection-unavailable";
          decided.textContent = item.evaluation_round_name
            ? `In ${item.evaluation_round_name}`
            : "Already decided";
          selectionCell.append(decided);
        }
        row.append(selectionCell);
        [["Speaker", item.speaker_name], ["Proposal", item.proposal_title], ["Status", item.status]].forEach(([label, value]) => {
          const cell = document.createElement("td");
          cell.dataset.label = label;
          if (label === "Proposal") cell.className = "proposal-inbox__title";
          if (label === "Status") cell.className = `proposal-inbox__status proposal-inbox__status--${String(value).toLowerCase()}`;
          cell.textContent = value;
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
    const recommendations = String(recommendationInput.value || "").split(",").map((choice) => choice.trim()).filter(Boolean);
    form.elements.rating_max.setCustomValidity(maximum > minimum ? "" : "Maximum rating must be greater than minimum rating.");
    const recommendationError = recommendations.length < 2 || recommendations.length > 8
      ? "Enter 2–8 recommendations."
      : recommendations.some((choice) => choice.length > 80)
        ? "Each recommendation must be at most 80 characters."
        : new Set(recommendations).size !== recommendations.length
          ? "Recommendations must be unique."
          : "";
    recommendationInput.setCustomValidity(recommendationError);
    const opens = inputMillis(form.elements.review_opens_at.value);
    const closes = inputMillis(form.elements.review_closes_at.value);
    form.elements.review_opens_at.setCustomValidity(Number.isNaN(opens) ? `Choose a valid local time in ${state.timeZone}.` : "");
    form.elements.review_closes_at.setCustomValidity(Number.isNaN(closes)
      ? `Choose a valid local time in ${state.timeZone}.`
      : opens !== null && closes !== null && closes <= opens
        ? "Review close must be after review open."
        : "");
    const weights = [...form.querySelectorAll('input[name="criterion_weight"]')].map((input) => Number(input.value));
    const weightError = weights.reduce((total, value) => total + value, 0) === 100 ? "" : "Criterion weights must total 100.";
    form.querySelector('input[name="criterion_weight"]')?.setCustomValidity(weightError);
    // A draft is a work in progress: it may be saved with no proposals and no reviewers
    // yet. The API applies the same rule, and refuses to OPEN a round with no
    // assignments, so the constraint lives at the point where it actually matters.
    if (!isDraftSubmission(form)) {
      const submissions = [...document.querySelectorAll('input[name="submission_ids"]:checked')];
      const evaluators = form.querySelectorAll('input[name="evaluator_user_ids"]:checked');
      if (!submissions.length || !evaluators.length) {
        showRoundError(!submissions.length
          ? "Select at least one proposal."
          : "Select at least one reviewer.");
        return false;
      }
    }
    return form.reportValidity();
  }
  byId("round-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  byId("round-form").elements.round_status?.addEventListener("change", updatePrerequisites);
  byId("round-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("open-round");
    button.disabled = true;
    try {
      if (!validateRound(event.currentTarget)) { button.disabled = false; return; }
      const values = new FormData(event.currentTarget);
      const recommendations = String(values.get("recommendations") || "").split(",").map((choice) => choice.trim()).filter(Boolean);
      const labels = values.getAll("criterion_label").map((value) => String(value).trim());
      const weights = values.getAll("criterion_weight").map(Number);
      const usedKeys = new Set();
      const criteria = labels.map((label, index) => {
        const base = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 32) || `criterion_${index + 1}`;
        let key = /^[a-z]/.test(base) ? base : `criterion_${base}`;
        while (usedKeys.has(key)) key = `${key.slice(0, 36)}_${index + 1}`;
        usedKeys.add(key);
        return { key, label, weight: weights[index] };
      });
      const roundStatus = isDraftSubmission(event.currentTarget) ? "draft" : "open";
      const reviewOpens = inputMillis(String(values.get("review_opens_at") || ""));
      const reviewCloses = inputMillis(String(values.get("review_closes_at") || ""));
      const round = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` },
        body: JSON.stringify({ name: values.get("name"), rating_min: Number(values.get("rating_min")), rating_max: Number(values.get("rating_max")), recommendations, evaluator_guidance: values.get("evaluator_guidance"), comment_required: values.get("comment_required") === "on", criteria, blind_review: values.get("blind_review") === "on", review_opens_at_ms: reviewOpens, review_closes_at_ms: reviewCloses, assignment_strategy: values.get("assignment_strategy"), status: roundStatus, submission_ids: selectedSubmissionIds(), evaluator_user_ids: values.getAll("evaluator_user_ids") })
      });
      byId("status").classList.remove("error");
      byId("status").textContent = round.status === "draft"
        ? `${round.name} saved as a draft with ${round.assignment_count} assignments across ${round.evaluator_count} reviewers. Open it when the current round closes.`
        : `${round.name} opened with ${round.assignment_count} assignments across ${round.evaluator_count} evaluators.`;
      renderRoundHistory([round, ...state.rounds.filter((item) => item.id !== round.id)]);
      if (round.status === "open") showRound(round);
      else byId("open-round").disabled = false;
    } catch (error) {
      showRoundError(window.SessionBuddyApi.message(error));
      button.disabled = false;
    }
  });
  byId("add-criterion").addEventListener("click", () => {
    const container = byId("criteria");
    if (container.querySelectorAll(".criterion-row").length >= 8) { byId("status").textContent = "A scorecard can contain up to eight criteria."; return; }
    const row = document.createElement("div"); row.className = "form-grid criterion-row";
    const label = document.createElement("label"); label.textContent = "Criterion"; const name = document.createElement("input"); name.name = "criterion_label"; name.required = true; name.maxLength = 120; label.append(name);
    const weightLabel = document.createElement("label"); weightLabel.textContent = "Weight"; const weight = document.createElement("input"); weight.name = "criterion_weight"; weight.type = "number"; weight.min = "1"; weight.max = "100"; weight.required = true; weightLabel.append(weight);
    row.append(label, weightLabel); addRemoveButton(row); container.append(row); name.focus();
  });
  installReviewerLookup();
  load();
})();

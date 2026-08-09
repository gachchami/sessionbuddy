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
  const parts = location.pathname.split("/").filter(Boolean);
  const programId = parts[2] || "";
  const state = { csrf: "", userId: "", submissions: [], evaluators: [] };
  function renderRoundHistory(rounds) {
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
      actions.append(monitor, exportLink); card.append(heading, summary, actions); container.append(card);
    }
  }
  function showRound(round) {
    const link = document.createElement("a");
    link.href = `/admin/evaluation-rounds/${round.id}`;
    link.textContent = "Monitor round and record decisions";
    byId("round-result").replaceChildren(link);
    byId("open-round").disabled = true;
    byId("round-fields").disabled = true;
  }
  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = { schema_version: 1, page_template: "/admin/programs/{program_id}/submissions", navigation_type: navigation?.type || "unknown", device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop", sampled: false, lcp_ms: null, inp_ms: null, cls: null, ttfb_ms: navigation?.responseStart ?? null, fcp_ms: null, route_transition_ms: null, critical_api_ms: Math.max(0, performance.now() - started), api_request_id: response.headers.get("x-request-id") };
  }
  async function api(path, options = {}) {
    const started = performance.now();
    return window.SessionBuddyApi.request(path, options, {
      onResponse: (response) => recordTelemetry(started, response)
    });
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
  function showSubmission(item, trigger) {
    const details = byId("submission-detail-list");
    details.replaceChildren(
      detailRow("Speaker", item.speaker_name),
      detailRow("Email", item.speaker_email),
      detailRow("Title", item.proposal_title),
      detailRow("Abstract", item.proposal_abstract),
      detailRow("Status", item.status),
      detailRow("Submitted", new Date(item.submitted_at_ms).toLocaleString()),
      detailRow("Routed category", item.routed_category),
      detailRow("Routed track", item.routed_track),
      detailRow("Review queue", item.routed_review_queue),
      ...Object.entries(item.answers || {}).map(([key, value]) => detailRow(humanize(key), answerText(value)))
    );
    const dialog = byId("submission-detail");
    dialog.addEventListener("close", () => trigger.focus(), { once: true });
    dialog.showModal();
  }
  async function load() {
    try {
      const session = await api("/api/v1/auth/session");
      state.csrf = session.csrf_token;
      state.userId = session.user_id;
      const result = await api(`/api/v1/admin/programs/${encodeURIComponent(programId)}/submissions`);
      state.submissions = result.data;
      byId("cfp-workspace-link").href = `/admin/events/${encodeURIComponent(result.event_id)}/cfp`;
      const evaluatorResult = await api(`/api/v1/admin/programs/${encodeURIComponent(programId)}/evaluators`);
      state.evaluators = evaluatorResult.data;
      const evaluatorChoices = byId("evaluators");
      evaluatorChoices.replaceChildren();
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
      const body = byId("submissions");
      body.replaceChildren();
      if (!result.data.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 6;
        cell.textContent = "No submissions yet.";
        row.append(cell);
        body.append(row);
      }
      result.data.forEach((item) => {
        const row = document.createElement("tr");
        const selectionCell = document.createElement("td");
        const selection = document.createElement("input");
        selection.type = "checkbox";
        selection.name = "submission_ids";
        selection.value = item.id;
        selection.checked = true;
        selection.setAttribute("aria-label", `Include ${item.proposal_title}`);
        selectionCell.append(selection);
        row.append(selectionCell);
        [item.speaker_name, item.proposal_title, item.proposal_abstract, item.status].forEach((value) => {
          const cell = document.createElement("td");
          cell.textContent = value;
          row.append(cell);
        });
        const detailCell = document.createElement("td");
        const detailButton = document.createElement("button");
        detailButton.type = "button";
        detailButton.className = "secondary";
        detailButton.textContent = "View details";
        detailButton.addEventListener("click", () => showSubmission(item, detailButton));
        detailCell.append(detailButton);
        row.append(detailCell);
        body.append(row);
      });
      byId("status").textContent = `${result.data.length} submission${result.data.length === 1 ? "" : "s"}.`;
      byId("open-round").disabled = !result.data.length || !state.evaluators.length;
      const history = await api(`/api/v1/admin/programs/${encodeURIComponent(programId)}/evaluation-rounds`);
      renderRoundHistory(history.data);
      const currentRound = history.data.find((round) => round.status === "open") || null;
      if (currentRound) showRound(currentRound);
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error, "Submissions could not be loaded. Return to Programs and try again.");
      byId("status").classList.add("error");
    }
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
    const opens = form.elements.review_opens_at.value ? new Date(form.elements.review_opens_at.value).getTime() : null;
    const closes = form.elements.review_closes_at.value ? new Date(form.elements.review_closes_at.value).getTime() : null;
    form.elements.review_closes_at.setCustomValidity(opens !== null && closes !== null && closes <= opens ? "Review close must be after review open." : "");
    const weights = [...form.querySelectorAll('input[name="criterion_weight"]')].map((input) => Number(input.value));
    const weightError = weights.reduce((total, value) => total + value, 0) === 100 ? "" : "Criterion weights must total 100.";
    form.querySelector('input[name="criterion_weight"]')?.setCustomValidity(weightError);
    const submissions = [...document.querySelectorAll('input[name="submission_ids"]:checked')];
    const evaluators = form.querySelectorAll('input[name="evaluator_user_ids"]:checked');
    if (!submissions.length || !evaluators.length) {
      byId("status").textContent = !submissions.length
        ? "Select at least one submission."
        : "Select at least one reviewer.";
      byId("status").classList.add("error");
      (submissions.length ? byId("evaluators") : byId("submissions")).focus?.();
      return false;
    }
    return form.reportValidity();
  }
  byId("round-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
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
      const reviewOpens = values.get("review_opens_at") ? new Date(String(values.get("review_opens_at"))).getTime() : null;
      const reviewCloses = values.get("review_closes_at") ? new Date(String(values.get("review_closes_at"))).getTime() : null;
      const round = await api(`/api/v1/admin/programs/${encodeURIComponent(programId)}/evaluation-rounds`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` },
        body: JSON.stringify({ name: values.get("name"), rating_min: Number(values.get("rating_min")), rating_max: Number(values.get("rating_max")), recommendations, evaluator_guidance: values.get("evaluator_guidance"), criteria, blind_review: values.get("blind_review") === "on", review_opens_at_ms: reviewOpens, review_closes_at_ms: reviewCloses, assignment_strategy: values.get("assignment_strategy"), submission_ids: [...document.querySelectorAll('input[name="submission_ids"]:checked')].map((input) => input.value), evaluator_user_ids: values.getAll("evaluator_user_ids") })
      });
      byId("status").textContent = `${round.name} opened with ${round.assignment_count} assignments across ${round.evaluator_count} evaluators.`;
      showRound(round);
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
      button.disabled = false;
    }
  });
  byId("add-criterion").addEventListener("click", () => {
    const container = byId("criteria");
    if (container.querySelectorAll(".criterion-row").length >= 8) { byId("status").textContent = "A scorecard can contain up to eight criteria."; return; }
    const row = document.createElement("div"); row.className = "form-grid criterion-row";
    const label = document.createElement("label"); label.textContent = "Criterion"; const name = document.createElement("input"); name.name = "criterion_label"; name.required = true; name.maxLength = 120; label.append(name);
    const weightLabel = document.createElement("label"); weightLabel.textContent = "Weight"; const weight = document.createElement("input"); weight.name = "criterion_weight"; weight.type = "number"; weight.min = "1"; weight.max = "100"; weight.required = true; weightLabel.append(weight);
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary"; remove.textContent = "Remove"; remove.addEventListener("click", () => row.remove());
    row.append(label, weightLabel, remove); container.append(row); name.focus();
  });
  load();
})();

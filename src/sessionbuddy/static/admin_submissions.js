(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const parts = location.pathname.split("/").filter(Boolean);
  const programId = parts[2] || "";
  const state = { csrf: "", userId: "", submissions: [], evaluators: [] };
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
    const response = await fetch(path, options);
    recordTelemetry(started, response);
    const body = await response.json();
    if (!response.ok) throw new Error(body.error?.message || `Request failed (${response.status})`);
    return body;
  }
  async function load() {
    try {
      const session = await api("/api/v1/demo/session", { method: "POST" });
      state.csrf = session.csrf_token;
      state.userId = session.user_id;
      const result = await api(`/api/v1/admin/programs/${encodeURIComponent(programId)}/submissions`);
      state.submissions = result.data;
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
        cell.colSpan = 5;
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
        body.append(row);
      });
      byId("status").textContent = `${result.data.length} submission${result.data.length === 1 ? "" : "s"}.`;
      byId("open-round").disabled = !result.data.length || !state.evaluators.length;
      const currentRound = await api(`/api/v1/admin/programs/${encodeURIComponent(programId)}/evaluation-rounds/current`);
      if (currentRound) showRound(currentRound);
    } catch (error) {
      byId("status").textContent = `${error.message}. Return to Programs and sign in.`;
      byId("status").classList.add("error");
    }
  }
  byId("round-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("open-round");
    button.disabled = true;
    try {
      const values = new FormData(event.currentTarget);
      const recommendations = String(values.get("recommendations") || "").split(",").map((choice) => choice.trim()).filter(Boolean);
      const round = await api(`/api/v1/admin/programs/${encodeURIComponent(programId)}/evaluation-rounds`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` },
        body: JSON.stringify({ name: values.get("name"), rating_min: Number(values.get("rating_min")), rating_max: Number(values.get("rating_max")), recommendations, evaluator_guidance: values.get("evaluator_guidance"), assignment_strategy: values.get("assignment_strategy"), submission_ids: [...document.querySelectorAll('input[name="submission_ids"]:checked')].map((input) => input.value), evaluator_user_ids: values.getAll("evaluator_user_ids") })
      });
      byId("status").textContent = `${round.name} opened with ${round.assignment_count} assignments across ${round.evaluator_count} evaluators.`;
      showRound(round);
    } catch (error) {
      byId("status").textContent = error.message;
      byId("status").classList.add("error");
      button.disabled = false;
    }
  });
  load();
})();

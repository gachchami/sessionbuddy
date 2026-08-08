(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const slug = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const publicSession = crypto.randomUUID();
  let csrf = "";
  let draftVersion = 0;
  let applyFormConditions = () => {};
  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = { schema_version: 1, page_template: "/cfp/{slug}", navigation_type: navigation?.type || "unknown", device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop", sampled: false, lcp_ms: null, inp_ms: null, cls: null, ttfb_ms: navigation?.responseStart ?? null, fcp_ms: null, route_transition_ms: null, critical_api_ms: Math.max(0, performance.now() - started), api_request_id: response.headers.get("x-request-id") };
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
      const form = await api(`/api/v1/forms/${encodeURIComponent(slug)}`);
      byId("welcome").textContent = form.welcome_text;
      renderFields(form.fields || [], form.conditions || []);
      const sessionResponse = await fetch("/api/v1/auth/session");
      if (sessionResponse.ok) {
        const session = await sessionResponse.json();
        csrf = session.csrf_token;
        byId("proposal-card").hidden = false;
        byId("status").textContent = "Signed in. Your draft can be saved on this device or another one.";
        await loadDraft();
      } else {
        byId("sign-in-card").hidden = false;
        byId("status").textContent = "Sign in with your email address to begin.";
      }
    } catch (error) {
      byId("status").textContent = error.message;
      byId("status").classList.add("error");
    }
  }
  function renderFields(fields, conditions) {
    const container = byId("dynamic-fields");
    container.replaceChildren();
    for (const field of fields) {
      const label = document.createElement("label");
      label.append(document.createTextNode(field.label));
      let input;
      if (field.type === "textarea") {
        input = document.createElement("textarea");
        input.maxLength = 5000;
      } else if (field.type === "select") {
        input = document.createElement("select");
        const empty = document.createElement("option");
        empty.value = "";
        empty.textContent = "Choose…";
        input.append(empty);
        for (const choice of field.choices || []) {
          const option = document.createElement("option");
          option.value = choice;
          option.textContent = choice;
          input.append(option);
        }
      } else {
        input = document.createElement("input");
        input.type = field.type === "url" || field.type === "email" ? field.type : "text";
        input.maxLength = 500;
      }
      input.name = field.key;
      input.required = Boolean(field.required);
      input.dataset.required = field.required ? "true" : "false";
      if (field.key === "speaker_name") input.autocomplete = "name";
      if (field.key === "speaker_email") input.autocomplete = "email";
      label.append(input);
      if (field.help_text) {
        const help = document.createElement("span");
        help.className = "help";
        help.textContent = field.help_text;
        label.append(help);
      }
      container.append(label);
    }
    function applyConditions() {
      for (const condition of conditions) {
        const source = byId("proposal-form").elements.namedItem(condition.source_key);
        const target = byId("proposal-form").elements.namedItem(condition.target_key);
        if (!source || !target) continue;
        const matches = String(source.value) === condition.value;
        const visible = condition.operator === "equals" ? matches : !matches;
        target.closest("label").hidden = !visible;
        target.disabled = !visible;
        target.required = visible && target.dataset.required === "true";
      }
    }
    applyFormConditions = applyConditions;
    container.addEventListener("input", applyConditions);
    container.addEventListener("change", applyConditions);
    applyConditions();
  }
  function answers() { return Object.fromEntries(new FormData(byId("proposal-form"))); }
  async function loadDraft() {
    const response = await fetch(`/api/v1/forms/${encodeURIComponent(slug)}/draft`);
    if (!response.ok) return;
    const draft = await response.json();
    if (!draft) return;
    draftVersion = draft.version;
    for (const [name, value] of Object.entries(draft.answers)) {
      const field = byId("proposal-form").elements.namedItem(name);
      if (field) field.value = value;
    }
    applyFormConditions();
    byId("status").textContent = "Your saved draft has been restored.";
  }
  byId("sign-in-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const email = new FormData(event.currentTarget).get("email");
    try {
      await api("/api/v1/auth/magic-links", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, form_slug: slug, redirect_path: location.pathname }) });
      byId("status").textContent = "Check your email for the one-time sign-in link. It expires in 15 minutes.";
    } catch (error) { byId("status").textContent = error.message; }
  });
  byId("save-draft").addEventListener("click", async () => {
    try {
      const draft = await api(`/api/v1/forms/${encodeURIComponent(slug)}/draft`, { method: "PUT", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify({ answers: answers(), version: draftVersion }) });
      draftVersion = draft.version;
      byId("status").textContent = "Draft saved.";
    } catch (error) { byId("status").textContent = error.message; }
  });
  byId("proposal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = byId("submit-proposal");
    button.disabled = true;
    button.textContent = "Submitting…";
    byId("status").classList.remove("error");
    try {
      const values = answers();
      const { speaker_name, speaker_email, proposal_title, proposal_abstract } = values;
      const submission = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}`, "x-public-session-id": publicSession }, body: JSON.stringify({ speaker_name, speaker_email, proposal_title, proposal_abstract, answers: values }) });
      byId("status").textContent = "Proposal submitted.";
      byId("receipt").textContent = `Receipt ${submission.id} · ${submission.status}`;
      form.querySelectorAll("input, textarea, select, button").forEach((field) => { field.disabled = true; });
    } catch (error) {
      byId("status").textContent = error.message;
      byId("status").classList.add("error");
      byId("status").focus();
      button.disabled = false;
      button.textContent = "Submit proposal";
    }
  });
  load();
})();

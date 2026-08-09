(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const slug = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const state = {
    csrf: "", form: null, draftVersion: 0, publicSession: crypto.randomUUID(),
    files: new Map(), uploaded: new Map(), applyConditions: () => {}
  };

  function setStatus(message, kind = "") {
    const node = byId("status");
    node.textContent = message;
    node.className = `status${kind ? ` ${kind}` : ""}`;
  }

  function setStep(step) {
    ["details", "review", "done"].forEach((name) => byId(`step-${name}`).removeAttribute("aria-current"));
    byId(`step-${step}`).setAttribute("aria-current", "step");
  }

  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = {
      schema_version: 1, page_template: "/cfp/{slug}", navigation_type: navigation?.type || "unknown",
      device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop", sampled: false,
      lcp_ms: null, inp_ms: null, cls: null, ttfb_ms: navigation?.responseStart ?? null, fcp_ms: null,
      route_transition_ms: null, critical_api_ms: Math.max(0, performance.now() - started),
      api_request_id: response.headers.get("x-request-id")
    };
  }

  async function api(path, options = {}) {
    const started = performance.now();
    const response = await fetch(path, { credentials: "same-origin", ...options });
    recordTelemetry(started, response);
    let body = null;
    try { body = await response.json(); } catch (_) { body = null; }
    if (!response.ok) {
      const error = new Error(body?.error?.message || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function make(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function fieldValue(field) {
    const control = byId("proposal-form").elements.namedItem(field.key);
    if (!control) return null;
    if (field.type === "checkbox") return Boolean(control.checked);
    if (field.type === "multiselect") return [...control.selectedOptions].map((option) => option.value);
    if (["file", "image"].includes(field.type)) return state.uploaded.get(field.key) || "";
    return control.value;
  }

  function answers({ includeUploads = true } = {}) {
    const result = {};
    for (const field of state.form?.fields || []) {
      const value = fieldValue(field);
      if (!includeUploads && ["file", "image"].includes(field.type)) continue;
      result[field.key] = value;
    }
    return result;
  }

  function conditionMatches(condition) {
    const actual = fieldValue({ key: condition.source_key, type: state.form.fields.find((field) => field.key === condition.source_key)?.type });
    const comparable = Array.isArray(actual) ? actual : [String(actual ?? "")];
    const matches = comparable.includes(condition.value);
    return condition.operator === "equals" ? matches : !matches;
  }

  function renderFields(fields, conditions) {
    const container = byId("dynamic-fields");
    container.replaceChildren();
    for (const field of fields) {
      const label = make("label", field.label);
      if (field.required) label.append(make("span", "Required", "required-marker"));
      label.dataset.fieldKey = field.key;
      let input;
      if (field.type === "textarea") {
        input = document.createElement("textarea");
        input.maxLength = 5000;
      } else if (["select", "multiselect"].includes(field.type)) {
        input = document.createElement("select");
        input.multiple = field.type === "multiselect";
        if (!input.multiple) input.append(new Option("Choose…", ""));
        for (const choice of field.choices || []) input.add(new Option(choice, choice));
      } else if (field.type === "checkbox") {
        input = document.createElement("input");
        input.type = "checkbox";
        label.classList.add("check-label");
      } else if (["file", "image"].includes(field.type)) {
        input = document.createElement("input");
        input.type = "file";
        input.accept = field.type === "image" ? "image/jpeg,image/png,image/webp" : "application/pdf";
        input.addEventListener("change", () => {
          const file = input.files?.[0];
          if (file) state.files.set(field.key, file); else state.files.delete(field.key);
          state.uploaded.delete(field.key);
        });
      } else {
        input = document.createElement("input");
        input.type = ["email", "url", "tel"].includes(field.type === "phone" ? "tel" : field.type)
          ? (field.type === "phone" ? "tel" : field.type) : "text";
        input.maxLength = 500;
      }
      input.name = field.key;
      input.id = `field-${field.key}`;
      input.required = Boolean(field.required);
      input.dataset.required = field.required ? "true" : "false";
      if (field.placeholder && !["select", "multiselect", "checkbox", "file", "image"].includes(field.type)) input.placeholder = field.placeholder;
      if (field.key === "speaker_name") input.autocomplete = "name";
      if (field.key === "speaker_email") input.autocomplete = "email";
      label.append(input);
      if (field.help_text) label.append(make("small", field.help_text));
      container.append(label);
    }
    state.applyConditions = () => {
      for (const field of fields) {
        const related = conditions.filter((condition) => condition.target_key === field.key);
        const visible = related.every(conditionMatches);
        const wrapper = container.querySelector(`[data-field-key="${CSS.escape(field.key)}"]`);
        const control = byId("proposal-form").elements.namedItem(field.key);
        if (!wrapper || !control) continue;
        wrapper.hidden = !visible;
        control.disabled = !visible;
        control.required = visible && control.dataset.required === "true";
      }
    };
    container.addEventListener("input", state.applyConditions);
    container.addEventListener("change", state.applyConditions);
    state.applyConditions();
  }

  function restoreValues(values) {
    for (const field of state.form.fields || []) {
      const control = byId("proposal-form").elements.namedItem(field.key);
      const value = values[field.key];
      if (!control || value === undefined || ["file", "image"].includes(field.type)) continue;
      if (field.type === "checkbox") control.checked = Boolean(value);
      else if (field.type === "multiselect" && Array.isArray(value)) {
        [...control.options].forEach((option) => { option.selected = value.includes(option.value); });
      } else control.value = String(value ?? "");
    }
    state.applyConditions();
  }

  async function loadDraft() {
    const response = await fetch(`/api/v1/forms/${encodeURIComponent(slug)}/draft`, { credentials: "same-origin" });
    if (!response.ok) return;
    const draft = await response.json();
    if (!draft) return;
    state.draftVersion = draft.version;
    restoreValues(draft.answers || {});
    setStatus("Your saved draft has been restored.");
  }

  function renderReview() {
    const list = byId("review-list");
    list.replaceChildren();
    for (const field of state.form.fields) {
      const wrapper = byId("proposal-form").elements.namedItem(field.key)?.closest("label");
      if (wrapper?.hidden) continue;
      let value = ["file", "image"].includes(field.type)
        ? state.files.get(field.key)?.name || "No file selected" : fieldValue(field);
      if (typeof value === "boolean") value = value ? "Yes" : "No";
      if (Array.isArray(value)) value = value.join(", ");
      const group = document.createElement("div");
      group.append(make("dt", field.label), make("dd", String(value || "Not provided")));
      list.append(group);
    }
  }

  function showReview(reviewing) {
    byId("dynamic-fields").hidden = reviewing;
    byId("review-panel").hidden = !reviewing;
    byId("save-draft").hidden = reviewing;
    byId("review-proposal").hidden = reviewing;
    byId("back-to-form").hidden = !reviewing;
    byId("submit-proposal").hidden = !reviewing;
    setStep(reviewing ? "review" : "details");
    if (reviewing) renderReview();
  }

  function safeUploadUrl(value) {
    try {
      const url = new URL(value, location.origin);
      const trustedR2 = url.protocol === "https:" && url.hostname.endsWith(".r2.cloudflarestorage.com");
      return url.origin === location.origin || trustedR2 ? url : null;
    } catch (_) { return null; }
  }

  async function checksum(file) {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  async function uploadAnswer(field, file) {
    const kind = field.type === "image" ? "headshot" : "supporting_document";
    const max = field.type === "image" ? 5 * 1024 * 1024 : 20 * 1024 * 1024;
    if (file.size <= 0 || file.size > max) throw new Error(`${field.label} is too large.`);
    const authorization = await api(`/api/v1/speaker/events/${encodeURIComponent(state.form.event_id)}/upload-authorizations`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` },
      body: JSON.stringify({ kind, submission_id: null, task_id: null, filename: file.name, content_type: file.type, byte_size: file.size, checksum_sha256: await checksum(file) })
    });
    const uploadUrl = safeUploadUrl(authorization.upload_url);
    if (!uploadUrl || authorization.expires_at_ms <= Date.now()) throw new Error("The upload authorization expired.");
    const upload = await fetch(uploadUrl, { method: "PUT", headers: { "content-type": file.type, ...(authorization.headers || {}) }, body: file });
    if (!upload.ok) throw new Error(`Upload failed (${upload.status}).`);
    const completionPath = `/api/v1/speaker/events/${encodeURIComponent(state.form.event_id)}/upload-intents/${encodeURIComponent(authorization.intent_id)}/complete`;
    let completion = await api(completionPath, {
      method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` }, body: "{}"
    });
    for (let attempt = 0; ["uploaded", "scanning"].includes(completion.state) && attempt < 60; attempt += 1) {
      setStatus(`Checking ${field.label} for safety…`);
      await new Promise((resolve) => setTimeout(resolve, 1000));
      completion = await api(completionPath, {
        method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` }, body: "{}"
      });
    }
    if (completion.state === "rejected") throw new Error(`${field.label} did not pass the safety check.`);
    if (completion.state !== "clean") throw new Error(`${field.label} is still being checked. Try again in a moment.`);
    state.uploaded.set(field.key, `upload:${authorization.intent_id}`);
  }

  async function uploadFiles() {
    for (const field of state.form.fields.filter((item) => ["file", "image"].includes(item.type))) {
      const file = state.files.get(field.key);
      if (file && !state.uploaded.has(field.key)) {
        setStatus(`Uploading ${field.label}…`);
        await uploadAnswer(field, file);
      }
    }
  }

  async function load() {
    try {
      state.form = await api(`/api/v1/forms/${encodeURIComponent(slug)}`);
      byId("welcome").textContent = state.form.welcome_text;
      if (state.form.event_name) {
        byId("brand-name").textContent = state.form.event_name;
        byId("title").textContent = `Submit to ${state.form.event_name}`;
      }
      if (state.form.accent_color) document.documentElement.style.setProperty("--blue", state.form.accent_color);
      if (state.form.logo_url) { byId("event-logo").src = state.form.logo_url; byId("event-logo").hidden = false; }
      renderFields(state.form.fields || [], state.form.conditions || []);
      if (state.form.accepting_submissions === false) {
        byId("closed-card").hidden = false;
        byId("availability").textContent = state.form.availability_message;
        setStatus(state.form.availability_message);
        return;
      }
      const sessionResponse = await fetch("/api/v1/auth/session", { credentials: "same-origin" });
      if (sessionResponse.ok) {
        const session = await sessionResponse.json();
        state.csrf = session.csrf_token;
        byId("proposal-card").hidden = false;
        setStatus("Your proposal is ready to edit.");
        await loadDraft();
      } else {
        byId("sign-in-card").hidden = false;
        setStatus("Sign in with your email address to begin.");
      }
    } catch (error) { setStatus(error.message, "error"); }
  }

  byId("sign-in-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const email = new FormData(form).get("email");
    const button = form.querySelector("button");
    form.elements.email.disabled = true;
    button.disabled = true;
    button.textContent = "Sending…";
    try {
      await api("/api/v1/auth/magic-links", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, form_slug: slug, redirect_path: location.pathname }) });
      byId("cfp-sent-message").textContent = `We sent a sign-in link to ${email}.`;
      byId("cfp-sign-in-entry").hidden = true;
      byId("cfp-sign-in-sent").hidden = false;
      byId("cfp-sign-in-sent").focus();
      setStatus("Check your email for the one-time sign-in link. It expires in 15 minutes.", "success");
    } catch (error) {
      form.elements.email.disabled = false;
      button.disabled = false;
      button.textContent = "Sign in";
      setStatus(error.message, "error");
    }
  });

  byId("change-cfp-email").addEventListener("click", () => {
    const form = byId("sign-in-form");
    const button = form.querySelector("button");
    byId("cfp-sign-in-sent").hidden = true;
    byId("cfp-sign-in-entry").hidden = false;
    form.elements.email.disabled = false;
    button.disabled = false;
    button.textContent = "Sign in";
    form.elements.email.focus();
    form.elements.email.select();
    setStatus("Sign in with your email address to begin.");
  });

  byId("save-draft").addEventListener("click", async () => {
    try {
      const draft = await api(`/api/v1/forms/${encodeURIComponent(slug)}/draft`, { method: "PUT", headers: { "content-type": "application/json", "x-csrf-token": state.csrf }, body: JSON.stringify({ answers: answers({ includeUploads: false }), version: state.draftVersion }) });
      state.draftVersion = draft.version;
      setStatus("Draft saved.", "success");
    } catch (error) { setStatus(error.message, "error"); }
  });

  byId("review-proposal").addEventListener("click", () => {
    if (!byId("proposal-form").reportValidity()) return;
    showReview(true);
    setStatus("Not submitted yet. Review your proposal, then select Confirm submission.");
    byId("review-title").focus?.();
  });
  byId("back-to-form").addEventListener("click", () => { showReview(false); setStatus("You can continue editing your proposal."); });

  byId("proposal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = byId("submit-proposal");
    button.disabled = true;
    button.textContent = "Submitting…";
    try {
      await uploadFiles();
      const values = answers();
      const submission = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}`, "x-public-session-id": state.publicSession },
        body: JSON.stringify({ speaker_name: values.speaker_name, speaker_email: values.speaker_email, proposal_title: values.proposal_title, proposal_abstract: values.proposal_abstract, answers: values })
      });
      setStep("done");
      form.hidden = true;
      const receipt = byId("receipt");
      receipt.className = "empty-state";
      receipt.replaceChildren(make("h2", state.form.success_title), make("p", state.form.success_message), make("p", `Receipt ${submission.id}`));
      if (state.form.redirect_to_portal) {
        const link = make("a", "Open speaker portal", "button");
        link.href = "/speaker";
        receipt.append(link);
      }
      setStatus("Proposal submitted successfully.", "success");
    } catch (error) {
      setStatus(error.status === 422 ? "A required answer is missing or invalid. Go back and review every required field." : error.message, "error");
      byId("status").focus();
      button.disabled = false;
      button.textContent = "Confirm submission";
    }
  });

  load();
})();

(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const slug = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
  const state = {
    csrf: "", form: null, draftVersion: 0, publicSession: crypto.randomUUID(),
    files: new Map(), uploaded: new Map(), applyConditions: () => {},
    sessionEmail: "", editingSubmission: null, submissions: []
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
    return window.SessionBuddyApi.request(path, options, {
      onResponse: (response) => recordTelemetry(started, response)
    });
  }

  function make(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function renderCallDetails(form) {
    const dates = [];
    if (form.opens_at_ms) dates.push(`Opens ${new Date(form.opens_at_ms).toLocaleString()}`);
    if (form.closes_at_ms) dates.push(`Closes ${new Date(form.closes_at_ms).toLocaleString()}`);
    byId("call-deadline").textContent = dates.length
      ? `${dates.join(" · ")}. Times are shown in your local time zone.`
      : "The organizer has not set a closing date.";
    byId("call-fields").replaceChildren(...form.fields.map((field) =>
      make("li", `${field.label}${field.required ? " (required)" : ""}`)
    ));
    byId("call-details").hidden = false;
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

  function addCoSpeakerRow(value = {}) {
    const row = make("div", undefined, "form-grid co-speaker-row");
    const nameLabel = make("label", "Name"); const name = document.createElement("input");
    name.name = "co_speaker_name"; name.required = true; name.maxLength = 200; name.value = value.display_name || ""; nameLabel.append(name);
    const emailLabel = make("label", "Email"); const email = document.createElement("input");
    email.name = "co_speaker_email"; email.type = "email"; email.required = true; email.maxLength = 320; email.value = value.email || ""; emailLabel.append(email);
    const role = make("p", "Role: Co-speaker", "help");
    const remove = make("button", "Remove", "secondary"); remove.type = "button"; remove.addEventListener("click", () => row.remove());
    row.append(nameLabel, emailLabel, role, remove); byId("co-speaker-rows").append(row);
  }

  function coSpeakers() {
    return [...document.querySelectorAll(".co-speaker-row")].map((row) => ({
      display_name: row.querySelector('[name="co_speaker_name"]').value.trim(),
      email: row.querySelector('[name="co_speaker_email"]').value.trim(),
      role: "co_speaker"
    }));
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
    let draft;
    try {
      draft = await api(`/api/v1/forms/${encodeURIComponent(slug)}/draft`);
    } catch (error) {
      if ([401, 404].includes(error.status)) return;
      throw error;
    }
    if (!draft) return;
    state.draftVersion = draft.version;
    restoreValues(draft.answers || {});
    setStatus("Your saved draft has been restored.");
  }

  function lockSignedInEmail() {
    const email = byId("proposal-form").elements.namedItem("speaker_email");
    if (!email || !state.sessionEmail) return;
    email.value = state.sessionEmail;
    email.readOnly = true;
    email.setAttribute("aria-describedby", "signed-in-email-help");
    let help = byId("signed-in-email-help");
    if (!help) {
      help = make("small");
      help.id = "signed-in-email-help";
      email.closest("label")?.append(help);
    }
    help.textContent = `Signed in as ${state.sessionEmail}.`;
  }

  function chooseSubmission(submission) {
    state.editingSubmission = submission;
    restoreValues({ ...submission.answers, speaker_name: submission.speaker_name,
      speaker_email: submission.speaker_email, proposal_title: submission.proposal_title,
      proposal_abstract: submission.proposal_abstract });
    lockSignedInEmail();
    byId("co-speaker-rows").replaceChildren();
    (submission.co_speakers || []).forEach(addCoSpeakerRow);
    byId("submit-proposal").textContent = "Save changes";
    setStatus(`Editing “${submission.proposal_title}”. Changes update this proposal; they do not create a duplicate.`, "success");
  }

  function renderExistingSubmissions() {
    const card = byId("proposal-card");
    card.querySelector(".existing-submissions")?.remove();
    if (!state.submissions.length) return;
    const section = make("section", undefined, "existing-submissions");
    section.append(make("h2", "Your proposals"), make("p", "Edit an existing proposal or start another one."));
    for (const submission of state.submissions) {
      const button = make("button", `Edit “${submission.proposal_title}”`, "secondary");
      button.type = "button";
      button.addEventListener("click", () => chooseSubmission(submission));
      section.append(button);
    }
    const another = make("button", "Start another proposal", "secondary");
    another.type = "button";
    another.addEventListener("click", () => {
      state.editingSubmission = null;
      byId("proposal-form").reset();
      byId("co-speaker-rows").replaceChildren();
      lockSignedInEmail();
      byId("submit-proposal").textContent = "Confirm submission";
      setStatus("Starting a new proposal. This will create a separate submission.");
    });
    section.append(another);
    card.prepend(section);
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
    let upload;
    try {
      upload = await fetch(uploadUrl, { method: "PUT", headers: { "content-type": file.type, ...(authorization.headers || {}) }, body: file });
    } catch (_) {
      throw new Error("The file could not be uploaded. Check your connection and try again.");
    }
    if (!upload.ok) throw new Error("The file could not be uploaded. Try again.");
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
      renderCallDetails(state.form);
      if (state.form.accepting_submissions === false) {
        byId("closed-card").hidden = false;
        byId("availability").textContent = state.form.availability_message;
        setStatus(state.form.availability_message);
        return;
      }
      try {
        const session = await api("/api/v1/auth/session");
        state.csrf = session.csrf_token;
        state.sessionEmail = session.email || "";
        await api(`/api/v1/forms/${encodeURIComponent(slug)}/access`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
          body: "{}"
        });
        byId("proposal-card").hidden = false;
        lockSignedInEmail();
        const mine = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions/mine`);
        state.submissions = mine.data || [];
        renderExistingSubmissions();
        const requested = new URLSearchParams(location.search).get("submission_id");
        const selected = state.submissions.find((submission) => submission.id === requested);
        if (selected) chooseSubmission(selected);
        else {
          setStatus(state.submissions.length ? "Choose an existing proposal to edit, or start another one." : "Start your first proposal below.");
          await loadDraft();
        }
      } catch (error) {
        if (error.status !== 401) throw error;
        byId("sign-in-card").hidden = false;
        setStatus("Sign in with your email address to begin.");
      }
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), "error"); }
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
      setStatus(window.SessionBuddyApi.message(error, "We could not send the sign-in link. Try again."), "error");
    }
  });
  byId("add-co-speaker").addEventListener("click", () => addCoSpeakerRow());

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
      if (state.editingSubmission) {
        if (!byId("proposal-form").reportValidity()) {
          setStatus("Complete the highlighted required fields before saving changes.", "error");
          return;
        }
        const values = { ...state.editingSubmission.answers, ...answers({ includeUploads: false }) };
        const submission = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions/${encodeURIComponent(state.editingSubmission.id)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` },
          body: JSON.stringify({ speaker_name: values.speaker_name, speaker_email: values.speaker_email, proposal_title: values.proposal_title, proposal_abstract: values.proposal_abstract, answers: values, co_speakers: coSpeakers(), version: state.editingSubmission.version })
        });
        state.editingSubmission = submission;
        state.submissions = state.submissions.map((item) => item.id === submission.id ? submission : item);
        setStatus("Changes saved to this proposal.", "success");
        return;
      }
      const draft = await api(`/api/v1/forms/${encodeURIComponent(slug)}/draft`, { method: "PUT", headers: { "content-type": "application/json", "x-csrf-token": state.csrf }, body: JSON.stringify({ answers: answers({ includeUploads: false }), version: state.draftVersion }) });
      state.draftVersion = draft.version;
      setStatus("Draft saved.", "success");
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), "error"); }
  });

  byId("review-proposal").addEventListener("click", () => {
    if (!byId("proposal-form").reportValidity()) {
      setStatus("Complete the highlighted required fields before reviewing your proposal.", "error");
      return;
    }
    showReview(true);
    setStatus("Not submitted yet. Review your proposal, then select Confirm submission.");
    byId("review-title").focus?.();
  });
  byId("proposal-form").addEventListener("invalid", () => {
    setStatus("Complete the highlighted required fields before continuing.", "error");
  }, true);
  byId("back-to-form").addEventListener("click", () => { showReview(false); setStatus("You can continue editing your proposal."); });

  byId("proposal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = byId("submit-proposal");
    button.disabled = true;
    button.textContent = "Submitting…";
    try {
      await uploadFiles();
      const enteredValues = answers();
      const values = state.editingSubmission
        ? { ...state.editingSubmission.answers, ...enteredValues }
        : enteredValues;
      const target = state.editingSubmission
        ? `/api/v1/forms/${encodeURIComponent(slug)}/submissions/${encodeURIComponent(state.editingSubmission.id)}`
        : `/api/v1/forms/${encodeURIComponent(slug)}/submissions`;
      const submission = await api(target, {
        method: state.editingSubmission ? "PATCH" : "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}`, "x-public-session-id": state.publicSession },
        body: JSON.stringify({ speaker_name: values.speaker_name, speaker_email: values.speaker_email, proposal_title: values.proposal_title, proposal_abstract: values.proposal_abstract, answers: values, co_speakers: coSpeakers(), ...(state.editingSubmission ? { version: state.editingSubmission.version } : {}) })
      });
      setStep("done");
      form.hidden = true;
      const receipt = byId("receipt");
      receipt.className = "empty-state";
      receipt.replaceChildren(make("h2", state.editingSubmission ? "Proposal updated" : state.form.success_title), make("p", state.editingSubmission ? "Your changes were saved to the existing proposal." : state.form.success_message), make("p", `Receipt ${submission.id}`));
      if (state.form.redirect_to_portal) {
        const link = make("a", "Open speaker portal", "button");
        link.href = "/speaker";
        receipt.append(link);
      }
      setStatus(state.editingSubmission ? "Proposal updated successfully." : "Proposal submitted successfully.", "success");
    } catch (error) {
      setStatus(error.status === 422 ? "A required answer is missing or invalid. Go back and review every required field." : window.SessionBuddyApi.message(error), "error");
      byId("status").focus();
      button.disabled = false;
      button.textContent = "Confirm submission";
    }
  });

  load();
})();

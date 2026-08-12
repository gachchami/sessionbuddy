(() => {
  "use strict";
  const state = {
    csrf: "", portal: null, assets: [],
    // The portfolio holds every event this speaker belongs to, keyed by event
    // id, so the page can group submissions by event without refetching. The
    // "active" event is the one the proposal composer and uploads act on.
    portfolio: new Map(), eventOrder: [], activeEventId: "", resources: [],
    sessionEmail: "", sessionName: "",
    // Proposal composer: the published schema plus the files chosen for its
    // upload fields and the staged references already accepted for them.
    form: null, composerEventId: null,
    files: new Map(), uploaded: new Map(), applyConditions: () => {},
    closedCallForms: new Set()
  };
  const PROPOSAL_UPLOAD_TYPES = ["file", "image"];
  const uploadRules = {
    headshot: { max: 5 * 1024 * 1024, types: new Set(["image/jpeg", "image/png", "image/webp"]) },
    slides: { max: 50 * 1024 * 1024, types: new Set(["application/pdf", "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.oasis.opendocument.presentation"]) },
    supporting_document: { max: 20 * 1024 * 1024, types: new Set(["application/pdf"]) }
  };
  const byId = (id) => document.getElementById(id);
  // The proposal composer moved to the shared CFP workspace. These helpers
  // remain only for an in-flight page instance that may finish unloading.
  const retiredComposerNode = (id) => document.getElementById(id);
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function idempotencyKey() {
    if (typeof crypto.randomUUID === "function") return `${crypto.randomUUID()}-${crypto.randomUUID()}`;
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  }

  function setStatus(message, kind = "") {
    const status = byId("status");
    status.textContent = message;
    status.className = `status${kind ? ` ${kind}` : ""}`;
    status.hidden = kind === "success" && message === "Speaker details are ready.";
  }

  function renderProposalDrafts(drafts) {
    const section = byId("saved-proposal-drafts");
    const list = byId("saved-proposal-draft-list");
    list.replaceChildren();
    for (const draft of drafts) {
      const item = make("li", undefined, "proposal-summary-row");
      const link = make("a", undefined, "proposal-summary-row__link");
      link.href = draft.edit_path;
      link.setAttribute("aria-label", `Continue editing ${draft.proposal_title} for ${draft.event_name}`);
      const title = make("strong", draft.proposal_title);
      const event = make("span", draft.event_name, "proposal-summary-row__event");
      const saved = make("time", `Saved ${new Date(draft.updated_at_ms).toLocaleString()}`);
      saved.dateTime = new Date(draft.updated_at_ms).toISOString();
      link.append(title, event, saved);
      item.append(link);
      list.append(item);
    }
    section.hidden = drafts.length === 0;
  }

  async function loadProposalDrafts() {
    try {
      const result = await api("/api/v1/speaker/proposal-drafts");
      const drafts = result.data || [];
      renderProposalDrafts(drafts);
      return drafts.length;
    } catch (_) {
      renderProposalDrafts([]);
      return 0;
    }
  }

  function composerDraftKey(eventId = state.composerEventId, formId = state.form?.id) {
    return eventId && formId ? `sessionbuddy:proposal-draft:${eventId}:${formId}` : "";
  }

  function composerIsDirty() {
    const form = retiredComposerNode("proposal-composer-form");
    return Boolean(form?.dataset.dirty === "true" || state.files.size || state.uploaded.size);
  }

  function confirmComposerDiscard() {
    if (!composerIsDirty()) return true;
    return confirm("Discard this proposal draft? Your entered details and selected files will be removed.");
  }

  function clearComposerDraft() {
    const key = composerDraftKey();
    if (key) {
      try { sessionStorage.removeItem(key); } catch (_) { /* Storage can be disabled by the browser. */ }
    }
  }

  function saveComposerDraft() {
    const form = retiredComposerNode("proposal-composer-form");
    const key = composerDraftKey();
    if (!form || !key) return;
    const values = {};
    for (const field of state.form?.fields || []) {
      if (!PROPOSAL_UPLOAD_TYPES.includes(field.type)) values[field.key] = proposalFieldValue(field);
    }
    const coSpeakers = [...form.querySelectorAll(".co-speaker-row")].map((row) => ({
      display_name: row.querySelector("[data-co-speaker-name]")?.value || "",
      email: row.querySelector("[data-co-speaker-email]")?.value || ""
    }));
    try { sessionStorage.setItem(key, JSON.stringify({ values, coSpeakers })); } catch (_) { /* Confirmation still prevents data loss. */ }
  }

  function showComposerError(message, control = null) {
    const summary = retiredComposerNode("proposal-composer-error");
    if (!summary) {
      setStatus(message, "error");
      return;
    }
    summary.textContent = message;
    summary.hidden = false;
    if (control) control.setAttribute("aria-invalid", "true");
    (control || summary).focus();
  }

  function clearComposerErrors() {
    const summary = retiredComposerNode("proposal-composer-error");
    if (summary) { summary.hidden = true; summary.textContent = ""; }
    retiredComposerNode("proposal-composer-form")?.querySelectorAll('[aria-invalid="true"]').forEach((control) => control.removeAttribute("aria-invalid"));
  }

  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = {
      schema_version: 1, page_template: "/speaker",
      navigation_type: navigation?.type || "unknown",
      device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop",
      sampled: false, lcp_ms: null, inp_ms: null, cls: null,
      ttfb_ms: navigation?.responseStart ?? null, fcp_ms: null,
      route_transition_ms: null, critical_api_ms: Math.max(0, performance.now() - started),
      api_request_id: response.headers.get("x-request-id")
    };
  }

  async function api(path, options = {}, behavior = {}) {
    const started = performance.now();
    return window.SessionBuddyApi.request(path, options, {
      ...behavior,
      onResponse: (response) => {
        recordTelemetry(started, response);
        behavior.onResponse?.(response);
      }
    });
  }

  function formatDate(value, timezone) {
    if (!value) return "No due date";
    try {
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short", timeZone: timezone
      }).format(new Date(value));
    } catch (_) { return "Date unavailable"; }
  }

  // Event runs are whole days; only tasks and messages need a clock time.
  function formatDay(value, timezone) {
    if (!value) return "";
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: timezone }).format(new Date(value));
    } catch (_) { return "Date unavailable"; }
  }

  function eventTimeLabel(value, timezone) {
    return `${formatDate(value, timezone)} · Event time (${timezone})`;
  }

  function safeMessageLink(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
        ? url
        : null;
    } catch (_) { return null; }
  }

  function taskDestination(task) {
    // Only same-page destinations are accepted; arbitrary API-provided URLs are never used.
    if (task.destination_path === "#profile") return "#tasks";
    if (task.destination_path === "#assets") return "#submissions";
    if (["#tasks", "#submissions"].includes(task.destination_path)) {
      return task.destination_path;
    }
    if (["profile", "biography", "headshot"].includes(task.task_type)) return "#tasks";
    if (["slides", "supporting_document"].includes(task.task_type)) return "#submissions";
    return "#tasks";
  }

  function portalPath(eventId = state.portal?.event?.id) {
    return eventId
      ? `/api/v1/speaker/portal?event_id=${encodeURIComponent(eventId)}`
      : "/api/v1/speaker/portal";
  }

  function customTaskForm(task, eventId) {
    const form = make("form", undefined, "task-form");
    form.dataset.taskId = task.id;
    for (const field of task.form_fields || []) {
      const label = make("label", field.label);
      let input;
      if (field.type === "textarea") {
        input = document.createElement("textarea");
        input.maxLength = 4000;
      }
      else if (field.type === "select") {
        input = document.createElement("select");
        input.append(new Option("Choose…", ""));
        (field.choices || []).forEach((choice) => input.add(new Option(choice, choice)));
      } else {
        input = document.createElement("input");
        input.type = field.type === "checkbox" ? "checkbox" : field.type === "url" ? "url" : "text";
        if (field.type === "checkbox") label.classList.add("check-label");
      }
      input.name = field.key;
      input.required = Boolean(field.required);
      label.append(input);
      form.append(label);
    }
    const submit = make("button", task.form_fields?.length ? "Send response" : "Mark complete");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      const values = {};
      for (const field of task.form_fields || []) {
        const input = form.elements.namedItem(field.key);
        values[field.key] = field.type === "checkbox" ? Boolean(input.checked) : input.value;
      }
      try {
        await api(`/api/v1/speaker/tasks/${encodeURIComponent(task.id)}/response`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
          body: JSON.stringify({ answers: values, version: task.version })
        });
        const portal = await api(portalPath(eventId || state.activeEventId));
        renderPortal(portal);
        announceOnboardingChange();
        setStatus("Task completed.", "success");
      } catch (error) {
        setStatus(error.status === 409 ? "This task changed. Reload the portal and try again." : error.message, "error");
        submit.disabled = false;
      }
    });
    return form;
  }

  function renderTasks(tasks, timezone, list, submissions = []) {
    list.replaceChildren();
    const outstanding = tasks.filter((task) => !["completed", "waived"].includes(task.state));
    if (!outstanding.length) list.append(make("li", "No actions due.", "empty"));
    outstanding.forEach((task) => {
      const item = make("li", undefined, "item-card");
      const heading = make("h3", task.title);
      const help = task.help_text ? make("p", task.help_text) : null;
      const meta = make("p", undefined, "item-meta");
      const overdue = task.due_at_ms !== null && task.due_at_ms < Date.now();
      const dueSoon = task.due_at_ms !== null && task.due_at_ms >= Date.now()
        && task.due_at_ms - Date.now() <= 7 * 24 * 60 * 60 * 1000;
      const stateText = overdue ? "Overdue" : dueSoon ? "Due soon" : "Outstanding";
      meta.append(
        make("span", stateText, `state-badge${overdue ? " overdue" : ""}`),
        make("span", `Due ${eventTimeLabel(task.due_at_ms, timezone)}`)
      );
      item.append(heading);
      if (help) item.append(help);
      item.append(meta);
      if (["profile", "biography", "headshot"].includes(task.task_type)) {
        const action = make("a", task.task_type === "headshot" ? "Manage headshot" : "Edit profile", "task-link");
        action.href = "/account";
        item.append(action);
      } else if (task.task_type === "custom") {
        item.append(customTaskForm(task, list.dataset.eventId));
      } else if (["slides", "supporting_document"].includes(task.task_type)) {
        const submissionId = submissions.length === 1 ? submissions[0].id : "";
        item.append(createUploadForm(task.task_type, submissionId));
      } else {
        const action = make("a", task.action_label || "Complete task", "task-link");
        action.href = taskDestination(task);
        item.append(action);
      }
      list.append(item);
    });
    const completed = tasks.filter((task) => ["completed", "waived"].includes(task.state));
    if (completed.length) {
      list.append(make("li", `Completed (${completed.length})`, "task-history-heading"));
      completed.forEach((task) => {
        const item = make("li", undefined, "item-card task-complete");
        item.append(
          make("h3", task.title),
          make("p", task.state === "waived" ? "Waived" : "Completed", "state-badge success"),
        );
        list.append(item);
      });
    }
  }

  // Status drives the pill colour. Anything unrecognised stays neutral rather
  // than borrowing a success or danger colour it has not earned.
  const SUBMISSION_TONE = {
    accepted: "success", rejected: "overdue", declined: "overdue",
    withdrawn: "muted", draft: "muted"
  };

  const CORE_ANSWER_KEYS = new Set([
    "speaker_name", "speaker_email", "proposal_title", "proposal_abstract"
  ]);

  function answerText(value) {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.filter((entry) => typeof entry !== "object").join(", ");
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (typeof value === "object") return "";
    return String(value);
  }

  function humanizeKey(key) {
    return key.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
  }

  // Every submission renders as a labelled field list so a speaker can scan
  // one event's proposals without opening each of them.
  function submissionFields(submission, assets) {
    const fields = make("ul", undefined, "session-fields");
    const row = (label, value) => {
      if (!value) return;
      const line = document.createElement("li");
      line.append(make("span", label, "session-fields__key"), make("span", value, "session-fields__value"));
      fields.append(line);
    };
    row("Call", submission.form_slug);
    row("Speaker", submission.speaker_name);
    const extras = Object.entries(submission.answers || {})
      .filter(([key]) => !CORE_ANSWER_KEYS.has(key))
      .map(([key, value]) => [key, answerText(value)])
      .filter(([, value]) => value)
      .slice(0, 6);
    extras.forEach(([key, value]) => row(humanizeKey(key), value));
    row("Files", assets.length
      ? assets.map((asset) => asset.kind === "slides" ? "Slides" : "Document").join(", ")
      : "None uploaded");
    row("Revision", `v${submission.version}`);
    return fields;
  }

  function renderSubmissions(submissions, list) {
    list.replaceChildren();
    if (!submissions.length) {
      list.append(make("li", "No proposals are connected to this account yet.", "empty"));
      return;
    }
    submissions.forEach((submission) => {
      const item = make("li", undefined, "proposal-summary-row");
      const link = make("a", undefined, "proposal-summary-row__link");
      link.href = `/speaker/proposals/${encodeURIComponent(submission.form_slug)}/${encodeURIComponent(submission.id)}`;
      const statusLabel = submission.status.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
      const tone = SUBMISSION_TONE[submission.status] || "";
      link.append(
        make("strong", submission.proposal_title),
        make("span", statusLabel, `state-badge${tone ? ` ${tone}` : ""}`),
        make("span", "→", "proposal-summary-row__arrow")
      );
      item.append(link);
      list.append(item);
    });
  }

  function publicSessionId() {
    // The submission endpoint requires a stable client identifier of its own,
    // separate from the authenticated session cookie.
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  }

  function renderOpenCall(call) {
    const availability = byId("call-availability");
    if (!call) {
      availability.hidden = true;
      return;
    }
    availability.hidden = call.accepting_submissions;
    if (!call.accepting_submissions) availability.textContent = call.availability_message;
  }

  function closeComposer({ discardDraft = false } = {}) {
    if (discardDraft) clearComposerDraft();
    state.form = null;
    state.composerEventId = null;
    state.files.clear();
    state.uploaded.clear();
    state.applyConditions = () => {};
  }

  function proposalFieldValue(field) {
    const form = retiredComposerNode("proposal-composer-form");
    const control = form?.elements.namedItem(field.key);
    if (!control) return null;
    if (field.type === "checkbox") return Boolean(control.checked);
    if (field.type === "multiselect") return [...control.selectedOptions].map((option) => option.value);
    if (PROPOSAL_UPLOAD_TYPES.includes(field.type)) return state.uploaded.get(field.key) || "";
    return control.value;
  }

  function proposalAnswers(form) {
    const result = {};
    for (const field of form?.fields || []) result[field.key] = proposalFieldValue(field);
    return result;
  }

  function proposalConditionMatches(condition) {
    const source = state.form?.fields?.find((field) => field.key === condition.source_key);
    const actual = proposalFieldValue({ key: condition.source_key, type: source?.type });
    const comparable = Array.isArray(actual) ? actual : [String(actual ?? "")];
    const matches = comparable.includes(condition.value);
    return condition.operator === "equals" ? matches : !matches;
  }

  function renderProposalFields(container, fields, conditions) {
    container.replaceChildren();
    for (const field of fields) {
      const label = make("label");
      const caption = make("span", field.label, "field-label");
      if (field.required) {
        const marker = make("span", "*", "required-marker");
        marker.setAttribute("aria-hidden", "true");
        caption.append(marker);
      }
      label.append(caption);
      label.dataset.fieldKey = field.key;
      let input;
      if (field.type === "textarea") {
        input = document.createElement("textarea");
        input.rows = 6;
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
      } else if (PROPOSAL_UPLOAD_TYPES.includes(field.type)) {
        input = document.createElement("input");
        input.type = "file";
        input.accept = field.type === "image" ? "image/jpeg,image/png,image/webp" : "application/pdf";
        input.addEventListener("change", () => {
          const file = input.files?.[0];
          if (file) state.files.set(field.key, file); else state.files.delete(field.key);
          state.uploaded.delete(field.key);
          delete input.dataset.stagedReference;
        });
      } else {
        const inputType = field.type === "phone" ? "tel" : field.type;
        input = document.createElement("input");
        input.type = ["email", "url", "tel"].includes(inputType) ? inputType : "text";
        input.maxLength = field.type === "email" || field.key === "speaker_email"
          ? 320
          : ["speaker_name", "proposal_title"].includes(field.key) ? 200 : 500;
      }
      input.name = field.key;
      input.id = `proposal-field-${field.key}`;
      input.required = Boolean(field.required);
      input.dataset.required = field.required ? "true" : "false";
      if (field.placeholder && !["select", "multiselect", "checkbox", ...PROPOSAL_UPLOAD_TYPES].includes(field.type)) {
        input.placeholder = field.placeholder;
      }
      if (field.key === "speaker_name") {
        input.autocomplete = "name";
        input.value = state.portal?.profile?.display_name || state.sessionName || "";
      }
      if (field.key === "speaker_email") {
        input.autocomplete = "email";
        input.value = state.sessionEmail || "";
        // The server rejects a proposal whose email is not the signed-in
        // account, so the field is shown but not editable.
        if (state.sessionEmail) input.readOnly = true;
      }
      label.append(input);
      if (field.help_text) label.append(make("small", field.help_text));
      if (field.key === "speaker_email" && state.sessionEmail) {
        label.append(make("small", `Signed in as ${state.sessionEmail}.`));
      }
      container.append(label);
    }
    state.applyConditions = () => {
      const form = retiredComposerNode("proposal-composer-form");
      for (const field of fields) {
        const related = conditions.filter((condition) => condition.target_key === field.key);
        const visible = related.every(proposalConditionMatches);
        const wrapper = container.querySelector(`[data-field-key="${CSS.escape(field.key)}"]`);
        const control = form?.elements.namedItem(field.key);
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

  function coSpeakerRows(container) {
    return [...container.querySelectorAll(".co-speaker-row")].map((row) => ({
      display_name: row.querySelector("[data-co-speaker-name]").value.trim(),
      email: row.querySelector("[data-co-speaker-email]").value.trim(),
      role: "co_speaker"
    })).filter((entry) => entry.display_name || entry.email);
  }

  function coSpeakerConflict(rows, speakerEmail) {
    // The server rejects duplicate or self-referencing co-speakers with an
    // unattributable 422, so name the offending address before uploading.
    const seen = new Set([String(speakerEmail || "").trim().toLowerCase()].filter(Boolean));
    for (const entry of rows) {
      const email = entry.email.toLowerCase();
      if (seen.has(email)) return { email, message: `${entry.email} is already listed on this proposal.` };
      seen.add(email);
    }
    return null;
  }

  function addCoSpeakerRow(container) {
    const row = make("div", undefined, "co-speaker-row");
    const nameLabel = make("label", "Co-speaker name");
    const name = document.createElement("input");
    name.type = "text";
    name.maxLength = 200;
    // Both halves are required once a row exists, so an incomplete co-speaker
    // is caught here instead of coming back as a server validation error.
    name.required = true;
    name.dataset.coSpeakerName = "true";
    nameLabel.append(name);
    const emailLabel = make("label", "Co-speaker email");
    const email = document.createElement("input");
    email.type = "email";
    email.maxLength = 320;
    email.required = true;
    email.dataset.coSpeakerEmail = "true";
    emailLabel.append(email);
    const remove = make("button", "Remove", "secondary");
    remove.type = "button";
    remove.addEventListener("click", () => { row.remove(); saveComposerDraft(); });
    row.append(nameLabel, emailLabel, remove);
    container.append(row);
    name.focus();
  }

  async function proposalChecksum(file) {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  async function uploadProposalFile(formId, field, file) {
    const kind = field.type === "image" ? "headshot" : "supporting_document";
    const max = field.type === "image" ? 5 * 1024 * 1024 : 20 * 1024 * 1024;
    if (file.size <= 0) throw new Error(`${field.label} is empty.`);
    if (file.size > max) throw new Error(`${field.label} is too large.`);
    const authorization = await api(`/api/v1/cfp/forms/${encodeURIComponent(formId)}/upload-authorizations`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
      body: JSON.stringify({
        kind, filename: file.name, content_type: file.type,
        byte_size: file.size, checksum_sha256: await proposalChecksum(file)
      })
    });
    const uploadUrl = safeUploadUrl(authorization.upload_url);
    if (!uploadUrl || authorization.expires_at_ms <= Date.now()) throw new Error("The upload authorization expired.");
    let upload;
    try {
      upload = await fetch(uploadUrl, {
        method: "PUT",
        headers: safeUploadHeaders(authorization.headers, file.type),
        body: file
      });
    } catch (_) {
      throw new Error("The file could not be uploaded. Check your connection and try again.");
    }
    if (!upload.ok) throw new Error("The file could not be uploaded. Try again.");
    const completionPath = `/api/v1/cfp/forms/${encodeURIComponent(formId)}/upload-authorizations/${encodeURIComponent(authorization.staged_id)}/complete`;
    const complete = () => api(completionPath, {
      method: "POST",
      headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
      body: "{}"
    });
    let completion = await complete();
    for (let attempt = 0; ["uploaded", "scanning"].includes(completion.state) && attempt < 60; attempt += 1) {
      setStatus(`Checking ${field.label} for safety…`);
      await new Promise((resolve) => setTimeout(resolve, 1000));
      completion = await complete();
    }
    if (completion.state === "rejected") throw new Error(`${field.label} did not pass the safety check.`);
    if (completion.state !== "staged") throw new Error(`${field.label} is still being checked. Try again in a moment.`);
    return `staged:${authorization.staged_id}`;
  }

  /**
   * Stage every chosen upload and record its reference in `staged`.
   *
   * Both maps are the caller's, not module state: a scan can take a minute,
   * and a composer torn down in that window must not be able to erase a
   * reference for a file that was already accepted.
   */
  async function uploadProposalFiles(form, chosen, staged) {
    for (const field of (form?.fields || []).filter((item) => PROPOSAL_UPLOAD_TYPES.includes(item.type))) {
      const file = chosen.get(field.key);
      if (file && !staged.has(field.key)) {
        setStatus(`Uploading ${field.label}…`);
        staged.set(field.key, await uploadProposalFile(form.id, field, file));
      }
    }
  }

  function buildComposer(call, form) {
    const composer = retiredComposerNode("proposal-composer");
    const shell = make("form", undefined, "proposal-composer__form");
    shell.id = "proposal-composer-form";
    const heading = make("div", undefined, "proposal-composer__heading");
    heading.append(make("h3", "New proposal"));
    heading.append(make("p", form.welcome_text || "Share the session you would like the event team to consider.", "help"));
    shell.append(heading);
    const errorSummary = make("p", "", "proposal-composer__error");
    errorSummary.id = "proposal-composer-error";
    errorSummary.setAttribute("role", "alert");
    errorSummary.tabIndex = -1;
    errorSummary.hidden = true;
    shell.append(errorSummary);
    const fields = make("div", undefined, "proposal-composer__fields");
    shell.append(fields);
    const coSpeakerLimit = Number(form.co_speaker_limit ?? 0);
    let coSpeakerRowHost = null;
    if (coSpeakerLimit > 0) {
      const coSpeakers = make("div", undefined, "proposal-composer__co-speakers");
      coSpeakerRowHost = make("div");
      const add = make("button", "Add co-speaker", "secondary");
      add.type = "button";
      add.addEventListener("click", () => {
        if (coSpeakerRowHost.querySelectorAll(".co-speaker-row").length >= coSpeakerLimit) {
          showComposerError(`This call allows up to ${coSpeakerLimit} co-speaker${coSpeakerLimit === 1 ? "" : "s"}.`);
          return;
        }
        addCoSpeakerRow(coSpeakerRowHost);
        shell.dataset.dirty = "true";
        saveComposerDraft();
      });
      coSpeakers.append(make("h4", "Co-speakers"), coSpeakerRowHost, add);
      shell.append(coSpeakers);
    }
    const actions = make("div", undefined, "actions");
    const cancel = make("button", "Cancel", "secondary");
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      if (!confirmComposerDiscard()) return;
      closeComposer({ discardDraft: true });
      retiredComposerNode("open-proposal-composer")?.focus();
    });
    const submit = make("button", "Submit proposal");
    submit.type = "submit";
    actions.append(cancel, submit);
    shell.append(actions);
    composer.replaceChildren(shell);
    composer.hidden = false;
    renderProposalFields(fields, form.fields || [], form.conditions || []);

    const draftKey = composerDraftKey();
    if (draftKey) {
      try {
        const draft = JSON.parse(sessionStorage.getItem(draftKey) || "null");
        for (const field of form.fields || []) {
          const control = shell.elements.namedItem(field.key);
          const value = draft?.values?.[field.key];
          if (!control || value === undefined || PROPOSAL_UPLOAD_TYPES.includes(field.type)) continue;
          // The authenticated account is authoritative. A draft can have been
          // saved before that lookup completed, but must never replace it.
          if (field.key === "speaker_email" && state.sessionEmail) continue;
          if (field.type === "checkbox") control.checked = Boolean(value);
          else if (field.type === "multiselect") [...control.options].forEach((option) => { option.selected = value.includes(option.value); });
          else control.value = value;
        }
        for (const entry of draft?.coSpeakers || []) {
          if (!coSpeakerRowHost || coSpeakerRowHost.children.length >= coSpeakerLimit) break;
          addCoSpeakerRow(coSpeakerRowHost);
          const row = coSpeakerRowHost.lastElementChild;
          row.querySelector("[data-co-speaker-name]").value = entry.display_name || "";
          row.querySelector("[data-co-speaker-email]").value = entry.email || "";
        }
        if (draft) shell.dataset.dirty = "true";
        state.applyConditions();
      } catch (_) {
        try { sessionStorage.removeItem(draftKey); } catch (_) { /* Storage can be disabled by the browser. */ }
      }
    }
    shell.addEventListener("input", (event) => {
      event.target.removeAttribute?.("aria-invalid");
      clearComposerErrors();
      shell.dataset.dirty = "true";
      saveComposerDraft();
    });
    shell.addEventListener("change", () => {
      shell.dataset.dirty = "true";
      saveComposerDraft();
    });
    shell.addEventListener("invalid", (event) => {
      event.preventDefault();
      if (retiredComposerNode("proposal-composer-error")?.hidden) {
        showComposerError("Complete the highlighted field before submitting.", event.target);
      }
    }, true);

    // Held across retries of an unchanged proposal. A lost response leaves the
    // proposal stored server-side; retrying with a fresh key would create a
    // second one, so the key only changes when the payload does — which is
    // also what the server's replay check requires (same key + changed
    // fingerprint is a 409).
    let pendingSubmission = null;

    shell.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (shell.getAttribute("aria-busy") === "true") return;
      clearComposerErrors();
      if (!shell.checkValidity()) {
        const invalid = shell.querySelector(":invalid");
        showComposerError("Complete the highlighted field before submitting.", invalid);
        invalid?.reportValidity();
        return;
      }
      const entered = coSpeakerRowHost ? coSpeakerRows(coSpeakerRowHost) : [];
      // Read the address actually being submitted, not the cached session one:
      // when the account lookup failed the field is editable and is the only
      // case where a speaker can list themselves as their own co-speaker.
      const conflict = coSpeakerConflict(
        entered,
        shell.elements.namedItem("speaker_email")?.value || state.sessionEmail
      );
      if (conflict) {
        const repeated = [...shell.querySelectorAll("[data-co-speaker-email]")].find(
          (input) => input.value.trim().toLowerCase() === conflict.email
        );
        showComposerError(conflict.message, repeated);
        return;
      }
      shell.setAttribute("aria-busy", "true");
      submit.disabled = true;
      cancel.disabled = true;
      submit.textContent = "Submitting…";
      setStatus("Submitting your proposal…");
      let created = null;
      // Read the controls and chosen files before the upload wait: scanning can
      // take a minute, and the composer may be torn down in that window.
      const activeForm = state.form;
      const values = proposalAnswers(activeForm);
      const chosen = new Map(state.files);
      const staged = new Map(state.uploaded);
      for (const field of (activeForm?.fields || []).filter((item) => PROPOSAL_UPLOAD_TYPES.includes(item.type))) {
        const reference = activeForm && shell.elements.namedItem(field.key)?.dataset?.stagedReference;
        if (reference) staged.set(field.key, reference);
      }
      try {
        try {
          await uploadProposalFiles(activeForm, chosen, staged);
        } finally {
          // Keep every successful write for a retry even when a later file
          // fails, so accepted files are not uploaded and scanned twice.
          if (state.form === activeForm) state.uploaded = new Map(staged);
          for (const field of (activeForm?.fields || []).filter((item) => PROPOSAL_UPLOAD_TYPES.includes(item.type))) {
            values[field.key] = staged.get(field.key) || "";
            const control = shell.elements.namedItem(field.key);
            if (control && values[field.key]) control.dataset.stagedReference = values[field.key];
          }
        }
        const payload = JSON.stringify({
          speaker_name: values.speaker_name,
          speaker_email: values.speaker_email,
          proposal_title: values.proposal_title,
          proposal_abstract: values.proposal_abstract,
          answers: values,
          co_speakers: entered
        });
        if (!pendingSubmission || pendingSubmission.payload !== payload) {
          pendingSubmission = { payload, key: idempotencyKey(), session: publicSessionId() };
        }
        created = await api(`/api/v1/forms/${encodeURIComponent(call.slug)}/submissions`, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": pendingSubmission.key,
            "x-public-session-id": pendingSubmission.session
          },
          body: payload
        });
      } catch (error) {
        setStatus("");
        const message = error?.status
          ? window.SessionBuddyApi.message(error, "The proposal could not be submitted. Review the form and try again.")
          : error?.message || "The proposal could not be submitted. Review the form and try again.";
        showComposerError(message);
        submit.disabled = false;
        cancel.disabled = false;
        submit.textContent = "Submit proposal";
      } finally {
        shell.setAttribute("aria-busy", "false");
      }
      if (!created) return;
      // The proposal exists from here on. Refreshing the portal must never be
      // reported as a submission failure, or the speaker submits again.
      clearComposerDraft();
      closeComposer();
      setStatus(`Proposal submitted. Receipt ${created.id}.`, "success");
      try {
        await loadEvent(state.portal?.event?.id);
        setStatus(`Proposal submitted. Receipt ${created.id}.`, "success");
      } catch (_) {
        setStatus(`Proposal submitted (receipt ${created.id}), but the portal could not be refreshed. Reload to see it.`, "success");
      }
      const trigger = retiredComposerNode("open-proposal-composer");
      (trigger.hidden ? byId("status") : trigger).focus();
    });
    const firstField = fields.querySelector("input:not(:disabled), textarea:not(:disabled), select:not(:disabled)");
    firstField?.focus();
  }

  async function openComposer() {
    const call = state.portal?.open_call;
    if (!call?.accepting_submissions) return;
    const requestedEventId = state.portal?.event?.id || "";
    const trigger = retiredComposerNode("open-proposal-composer");
    trigger.disabled = true;
    setStatus("Loading the call for proposals…");
    try {
      const [form, freshPortal] = await Promise.all([
        api(`/api/v1/forms/${encodeURIComponent(call.slug)}`),
        api(portalPath(requestedEventId))
      ]);
      // A concurrent event switch must not attach the old form to the newly
      // selected event. The authenticated portal response is also the source
      // of truth for the per-speaker submission limit.
      if (state.portal?.event?.id !== requestedEventId) return;
      const freshCall = freshPortal.open_call;
      if (!freshCall || freshCall.form_id !== call.form_id || !freshCall.accepting_submissions) {
        state.portal.open_call = freshCall || null;
        if (freshCall) state.closedCallForms.add(call.form_id);
        renderOpenCall(freshCall || null);
        setStatus(freshCall?.availability_message || "This call is no longer accepting proposals.", "error");
        return;
      }
      // The portal payload can be minutes old; the form endpoint is authoritative
      // about the window right now. Stop here rather than after an upload.
      if (form.accepting_submissions === false) {
        state.closedCallForms.add(call.form_id);
        state.portal.open_call = {
          ...call,
          accepting_submissions: false,
          availability_message: form.availability_message || "Applications are closed."
        };
        renderOpenCall(state.portal.open_call);
        setStatus(form.availability_message || "This call is no longer accepting proposals.", "error");
        return;
      }
      state.form = form;
      state.portal.open_call = freshCall;
      state.composerEventId = requestedEventId;
      state.files.clear();
      state.uploaded.clear();
      trigger.setAttribute("aria-expanded", "true");
      buildComposer(call, form);
      setStatus("Complete the proposal form to submit.", "");
    } catch (error) {
      setStatus(window.SessionBuddyApi.message(error, "The call for proposals could not be loaded."), "error");
    } finally {
      trigger.disabled = false;
    }
  }

  function createUploadForm(kind, submissionId) {
    const slides = kind === "slides";
    const headshot = kind === "headshot";
    const form = make("form", undefined, "session-upload-card");
    form.dataset.kind = kind;
    form.dataset.submissionId = submissionId;
    const title = headshot ? "Headshot" : slides ? "Slides" : "Supporting document";
    const fileLabel = make("label", `Choose ${headshot ? "headshot" : slides ? "slides" : "document"}`);
    const file = document.createElement("input");
    file.name = "file"; file.type = "file"; file.required = true;
    file.accept = headshot
      ? "image/jpeg,image/png,image/webp"
      : slides
      ? "application/pdf,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.oasis.opendocument.presentation"
      : "application/pdf";
    fileLabel.append(file);
    const commentLabel = make("label", "What changed?");
    const comment = document.createElement("textarea");
    comment.name = "version_comment"; comment.rows = 2; comment.minLength = 1; comment.maxLength = 1000; comment.required = true;
    commentLabel.append(comment);
    const button = make("button", `Upload ${headshot ? "headshot" : slides ? "slides" : "document"}`);
    button.type = "submit";
    const progress = document.createElement("progress");
    progress.max = 100; progress.value = 0; progress.hidden = true; progress.setAttribute("aria-label", `${title} upload progress`);
    const status = make("p", "", "upload-status"); status.setAttribute("role", "status");
    form.append(make("h4", title), fileLabel, commentLabel, button, progress, status);
    bindUploadForm(form);
    return form;
  }

  // A single event's portal payload arrives here; the portfolio keeps every
  // event the speaker belongs to so the page can render them all together.
  function rememberPortal(portal) {
    if (portal.open_call && state.closedCallForms.has(portal.open_call.form_id)) {
      portal.open_call = {
        ...portal.open_call,
        accepting_submissions: false,
        availability_message: portal.open_call.availability_message || "Applications are closed."
      };
    }
    const event = portal.event;
    const existing = state.portfolio.get(event.id);
    state.portfolio.set(event.id, { event, portal, assets: existing?.assets || [] });
    (portal.events || [event]).forEach((available) => {
      if (!state.eventOrder.includes(available.id)) state.eventOrder.push(available.id);
      if (!state.portfolio.has(available.id)) {
        state.portfolio.set(available.id, { event: available, portal: null, assets: [] });
      }
    });
    return event.id;
  }

  function renderPortal(portal) {
    state.activeEventId = rememberPortal(portal);
    state.portal = portal;
    state.assets = state.portfolio.get(portal.event.id)?.assets || [];
    byId("welcome-name").textContent = portal.profile.display_name || "speaker";
    const publicProfile = byId("public-profile-link");
    publicProfile.hidden = !portal.public_profile_url;
    if (portal.public_profile_url) publicProfile.href = portal.public_profile_url;
    renderOpenCall(portal.open_call || null);
    renderPortfolio();
    byId("auth-state").hidden = true;
    byId("portal").hidden = false;
  }

  function eventDateRange(event) {
    const start = formatDay(event.starts_at_ms, event.time_zone);
    const end = formatDay(event.ends_at_ms, event.time_zone);
    if (!start && !end) return "Dates to be confirmed";
    return !end || start === end ? start : `${start} – ${end}`;
  }

  function summaryCounts(entries) {
    const totals = { total: 0, accepted: 0, review: 0, rejected: 0, actions: 0 };
    entries.forEach(({ portal }) => {
      const submissions = portal?.submissions || [];
      totals.total += submissions.length;
      totals.accepted += submissions.filter((one) => one.status === "accepted").length;
      totals.review += submissions.filter((one) => ["submitted", "in_review", "under_review"].includes(one.status)).length;
      totals.rejected += submissions.filter((one) => ["rejected", "declined"].includes(one.status)).length;
      totals.actions += (portal?.tasks || []).filter((task) => !["completed", "waived"].includes(task.state)).length;
    });
    return totals;
  }

  function eventSection(entry) {
    const { event, portal } = entry;
    const section = make("article", undefined, "event-group");
    section.dataset.eventId = event.id;
    section.classList.toggle("is-active", event.id === state.activeEventId);
    const heading = make("header", undefined, "event-group__head");
    const copy = make("div");
    const title = make("h3", event.name);
    title.id = `event-heading-${event.id}`;
    const meta = make("p", undefined, "event-group__meta");
    meta.append(
      make("span", eventDateRange(event)),
      make("span", `Event time (${event.time_zone})`)
    );
    copy.append(title, meta);
    const submissions = portal?.submissions || [];
    heading.append(copy);
    section.setAttribute("aria-labelledby", title.id);
    section.append(heading);

    if (!portal) {
      section.append(make("p", "This event could not be loaded.", "empty"));
      return section;
    }

    const outstanding = (portal.tasks || []).filter((task) => !["completed", "waived"].includes(task.state));
    const notifications = portal.notifications || [];
    const resources = state.resources.filter((resource) => resource.event_id === event.id);
    const assets = entry.assets || [];
    const activities = portal.activities || [];
    if (!submissions.length && !outstanding.length && !notifications.length
        && !resources.length && !assets.length && !activities.length) {
      section.classList.add("is-empty-event");
      section.append(make("p", "No proposals or actions for this event.", "event-group__empty"));
      return section;
    }

    const proposalBlock = make("section", undefined, "event-group__primary");
    proposalBlock.append(subHeading("Your proposals", submissions.length));
    const sessionList = make("ul", undefined, "item-list submission-list");
    sessionList.dataset.eventId = event.id;
    renderSubmissions(submissions, sessionList);
    proposalBlock.append(sessionList);
    section.append(proposalBlock);

    // Required work is the only secondary object promoted above messages and
    // reference material. An empty task collection is represented by absence,
    // not a full table that competes with the proposal.
    if (outstanding.length) {
      const tasksBlock = make("section", undefined, "event-group__block event-group__attention");
      tasksBlock.append(subHeading("Needs attention", outstanding.length));
      const taskList = make("ul", undefined, "item-list task-list");
      taskList.dataset.eventId = event.id;
      renderTasks(outstanding, event.time_zone, taskList, submissions);
      tasksBlock.append(taskList);
      section.append(tasksBlock);
    }

    if (notifications.length || resources.length) {
      const support = make("div", undefined, "event-group__support");
      if (notifications.length) {
        const messages = make("section", undefined, "event-group__block event-group__messages");
        messages.append(subHeading("Messages", notifications.length));
        const list = make("ul", undefined, "notification-list");
        list.dataset.eventId = event.id;
        renderNotifications(notifications, event.time_zone, list);
        messages.append(list);
        support.append(messages);
      }
      if (resources.length) {
        const resourceBlock = make("section", undefined, "event-group__block event-group__resources");
        resourceBlock.append(subHeading("Resources", resources.length));
        const resourceContainer = make("div", undefined, "resource-list");
        renderResources(resources, resourceContainer);
        resourceBlock.append(resourceContainer);
        support.append(resourceBlock);
      }
      section.append(support);
    }

    if (assets.length || activities.length) {
      const more = make("details", undefined, "event-group__more");
      const moreSummary = make("summary");
      moreSummary.append(
        make("span", "Files and activity"),
        make("span", String(assets.length + activities.length), "count-badge")
      );
      more.append(moreSummary);
      if (assets.length) {
        const assetBlock = make("section", undefined, "event-group__block");
        assetBlock.append(subHeading("Files", assets.length), assetTable(assets));
        more.append(assetBlock);
      }
      if (activities.length) {
        const activityBlock = make("section", undefined, "event-group__block");
        activityBlock.append(subHeading("Activity", activities.length), activityTable(activities, event.time_zone));
        more.append(activityBlock);
      }
      section.append(more);
    }

    return section;
  }

  function subHeading(text, count) {
    const heading = make("div", undefined, "event-group__subheading");
    heading.append(make("h4", text), make("span", String(count), "count-badge"));
    return heading;
  }

  function dataTable(headings, rows, emptyRow) {
    const wrapper = make("div", undefined, "data-table-wrap");
    const table = make("table", undefined, `portal-data-table${rows.length ? "" : " is-empty"}`);
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    headings.forEach((label) => {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      headRow.append(cell);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    (rows.length ? rows : [emptyRow]).forEach((values) => {
      const row = document.createElement("tr");
      values.forEach((value, index) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        if (!rows.length && index === values.length - 1) cell.className = "portal-data-table__zero";
        row.append(cell);
      });
      body.append(row);
    });
    table.append(head, body);
    wrapper.append(table);
    return wrapper;
  }

  function emptyDataTable(itemHeading, statusHeading, itemText, statusText) {
    return dataTable([itemHeading, statusHeading], [], [itemText, statusText]);
  }

  function assetTable(assets) {
    return dataTable(
      ["File", "Type", "Versions"],
      assets.map((asset) => [
        asset.filename,
        asset.kind.replaceAll("_", " "),
        String(asset.version_count || 1)
      ]),
      ["No files uploaded", "—", "0"]
    );
  }

  function activityTable(activities, timezone) {
    return dataTable(
      ["Activity", "Item", "When"],
      activities.map((activity) => [
        `${activity.operation} ${activity.resource_type.replaceAll("_", " ")}`,
        activity.subject,
        eventTimeLabel(activity.occurred_at_ms, timezone)
      ]),
      ["No activity yet", "—", "0"]
    );
  }

  // Rebuilds every event section from portfolio state. The composer lives
  // outside this container so an open draft survives a re-render.
  function renderPortfolio() {
    const container = byId("event-sections");
    if (!container) return;
    const entries = state.eventOrder
      .map((eventId) => state.portfolio.get(eventId))
      .filter(Boolean)
      .filter((entry) => entry.event.id === state.activeEventId || entry.portal);
    const totals = summaryCounts(entries);
    const setSummary = (id, value, always = false) => {
      const output = byId(id);
      output.textContent = String(value);
      output.closest(".summary-tile").hidden = !always && value === 0;
    };
    setSummary("summary-total", totals.total, true);
    setSummary("summary-accepted", totals.accepted);
    setSummary("summary-review", totals.review);
    setSummary("summary-rejected", totals.rejected);
    setSummary("summary-actions", totals.actions);
    byId("portal-summary").textContent = entries.length === 1
      ? "Manage sessions, tasks, and resources for your event."
      : `Manage sessions, tasks, and resources across ${entries.length} events.`;
    container.replaceChildren();
    if (!entries.length) {
      container.append(make("p", "No events are connected to this account yet.", "empty"));
      return;
    }
    entries.forEach((entry) => container.append(eventSection(entry)));
  }

  function renderResources(resources, container) {
    if (!resources.length) {
      container.replaceChildren(emptyDataTable("Resource", "Status", "No resources published", "0"));
      return;
    }
    const cards = resources.map((resource) => {
      const details = make("details", undefined, "resource-card");
      details.append(make("summary", resource.title));
      if (resource.summary) details.append(make("p", resource.summary, "help"));
      if (resource.body_text) details.append(make("p", resource.body_text, "resource-card__body"));
      const embed = safeEmbedUrl(resource.embed_url);
      if (embed) {
        const frame = document.createElement("iframe");
        frame.className = "resource-embed";
        frame.src = embed;
        frame.title = resource.title;
        frame.loading = "lazy";
        frame.referrerPolicy = "no-referrer";
        frame.sandbox = "allow-scripts allow-same-origin allow-popups";
        details.append(frame);
      }
      return details;
    });
    container.replaceChildren(...cards);
  }

  function renderNotifications(notifications, timezone, list) {
    list.replaceChildren();
    if (!notifications.length) {
      list.append(make("li", "No updates from this event yet.", "empty"));
      return;
    }
    notifications.forEach((notification) => {
      const item = document.createElement("li");
      const detail = document.createElement("details");
      const summary = document.createElement("summary");
      summary.append(
        make("strong", notification.subject),
        make("time", `Delivered ${eventTimeLabel(notification.delivered_at_ms, timezone)}`)
      );
      const body = make("p", notification.body_text || "Message content is unavailable.", "help");
      detail.append(summary, body);
      const safeLinks = (notification.links || []).map(safeMessageLink).filter(Boolean);
      if (safeLinks.length) {
        const links = document.createElement("ul");
        links.className = "notification-links";
        safeLinks.forEach((url) => {
          const row = document.createElement("li");
          const link = make("a", url.href);
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener";
          row.append(link);
          links.append(row);
        });
        detail.append(links);
      }
      item.append(detail);
      list.append(item);
    });
  }

  function announceOnboardingChange() {
    const eventId = state.portal?.event?.id;
    if (!eventId || !("BroadcastChannel" in window)) return;
    const channel = new BroadcastChannel(`sessionbuddy:onboarding:${eventId}`);
    channel.postMessage({ type: "snapshot-invalidated" });
    channel.close();
  }

  async function load() {
    setStatus("Checking your secure session…");
    try {
      const session = await api("/api/v1/session");
      state.csrf = session.csrf_token;
      // The submission endpoint binds a proposal to the signed-in account's
      // email, so the composer needs the account address. /api/v1/session
      // deliberately omits it; the account view is the endpoint that carries it.
      try {
        const account = await api("/api/v1/auth/session");
        state.sessionEmail = account.email || "";
        state.sessionName = account.display_name || "";
      } catch (_) {
        state.sessionEmail = "";
        state.sessionName = "";
      }
      await loadProposalDrafts();
      await loadEvent();
      setStatus("Speaker details are ready.", "success");
    } catch (error) {
      if (error.status === 404 && state.csrf) {
        byId("portal").hidden = true;
        byId("auth-state").hidden = true;
        byId("empty-state").hidden = false;
        const draftCount = byId("saved-proposal-draft-list").children.length;
        setStatus(draftCount
          ? `${draftCount} saved proposal draft${draftCount === 1 ? "" : "s"}.`
          : "No proposals yet.");
      } else if (error.status === 401 || error.status === 404) {
        byId("portal").hidden = true;
        byId("empty-state").hidden = true;
        byId("auth-state").hidden = false;
        setStatus("Speaker access is required to view this portal.", "error");
      } else {
        setStatus("We couldn’t load your portal. Try again in a moment.", "error");
      }
    }
  }

  async function loadEvent(eventId = "") {
    const portal = await api(portalPath(eventId));
    renderPortal(portal);
    await Promise.all([loadAssets(), loadResources()]);
    await loadPortfolio();
  }

  // Four at a time keeps a speaker with a long history from opening a hundred
  // parallel requests while still filling the page quickly.
  async function inBatches(items, size, worker) {
    for (let index = 0; index < items.length; index += size) {
      await Promise.all(items.slice(index, index + size).map(worker));
    }
  }

  // The portal endpoint answers for one event at a time, so the remaining
  // events are fetched after the first paint and folded into the same view.
  async function loadPortfolio() {
    const pending = state.eventOrder.filter((eventId) => !state.portfolio.get(eventId)?.portal);
    if (!pending.length) return;
    await inBatches(pending, 4, async (eventId) => {
      try {
        const portal = await api(portalPath(eventId));
        rememberPortal(portal);
        if (portal.submissions?.length) await loadAssetsFor(eventId);
      } catch (_) {
        // One unreachable event must not blank the events that did load.
      }
    });
    renderPortfolio();
  }

  async function selectEvent(eventId) {
    if (eventId === state.activeEventId) return;
    if (!confirmComposerDiscard()) return;
    closeComposer({ discardDraft: true });
    setStatus("Loading event…");
    try {
      const portal = await api(portalPath(eventId));
      renderPortal(portal);
      await Promise.all([loadAssets(), loadResources()]);
      setStatus("Speaker details are ready.", "success");
    } catch (error) {
      setStatus(window.SessionBuddyApi.message(error, "This event could not be loaded."), "error");
    }
  }

  byId("speaker-sign-in").addEventListener("click", () => {
    location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`);
  });

  function safeUploadUrl(value) {
    try {
      const url = new URL(value, location.origin);
      const trustedR2 = url.protocol === "https:" && url.hostname.endsWith(".r2.cloudflarestorage.com");
      return url.origin === location.origin || trustedR2 ? url : null;
    } catch (_) { return null; }
  }

  function safeUploadHeaders(headers, contentType) {
    const safe = { "content-type": contentType };
    Object.entries(headers || {}).forEach(([name, value]) => {
      const lower = name.toLowerCase();
      if (["content-type", "x-amz-checksum-sha256"].includes(lower)) safe[lower] = String(value);
    });
    return safe;
  }

  function uploadFile(url, method, headers, file, progress) {
    return new Promise((resolve, reject) => {
      if (method !== "PUT") { reject(new Error("Upload authorization used an unsupported method.")); return; }
      const request = new XMLHttpRequest();
      request.open("PUT", url.toString());
      request.withCredentials = false;
      Object.entries(headers).forEach(([name, value]) => request.setRequestHeader(name, value));
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) progress.value = Math.round((event.loaded / event.total) * 100);
      });
      request.addEventListener("load", () => request.status >= 200 && request.status < 300
        ? resolve() : reject(new Error(`Storage upload failed (${request.status}).`)));
      request.addEventListener("error", () => reject(new Error("Storage upload could not be reached.")));
      request.send(file);
    });
  }

  async function checksum(file) {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function validateFile(kind, file) {
    const rule = uploadRules[kind];
    if (!file) return "Choose a file to upload.";
    if (!rule.types.has(file.type)) return "This file type is not allowed.";
    if (file.size <= 0 || file.size > rule.max) return `File must be larger than zero and no more than ${Math.round(rule.max / 1024 / 1024)} MB.`;
    return null;
  }

  function taskForKind(kind) {
    return state.portal?.tasks?.find((task) => task.task_type === kind && task.state === "open")?.id || null;
  }

  function safeEmbedUrl(value) {
    try {
      const url = new URL(value);
      const allowed = new Set(["www.youtube.com", "youtube.com", "www.youtube-nocookie.com", "player.vimeo.com", "docs.google.com", "drive.google.com", "calendar.google.com"]);
      return url.protocol === "https:" && allowed.has(url.hostname) ? url.toString() : null;
    } catch (_) { return null; }
  }

  async function loadResources() {
    try {
      const result = await api("/api/v1/speaker/resources");
      state.resources = result.data || [];
    } catch (_) {
      state.resources = [];
    }
    renderPortfolio();
  }

  async function loadAssetsFor(eventId) {
    const entry = state.portfolio.get(eventId);
    if (!entry) return;
    const result = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/assets`);
    entry.assets = result.data || [];
    if (eventId === state.activeEventId) state.assets = entry.assets;
  }

  async function loadAssets() {
    try {
      const eventId = state.activeEventId;
      if (!eventId) throw new Error("Speaker event is unavailable.");
      await loadAssetsFor(eventId);
      renderPortfolio();
    } catch (_) {
      const entry = state.portfolio.get(state.activeEventId);
      if (entry) entry.assets = [];
      state.assets = [];
      renderPortfolio();
      setStatus("Session files could not be loaded. Uploads are temporarily unavailable.", "error");
    }
  }

  function bindUploadForm(form) {
    let pendingCompletion = null;
    form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const kind = form.dataset.kind;
    const file = form.elements.file.files[0];
    const status = form.querySelector(".upload-status");
    const progress = form.querySelector("progress");
    const button = form.querySelector("button[type=submit]");
    const versionComment = form.elements.version_comment.value.trim();
    const validation = validateFile(kind, file);
    if (validation) { status.textContent = validation; status.classList.add("error"); return; }
    if (!versionComment) {
      form.elements.version_comment.setCustomValidity("Describe what changed in this version.");
      form.elements.version_comment.reportValidity();
      return;
    }
    form.elements.version_comment.setCustomValidity("");
    button.disabled = true; progress.hidden = false; progress.value = 0;
    status.classList.remove("error"); status.textContent = "Checking file integrity…";
    try {
      const eventId = state.portal?.event?.id;
      if (!eventId) throw new Error("Speaker event is unavailable.");
      const uploadRequest = {
        kind, submission_id: form.dataset.submissionId || null,
        task_id: taskForKind(kind), filename: file.name, content_type: file.type,
        byte_size: file.size, checksum_sha256: await checksum(file),
        version_comment: versionComment
      };
      const uploadFingerprint = JSON.stringify(uploadRequest);
      if (!pendingCompletion
          || pendingCompletion.fingerprint !== uploadFingerprint
          || pendingCompletion.expiresAtMs <= Date.now()) {
        pendingCompletion = null;
        const authorization = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/upload-authorizations`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
          body: JSON.stringify(uploadRequest)
        });
        const uploadUrl = safeUploadUrl(authorization.upload_url);
        if (!uploadUrl || authorization.expires_at_ms <= Date.now()) throw new Error("Upload authorization is invalid or expired.");
        status.textContent = "Uploading…";
        await uploadFile(uploadUrl, authorization.method, safeUploadHeaders(authorization.headers, file.type), file, progress);
        pendingCompletion = {
          fingerprint: uploadFingerprint,
          intentId: authorization.intent_id,
          expiresAtMs: authorization.expires_at_ms
        };
        status.textContent = "Upload received. Starting safety checks…";
      } else {
        status.textContent = "Upload received. Retrying safety checks…";
      }
      const completion = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/upload-intents/${encodeURIComponent(pendingCompletion.intentId)}/complete`, {
        method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() }, body: "{}"
      });
      pendingCompletion = null;
      progress.value = 100;
      if (completion.state === "rejected") {
        status.textContent = "This file was rejected by the safety scan. It was not made available. Choose a different file.";
        status.classList.add("error");
      } else if (completion.state === "clean") {
        status.textContent = "Upload checked and ready.";
      } else {
        status.textContent = "Upload complete. The file is being checked before it becomes current.";
      }
      form.elements.file.value = "";
      form.elements.version_comment.value = "";
      window.SessionBuddyApi.refreshCharacterCounters(form);
      await loadAssets();
      const portal = await api(portalPath()); renderPortal(portal);
      announceOnboardingChange();
    } catch (error) {
      status.textContent = pendingCompletion
        ? `File received. Safety checks are temporarily unavailable, so this file is not public or current yet. Press “${button.textContent.trim()}” again to retry. You do not need to choose or upload the file again.`
        : window.SessionBuddyApi.message(error, "Upload failed. Choose the file and try again.");
      status.classList.add("error");
    } finally { button.disabled = false; }
    });
  }

  load();
})();

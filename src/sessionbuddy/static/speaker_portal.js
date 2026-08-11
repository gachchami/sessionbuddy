(() => {
  "use strict";
  const state = {
    csrf: "", portal: null, assets: [],
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

  function composerDraftKey(eventId = state.composerEventId, formId = state.form?.id) {
    return eventId && formId ? `sessionbuddy:proposal-draft:${eventId}:${formId}` : "";
  }

  function composerIsDirty() {
    const form = byId("proposal-composer-form");
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
    const form = byId("proposal-composer-form");
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
    const summary = byId("proposal-composer-error");
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
    const summary = byId("proposal-composer-error");
    if (summary) { summary.hidden = true; summary.textContent = ""; }
    byId("proposal-composer-form")?.querySelectorAll('[aria-invalid="true"]').forEach((control) => control.removeAttribute("aria-invalid"));
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

  function customTaskForm(task) {
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
        const portal = await api(portalPath());
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

  function profileTaskForm() {
    const profile = state.portal?.profile || {};
    const form = make("form", undefined, "task-form");
    const fields = [
      ["display_name", "Display name", "text", true],
      ["job_title", "Job title", "text", false],
      ["company", "Company", "text", false],
      ["biography", "Biography", "textarea", true],
      ["location", "Location", "text", false],
      ["links", "Links (one per line)", "textarea", false]
    ];
    for (const [key, labelText, type, required] of fields) {
      const label = make("label", labelText);
      const input = type === "textarea" ? document.createElement("textarea") : document.createElement("input");
      input.name = key;
      input.required = required;
      input.maxLength = key === "biography" ? 5000 : key === "links" ? 20000 : 200;
      input.value = key === "links" ? (profile.links || []).join("\n") : profile[key] || "";
      label.append(input);
      form.append(label);
    }
    const submit = make("button", "Save profile");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      submit.disabled = true;
      try {
        await api("/api/v1/speaker/profile", {
          method: "PATCH",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
          body: JSON.stringify({
            display_name: form.elements.display_name.value,
            job_title: form.elements.job_title.value,
            company: form.elements.company.value,
            biography: form.elements.biography.value,
            location: form.elements.location.value,
            links: form.elements.links.value.split("\n").map((value) => value.trim()).filter(Boolean),
            version: profile.version
          })
        });
        await loadEvent(state.portal?.event?.id);
        announceOnboardingChange();
        setStatus("Profile saved.", "success");
      } catch (error) {
        setStatus(error.status === 409 ? "This profile changed. Reload and try again." : window.SessionBuddyApi.message(error, "The profile could not be saved."), "error");
        submit.disabled = false;
      }
    });
    return form;
  }

  function renderTasks(tasks, timezone) {
    const list = byId("task-list");
    list.replaceChildren();
    const outstanding = tasks.filter((task) => !["completed", "waived"].includes(task.state));
    byId("task-count").textContent = String(outstanding.length);
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
        action.href = "#speaker-profile-tools";
        item.append(action);
      } else if (task.task_type === "custom") {
        item.append(customTaskForm(task));
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

  function renderSubmissions(submissions) {
    const list = byId("submission-list");
    list.replaceChildren();
    if (!submissions.length) {
      list.append(make("li", "No proposals are connected to this account yet.", "empty"));
      return;
    }
    submissions.forEach((submission) => {
      const item = make("li", undefined, "item-card");
      const statusLabel = submission.status.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
      item.append(
        make("h3", submission.proposal_title),
        make("p", statusLabel, `state-badge${submission.status === "accepted" ? " success" : ""}`)
      );
      if (submission.editable) {
        const edit = make("button", "Edit proposal", "secondary");
        edit.type = "button";
        edit.addEventListener("click", () => {
          document.querySelectorAll(".proposal-editor").forEach((editor) => editor.remove());
          const editor = make("form", undefined, "proposal-editor");
          const titleLabel = make("label", "Proposal title");
          const title = document.createElement("input");
          title.name = "proposal_title";
          title.maxLength = 300;
          title.required = true;
          title.value = submission.proposal_title;
          titleLabel.append(title);
          const abstractLabel = make("label", "Proposal abstract");
          const abstract = document.createElement("textarea");
          abstract.name = "proposal_abstract";
          abstract.rows = 7;
          abstract.maxLength = 5000;
          abstract.required = true;
          abstract.value = submission.proposal_abstract;
          abstractLabel.append(abstract);
          const actions = make("div", undefined, "actions");
          const cancel = make("button", "Cancel", "secondary");
          cancel.type = "button";
          cancel.addEventListener("click", () => editor.remove());
          const save = make("button", "Save changes");
          save.type = "submit";
          actions.append(cancel, save);
          editor.append(titleLabel, abstractLabel, actions);
          editor.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!editor.reportValidity()) return;
            save.disabled = true;
            save.textContent = "Saving…";
            try {
              const updated = await api(`/api/v1/forms/${encodeURIComponent(submission.form_slug)}/submissions/${encodeURIComponent(submission.id)}`, {
                method: "PATCH",
                headers: {
                  "content-type": "application/json",
                  "x-csrf-token": state.csrf,
                  "idempotency-key": idempotencyKey()
                },
                body: JSON.stringify({
                  speaker_name: submission.speaker_name,
                  speaker_email: submission.speaker_email,
                  proposal_title: title.value.trim(),
                  proposal_abstract: abstract.value.trim(),
                  answers: {
                    ...submission.answers,
                    proposal_title: title.value.trim(),
                    proposal_abstract: abstract.value.trim()
                  },
                  version: submission.version
                })
              });
              Object.assign(submission, updated);
              renderSubmissions(submissions);
              setStatus("Proposal changes saved.", "success");
            } catch (error) {
              setStatus(error.status === 409
                ? "This proposal changed elsewhere. Reload the portal and try again."
                : window.SessionBuddyApi.message(error, "The proposal could not be saved."), "error");
              save.disabled = false;
              save.textContent = "Save changes";
            }
          });
          item.append(editor);
          title.focus();
        });
        item.append(edit);
        const withdraw = make("button", "Withdraw proposal", "secondary");
        withdraw.type = "button";
        withdraw.addEventListener("click", async () => {
          if (!confirm("Withdraw this proposal? It will become read-only and cannot enter review.")) return;
          withdraw.disabled = true;
          withdraw.textContent = "Withdrawing…";
          try {
            const updated = await api(`/api/v1/forms/${encodeURIComponent(submission.form_slug)}/submissions/${encodeURIComponent(submission.id)}/withdraw`, {
              method: "POST",
              headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
              body: "{}"
            });
            Object.assign(submission, updated);
            renderSubmissions(submissions);
            setStatus("Proposal withdrawn. It is now read-only.", "success");
          } catch (error) {
            setStatus(window.SessionBuddyApi.message(error, "The proposal could not be withdrawn."), "error");
            withdraw.disabled = false;
            withdraw.textContent = "Withdraw proposal";
          }
        });
        item.append(withdraw);
      }
      if (submission.status === "withdrawn") item.append(make("p", "This proposal is withdrawn and read-only.", "help"));
      const files = document.createElement("details");
      files.className = "session-files";
      files.append(make("summary", "Files"));
      const saved = make("ul", undefined, "session-file-list");
      const sessionAssets = state.assets.filter((asset) => asset.submission_id === submission.id);
      if (!sessionAssets.length) saved.append(make("li", "No files uploaded for this session.", "empty"));
      sessionAssets.forEach((asset) => saved.append(make("li", `${asset.kind === "slides" ? "Slides" : "Document"}: ${asset.filename}`)));
      files.append(saved);
      if (submission.status !== "withdrawn") {
        const uploads = make("div", undefined, "session-upload-grid");
        uploads.append(
          createUploadForm("slides", submission.id),
          createUploadForm("supporting_document", submission.id)
        );
        files.append(uploads);
      }
      item.append(files);
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
    const trigger = byId("open-proposal-composer");
    const availability = byId("call-availability");
    // A half-written proposal survives incidental portal refreshes (completing
    // a task, uploading session files). Only a different event, or the call
    // disappearing, discards it.
    const composing = !byId("proposal-composer").hidden;
    if (!composing || !call || state.composerEventId !== state.portal?.event?.id) closeComposer();
    if (!call) {
      trigger.hidden = true;
      availability.hidden = true;
      return;
    }
    const composerOpen = !byId("proposal-composer").hidden;
    trigger.hidden = !call.accepting_submissions && !composerOpen;
    availability.hidden = call.accepting_submissions;
    if (!call.accepting_submissions) availability.textContent = call.availability_message;
    if (call.accepting_submissions && typeof call.remaining_submissions === "number") {
      trigger.textContent = call.remaining_submissions === 1
        ? "Submit a proposal (1 left)"
        : `Submit a proposal (${call.remaining_submissions} left)`;
    } else {
      trigger.textContent = "Submit a proposal";
    }
  }

  function closeComposer({ discardDraft = false } = {}) {
    if (discardDraft) clearComposerDraft();
    const composer = byId("proposal-composer");
    composer.replaceChildren();
    composer.hidden = true;
    byId("open-proposal-composer").setAttribute("aria-expanded", "false");
    state.form = null;
    state.composerEventId = null;
    state.files.clear();
    state.uploaded.clear();
    state.applyConditions = () => {};
  }

  function proposalFieldValue(field) {
    const form = byId("proposal-composer-form");
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
      const form = byId("proposal-composer-form");
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
    const composer = byId("proposal-composer");
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
      byId("open-proposal-composer").focus();
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
      if (byId("proposal-composer-error")?.hidden) {
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
      const trigger = byId("open-proposal-composer");
      (trigger.hidden ? byId("status") : trigger).focus();
    });
    const firstField = fields.querySelector("input:not(:disabled), textarea:not(:disabled), select:not(:disabled)");
    firstField?.focus();
  }

  async function openComposer() {
    const call = state.portal?.open_call;
    if (!call?.accepting_submissions) return;
    const requestedEventId = state.portal?.event?.id || "";
    const trigger = byId("open-proposal-composer");
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

  function renderPortal(portal) {
    if (portal.open_call && state.closedCallForms.has(portal.open_call.form_id)) {
      portal.open_call = {
        ...portal.open_call,
        accepting_submissions: false,
        availability_message: portal.open_call.availability_message || "Applications are closed."
      };
    }
    state.portal = portal;
    const event = portal.event;
    byId("welcome-name").textContent = portal.profile.display_name || "speaker";
    const publicProfile = byId("public-profile-link");
    publicProfile.hidden = !portal.public_profile_url;
    if (portal.public_profile_url) publicProfile.href = portal.public_profile_url;
    const availableEvents = portal.events || [event];
    const activeList = byId("active-event-list");
    const pastList = byId("past-event-list");
    activeList.replaceChildren();
    pastList.replaceChildren();
    byId("event-count").textContent = String(availableEvents.length);
    const pastEvents = availableEvents.filter((available) => available.ends_at_ms < Date.now());
    byId("past-event-count").textContent = String(pastEvents.length);
    availableEvents.forEach((available) => {
      const option = make("button", undefined, "event-index__item");
      option.type = "button";
      option.dataset.eventId = available.id;
      option.classList.toggle("is-current", available.id === event.id);
      if (available.id === event.id) option.setAttribute("aria-current", "page");
      const copy = make("span");
      copy.append(make("strong", available.name), make("small", eventTimeLabel(available.starts_at_ms, available.time_zone)));
      option.append(copy, make("span", available.id === event.id ? "Open" : "View", "event-index__state"));
      option.addEventListener("click", () => selectEvent(available.id));
      (available.ends_at_ms < Date.now() ? pastList : activeList).append(option);
    });
    if (!activeList.children.length) activeList.append(make("p", "No upcoming events.", "empty"));
    if (!pastList.children.length) pastList.append(make("p", "No past events yet.", "empty"));
    const eventDates = `${formatDate(event.starts_at_ms, event.time_zone)}–${formatDate(event.ends_at_ms, event.time_zone)}`;
    byId("event-summary").textContent = `${event.name} · ${eventDates} · Event time (${event.time_zone})`;
    byId("task-event-label").textContent = event.name;
    byId("session-event-label").textContent = event.name;
    byId("notification-event-label").textContent = `${event.name} · Event time (${event.time_zone})`;
    const tasks = portal.tasks || [];
    renderTasks(tasks, event.time_zone);
    byId("speaker-profile-form").replaceChildren(profileTaskForm());
    byId("speaker-headshot-form").replaceChildren(createUploadForm("headshot", ""));
    renderOpenCall(portal.open_call || null);
    renderSubmissions(portal.submissions || []);
    renderNotifications(portal.notifications || [], event.time_zone);
    byId("auth-state").hidden = true;
    byId("portal").hidden = false;
  }

  function renderNotifications(notifications, timezone) {
    const list = byId("notification-list");
    list.replaceChildren();
    byId("notification-count").textContent = String(notifications.length);
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
      await loadEvent();
      setStatus("Speaker details are ready.", "success");
    } catch (error) {
      if (error.status === 401 || error.status === 404) {
        byId("portal").hidden = true;
        byId("auth-state").hidden = false;
        setStatus("Speaker access is required to view this portal.", "error");
      } else {
        setStatus("We couldn’t load your portal. Try again in a moment.", "error");
      }
    }
  }

  async function loadEvent(eventId = "") {
    state.assets = [];
    const portal = await api(portalPath(eventId));
    renderPortal(portal);
    await Promise.all([loadAssets(), loadResources()]);
  }

  async function selectEvent(eventId) {
    if (eventId === state.portal?.event?.id) return;
    if (!confirmComposerDiscard()) return;
    closeComposer({ discardDraft: true });
    setStatus("Loading event…");
    try {
      await loadEvent(eventId);
      setStatus("Speaker details are ready.", "success");
    } catch (error) {
      setStatus(window.SessionBuddyApi.message(error, "This event could not be loaded."), "error");
    }
  }

  byId("speaker-sign-in").addEventListener("click", () => {
    location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`);
  });

  byId("open-proposal-composer").addEventListener("click", () => {
    if (byId("proposal-composer").hidden) {
      openComposer();
      return;
    }
    // Never discard a proposal that is mid-flight; its uploads are still being
    // staged and the submission may already be on the wire.
    if (byId("proposal-composer-form")?.getAttribute("aria-busy") === "true") return;
    if (!confirmComposerDiscard()) return;
    closeComposer({ discardDraft: true });
  });

  addEventListener("beforeunload", (event) => {
    if (!composerIsDirty()) return;
    event.preventDefault();
    event.returnValue = "";
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
    const container = byId("resource-list");
    try {
      const result = await api("/api/v1/speaker/resources");
      const resources = result.data.filter((resource) => resource.event_id === state.portal?.event?.id);
      byId("resource-count").textContent = String(resources.length);
      if (!resources.length) {
        container.replaceChildren(make("p", "No resources have been published yet.", "empty"));
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
    } catch (_) {
      container.replaceChildren(make("p", "Resources could not be loaded.", "empty"));
    }
  }

  async function loadAssets() {
    try {
      const eventId = state.portal?.event?.id;
      if (!eventId) throw new Error("Speaker event is unavailable.");
      const result = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/assets`);
      const assets = result.data || [];
      state.assets = assets;
      renderSubmissions(state.portal?.submissions || []);
    } catch (_) {
      state.assets = [];
      renderSubmissions(state.portal?.submissions || []);
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

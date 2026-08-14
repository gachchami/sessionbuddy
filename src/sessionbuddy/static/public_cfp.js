(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const pathParts = location.pathname.split("/").filter(Boolean);
  const workspaceMatch = location.pathname.match(/^\/speaker\/proposals\/([^/]+)(?:\/([^/]+))?$/);
  const workspaceMode = Boolean(workspaceMatch);
  const workspaceSegment = workspaceMatch?.[2] ? decodeURIComponent(workspaceMatch[2]) : "";
  const slug = workspaceMode ? decodeURIComponent(workspaceMatch[1]) : decodeURIComponent(pathParts.pop() || "");
  if (workspaceMode) document.body.classList.add("proposal-management-page");
  const browserSessionId = () => {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const value = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
  };
  const state = {
    csrf: "", form: null, draftVersion: 0, publicSession: browserSessionId(),
    files: new Map(), uploaded: new Map(), existingFiles: new Map(),
    applyConditions: () => {},
    sessionEmail: "", sessionDisplayName: "", authenticated: false, editingSubmission: null,
    viewingSubmission: null, submissions: [], draftDirty: false, draftTimer: null,
    pendingSubmission: null
  };
  const browserDraftKey = () => `sessionbuddy:cfp:${slug}:draft:${workspaceMode
    ? (state.editingSubmission?.id || workspaceSegment) : "new"}`;
  const BROWSER_DRAFT_TTL_MS = 30 * 60 * 1000;

  function normalizedEmail(value) {
    return String(value || "").trim().toLowerCase();
  }

  function hasMeaningfulProposalAnswers(values) {
    return Object.entries(values || {}).some(([key, value]) =>
      !["speaker_name", "speaker_email"].includes(key)
      && (Array.isArray(value) ? value.length > 0 : String(value ?? "").trim().length > 0)
    );
  }

  function browserDraftMatchesServerDraft(browser, server) {
    if (!browser || !server) return false;
    if ((browser.coSpeakers || []).length || (browser.fileNames || []).length || browser.readyToSubmit) return false;
    const keys = new Set([...Object.keys(browser.answers || {}), ...Object.keys(server.answers || {})]);
    return [...keys].every((key) => JSON.stringify(browser.answers?.[key]) === JSON.stringify(server.answers?.[key]));
  }

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
      schema_version: 1, page_template: "/cfp/{event_key}/{slug}", navigation_type: navigation?.type || "unknown",
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

  async function updateDuplicateTitleWarning(input, warning, checkServer = true) {
    const title = input.value.trim().toLowerCase();
    let duplicate = state.submissions.find((submission) =>
      submission.id !== state.editingSubmission?.id
      && String(submission.proposal_title || "").trim().toLowerCase() === title
    );
    if (!duplicate && checkServer && state.authenticated && title) {
      const params = new URLSearchParams({ title: input.value.trim() });
      if (state.editingSubmission?.id) params.set("exclude_id", state.editingSubmission.id);
      try {
        duplicate = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions/title-match?${params}`);
      } catch (_) {
        // This warning is advisory; lookup failures must not block a valid proposal.
      }
    }
    warning.replaceChildren();
    warning.hidden = !duplicate;
    if (!duplicate) return;
    const date = new Date(duplicate.submitted_at_ms).toLocaleDateString(undefined, {
      day: "numeric", month: "short", year: "numeric"
    });
    warning.append(
      `You already have a proposal with this title, submitted ${date}, receipt ${duplicate.id.slice(0, 8)}. `
    );
    const link = make("a", "Open that proposal");
    link.href = `/speaker/proposals/${encodeURIComponent(slug)}/${encodeURIComponent(duplicate.id)}`;
    warning.append(link, ". You can still use this title.");
  }

  function make(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function renderRichText(container, markup, fallback) {
    const parsed = new DOMParser().parseFromString(markup || "", "text/html");
    container.replaceChildren(...[...parsed.body.childNodes].map((child) => document.importNode(child, true)));
    if (!container.textContent.trim()) container.textContent = fallback || "";
  }

  function renderCallBrief(form) {
    const section = byId("cfp-brief");
    const content = byId("cfp-brief-content");
    if (!form.description_html) {
      section.hidden = true;
      return;
    }
    renderRichText(content, form.description_html, "");
    const briefText = content.textContent.replace(/\s+/g, " ").trim();
    const welcomeText = String(form.welcome_text || "").replace(/\s+/g, " ").trim();
    section.hidden = !briefText || briefText === welcomeText;
  }

  function renderEventHeader(form) {
    byId("event-public-header").style.setProperty("--event-preview-accent", form.accent_color || "#3159d9");
    byId("event-title").textContent = form.event_name || "Event";
    byId("event-monogram").textContent = (form.event_name || "EV").slice(0, 2).toUpperCase();
    if (form.event_starts_at_ms && form.event_ends_at_ms) {
      const start = new Date(form.event_starts_at_ms);
      const end = new Date(form.event_ends_at_ms);
      const timeZone = form.event_time_zone || "UTC";
      const year = (date) => date.toLocaleDateString("en", { year: "numeric", timeZone });
      const sameYear = year(start) === year(end);
      const startLabel = start.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone, ...(sameYear ? {} : { year: "numeric" }) });
      const endLabel = end.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric", timeZone });
      byId("event-dates").textContent = `${startLabel} – ${endLabel}`;
    }
    const delivery = String(form.event_delivery_mode || "").replaceAll("_", " ");
    byId("event-location").textContent = [form.event_location, delivery].filter(Boolean).join(" · ");
    try {
      const website = new URL(form.event_website_url || "");
      if (["https:", "http:"].includes(website.protocol)) {
        const link = byId("event-website");
        link.href = website.toString();
        link.textContent = website.hostname;
        link.hidden = false;
      }
    } catch (_) { /* website is optional */ }
  }

  function renderImportantDates(dates) {
    const section = byId("important-dates");
    const list = byId("important-dates-list");
    const sorted = [...(dates || [])].sort((left, right) => left.at_ms - right.at_ms);
    list.replaceChildren(...sorted.map((date) => {
      const item = make("li");
      item.append(make("strong", date.label), make("time", new Date(date.at_ms).toLocaleString()));
      return item;
    }));
    section.hidden = sorted.length === 0;
  }

  function formatEventDate(value, form) {
    if (!value) return "";
    return new Intl.DateTimeFormat(undefined, {
      timeZone: form.event_time_zone || "UTC",
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short"
    }).format(new Date(value));
  }

  function availabilityMessage(form) {
    const boundary = formatEventDate(form.availability_boundary_at_ms, form);
    if (form.availability_state === "scheduled" && boundary) {
      return `${form.availability_message} Applications open ${boundary}.`;
    }
    if (form.availability_state === "closed" && boundary) {
      return `${form.availability_message} The call closed ${boundary}.`;
    }
    return form.availability_message;
  }

  function renderCallDetails(form) {
    const openingLabel = formatEventDate(form.opens_at_ms, form);
    const deadlineLabel = formatEventDate(form.closes_at_ms, form);
    byId("call-opens").textContent = openingLabel || "Open now";
    byId("call-deadline").textContent = deadlineLabel || "No closing date set";
    // The close date is the single most decision-relevant fact for a submitter,
    // so it also belongs in the event header rather than only in the sidebar
    // card, which sits below the fold in the two-column application layout.
    const headerDeadline = byId("event-deadline");
    headerDeadline.textContent = deadlineLabel ? `Proposal deadline: ${deadlineLabel}` : "";
    headerDeadline.hidden = !deadlineLabel;
    const conditionalTargets = new Set((form.conditions || []).map((condition) => condition.target_key));
    const visibleFields = form.fields.filter((field) => !conditionalTargets.has(field.key));
    const effort = visibleFields.reduce((minutes, field) => minutes + (field.type === "textarea" ? 3 : ["file", "image"].includes(field.type) ? 2 : 1), 0);
    byId("call-time-estimate").textContent = `${Math.max(5, Math.ceil(effort / 5) * 5)}–${Math.max(10, Math.ceil(effort / 5) * 5 + 5)} minutes`;
    byId("call-conditional-note").hidden = conditionalTargets.size === 0;
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
      const label = make("label");
      const fieldLabel = make("span", field.label, "field-label");
      if (field.required) {
        const marker = make("span", "*", "required-marker");
        marker.setAttribute("aria-hidden", "true");
        fieldLabel.append(marker);
      }
      label.append(fieldLabel);
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
        input.maxLength = field.key === "speaker_email" || field.type === "email"
          ? 320
          : ["speaker_name", "proposal_title"].includes(field.key)
            ? 200
            : 500;
      }
      input.name = field.key;
      input.id = `field-${field.key}`;
      input.required = Boolean(field.required);
      input.dataset.required = field.required ? "true" : "false";
      if (field.placeholder && !["select", "multiselect", "checkbox", "file", "image"].includes(field.type)) input.placeholder = field.placeholder;
      if (field.key === "speaker_name") input.autocomplete = "name";
      if (field.key === "speaker_email") input.autocomplete = "email";
      label.append(input);
      if (field.key === "proposal_title") {
        const warning = make("small", "", "field-warning duplicate-title-warning");
        warning.id = "duplicate-title-warning";
        warning.hidden = true;
        warning.setAttribute("aria-live", "polite");
        input.setAttribute("aria-describedby", warning.id);
        input.addEventListener("blur", () => { void updateDuplicateTitleWarning(input, warning); });
        input.addEventListener("input", () => {
          if (!warning.hidden) void updateDuplicateTitleWarning(input, warning, false);
        });
        label.append(warning);
      }
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

  function addCoSpeakerRow(value = {}, focus = false) {
    const row = make("div", undefined, "form-grid co-speaker-row");
    const nameLabel = make("label", "Name"); const name = document.createElement("input");
    name.name = "co_speaker_name"; name.required = true; name.maxLength = 200; name.value = value.display_name || ""; nameLabel.append(name);
    const emailLabel = make("label", "Email"); const email = document.createElement("input");
    email.name = "co_speaker_email"; email.type = "email"; email.required = true; email.maxLength = 320; email.value = value.email || ""; emailLabel.append(email);
    const roleLabel = make("label", "Role"); const role = document.createElement("select");
    role.name = "co_speaker_role";
    (state.form?.participant_roles || []).forEach(({ value: optionValue, label }) => {
      const option = document.createElement("option"); option.value = optionValue; option.textContent = label; role.append(option);
    });
    role.value = value.role || "co_speaker"; roleLabel.append(role);
    const remove = make("button", "Remove", "secondary"); remove.type = "button"; remove.addEventListener("click", () => {
      row.remove();
      validateCoSpeakers(byId("proposal-form"));
      queueBrowserDraft();
    });
    const actions = make("div", undefined, "co-speaker-row__actions");
    actions.append(remove);
    row.append(nameLabel, emailLabel, roleLabel, actions); byId("co-speaker-rows").append(row);
    if (focus) name.focus();
  }

  function coSpeakers() {
    return [...document.querySelectorAll(".co-speaker-row")].map((row) => ({
      display_name: row.querySelector('[name="co_speaker_name"]').value.trim(),
      email: row.querySelector('[name="co_speaker_email"]').value.trim(),
      role: row.querySelector('[name="co_speaker_role"]').value
    }));
  }

  function validateCoSpeakers(form) {
    const primaryEmail = String(form.elements.namedItem("speaker_email")?.value || "").trim().toLowerCase();
    const rows = [...byId("co-speaker-rows").querySelectorAll(".co-speaker-row")];
    const seen = new Set();
    const limit = state.form?.co_speaker_limit ?? 1;
    let valid = rows.length <= limit;
    for (const row of rows) {
      const email = row.querySelector('[name="co_speaker_email"]');
      const normalized = email.value.trim().toLowerCase();
      email.setCustomValidity("");
      if (normalized && normalized === primaryEmail) {
        email.setCustomValidity("An additional participant must use a different email from the primary speaker.");
        valid = false;
      } else if (normalized && seen.has(normalized)) {
        email.setCustomValidity("Each additional participant must use a different email.");
        valid = false;
      }
      if (normalized) seen.add(normalized);
    }
    byId("add-co-speaker").disabled = rows.length >= limit;
    return valid;
  }

  function invitationStatus(value) {
    return { pending: "Pending", accepted: "Accepted", declined: "Declined", removed: "Removed" }[value] || "Pending";
  }

  function invitationEndpoint(submission, invitation) {
    return `/api/v1/forms/${encodeURIComponent(slug)}/submissions/${encodeURIComponent(submission.id)}/co-speakers/${encodeURIComponent(invitation.id)}`;
  }

  async function reloadSubmissions(selectedId) {
    const mine = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions/mine`);
    state.submissions = mine.data || [];
    const selected = state.submissions.find((submission) => submission.id === selectedId);
    if (selected) chooseSubmission(selected);
  }

  async function recoverOptimisticConflict(error) {
    if (error.status !== 409 || !state.editingSubmission) return false;
    const submissionId = state.editingSubmission.id;
    clearBrowserDraft();
    state.draftDirty = false;
    await reloadSubmissions(submissionId);
    setStatus(
      "This proposal changed while you were editing. The latest saved version is loaded; review it before saving again.",
      "error"
    );
    return true;
  }

  function renderCoSpeakerInvitations(submission) {
    const section = byId("co-speaker-invitations");
    const list = byId("co-speaker-invitation-list");
    const invitations = (submission.co_speakers || []).filter((item) => item.id);
    section.hidden = !submission.editable || invitations.length === 0;
    list.replaceChildren();
    for (const invitation of invitations) {
      const card = make("article", undefined, "co-speaker-invitation");
      const identity = make("div");
      identity.append(
        make("strong", invitation.display_name),
        make("span", `${invitation.role_label} · ${invitation.email}`, "help")
      );
      const meta = make("div", undefined, "co-speaker-invitation__meta");
      const badge = make("span", invitationStatus(invitation.invitation_status), `badge${invitation.invitation_status === "accepted" ? " success" : ""}`);
      meta.append(badge);
      if (invitation.invitation_status === "pending" && invitation.expires_at_ms) {
        meta.append(make("span", `Expires ${new Date(invitation.expires_at_ms).toLocaleString()}`, "help"));
      }
      const actions = make("div", undefined, "actions");
      if (["pending", "declined"].includes(invitation.invitation_status)) {
        const resend = make("button", "Send again", "secondary");
        resend.type = "button";
        resend.addEventListener("click", async () => {
          resend.disabled = true;
          try {
            await api(`${invitationEndpoint(submission, invitation)}/resend`, {
              method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${browserSessionId()}-${browserSessionId()}` }, body: "{}"
            });
            await reloadSubmissions(submission.id);
            setStatus(`A new invitation was sent to ${invitation.email}.`, "success");
          } catch (error) { setStatus(window.SessionBuddyApi.message(error), "error"); resend.disabled = false; }
        });
        actions.append(resend);
      }
      if (invitation.invitation_status !== "removed") {
        const remove = make("button", "Remove", "secondary");
        remove.type = "button";
        remove.addEventListener("click", async () => {
          remove.disabled = true;
          try {
            await api(invitationEndpoint(submission, invitation), {
              method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": state.csrf }
            });
            await reloadSubmissions(submission.id);
            setStatus(`${invitation.display_name} was removed from this proposal.`, "success");
          } catch (error) { setStatus(window.SessionBuddyApi.message(error), "error"); remove.disabled = false; }
        });
        actions.append(remove);
      }
      card.append(identity, meta, actions); list.append(card);
    }
  }

  function resetProposalFiles() {
    // One reset for every piece of per-proposal file state so nothing leaks
    // between proposals within a single page session: selections, uploaded
    // references, existing-attachment notes, and required-field relaxations.
    state.files.clear();
    state.uploaded.clear();
    state.existingFiles.clear();
    for (const field of state.form?.fields || []) {
      if (!["file", "image"].includes(field.type)) continue;
      const control = byId("proposal-form").elements.namedItem(field.key);
      if (!control) continue;
      control.value = "";
      control.required = control.dataset.required === "true";
      document.getElementById(`${field.key}-existing-file`)?.remove();
    }
    state.applyConditions();
    window.SessionBuddyApi.refreshCharacterCounters?.(byId("proposal-form"));
  }

  function restoreValues(values) {
    for (const field of state.form.fields || []) {
      const control = byId("proposal-form").elements.namedItem(field.key);
      const value = values[field.key];
      if (!control || value === undefined) continue;
      if (["file", "image"].includes(field.type)) {
        // Track that a file already exists and stop requiring a re-upload;
        // the server keeps the stored file unless a new one is chosen.
        if ((state.editingSubmission || state.viewingSubmission) && value) {
          state.existingFiles.set(field.key, value);
        }
        if (state.editingSubmission && value) {
          control.required = false;
          const noteId = `${field.key}-existing-file`;
          if (!document.getElementById(noteId) && control.closest("label")) {
            const note = document.createElement("p");
            note.id = noteId;
            note.className = "help";
            note.textContent = "A file is already attached. Choose a new file only to replace it.";
            control.closest("label").append(note);
          }
        }
        continue;
      }
      if (field.type === "checkbox") control.checked = Boolean(value);
      else if (field.type === "multiselect" && Array.isArray(value)) {
        [...control.options].forEach((option) => { option.selected = value.includes(option.value); });
      } else control.value = String(value ?? "");
    }
    state.applyConditions();
    window.SessionBuddyApi.refreshCharacterCounters?.(byId("proposal-form"));
  }

  function browserDraft() {
    try {
      const saved = JSON.parse(localStorage.getItem(browserDraftKey()) || "null");
      if (!saved || saved.schemaVersion !== 1) return null;
      if (!Number.isSafeInteger(saved.savedAt) || Date.now() - saved.savedAt > BROWSER_DRAFT_TTL_MS || saved.savedAt > Date.now() + 60_000) {
        localStorage.removeItem(browserDraftKey());
        return null;
      }
      return saved;
    } catch (_) { return null; }
  }

  function saveBrowserDraft(readyToSubmit = false) {
    const value = {
      schemaVersion: 1,
      formVersion: state.form.version,
      answers: answers({ includeUploads: false }),
      coSpeakers: coSpeakers(),
      submissionId: state.editingSubmission?.id || null,
      fileNames: [...state.files.values()].map((file) => file.name),
      readyToSubmit,
      ownerEmail: normalizedEmail(answers({ includeUploads: false }).speaker_email),
      savedAt: Date.now()
    };
    try { localStorage.setItem(browserDraftKey(), JSON.stringify(value)); }
    catch (_) { throw new Error("This browser could not retain the proposal. Enable site storage and try again."); }
    return value;
  }

  function queueBrowserDraft() {
    state.draftDirty = true;
    clearTimeout(state.draftTimer);
    state.draftTimer = setTimeout(() => {
      try {
        saveBrowserDraft(false);
      } catch (error) {
        setStatus(error.message, "error");
      }
    }, 500);
  }

  function restoreBrowserDraft(expectedEmail) {
    const saved = browserDraft();
    if (!saved || saved.formVersion !== state.form.version) return null;
    if (!saved.ownerEmail || saved.ownerEmail !== normalizedEmail(expectedEmail)) return null;
    if (!hasMeaningfulProposalAnswers(saved.answers) && !(saved.coSpeakers || []).length && !(saved.fileNames || []).length && !saved.readyToSubmit) {
      clearBrowserDraft();
      return null;
    }
    restoreValues(saved.answers || {});
    byId("co-speaker-rows").replaceChildren();
    (saved.coSpeakers || []).forEach(addCoSpeakerRow);
    state.draftDirty = true;
    return saved;
  }

  function clearBrowserDraft() {
    try { localStorage.removeItem(browserDraftKey()); } catch (_) { /* best effort */ }
  }

  async function loadDraft() {
    let draft;
    try {
      draft = await api(`/api/v1/forms/${encodeURIComponent(slug)}/draft`);
    } catch (error) {
      if ([401, 404].includes(error.status)) return;
      throw error;
    }
    if (!draft || !hasMeaningfulProposalAnswers(draft.answers)) return null;
    state.draftVersion = draft.version;
    restoreValues(draft.answers || {});
    setStatus("Your saved draft has been restored.");
    return draft;
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

  function applySignedInIdentity() {
    // Account identity wins for a new proposal, including after restoring a
    // browser or server draft created before the account lookup completed.
    if (state.editingSubmission || state.viewingSubmission) return;
    const form = byId("proposal-form");
    const name = form.elements.namedItem("speaker_name");
    if (name && state.sessionDisplayName) name.value = state.sessionDisplayName;
    lockSignedInEmail();
  }

  function chooseSubmission(submission) {
    const editable = submission.editable === true && state.form.accepting_submissions !== false;
    resetProposalFiles();
    state.viewingSubmission = submission;
    state.editingSubmission = editable ? submission : null;
    if (!editable) {
      clearTimeout(state.draftTimer);
      state.draftTimer = null;
      state.draftDirty = false;
      clearBrowserDraft();
    }
    byId("proposal-form-title").textContent = editable ? "Edit proposal" : "Proposal details";
    restoreValues({ ...submission.answers, speaker_name: submission.speaker_name,
      speaker_email: submission.speaker_email, proposal_title: submission.proposal_title,
      proposal_abstract: submission.proposal_abstract });
    byId("co-speaker-rows").replaceChildren();
    (submission.co_speakers || []).filter((item) => item.invitation_status !== "removed").forEach(addCoSpeakerRow);
    renderCoSpeakerInvitations(submission);
    const form = byId("proposal-form");
    validateCoSpeakers(form);
    for (const control of form.elements) control.disabled = !editable;
    if (editable) lockSignedInEmail();
    byId("withdraw-proposal").hidden = !editable || submission.status === "withdrawn";
    byId("submit-proposal").textContent = editable ? "Save changes" : "Confirm submission";
    let viewingStatus = `Viewing “${submission.proposal_title}”. Only the primary submitter can make changes.`;
    if (state.form.accepting_submissions === false) {
      viewingStatus = `Viewing “${submission.proposal_title}”. The call for proposals is closed, so this proposal is read-only.`;
    } else if (submission.status === "withdrawn") {
      viewingStatus = `Viewing “${submission.proposal_title}”. This proposal was withdrawn and is read-only.`;
    } else if (["accepted", "rejected"].includes(submission.status)) {
      viewingStatus = `Viewing “${submission.proposal_title}”. A final decision has been recorded, so this proposal is read-only.`;
    }
    setStatus(
      editable
        ? `Editing “${submission.proposal_title}”. Changes update this proposal; they do not create a duplicate.`
        : viewingStatus,
      "success"
    );
  }

  byId("withdraw-proposal").addEventListener("click", async (event) => {
    const submission = state.editingSubmission;
    if (!submission || !confirm("Withdraw this proposal? It will become read-only and cannot enter review.")) return;
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Withdrawing…";
    try {
      const updated = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions/${encodeURIComponent(submission.id)}/withdraw`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${browserSessionId()}-${browserSessionId()}` },
        body: "{}"
      });
      Object.assign(submission, updated, { editable: false });
      chooseSubmission(submission);
      setStatus("Proposal withdrawn. It is now read-only.", "success");
    } catch (error) {
      button.disabled = false;
      button.textContent = "Withdraw proposal";
      setStatus(window.SessionBuddyApi.message(error), "error");
    }
  });

  function renderReview() {
    const list = byId("review-list");
    list.replaceChildren();
    for (const field of state.form.fields) {
      const wrapper = byId("proposal-form").elements.namedItem(field.key)?.closest("label");
      if (wrapper?.hidden) continue;
      let value = ["file", "image"].includes(field.type)
        ? state.files.get(field.key)?.name
          || (state.existingFiles.has(field.key) ? "Existing file attached" : "No file selected")
        : fieldValue(field);
      if (typeof value === "boolean") value = value ? "Yes" : "No";
      if (Array.isArray(value)) value = value.join(", ");
      const group = document.createElement("div");
      group.append(make("dt", field.label), make("dd", String(value || "Not provided")));
      list.append(group);
    }
    const speakers = coSpeakers();
    if (speakers.length) {
      const group = document.createElement("div");
      group.append(make("dt", "Co-speakers"), make("dd", speakers.map((speaker) => `${speaker.display_name} (${speaker.email})`).join(", ")));
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
    // Staged CFP uploads only need a verified email session; the speaker
    // record is created when the submission itself succeeds.
    const authorization = await api(`/api/v1/cfp/forms/${encodeURIComponent(state.form.id)}/upload-authorizations`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${browserSessionId()}-${browserSessionId()}` },
      body: JSON.stringify({ kind, filename: file.name, content_type: file.type, byte_size: file.size, checksum_sha256: await checksum(file) })
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
    const completionPath = `/api/v1/cfp/forms/${encodeURIComponent(state.form.id)}/upload-authorizations/${encodeURIComponent(authorization.staged_id)}/complete`;
    let completion = await api(completionPath, {
      method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${browserSessionId()}-${browserSessionId()}` }, body: "{}"
    });
    for (let attempt = 0; ["uploaded", "scanning"].includes(completion.state) && attempt < 60; attempt += 1) {
      setStatus(`Checking ${field.label} for safety…`);
      await new Promise((resolve) => setTimeout(resolve, 1000));
      completion = await api(completionPath, {
        method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${browserSessionId()}-${browserSessionId()}` }, body: "{}"
      });
    }
    if (completion.state === "rejected") throw new Error(`${field.label} did not pass the safety check.`);
    if (completion.state !== "staged") throw new Error(`${field.label} is still being checked. Try again in a moment.`);
    state.uploaded.set(field.key, `staged:${authorization.staged_id}`);
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
      renderEventHeader(state.form);
      renderCallBrief(state.form);
      renderImportantDates(state.form.important_dates);
      if (state.form.event_name) {
        document.title = workspaceMode
          ? `Proposal · ${state.form.event_name}`
          : `Call for Proposals · ${state.form.event_name}`;
      }
      if (workspaceMode) {
        byId("title").textContent = "Proposal";
        byId("welcome").textContent = state.form.event_name;
      }
      if (state.form.accent_color) document.documentElement.style.setProperty("--blue", state.form.accent_color);
      if (state.form.logo_url) { byId("event-logo").src = state.form.logo_url; byId("event-logo").hidden = false; }
      if (state.form.cover_image_url) { byId("event-cover").src = state.form.cover_image_url; byId("event-cover").alt = `${state.form.event_name} cover`; byId("event-cover").hidden = false; byId("event-cover-empty").hidden = true; }
      renderFields(state.form.fields || [], state.form.conditions || []);
      renderCallDetails(state.form);
      byId("co-speakers").hidden = (state.form.co_speaker_limit ?? 1) === 0;
      if (state.form.accepting_submissions === false) {
        byId("closed-card").hidden = false;
        const message = availabilityMessage(state.form);
        byId("availability").textContent = message;
        setStatus(message);
        if (!workspaceMode) return;
      }
      const saved = browserDraft();
      try {
        const session = await api("/api/v1/auth/session");
        state.authenticated = true;
        state.csrf = session.csrf_token;
        state.sessionEmail = session.email || "";
        state.sessionDisplayName = session.display_name || "";
        byId("proposal-card").hidden = false;
        byId("sign-in-card").hidden = true;
        if (!workspaceMode) {
          try {
            state.submissions = (await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions/mine`)).data || [];
          } catch (_) {
            // Duplicate-title guidance is advisory and must never block a valid proposal.
            state.submissions = [];
          }
          const serverDraft = await loadDraft();
          if (saved && browserDraftMatchesServerDraft(saved, serverDraft)) clearBrowserDraft();
          const restored = saved?.submissionId || browserDraftMatchesServerDraft(saved, serverDraft)
            ? null
            : restoreBrowserDraft(state.sessionEmail);
          applySignedInIdentity();
          if (restored) {
            const needsFiles = (restored.fileNames || []).length > 0;
            const readyToReview = Boolean(restored.readyToSubmit)
              && !needsFiles
              && byId("proposal-form").checkValidity();
            showReview(readyToReview);
            setStatus(needsFiles
              ? "Email verified. Your answers were restored; reattach the selected files before continuing."
              : readyToReview
                ? "Email verified. Review your restored proposal, then confirm submission."
                : "Your proposal was restored from this browser.", "success");
          } else {
            if (!serverDraft) setStatus("Start a new proposal below.");
            applySignedInIdentity();
          }
          return;
        }
        state.submissions = (await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions/mine`)).data || [];
        const selected = state.submissions.find((submission) => submission.id === workspaceSegment);
        if (!selected) throw new Error("This proposal is unavailable.");
        chooseSubmission(selected);
        byId("title").textContent = selected.editable ? "Edit proposal" : "View proposal";
        byId("welcome").textContent = selected.proposal_title || state.form.event_name;
        if (state.editingSubmission && saved?.submissionId === selected.id && restoreBrowserDraft(state.sessionEmail)) {
          setStatus("Your unsaved proposal changes were restored from this browser.", "success");
        }
      } catch (error) {
        if (![401, 403].includes(error.status)) throw error;
        state.authenticated = false;
        const signedInWithoutSpeakerAccess = error.status === 403;
        const email = byId("proposal-form").elements.namedItem("speaker_email");
        if (signedInWithoutSpeakerAccess && email) {
          email.readOnly = false;
          email.removeAttribute("aria-describedby");
          byId("signed-in-email-help")?.remove();
        }
        // A visitor who is not signed in still gets the real form. Every field,
        // its conditional logic, and its validation already arrived in the public
        // payload this page just loaded, and the anonymous branches of Save draft
        // and Confirm submission below were written for exactly this state: the
        // answers are held in this browser and replayed once the email is
        // verified. Hiding the form was the only thing stopping a speaker from
        // reading the questions before committing to an account, and it is the
        // server -- never this page -- that refuses an unauthenticated write.
        // A proposal workspace URL names one stored submission, so it has nothing
        // to preview and stays behind the sign-in card.
        byId("proposal-card").hidden = workspaceMode;
        byId("preview-note").hidden = workspaceMode;
        byId("sign-in-card").hidden = false;
        const signInEmail = byId("sign-in-form").elements.email;
        if (saved?.ownerEmail) signInEmail.value = saved.ownerEmail;
        setStatus(saved
          ? "A recent draft is waiting in this browser. Sign in with its proposal email to restore it."
          : signedInWithoutSpeakerAccess
            ? "This organizer account cannot submit proposals. Sign in with a speaker email to continue."
            : workspaceMode
              ? "Sign in to open this proposal."
              : "Read every question and start your proposal below. You verify your email when you submit it.");
      }
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), "error"); }
  }

  byId("sign-in-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const email = String(new FormData(form).get("email") || "").trim();
    const password = String(new FormData(form).get("password") || "");
    const proposalEmail = byId("proposal-form").elements.namedItem("speaker_email");
    if (proposalEmail) proposalEmail.value = email;
    const passwordButton = byId("cfp-password-sign-in");
    const linkButton = byId("cfp-send-sign-in-link");
    form.elements.email.disabled = true;
    form.elements.password.disabled = true;
    passwordButton.disabled = true;
    linkButton.disabled = true;
    passwordButton.textContent = "Signing in…";
    try {
      // Account creation only preserves progress. It must never mark a blank or
      // partially completed proposal as ready for the review step.
      saveBrowserDraft(false);
      const session = await api("/api/v1/auth/password/sign-in", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password, redirect_path: location.pathname }) });
      location.assign(session.redirect_path || location.pathname);
    } catch (error) {
      form.elements.email.disabled = false;
      form.elements.password.disabled = false;
      passwordButton.disabled = false;
      linkButton.disabled = false;
      passwordButton.textContent = "Sign in";
      setStatus(error.status === 401 ? "Email or password is incorrect. Try again or request a sign-in link." : window.SessionBuddyApi.message(error, "We could not sign you in. Try again."), "error");
    }
  });

  byId("cfp-send-sign-in-link").addEventListener("click", async () => {
    const form = byId("sign-in-form");
    if (!form.elements.email.reportValidity()) return;
    const email = String(form.elements.email.value || "").trim();
    const proposalEmail = byId("proposal-form").elements.namedItem("speaker_email");
    if (proposalEmail) proposalEmail.value = email;
    const passwordButton = byId("cfp-password-sign-in");
    const linkButton = byId("cfp-send-sign-in-link");
    form.elements.email.disabled = true;
    form.elements.password.disabled = true;
    passwordButton.disabled = true;
    linkButton.disabled = true;
    linkButton.textContent = "Sending signup link…";
    try {
      // Confirming an anonymous proposal records that it is ready for the
      // review step. Requesting the verification link must preserve that
      // state; otherwise the verified speaker is sent back to editing even
      // though they already confirmed the complete proposal. A link requested
      // before confirmation remains an ordinary in-progress draft.
      const readyToSubmit = Boolean(browserDraft()?.readyToSubmit)
        && byId("proposal-form").checkValidity();
      saveBrowserDraft(readyToSubmit);
      await api("/api/v1/auth/magic-links", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, form_slug: slug, redirect_path: location.pathname }) });
      byId("cfp-sent-message").textContent = `We sent a sign-in link to ${email}.`;
      byId("cfp-sign-in-entry").hidden = true;
      byId("cfp-sign-in-sent").hidden = false;
      byId("cfp-sign-in-sent").focus();
      setStatus("Check your email for the one-time sign-in link. It expires in 15 minutes.", "success");
    } catch (error) {
      form.elements.email.disabled = false;
      form.elements.password.disabled = false;
      passwordButton.disabled = false;
      linkButton.disabled = false;
      linkButton.textContent = "Email me a signup link";
      setStatus(window.SessionBuddyApi.message(error, "We could not send the sign-in link. Try again."), "error");
    }
  });
  byId("cfp-change-sign-in-email").addEventListener("click", () => {
    const form = byId("sign-in-form");
    byId("cfp-sign-in-sent").hidden = true;
    byId("cfp-sign-in-entry").hidden = false;
    form.elements.email.disabled = false;
    form.elements.password.disabled = false;
    byId("cfp-password-sign-in").disabled = false;
    byId("cfp-send-sign-in-link").disabled = false;
    byId("cfp-send-sign-in-link").textContent = "Email me a signup link";
    form.elements.password.value = "";
    form.elements.email.focus();
    form.elements.email.select();
    setStatus("");
  });
  byId("add-co-speaker").addEventListener("click", () => {
    if (byId("co-speaker-rows").children.length < (state.form?.co_speaker_limit ?? 1)) addCoSpeakerRow({}, true);
    validateCoSpeakers(byId("proposal-form"));
    queueBrowserDraft();
  });

  byId("save-draft").addEventListener("click", async () => {
    try {
      if (!state.authenticated && !state.editingSubmission) {
        saveBrowserDraft(false);
        setStatus("Draft saved in this browser.", "success");
        return;
      }
      if (state.editingSubmission) {
        validateCoSpeakers(byId("proposal-form"));
        if (!byId("proposal-form").reportValidity()) {
          setStatus("Complete the highlighted required fields before saving changes.", "error");
          return;
        }
        const values = { ...state.editingSubmission.answers, ...answers({ includeUploads: false }) };
        const submission = await api(`/api/v1/forms/${encodeURIComponent(slug)}/submissions/${encodeURIComponent(state.editingSubmission.id)}`, {
          method: "PATCH",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${browserSessionId()}-${browserSessionId()}` },
          body: JSON.stringify({ speaker_name: values.speaker_name, speaker_email: values.speaker_email, proposal_title: values.proposal_title, proposal_abstract: values.proposal_abstract, answers: values, co_speakers: coSpeakers(), version: state.editingSubmission.version })
        });
        state.editingSubmission = submission;
        state.submissions = state.submissions.map((item) => item.id === submission.id ? submission : item);
        state.draftDirty = false;
        try { localStorage.removeItem(browserDraftKey()); } catch (_) { /* best effort */ }
        setStatus("Changes saved to this proposal.", "success");
        return;
      }
      const draft = await api(`/api/v1/forms/${encodeURIComponent(slug)}/draft`, { method: "PUT", headers: { "content-type": "application/json", "x-csrf-token": state.csrf }, body: JSON.stringify({ answers: answers({ includeUploads: false }), version: state.draftVersion }) });
        state.draftVersion = draft.version;
        state.draftDirty = false;
        clearTimeout(state.draftTimer);
        state.draftTimer = null;
        try { localStorage.removeItem(browserDraftKey()); } catch (_) { /* best effort */ }
      setStatus("Draft saved.", "success");
    } catch (error) {
      if (error.code === "decision_conflict" && state.editingSubmission) {
        const submissionId = state.editingSubmission.id;
        clearBrowserDraft();
        state.draftDirty = false;
        await reloadSubmissions(submissionId);
        setStatus("A final decision was recorded while you were editing. Your unsaved changes were not saved, and this proposal is now read-only.", "error");
        return;
      }
      if (await recoverOptimisticConflict(error)) return;
      setStatus(window.SessionBuddyApi.message(error), "error");
    }
  });

  byId("review-proposal").addEventListener("click", () => {
    validateCoSpeakers(byId("proposal-form"));
    if (!byId("proposal-form").reportValidity()) {
      setStatus("Complete the highlighted required fields before reviewing your proposal.", "error");
      return;
    }
    showReview(true);
    setStatus(state.editingSubmission
      ? "Review your changes, then select Save changes."
      : "Not submitted yet. Review your proposal, then select Confirm submission.");
    byId("review-title").focus?.();
  });
  byId("proposal-form").addEventListener("invalid", (event) => {
    const label = event.target.closest("label")?.querySelector(".field-label")?.textContent?.replace("*", "").trim()
      || event.target.name?.replaceAll("_", " ")
      || "This field";
    setStatus(`${label}: ${event.target.validationMessage || "check this field."}`, "error");
  }, true);
  byId("proposal-form").addEventListener("input", queueBrowserDraft);
  byId("proposal-form").addEventListener("change", queueBrowserDraft);
  window.addEventListener("beforeunload", (event) => {
    if (!state.draftDirty) return;
    try { saveBrowserDraft(false); } catch (_) { /* beforeunload must remain synchronous */ }
    event.preventDefault();
    event.returnValue = "";
  });
  byId("back-to-form").addEventListener("click", () => { showReview(false); setStatus("You can continue editing your proposal."); });

  byId("proposal-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = byId("submit-proposal");
    if (form.getAttribute("aria-busy") === "true") return;
    const idleLabel = state.editingSubmission ? "Save changes" : "Confirm submission";
    form.setAttribute("aria-busy", "true");
    button.disabled = true;
    button.textContent = state.editingSubmission ? "Saving…" : "Submitting…";
    setStatus(state.editingSubmission ? "Saving your proposal changes…" : "Submitting your proposal…");
    let completed = false;
    try {
      validateCoSpeakers(form);
      if (!form.reportValidity()) {
        setStatus("Check each co-speaker name and email before continuing.", "error");
        return;
      }
      if (!state.authenticated) {
        const saved = saveBrowserDraft(true);
        // This explicit checkpoint supersedes any queued field autosave. Mark
        // it clean so the verification navigation cannot overwrite
        // readyToSubmit with an ordinary in-progress browser draft.
        clearTimeout(state.draftTimer);
        state.draftDirty = false;
        const email = String(saved.answers.speaker_email || "").trim();
        const signIn = byId("sign-in-form");
        signIn.elements.email.value = email;
        byId("sign-in-card").hidden = false;
        byId("sign-in-card").scrollIntoView({ behavior: "smooth", block: "start" });
        setStatus("Your completed proposal is saved in this browser. Verify the proposal email to submit it.");
        // Hand the next step to the visitor rather than submitting an
        // authentication form on their behalf: it requires a password this
        // speaker may never have set, so requestSubmit() would only raise a
        // validation bubble on an empty field, and mailing a one-time link
        // unprompted would send it to whatever address the proposal carries.
        if (!byId("cfp-sign-in-entry").hidden) signIn.elements.password.focus();
        return;
      }
      await uploadFiles();
      const enteredValues = answers();
      const values = state.editingSubmission
        ? { ...state.editingSubmission.answers, ...enteredValues }
        : enteredValues;
      const target = state.editingSubmission
        ? `/api/v1/forms/${encodeURIComponent(slug)}/submissions/${encodeURIComponent(state.editingSubmission.id)}`
        : `/api/v1/forms/${encodeURIComponent(slug)}/submissions`;
      const method = state.editingSubmission ? "PATCH" : "POST";
      const payload = JSON.stringify({ speaker_name: values.speaker_name, speaker_email: values.speaker_email, proposal_title: values.proposal_title, proposal_abstract: values.proposal_abstract, answers: values, co_speakers: coSpeakers(), ...(state.editingSubmission ? { version: state.editingSubmission.version } : {}) });
      // Held across retries of an unchanged proposal: if a response is lost
      // after the server stored it, a fresh key on retry would create a second
      // proposal instead of replaying the first.
      const attempt = `${method} ${target} ${payload}`;
      if (state.pendingSubmission?.attempt !== attempt) {
        state.pendingSubmission = { attempt, key: `${browserSessionId()}-${browserSessionId()}` };
      }
      const submission = await api(target, {
        method,
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": state.pendingSubmission.key, "x-public-session-id": state.publicSession },
        body: payload
      });
      state.pendingSubmission = null;
      setStep("done");
      form.hidden = true;
      byId("call-details").hidden = true;
      byId("sign-in-card").hidden = true;
      const receipt = byId("receipt");
      receipt.className = "empty-state";
      receipt.replaceChildren(make("h2", state.editingSubmission ? "Proposal updated" : "Submission confirmed"), make("p", state.editingSubmission ? "Your changes were saved to the existing proposal." : state.form.success_message), make("p", `Receipt ${submission.id}`));
      if (workspaceMode) {
        const proposalLink = make("a", "Back to speaker portal", "button");
        proposalLink.href = "/speaker";
        receipt.append(proposalLink);
      }
      if (state.form.redirect_to_portal) {
        const link = make("a", "Open speaker portal", "button secondary");
        link.href = "/speaker";
        receipt.append(link);
      }
      setStatus(state.editingSubmission ? "Proposal updated successfully." : "Proposal submitted successfully.", "success");
      completed = true;
      state.draftDirty = false;
      button.textContent = state.editingSubmission ? "Saved ✓" : "Submitted ✓";
      receipt.scrollIntoView({ behavior: "smooth", block: "center" });
      receipt.focus({ preventScroll: true });
      clearBrowserDraft();
      resetProposalFiles();
    } catch (error) {
      if (error.code === "decision_conflict" && state.editingSubmission) {
        const submissionId = state.editingSubmission.id;
        clearBrowserDraft();
        state.draftDirty = false;
        await reloadSubmissions(submissionId);
        showReview(false);
        setStatus("A final decision was recorded while you were editing. Your unsaved changes were not saved, and this proposal is now read-only.", "error");
        return;
      }
      if (await recoverOptimisticConflict(error)) return;
      const clientMessage = error instanceof Error && !error.status ? error.message : "";
      setStatus(clientMessage || window.SessionBuddyApi.message(error, "Check the highlighted proposal fields and try again."), "error");
      byId("status").focus();
    } finally {
      form.setAttribute("aria-busy", "false");
      if (!completed) {
        button.disabled = false;
        button.textContent = idleLabel;
      }
    }
  });

  load();
})();

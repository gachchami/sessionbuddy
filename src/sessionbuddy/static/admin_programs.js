(() => {
  "use strict";
  const coreFields = [
    { key: "speaker_name", type: "text", label: "Speaker name", required: true, choices: [] },
    { key: "speaker_email", type: "email", label: "Email", required: true, choices: [] },
    { key: "proposal_title", type: "text", label: "Proposal title", required: true, choices: [] },
    { key: "proposal_abstract", type: "textarea", label: "Proposal abstract", required: true, choices: [] }
  ];
  const standardProposalFields = [
    {
      key: "session_type",
      type: "select",
      label: "Session format",
      required: true,
      choices: ["Keynote (45 min)", "Talk (30 min)", "Lightning Talk (10 min)", "Workshop (120 min)", "Panel (45 min)"]
    },
    { key: "track", type: "select", label: "Track", required: false, choices: [] },
    { key: "proposal_description", type: "textarea", label: "Full description", required: false, choices: [] }
  ];
  // Account-backed identity fields. They are never editable in the builder and
  // never reorderable, so every predicate that used to name the pair by hand
  // reads this instead.
  const identityFieldKeys = ["speaker_name", "speaker_email"];
  const proposalFieldKeys = new Set(["proposal_title", "proposal_abstract", ...standardProposalFields.map((field) => field.key)]);
  const state = { context: null, csrf: null, userId: "", eventName: "", eventStatus: "", eventStartsAtMs: null, eventTimeZone: "", eventTracks: [], publishedForm: null, availabilityTimer: null, editing: false, dirty: false, draftTimer: null, selectedOutline: "basics", collapsedFieldKeys: new Set(), fields: structuredClone([...coreFields, ...standardProposalFields]), routingRules: [], importantDates: [] };
  const byId = (id) => document.getElementById(id);
  const jsonHeaders = () => ({ "content-type": "application/json" });
  const admin = () => ({ ...jsonHeaders(), "x-csrf-token": state.csrf });
  const key = () => `${crypto.randomUUID()}-${crypto.randomUUID()}`;
  const draftKey = () => `sessionbuddy:cfp-draft:${state.userId || "unknown"}:${state.context?.event_id || "unknown"}`;
  let pendingLinkRange = null;
  let linkDialogTrigger = null;

  function clearValidation() {
    const form = byId("publish-form");
    form.querySelectorAll(".field-error").forEach((node) => node.remove());
    form.querySelectorAll('[aria-invalid="true"]').forEach((node) => node.removeAttribute("aria-invalid"));
    byId("cfp-validation-summary").hidden = true;
    byId("cfp-validation-list").replaceChildren();
    document.querySelectorAll(".cfp-outline-item.has-errors").forEach((button) => button.classList.remove("has-errors"));
  }

  function showValidation(errors) {
    clearValidation();
    const summary = byId("cfp-validation-summary");
    const list = byId("cfp-validation-list");
    errors.forEach(({ field, message }, index) => {
      const id = field.id || `cfp-invalid-${index + 1}`;
      field.id = id;
      field.setAttribute("aria-invalid", "true");
      const error = document.createElement("small");
      error.className = "field-error";
      error.id = `${id}-error`;
      error.textContent = message;
      field.setAttribute("aria-describedby", [field.getAttribute("aria-describedby"), error.id].filter(Boolean).join(" "));
      const host = field.closest("label, .slug-field, .question-editor__body, .routing-rule") || field;
      host.insertAdjacentElement("afterend", error);
      const section = field.closest(".cfp-editor-section");
      const sectionLink = section && document.querySelector(`.cfp-outline-item[data-section="${section.id.replace("cfp-", "")}"]`);
      sectionLink?.classList.add("has-errors");
      const details = field.closest("details");
      if (details) details.open = true;
      const item = document.createElement("li");
      const link = document.createElement("a");
      link.textContent = message;
      link.href = `#${id}`;
      link.addEventListener("click", (event) => {
        event.preventDefault();
        const questionCard = field.closest(".question-card");
        const questionIndex = questionCard ? [...byId("form-fields").querySelectorAll(".question-card:not(.question-card--system)")].indexOf(questionCard) : -1;
        selectOutline(questionIndex >= 0 ? `question:${questionIndex}` : section.id.replace("cfp-", ""), false);
        if (details) details.open = true;
        field.focus();
        field.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      item.append(link);
      list.append(item);
    });
    summary.hidden = false;
    summary.querySelector("strong").textContent = `Fix ${errors.length} ${errors.length === 1 ? "field" : "fields"} before continuing`;
    summary.focus();
  }

  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = {
      schema_version: 1,
      page_template: "/admin/events/{event_id}/cfp",
      navigation_type: navigation?.type || "unknown",
      device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop",
      sampled: false,
      lcp_ms: null,
      inp_ms: null,
      cls: null,
      ttfb_ms: navigation?.responseStart ?? null,
      fcp_ms: null,
      route_transition_ms: null,
      critical_api_ms: Math.max(0, performance.now() - started),
      api_request_id: response.headers.get("x-request-id")
    };
  }

  async function api(path, options = {}) {
    const started = performance.now();
    return window.SessionBuddyApi.request(path, options, {
      onResponse: (response) => recordTelemetry(started, response)
    });
  }

  async function loadWorkspace(eventId) {
    return api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/cfp`);
  }

  async function loadEventTracks(eventId) {
    try {
      const response = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/tracks`);
      state.eventTracks = (response.data || []).map((track) => track.name);
    } catch (_) {
      state.eventTracks = [];
    }
    byId("event-track-options").replaceChildren(
      ...state.eventTracks.map((name) => new Option(name, name))
    );
  }

  function syncEventTrackField() {
    const index = state.fields.findIndex((field) => field.key === "track");
    const hasTracks = state.eventTracks.length > 0;
    // The row stays even with no tracks. Removing it told two eval organizers
    // that the product has no track support at all, and both rebuilt Track as a
    // custom question -- which loses routing, agenda linkage and blind review.
    // Tracks are created on the Agenda page, which this screen otherwise never
    // mentions, so the empty state has to say where to go. `readFields` drops
    // the field from the published form while it has no choices, because
    // `FormFieldDefinition.validate_choices` requires at least one.
    const trackField = {
      key: "track",
      type: "select",
      label: "Track",
      help_text: hasTracks
        ? "Choose the event track that best fits this proposal."
        : "No tracks yet. Add them on the Agenda page to offer this question.",
      placeholder: "",
      required: hasTracks,
      choices: [...state.eventTracks],
      blind_visible: true
    };
    if (index >= 0) state.fields[index] = { ...state.fields[index], ...trackField };
    else {
      const sessionTypeIndex = state.fields.findIndex((field) => field.key === "session_type");
      state.fields.splice(sessionTypeIndex >= 0 ? sessionTypeIndex + 1 : state.fields.length, 0, trackField);
    }
  }

  function setStatus(message, kind = "") {
    const variant = kind === true ? "error" : kind;
    byId("status").textContent = message;
    byId("status").classList.toggle("error", variant === "error");
    byId("status").classList.toggle("success", variant === "success");
  }

  function make(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function richTextMarkup(node) {
    const serializer = new XMLSerializer();
    return [...node.childNodes].map((child) => serializer.serializeToString(child)).join("");
  }

  function setRichText(node, markup, fallback = "") {
    const documentNode = new DOMParser().parseFromString(markup || "", "text/html");
    node.replaceChildren(...[...documentNode.body.childNodes].map((child) => document.importNode(child, true)));
    if (!node.textContent.trim() && fallback) node.textContent = fallback;
  }

  function syncDescription() {
    const editor = byId("cfp-description-editor");
    const text = editor.textContent.replace(/\s+/g, " ").trim();
    const opening = editor.innerText.split(/\n+/).map((line) => line.trim()).find(Boolean) || text;
    byId("publish-form").elements.description_html.value = richTextMarkup(editor);
    byId("publish-form").elements.welcome_text.value = opening.slice(0, 1000);
  }

  function renderImportantDates() {
    const rows = byId("important-date-rows");
    rows.replaceChildren(...state.importantDates.map((date, index) => {
      const row = make("div");
      row.className = "important-date-row";
      const label = make("label", "Label");
      const labelInput = make("input");
      labelInput.value = date.label || "";
      labelInput.maxLength = 120;
      labelInput.addEventListener("input", () => { state.importantDates[index].label = labelInput.value; });
      label.append(labelInput);
      const when = make("label", "Date");
      const whenInput = make("input");
      whenInput.type = "datetime-local";
      whenInput.value = toLocalInput(date.at_ms);
      whenInput.addEventListener("input", () => { state.importantDates[index].at_ms = toEpoch(whenInput.value); });
      when.append(whenInput);
      const remove = make("button", "Remove");
      remove.type = "button";
      remove.className = "secondary";
      remove.addEventListener("click", () => { state.importantDates.splice(index, 1); renderImportantDates(); });
      row.append(label, when, remove);
      return row;
    }));
  }

  function readImportantDates() {
    return state.importantDates.filter((date) => date.label.trim() && Number.isFinite(date.at_ms));
  }

  function inputLabel(text, input, help = "") {
    const label = make("label", text);
    label.append(input);
    if (help) label.append(make("small", help));
    return label;
  }

  function textInput(name, value, required = false) {
    const input = document.createElement("input");
    input.name = name;
    input.value = value || "";
    input.required = required;
    input.maxLength = 200;
    return input;
  }

  function partsInTimeZone(value) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: state.eventTimeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    }).formatToParts(new Date(value));
    return Object.fromEntries(parts
      .filter(({ type }) => type !== "literal")
      .map(({ type, value: part }) => [type, Number(part)]));
  }

  function toLocalInput(value) {
    if (!value) return "";
    const parts = partsInTimeZone(value);
    const pad = (part) => String(part).padStart(2, "0");
    return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
  }

  function toEpoch(value) {
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
    return toLocalInput(timestamp) === value ? timestamp : Number.NaN;
  }

  function formatCfpDate(value, fallback) {
    if (!value) return fallback;
    return new Intl.DateTimeFormat(undefined, {
      timeZone: state.eventTimeZone,
      dateStyle: "medium",
      timeStyle: "short"
    }).format(new Date(value));
  }

  // The API decides availability once, in sessionbuddy/cfp/availability.py, and
  // ships it as published_form.availability_state. This page renders that word
  // and never recomputes it from the timestamps: a second local rule is what let
  // this badge read "Live" while the public form was already closed.
  const availabilityLabels = { scheduled: "Scheduled", open: "Open", closed: "Closed" };
  const MAX_REFRESH_DELAY_MS = 86_400_000;
  const REFRESH_RETRY_MS = 60_000;

  function cfpAvailability(form) {
    return availabilityLabels[form.availability_state] || "Open";
  }

  function cfpAvailabilityDetail(form) {
    if (!form) return "";
    const boundary = formatCfpDate(form.availability_boundary_at_ms, "");
    if (form.availability_state === "scheduled" && boundary) {
      return `Published, but not accepting proposals until ${boundary} (${state.eventTimeZone}).`;
    }
    if (form.availability_state === "closed" && boundary) {
      return `Published, but no longer accepting proposals. The call closed ${boundary} (${state.eventTimeZone}).`;
    }
    if (form.availability_state === "open" && boundary) {
      return `Published and accepting proposals until ${boundary} (${state.eventTimeZone}).`;
    }
    return form.availability_message || "";
  }

  // A published state is a snapshot. Re-read the workspace once the next
  // boundary the server told us about has passed, so a page left open across
  // the deadline stops advertising an open call.
  function scheduleAvailabilityRefresh(form) {
    window.clearTimeout(state.availabilityTimer);
    state.availabilityTimer = null;
    if (!form || !state.context) return;
    const now = Date.now();
    const boundary = [form.opens_at_ms, form.closes_at_ms]
      .filter((value) => typeof value === "number" && value > now)
      .sort((first, second) => first - second)[0];
    if (boundary === undefined) return;
    // setTimeout saturates beyond ~24.8 days, so a distant boundary is walked
    // toward in capped hops instead of being skipped: a page opened a week
    // before the deadline still stops saying "Open" when the deadline lands.
    const wait = Math.min(boundary - now + 1000, MAX_REFRESH_DELAY_MS);
    state.availabilityTimer = window.setTimeout(() => {
      if (Date.now() < boundary) {
        scheduleAvailabilityRefresh(form);
        return;
      }
      refreshPublishedForm();
    }, wait);
  }

  // Re-read the server's answer after a boundary. A failure here must not leave
  // a stale "Open" badge on screen forever, and by this point every boundary is
  // in the past, so the retry is a plain interval rather than another hop.
  async function refreshPublishedForm() {
    try {
      const workspace = await loadWorkspace(state.context.event_id);
      state.publishedForm = workspace.published_form;
      renderWorkspace();
    } catch (_) {
      window.clearTimeout(state.availabilityTimer);
      state.availabilityTimer = window.setTimeout(refreshPublishedForm, REFRESH_RETRY_MS);
    }
  }

  function syncAvailabilityLimits(form) {
    const opens = form.elements.opens_at;
    const closes = form.elements.closes_at;
    opens.removeAttribute("min");
    opens.removeAttribute("max");
    const opening = toEpoch(opens.value);
    closes.min = Number.isFinite(opening) ? toLocalInput(opening + 60000) : "";
    closes.max = state.eventStartsAtMs ? toLocalInput(state.eventStartsAtMs - 1) : "";
  }

  function eventIdFromPage(session) {
    const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/cfp$/);
    if (match) {
      try { return decodeURIComponent(match[1]); } catch (_) { return ""; }
    }
    return new URLSearchParams(location.search).get("event_id") || session.event_id || "";
  }

  function renderWorkspace() {
    const published = state.publishedForm;
    if (state.context) document.body.dataset.eventId = state.context.event_id;
    window.dispatchEvent(new Event("sessionbuddy:event-context"));
    const publishForm = byId("publish-form");
    const publishButton = publishForm.querySelector('button[type="submit"], button:not([type])');
    const eventIsActive = state.eventStatus === "active";
    publishButton.disabled = !state.context || (!published && !eventIsActive);
    publishButton.textContent = published ? "Update live CFP" : "Publish CFP";
    byId("publish-action-label").textContent = !eventIsActive
      ? "Event draft"
      : published ? "Unpublished changes" : "Draft";
    byId("published-note").hidden = !published;
    byId("cfp-page-meta").textContent = `${Math.max(0, state.fields.length - 2)} proposal fields · ${!eventIsActive ? "Event draft" : published ? cfpAvailability(published) : "Draft"}`;
    const liveProposalCount = (published?.fields || []).filter((field) => proposalFieldKeys.has(field.key)).length;
    byId("cfp-page-meta").textContent = published && liveProposalCount !== Math.max(0, state.fields.length - 2)
      ? `${Math.max(0, state.fields.length - 2)} draft fields · ${liveProposalCount} live`
      : byId("cfp-page-meta").textContent;
    if (published && !state.dirty) {
      byId("cfp-autosave-state").textContent = "Unsaved live changes are backed up in this browser";
    }
    byId("publish-settings").hidden = Boolean(published) && !state.editing;
    byId("cfp-notification-settings").hidden = Boolean(published) && !state.editing;
    byId("cfp-summary").hidden = false;
    byId("edit-cfp").hidden = !published || state.editing;
    byId("cancel-cfp-edit").hidden = !published;
    const savedState = byId("cfp-saved-state");
    savedState.hidden = !published || state.editing;
    if (published && !savedState.textContent) savedState.textContent = "Saved";
    byId("publish-result").textContent = !eventIsActive
      ? "Activate the event before publishing its CFP."
      : published && state.editing
      ? "Nothing changes publicly until you update the live CFP."
      : published
      ? cfpAvailabilityDetail(published)
      : "Complete the form settings below, then publish.";

    const live = byId("cfp-link-live");
    const empty = byId("cfp-link-empty");
    const badge = byId("cfp-state");
    live.hidden = !published || !eventIsActive;
    empty.hidden = Boolean(published) && eventIsActive;
    const availability = published ? cfpAvailability(published) : "";
    badge.className = `badge${published && availability === "Open" ? " success" : ""}`;
    badge.textContent = !eventIsActive ? "Event draft" : published ? availability : "Not published";
    scheduleAvailabilityRefresh(eventIsActive ? published : null);
    byId("cfp-live-bar").hidden = !published || !eventIsActive;
    if (!eventIsActive) {
      empty.textContent = "Activate the event to make its CFP public.";
      return;
    }
    if (!published) {
      empty.textContent = "Configure and publish the proposal form below to get a shareable link.";
      return;
    }
    const eventKey = state.context.event_id.replace(/[^a-z0-9]/gi, "").slice(0, 6).toLowerCase();
    const publicUrl = `${location.origin}/cfp/${eventKey}/${published.slug}`;
    byId("cfp-live-bar").hidden = false;
    byId("cfp-live-url").textContent = publicUrl;
    byId("cfp-live-url").href = publicUrl;
    byId("cfp-live-url-prefix").textContent = `${location.origin}/cfp/`;
    byId("copy-cfp-header").hidden = false;
    byId("cfp-url").value = publicUrl;
    byId("open-cfp-url").href = publicUrl;
    byId("review-submissions").href = `/admin/events/${encodeURIComponent(state.context.event_id)}/submissions`;
    byId("cfp-summary-welcome").textContent = published.welcome_text || "No welcome message";
    byId("cfp-summary-opens").textContent = formatCfpDate(published.opens_at_ms, "Open immediately");
    byId("cfp-summary-closes").textContent = formatCfpDate(published.closes_at_ms, "No closing date");
    byId("cfp-summary-limit").textContent = published.submission_limit || "No limit";
    const fields = published.fields || [];
    const required = fields.filter((field) => field.required).length;
    byId("cfp-summary-questions").textContent = `${fields.length} total · ${required} required`;
    byId("cfp-summary-fields").textContent = fields.map((field) => field.label).join(" · ") || "No questions configured";
  }

  function loadPublishedSettings(form) {
    const conditions = new Map((form.conditions || []).map((item) => [item.target_key, item]));
    state.fields = (form.fields || coreFields).map((field) => ({
      ...field,
      condition: conditions.get(field.key)
    }));
    standardProposalFields.forEach((standard) => {
      if (!state.fields.some((field) => field.key === standard.key)) state.fields.push(structuredClone(standard));
    });
    state.routingRules = structuredClone(form.routing_rules || []);
    const editor = byId("publish-form");
    editor.elements.slug.value = form.slug;
    setRichText(byId("cfp-description-editor"), form.description_html, form.welcome_text);
    syncDescription();
    state.importantDates = structuredClone(form.important_dates || []);
    renderImportantDates();
    editor.elements.opens_at.value = toLocalInput(form.opens_at_ms);
    editor.elements.closes_at.value = toLocalInput(form.closes_at_ms);
    editor.elements.submission_limit.value = form.submission_limit || "";
    editor.elements.co_speaker_limit.value = form.co_speaker_limit ?? 1;
    if (form.success_title) editor.elements.success_title.value = form.success_title;
    if (form.success_message) editor.elements.success_message.value = form.success_message;
    if (form.confirmation_subject) editor.elements.confirmation_subject.value = form.confirmation_subject;
    if (form.confirmation_body) editor.elements.confirmation_body.value = form.confirmation_body;
    window.SessionBuddyApi.refreshCharacterCounters(editor);
    if (typeof form.redirect_to_portal === "boolean") {
      editor.elements.redirect_to_portal.checked = form.redirect_to_portal;
    }
    renderFields();
    renderRoutingRules();
  }

  function renderFields() {
    const list = byId("form-fields");
    list.replaceChildren();
    state.fields.forEach((field, index) => {
      const system = identityFieldKeys.includes(field.key) || proposalFieldKeys.has(field.key);
      const card = make("fieldset");
      card.className = "question-card";
      card.dataset.index = String(index);
      const legend = make("legend", system ? `System field: ${field.key}` : `Custom field: ${field.key}`);
      legend.className = "sr-only";
      const editor = make("details");
      editor.className = "question-editor";
      editor.open = !system && !state.collapsedFieldKeys.has(field.key);
      const editorSummary = make("summary");
      const summaryIdentity = make("span");
      summaryIdentity.append(make("strong", field.label));
      if (!system) summaryIdentity.append(make("small", field.key));
      const summaryMeta = make("span");
      summaryMeta.className = "question-editor__meta";
      if (system) {
        const systemBadge = make("span", "System");
        systemBadge.className = "badge";
        summaryMeta.append(systemBadge);
      } else {
        const typeNames = { text: "Short answer", textarea: "Long answer", email: "Email", url: "URL", phone: "Phone", select: "Single choice", multiselect: "Multiple choice", checkbox: "Checkbox", file: "File", image: "Image" };
        const typeBadge = make("span", typeNames[field.type] || "Question");
        typeBadge.className = "badge";
        summaryMeta.append(typeBadge);
      }
      if (field.required) {
        const requiredBadge = make("span", "Required");
        requiredBadge.className = "required-marker";
        summaryMeta.append(requiredBadge);
      }
      if (!identityFieldKeys.includes(field.key)) {
        const orderControls = make("span");
        orderControls.className = "question-order-controls";
        const move = (direction) => {
          readFields();
          const current = state.fields.findIndex((item) => item.key === field.key);
          const target = current + direction;
          if (target < identityFieldKeys.length || target >= state.fields.length) return;
          [state.fields[current], state.fields[target]] = [state.fields[target], state.fields[current]];
          renderFields();
          selectOutline(proposalFieldKeys.has(field.key) ? "proposal" : "custom", false);
          state.dirty = true;
          queueLocalDraft();
        };
        const up = make("button", "↑");
        up.type = "button";
        up.className = "question-order-button";
        up.setAttribute("aria-label", `Move ${field.label} up`);
        up.disabled = index <= identityFieldKeys.length;
        up.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); move(-1); });
        const down = make("button", "↓");
        down.type = "button";
        down.className = "question-order-button";
        down.setAttribute("aria-label", `Move ${field.label} down`);
        down.disabled = index === state.fields.length - 1;
        down.addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); move(1); });
        orderControls.append(up, down);
        summaryMeta.append(orderControls);
      }
      editorSummary.append(summaryIdentity, summaryMeta);
      const editorBody = make("div");
      editorBody.className = "question-editor__body";
      const keyInput = textInput("field_key", field.key, true);
      keyInput.pattern = "[a-z][a-z0-9_]*";
      keyInput.readOnly = system;
      const type = document.createElement("select");
      type.name = "field_type";
      [
        ["Short text", "text"], ["Long text", "textarea"], ["Email", "email"],
        ["URL", "url"], ["Phone", "phone"], ["Single choice", "select"],
        ["Multiple choice", "multiselect"], ["Checkbox", "checkbox"],
        ["File upload", "file"], ["Image upload", "image"]
      ].forEach(([label, value]) => type.add(new Option(label, value)));
      type.value = field.type;
      if (system) type.disabled = true;
      const required = document.createElement("input");
      required.type = "checkbox";
      required.name = "field_required";
      required.checked = field.required;
      required.disabled = system;
      const blindVisible = document.createElement("input");
      blindVisible.type = "checkbox";
      blindVisible.name = "field_blind_visible";
      blindVisible.checked = Boolean(field.blind_visible);
      const choices = textInput("field_choices", (field.choices || []).join(", "));
      choices.placeholder = "Choice one, Choice two";
      const conditionSource = textInput("condition_source", field.condition?.source_key || "");
      conditionSource.placeholder = "format";
      const conditionValue = textInput("condition_value", field.condition?.value || "");
      conditionValue.placeholder = "workshop";
      const operator = document.createElement("select");
      operator.name = "condition_operator";
      operator.add(new Option("equals", "equals"));
      operator.add(new Option("does not equal", "not_equals"));
      operator.value = field.condition?.operator || "equals";
      if (system) {
        keyInput.type = "hidden";
        type.hidden = true;
        required.hidden = true;
        blindVisible.hidden = true;
        choices.type = "hidden";
        editorBody.append(
          keyInput,
          type,
          required,
          blindVisible,
          choices,
          inputLabel(
            field.key === "session_type" ? "Session format label" : "Field label",
            textInput("field_label", field.label, true)
          ),
          inputLabel("Placeholder", textInput("field_placeholder", field.placeholder || "")),
          inputLabel("Help text", textInput("field_help", field.help_text || ""))
        );
        if (field.key === "session_type") {
          choices.type = "text";
          choices.required = true;
          choices.setAttribute("aria-label", "Session format choices");
          choices.addEventListener("input", () => {
            state.fields[index].choices = choices.value
              .split(",")
              .map((value) => value.trim())
              .filter(Boolean);
          });
          editorBody.append(inputLabel(
            "Session formats",
            choices,
            "Enter the formats speakers can propose, separated by commas. Include the duration in each label."
          ));
        }
      } else {
        keyInput.type = "hidden";
        const requiredLabel = make("label");
        requiredLabel.className = "check-label";
        requiredLabel.append(required, make("span", "Required"));
        const blindVisibleLabel = make("label");
        blindVisibleLabel.className = "check-label";
        blindVisibleLabel.append(blindVisible, make("span", "Show in blind review"));
        const choicesLabel = inputLabel(
          "Answer choices",
          choices,
          "Enter at least two choices, separated by commas."
        );
        const syncChoiceVisibility = () => {
          choicesLabel.hidden = !["select", "multiselect"].includes(type.value);
        };
        type.addEventListener("change", syncChoiceVisibility);
        syncChoiceVisibility();
        editorBody.append(
          keyInput,
          inputLabel("Question", textInput("field_label", field.label, true), "This is the prompt speakers will see."),
          inputLabel("Answer format", type),
          requiredLabel,
          blindVisibleLabel,
          inputLabel("Placeholder text", textInput("field_placeholder", field.placeholder || ""), "Optional example shown inside an empty answer."),
          inputLabel("Instructions for speakers", textInput("field_help", field.help_text || ""), "Optional guidance shown below the question."),
          choicesLabel
        );
      }
      if (!system) {
        const conditionQuestion = document.createElement("select");
        conditionQuestion.name = "condition_source";
        conditionQuestion.add(new Option("Choose a question or event field", ""));
        // Flat, deliberately: an <optgroup> hides every option nested inside it
        // from the accessibility snapshot that browser automation reads, so the
        // chosen option never reports as selected. An eval agent burned 15 turns
        // re-selecting a dropdown that had been correct since the first attempt.
        // Event proposal fields are still listed before custom questions.
        const eventFields = [];
        const customFields = [];
        state.fields.forEach((candidate, candidateIndex) => {
          if (candidate.key === field.key || candidateIndex >= index) return;
          // The browser-facing value intentionally matches the visible label.
          // Browser automation and assistive tooling select native options by
          // that exposed value, while the stable schema key remains separate.
          const option = new Option(candidate.label, candidate.label);
          option.dataset.sourceKey = candidate.key;
          (proposalFieldKeys.has(candidate.key) || candidate.key === "track" ? eventFields : customFields).push(option);
        });
        for (const option of [...eventFields, ...customFields]) conditionQuestion.add(option);
        const selectedConditionSource = [...conditionQuestion.options].find(
          (option) => option.dataset.sourceKey === field.condition?.source_key
        );
        if (field.condition?.source_key && !selectedConditionSource) {
          const unresolvedSource = new Option(
            `Unavailable question (${field.condition.source_key}) — repair or clear`,
            field.condition.source_key
          );
          unresolvedSource.dataset.sourceKey = field.condition.source_key;
          unresolvedSource.dataset.unresolved = "true";
          unresolvedSource.selected = true;
          conditionQuestion.add(unresolvedSource, 1);
        } else {
          selectedConditionSource?.setAttribute("selected", "");
        }
        operator.options[0].textContent = "is";
        operator.options[1].textContent = "is not";
        conditionSource.remove();
        const advanced = make("details");
        advanced.className = "question-advanced";
        advanced.open = Boolean(field.condition?.source_key);
        const advancedSummary = make("summary", "Display rules (optional)");
        const advancedBody = make("div");
        const answerHost = make("div");
        const answerLabel = make("label", "Answer");
        const answerControls = make("span");
        const choiceConditionValue = document.createElement("select");
        choiceConditionValue.name = "condition_value";
        choiceConditionValue.disabled = true;
        choiceConditionValue.add(new Option("Choose a question first", ""));
        conditionValue.hidden = true;
        conditionValue.disabled = true;
        conditionValue.removeAttribute("name");
        answerControls.append(choiceConditionValue, conditionValue);
        answerLabel.append(answerControls);
        answerHost.append(answerLabel);
        const conditionWarning = make("p");
        conditionWarning.className = "condition-warning";
        conditionWarning.setAttribute("role", "alert");
        const selectedSourceKey = () => conditionQuestion.selectedOptions[0]?.dataset.sourceKey || "";
        const renderConditionAnswer = () => {
          const source = state.fields.find((candidate) => candidate.key === selectedSourceKey());
          const activeControl = answerHost.querySelector('[name="condition_value"]');
          const existing = activeControl?.value || conditionValue.value || choiceConditionValue.value;
          let control;
          if (source?.choices?.length) {
            choiceConditionValue.replaceChildren(new Option("Choose an answer", ""));
            source.choices.forEach((choice) => choiceConditionValue.add(new Option(choice, choice)));
            if (existing && !source.choices.includes(existing)) {
              const unresolvedChoice = new Option(`Unavailable answer (${existing}) — repair or clear`, existing);
              unresolvedChoice.dataset.unresolved = "true";
              choiceConditionValue.add(unresolvedChoice, 1);
            }
            choiceConditionValue.value = existing;
            choiceConditionValue.name = "condition_value";
            choiceConditionValue.disabled = false;
            choiceConditionValue.hidden = false;
            conditionValue.removeAttribute("name");
            conditionValue.disabled = true;
            conditionValue.hidden = true;
            control = choiceConditionValue;
          } else if (source || selectedSourceKey()) {
            conditionValue.value = existing;
            conditionValue.name = "condition_value";
            conditionValue.disabled = false;
            conditionValue.hidden = false;
            choiceConditionValue.removeAttribute("name");
            choiceConditionValue.disabled = true;
            choiceConditionValue.hidden = true;
            control = conditionValue;
          } else {
            choiceConditionValue.replaceChildren(new Option("Choose a question first", ""));
            choiceConditionValue.name = "condition_value";
            choiceConditionValue.disabled = true;
            choiceConditionValue.hidden = false;
            conditionValue.removeAttribute("name");
            conditionValue.disabled = true;
            conditionValue.hidden = true;
            control = choiceConditionValue;
          }
          const unresolved = conditionQuestion.selectedOptions[0]?.dataset.unresolved === "true"
            || control.selectedOptions?.[0]?.dataset.unresolved === "true";
          conditionWarning.textContent = unresolved
            ? "This saved display rule is no longer valid. Choose an available question and answer, or clear the question selection to remove the rule."
            : "";
          conditionWarning.hidden = !unresolved;
        };
        conditionQuestion.addEventListener("change", () => {
          if (!selectedSourceKey()) {
            conditionValue.value = "";
            choiceConditionValue.value = "";
          }
          renderConditionAnswer();
          const answer = answerHost.querySelector('[name="condition_value"]');
          if (answer && !answer.disabled) answer.focus();
        });
        advancedBody.append(
          make("p", "Show this question only when a speaker gives a particular answer to an earlier question or event field."),
          inputLabel("Question or event field", conditionQuestion),
          inputLabel("Comparison", operator),
          answerHost,
          conditionWarning
        );
        renderConditionAnswer();
        advanced.append(advancedSummary, advancedBody);
        const syncAdvancedVisibility = () => { advancedBody.hidden = !advanced.open; };
        advanced.addEventListener("toggle", syncAdvancedVisibility);
        syncAdvancedVisibility();
        editorBody.append(advanced);
        const remove = make("button", "Remove field");
        remove.type = "button";
        remove.className = "secondary";
        remove.addEventListener("click", () => {
          readFields();
          state.collapsedFieldKeys.delete(field.key);
          state.fields.splice(index, 1);
          renderFields();
        });
        const done = make("button", "Done editing question");
        done.type = "button";
        done.className = "secondary";
        done.addEventListener("click", () => {
          const labelInput = card.elements.field_label;
          if (!labelInput.reportValidity()) return;
          readFields();
          state.collapsedFieldKeys.add(field.key);
          state.selectedOutline = "custom";
          renderFields();
          byId("add-field").focus();
        });
        const actions = make("div");
        actions.className = "field-actions";
        actions.append(done, remove);
        editorBody.append(actions);
      }
      editor.append(editorSummary, editorBody);
      card.append(legend, editor);
      list.append(card);
      if (index === 1) {
        const coSpeakers = make("article");
        coSpeakers.className = "question-card question-card--system";
        const coSpeakerDetails = make("details");
        coSpeakerDetails.className = "question-editor";
        const coSpeakerSummary = make("summary");
        const coSpeakerIdentity = make("span");
        coSpeakerIdentity.append(
          make("strong", "Additional participants"),
          make("small", "Name, email, and role for each additional participant")
        );
        const coSpeakerMeta = make("span");
        coSpeakerMeta.className = "question-editor__meta";
        const optionalBadge = make("span", "Optional");
        optionalBadge.className = "badge";
        const repeatableBadge = make("span", "Repeatable");
        repeatableBadge.className = "badge";
        coSpeakerMeta.append(optionalBadge, repeatableBadge);
        coSpeakerSummary.append(coSpeakerIdentity, coSpeakerMeta);
        const coSpeakerBody = make("div");
        coSpeakerBody.className = "question-editor__body";
        coSpeakerBody.append(
          make(
            "p",
            "Each participant receives an invitation to accept or decline and complete their own profile."
          )
        );
        coSpeakerDetails.append(coSpeakerSummary, coSpeakerBody);
        coSpeakers.append(coSpeakerDetails);
        list.append(coSpeakers);
      }
    });
    renderOutline();
  }

  function outlineButton(label, section, selection) {
    const button = make("button");
    button.type = "button";
    button.className = "cfp-outline-item";
    button.dataset.section = section;
    button.dataset.selection = selection;
    button.setAttribute("aria-pressed", String(state.selectedOutline === selection));
    const text = make("span", label);
    text.className = "cfp-outline-label";
    button.append(text);
    button.addEventListener("click", () => selectOutline(selection));
    return button;
  }

  function renderOutline() {
    const items = byId("cfp-outline-items");
    if (!items) return;
    const proposalCount = state.fields.filter((field) => proposalFieldKeys.has(field.key)).length;
    const customCount = state.fields.filter((field) => !identityFieldKeys.includes(field.key) && !proposalFieldKeys.has(field.key)).length;
    const proposal = outlineButton("Proposal details", "questions", "proposal");
    proposal.append(make("small", String(proposalCount)));
    const custom = outlineButton("Custom questions", "questions", "custom");
    custom.append(make("small", String(customCount)));
    const availability = outlineButton("Availability", "availability", "availability");
    const nodes = [
      outlineButton("Description", "basics", "basics"), proposal, custom,
      outlineButton("Participants", "co-speakers", "co-speakers"),
      outlineButton("Confirmation", "confirmation", "confirmation"),
      availability
    ];
    items.replaceChildren(...nodes);
    selectOutline(state.selectedOutline, false);
  }

  function selectOutline(selection, focus = true) {
    state.selectedOutline = selection;
    const [kind, rawIndex] = selection.split(":");
    const sectionName = ["proposal", "custom", "question"].includes(kind) ? "questions" : kind;
    document.querySelectorAll(".cfp-editor-section").forEach((section) => { section.hidden = section.id !== `cfp-${sectionName}`; });
    const outlineSelection = kind === "question" ? "custom" : selection;
    document.querySelectorAll(".cfp-outline-item").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.selection === outlineSelection)));
    const activeOutline = document.querySelector('.cfp-outline-item[aria-pressed="true"]');
    const allQuestionCards = [...byId("form-fields").querySelectorAll(".question-card")];
    const questionCards = [...byId("form-fields").querySelectorAll(".question-card[data-index]")];
    allQuestionCards.forEach((card) => {
      const field = state.fields[Number(card.dataset.index)];
      card.hidden = kind === "proposal" ? !field || !proposalFieldKeys.has(field.key)
        : kind === "custom" ? !field || identityFieldKeys.includes(field.key) || proposalFieldKeys.has(field.key)
        : kind === "question" && card.dataset.index !== rawIndex;
    });
    byId("cfp-questions").classList.toggle("cfp-editor-section--single-question", ["question", "proposal"].includes(kind));
    // The proposal screen lists only system fields, so "+ Add custom question"
    // does not belong to it. Every other screen keeps the control -- including
    // the single-question screen you land on right after adding one.
    byId("cfp-questions").classList.toggle("cfp-editor-section--proposal", kind === "proposal");
    byId("cfp-questions").classList.toggle("cfp-editor-section--custom", kind === "custom");
    const customCount = state.fields.filter((field) => !identityFieldKeys.includes(field.key) && !proposalFieldKeys.has(field.key)).length;
    byId("cfp-custom-empty").hidden = kind !== "custom" || customCount > 0;
    const selectedField = kind === "question" ? state.fields[Number(rawIndex)] : null;
    const titles = { basics: "Description", proposal: "Proposal details", custom: "Custom questions", "co-speakers": "Additional participants", availability: "Availability", confirmation: "Confirmation" };
    byId("cfp-selection-title").textContent = selectedField?.label || titles[kind] || "Form";
    byId("cfp-selection-context").textContent = kind === "availability" ? "Form setting" : `${titles[kind] || "Proposal"} screen`;
    byId("cfp-selection-preview").hidden = true;
    document.querySelectorAll(".cfp-editor-section").forEach((section) => { if (!section.hidden) section.removeAttribute("aria-hidden"); });
    if (selectedField) questionCards[Number(rawIndex)]?.querySelector("details")?.setAttribute("open", "");
    if (focus) activeOutline?.focus({ preventScroll: true });
    activeOutline?.scrollIntoView({ block: "nearest", inline: "center" });
  }

  function readFields() {
    const fields = [];
    const conditions = [];
    byId("form-fields").querySelectorAll("fieldset").forEach((card, index) => {
      const keyValue = card.elements.field_key.value.trim();
      const type = card.elements.field_type?.value || "text";
      const choices = card.elements.field_choices.value.split(",").map((value) => value.trim()).filter(Boolean);
      const field = {
        key: keyValue,
        type,
        label: card.elements.field_label.value.trim(),
        required: ["speaker_name", "speaker_email", "proposal_title", "proposal_abstract"].includes(keyValue)
          || Boolean(card.elements.field_required?.checked),
        help_text: card.elements.field_help.value.trim(),
        placeholder: card.elements.field_placeholder.value.trim(),
        choices: ["select", "multiselect"].includes(type) ? choices : [],
        blind_visible: Boolean(card.elements.field_blind_visible?.checked)
      };
      const sourceControl = card.elements.condition_source;
      const source = sourceControl?.selectedOptions[0]?.dataset.sourceKey
        || sourceControl?.value.trim();
      const value = card.elements.condition_value?.value.trim();
      if (source && value) {
        field.condition = { source_key: source, operator: card.elements.condition_operator.value, value };
        conditions.push({ ...field.condition, target_key: keyValue });
      }
      fields.push(field);
    });
    state.fields = fields;
    // A choice-less Track is a builder affordance, not a publishable field:
    // the server requires at least one choice for it (cfp/models.py
    // `validate_choices`), so publishing with an empty one would 422.
    const publishable = fields.filter(
      (field) => field.key !== "track" || (field.choices || []).length > 0
    );
    return { fields: publishable.map(({ condition, ...field }) => field), conditions };
  }

  function renderRoutingRules() {
    const list = byId("routing-rules");
    list.replaceChildren();
    state.routingRules.forEach((rule, index) => {
      const card = make("fieldset");
      card.className = "routing-rule";
      card.append(make("legend", `Routing rule ${index + 1}`));
      const source = document.createElement("select");
      source.name = "routing_source";
      source.setAttribute("aria-label", "Question");
      source.required = true;
      source.add(new Option("Choose a question", ""));
      state.fields.forEach((field) => source.add(new Option(field.label, field.key)));
      source.value = rule.source_key || "";
      const operator = document.createElement("select");
      operator.name = "routing_operator";
      operator.setAttribute("aria-label", "Condition");
      [["Equals", "equals"], ["Does not equal", "not_equals"], ["Contains", "contains"]]
        .forEach(([label, value]) => operator.add(new Option(label, value)));
      operator.value = rule.operator || "equals";
      const value = textInput("routing_value", rule.value || "", true);
      value.setAttribute("aria-label", "Match value");
      value.placeholder = "Value";
      const destinationType = document.createElement("select");
      destinationType.name = "routing_destination_type";
      destinationType.setAttribute("aria-label", "Destination type");
      [["Category", "category"], ["Track", "track"], ["Review queue", "review_queue"]]
        .forEach(([label, choice]) => destinationType.add(new Option(label, choice)));
      destinationType.value = rule.track ? "track" : rule.review_queue ? "review_queue" : "category";
      const destination = textInput("routing_destination", rule[destinationType.value] || "", true);
      destination.setAttribute("aria-label", "Destination");
      const configureDestination = () => {
        const isTrack = destinationType.value === "track";
        if (isTrack) destination.setAttribute("list", "event-track-options");
        else destination.removeAttribute("list");
        destination.placeholder = isTrack ? "Choose a track" : destinationType.selectedOptions[0].text;
      };
      destinationType.addEventListener("change", configureDestination);
      configureDestination();
      card.append(
        make("span", "When", "routing-rule__word"), source, operator, value,
        make("span", "send to", "routing-rule__word"), destinationType, destination
      );
      const remove = make("button", "−");
      remove.type = "button";
      remove.className = "secondary routing-rule__remove";
      remove.setAttribute("aria-label", `Remove routing rule ${index + 1}`);
      remove.addEventListener("click", () => {
        readRoutingRules();
        state.routingRules.splice(index, 1);
        renderRoutingRules();
      });
      card.append(remove);
      list.append(card);
    });
  }

  function readRoutingRules() {
    state.routingRules = [...byId("routing-rules").querySelectorAll(".routing-rule")].map((card) => {
      const destinationType = card.elements.routing_destination_type.value;
      const destination = card.elements.routing_destination.value.trim() || null;
      return {
        source_key: card.elements.routing_source.value.trim(),
        operator: card.elements.routing_operator.value,
        value: card.elements.routing_value.value.trim(),
        category: destinationType === "category" ? destination : null,
        track: destinationType === "track" ? destination : null,
        review_queue: destinationType === "review_queue" ? destination : null
      };
    });
    return state.routingRules;
  }

  function draftSnapshot() {
    const form = byId("publish-form");
    syncDescription();
    const values = Object.fromEntries([...new FormData(form)].filter(([, value]) => typeof value === "string"));
    values.redirect_to_portal = form.elements.redirect_to_portal.checked;
    const schema = readFields();
    return { values, fields: schema.fields, conditions: schema.conditions, routing_rules: readRoutingRules(), important_dates: readImportantDates(), form_version: state.publishedForm?.version ?? null, saved_at_ms: Date.now() };
  }

  function saveLocalDraft() {
    if (!state.context) return;
    try {
      sessionStorage.setItem(draftKey(), JSON.stringify(draftSnapshot()));
    } catch (_) {
      byId("cfp-autosave-state").textContent = "Browser recovery is unavailable";
      return;
    }
    byId("cfp-autosave-state").textContent = state.publishedForm
      ? "Unsaved live changes backed up in this browser"
      : "Draft saved in this browser";
  }

  function queueLocalDraft() {
    byId("cfp-autosave-state").textContent = state.publishedForm
      ? "Backing up unsaved live changes…"
      : "Saving draft…";
    clearTimeout(state.draftTimer);
    state.draftTimer = setTimeout(saveLocalDraft, 500);
  }

  function restoreLocalDraft() {
    const raw = sessionStorage.getItem(draftKey());
    if (!raw) return false;
    try {
      const draft = JSON.parse(raw);
      if (state.publishedForm && draft.form_version !== state.publishedForm.version) {
        sessionStorage.removeItem(draftKey());
        return false;
      }
      const form = byId("publish-form");
      Object.entries(draft.values || {}).forEach(([name, value]) => {
        const field = form.elements[name];
        if (!field) return;
        if (field.type === "checkbox") field.checked = Boolean(value);
        else field.value = String(value);
      });
      setRichText(byId("cfp-description-editor"), draft.values?.description_html, draft.values?.welcome_text);
      syncDescription();
      if (Array.isArray(draft.fields) && draft.fields.length >= coreFields.length) {
        state.fields = draft.fields.map((field) => ({ ...field }));
        for (const condition of draft.conditions || []) {
          const target = state.fields.find((field) => field.key === condition.target_key);
          if (target) target.condition = { source_key: condition.source_key, operator: condition.operator, value: condition.value };
        }
      }
      state.routingRules = Array.isArray(draft.routing_rules) ? draft.routing_rules : [];
      state.importantDates = Array.isArray(draft.important_dates) ? draft.important_dates : [];
      renderFields();
      renderRoutingRules();
      renderImportantDates();
      if (state.publishedForm) {
        state.editing = true;
        state.dirty = true;
      }
      byId("cfp-autosave-state").textContent = state.publishedForm
        ? "Unsaved live changes restored from this browser"
        : "Draft restored from this browser";
      return true;
    } catch (_) {
      sessionStorage.removeItem(draftKey());
      return false;
    }
  }

  function validatePublishForm(form) {
    clearValidation();
    const opens = form.elements.opens_at;
    const closes = form.elements.closes_at;
    opens.setCustomValidity(""); closes.setCustomValidity("");
    syncAvailabilityLimits(form);
    const opensAt = toEpoch(opens.value);
    const closesAt = toEpoch(closes.value);
    if (Number.isNaN(opensAt)) {
      opens.setCustomValidity(`Choose a valid local time in ${state.eventTimeZone}.`);
    }
    if (Number.isNaN(closesAt)) {
      closes.setCustomValidity(`Choose a valid local time in ${state.eventTimeZone}.`);
    } else if (opensAt !== null && closesAt !== null && closesAt <= opensAt) {
      closes.setCustomValidity("Closing time must be after opening time.");
    } else if (closesAt !== null && closesAt >= state.eventStartsAtMs) {
      closes.setCustomValidity("The Call for Proposals must close before the event starts.");
    }

    const schema = readFields();
    // Validate against state.fields, not schema.fields. `readFields` drops a
    // choice-less Track from the publishable list while `renderFields` still
    // renders a card for it, so schema.fields is one short of the card list:
    // indexing by card position would validate every field after Track against
    // its neighbour's data and the last card against undefined. state.fields is
    // built from these same cards, so it stays 1:1 with them. Keys come from it
    // too, or a rule pointing at the visible-but-unpublished Track would be
    // reported as referencing a question that does not exist.
    const builderFields = state.fields;
    const keys = new Set(builderFields.map((field) => field.key));
    const keyCounts = builderFields.reduce((counts, field) => counts.set(field.key, (counts.get(field.key) || 0) + 1), new Map());
    byId("form-fields").querySelectorAll("fieldset").forEach((card, index) => {
      const field = builderFields[index];
      const keyInput = card.elements.field_key;
      keyInput.setCustomValidity(keyCounts.get(field.key) > 1 ? "Use a unique field key." : "");
      const choices = card.elements.field_choices;
      choices.setCustomValidity("");
      if (["select", "multiselect"].includes(field.type)) {
        const minimumChoices = field.key === "track" ? 1 : 2;
        if (field.choices.length < minimumChoices) choices.setCustomValidity(`Choice fields need at least ${minimumChoices === 1 ? "one choice" : "two choices"}.`);
        else if (new Set(field.choices).size !== field.choices.length) choices.setCustomValidity("Choices must be unique.");
      }
      const source = card.elements.condition_source;
      const value = card.elements.condition_value;
      if (source && value) {
        source.setCustomValidity(""); value.setCustomValidity("");
        const sourceKey = source.selectedOptions[0]?.dataset.sourceKey || source.value.trim();
        if (Boolean(sourceKey) !== Boolean(value.value.trim())) {
          (sourceKey ? value : source).setCustomValidity("Complete both parts of the display condition.");
        } else if (sourceKey && !keys.has(sourceKey)) {
          source.setCustomValidity("This saved display rule references an unavailable question. Repair it or explicitly clear the rule.");
        } else if (sourceKey && sourceKey === field.key) {
          source.setCustomValidity("A question cannot depend on itself.");
        } else if (value.selectedOptions?.[0]?.dataset.unresolved === "true") {
          value.setCustomValidity("This saved display rule references an unavailable answer. Repair it or explicitly clear the rule.");
        }
      }
    });

    const rules = readRoutingRules();
    [...byId("routing-rules").querySelectorAll(".routing-rule")].forEach((card, index) => {
      const rule = rules[index];
      card.elements.routing_source.setCustomValidity(keys.has(rule.source_key) ? "" : "Use the key of an existing question.");
      const hasDestination = Boolean(rule.category || rule.track || rule.review_queue);
      card.elements.routing_destination.setCustomValidity(hasDestination ? "" : "Choose where matching proposals should go.");
      if (rule.track && !state.eventTracks.includes(rule.track)) {
        card.elements.routing_destination.setCustomValidity("Choose an active track from this event.");
      }
    });
    const dependencies = new Map(schema.conditions.map((condition) => [condition.target_key, condition.source_key]));
    for (const [target] of dependencies) {
      const visited = new Set([target]);
      let source = dependencies.get(target);
      while (source && dependencies.has(source)) {
        if (visited.has(source)) {
          const index = builderFields.findIndex((field) => field.key === target);
          const input = byId("form-fields").querySelectorAll("fieldset")[index]?.elements.condition_source;
          input?.setCustomValidity("This display condition creates a circular dependency.");
          break;
        }
        visited.add(source);
        source = dependencies.get(source);
      }
    }
    const errors = [...form.querySelectorAll(":invalid")]
      .filter((field) => field.type !== "hidden")
      .map((field) => ({ field, message: field.validationMessage || "Check this field." }));
    if (errors.length) showValidation(errors);
    return errors.length === 0;
  }

  function selectedDescriptionRange() {
    const editor = byId("cfp-description-editor");
    const selection = window.getSelection();
    if (!selection || selection.rangeCount !== 1 || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    return editor.contains(range.commonAncestorContainer) ? range.cloneRange() : null;
  }

  function openLinkDialog(trigger) {
    const range = selectedDescriptionRange();
    if (!range) {
      setStatus("Select the description text you want to turn into a link.", true);
      byId("cfp-description-editor").focus();
      return;
    }
    pendingLinkRange = range;
    linkDialogTrigger = trigger;
    const dialog = byId("cfp-link-dialog");
    const input = byId("cfp-link-url");
    input.value = "";
    input.setCustomValidity("");
    byId("cfp-link-error").textContent = "";
    dialog.showModal();
    queueMicrotask(() => input.focus());
  }

  function closeLinkDialog() {
    if (byId("cfp-link-dialog").open) byId("cfp-link-dialog").close("cancel");
  }

  function installLinkDialog() {
    const dialog = byId("cfp-link-dialog");
    const form = byId("cfp-link-form");
    const input = byId("cfp-link-url");
    const error = byId("cfp-link-error");
    byId("cancel-cfp-link").addEventListener("click", closeLinkDialog);
    dialog.addEventListener("close", () => {
      pendingLinkRange = null;
      const trigger = linkDialogTrigger;
      linkDialogTrigger = null;
      trigger?.focus();
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      input.setCustomValidity("");
      error.textContent = "";
      let url;
      try {
        url = new URL(input.value.trim());
      } catch (_) {
        input.setCustomValidity("Enter a complete web address.");
      }
      if (url && !["https:", "http:"].includes(url.protocol)) {
        input.setCustomValidity("Use an https:// or http:// web address.");
      }
      if (!input.checkValidity()) {
        error.textContent = input.validationMessage;
        input.focus();
        return;
      }
      const editor = byId("cfp-description-editor");
      if (!pendingLinkRange || !editor.contains(pendingLinkRange.commonAncestorContainer)) {
        error.textContent = "The selected text is no longer available. Cancel and select it again.";
        return;
      }
      const link = document.createElement("a");
      link.href = url.href;
      link.rel = "noopener";
      try {
        pendingLinkRange.surroundContents(link);
      } catch (_) {
        link.append(pendingLinkRange.extractContents());
        pendingLinkRange.insertNode(link);
      }
      syncDescription();
      editor.dispatchEvent(new Event("input", { bubbles: true }));
      dialog.close("added");
    });
  }

  function installBuilder() {
    const publish = byId("publish-form");
    byId("cfp-description-editor").addEventListener("input", syncDescription);
    document.querySelectorAll("[data-rich-command]").forEach((button) => {
      button.addEventListener("mousedown", (event) => event.preventDefault());
      button.addEventListener("click", () => {
      const command = button.dataset.richCommand;
      if (command === "createLink") {
        openLinkDialog(button);
        return;
      }
      byId("cfp-description-editor").focus();
      document.execCommand(command, false, null);
      syncDescription();
      });
    });
    byId("add-important-date").addEventListener("click", () => {
      state.importantDates.push({ label: "", at_ms: null });
      renderImportantDates();
      state.dirty = true;
      queueLocalDraft();
      byId("important-date-rows").querySelector(".important-date-row:last-child input")?.focus();
    });
    renderImportantDates();
    publish.addEventListener("input", (event) => {
      event.target.setCustomValidity?.("");
      event.target.removeAttribute?.("aria-invalid");
      const describedBy = (event.target.getAttribute?.("aria-describedby") || "").split(/\s+/).filter(Boolean);
      describedBy.filter((id) => id.endsWith("-error")).forEach((id) => byId(id)?.remove());
      event.target.setAttribute?.("aria-describedby", describedBy.filter((id) => !id.endsWith("-error")).join(" "));
      state.dirty = true;
      if (state.publishedForm) {
        byId("publish-action-label").textContent = "Unsaved changes";
        byId("publish-result").textContent = "Nothing changes publicly until you update the live CFP.";
      }
      queueLocalDraft();
    });
    publish.elements.opens_at.addEventListener("input", () => syncAvailabilityLimits(publish));
    byId("open-cfp-immediately").addEventListener("click", () => {
      publish.elements.opens_at.value = "";
      publish.elements.opens_at.dispatchEvent(new Event("input", { bubbles: true }));
      publish.elements.opens_at.focus();
    });
    const add = byId("add-field");
    add.addEventListener("click", () => {
      readFields();
      const field = { key: `question_${state.fields.length - 3}`, type: "text", label: "New question", required: false, choices: [] };
      state.collapsedFieldKeys.delete(field.key);
      state.fields.push(field);
      renderFields();
      selectOutline(`question:${state.fields.length - 1}`);
      state.dirty = true;
      queueLocalDraft();
    });
    renderFields();
    byId("add-routing").textContent = "+ Add rule";
    byId("add-routing").addEventListener("click", () => {
      readRoutingRules();
      state.routingRules.push({ source_key: "", operator: "equals", value: "", category: "", track: "", review_queue: "" });
      renderRoutingRules();
      state.dirty = true;
      queueLocalDraft();
    });
    renderRoutingRules();
    byId("cfp-notification-settings").addEventListener("input", (event) => {
      event.target.setCustomValidity?.("");
      state.dirty = true;
      if (state.publishedForm) {
        byId("publish-action-label").textContent = "Unsaved changes";
        byId("publish-result").textContent = "Nothing changes publicly until you update the live CFP.";
      }
      queueLocalDraft();
    });
  }

  async function restoreSession() {
    try {
      const session = await api("/api/v1/auth/session");
      state.userId = session.user_id;
      const eventId = eventIdFromPage(session);
      if (!eventId) throw new Error("Choose an event before opening its Call for Proposals.");
      state.csrf = session.csrf_token;
      const workspace = await loadWorkspace(eventId);
      state.context = { organization_id: workspace.organization_id, event_id: workspace.event_id };
      state.eventName = workspace.event_name;
      state.eventStartsAtMs = workspace.event_starts_at_ms;
      const currentEvent = await api(`/api/v1/admin/events/${encodeURIComponent(workspace.event_id)}`);
      if (!currentEvent?.time_zone) throw new Error("The event time zone could not be loaded.");
      state.eventStatus = currentEvent.status;
      state.eventTimeZone = currentEvent.time_zone;
      byId("cfp-time-zone").textContent = state.eventTimeZone;
      await loadEventTracks(eventId);
      state.publishedForm = workspace.published_form;
      state.editing = true;
      if (state.publishedForm) {
        loadPublishedSettings(state.publishedForm);
        restoreLocalDraft();
      }
      else {
        const slug = byId("publish-form").elements.slug;
        const readableSlug = state.eventName.toLowerCase()
          .normalize("NFKD")
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "") || "event";
        slug.value = readableSlug.slice(0, 80);
        restoreLocalDraft();
      }
      syncEventTrackField();
      renderFields();
      syncAvailabilityLimits(byId("publish-form"));
      renderWorkspace();
      setStatus(state.publishedForm ? "" : "Configure and publish the proposal form.");
    } catch (error) {
      if (error.status === 401) {
        location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname + location.search)}`);
        return;
      }
      setStatus(window.SessionBuddyApi.message(error), true);
    }
  }

  byId("publish-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const submitButton = formElement.querySelector('button[type="submit"], button:not([type])');
    if (formElement.getAttribute("aria-busy") === "true") return;
    const updating = Boolean(state.publishedForm);
    const idleLabel = updating ? "Update live CFP" : "Publish CFP";
    let completed = false;
    try {
      syncDescription();
      if (!formElement.elements.welcome_text.value) {
        selectOutline("basics", false);
        setStatus("Add a CFP description before publishing.", true);
        byId("cfp-description-editor").focus();
        return;
      }
      if (!validatePublishForm(formElement)) return;
      formElement.setAttribute("aria-busy", "true");
      submitButton.disabled = true;
      submitButton.textContent = updating ? "Saving…" : "Publishing…";
      byId("publish-action-label").textContent = updating ? "Saving changes" : "Publishing CFP";
      byId("publish-result").textContent = "Please wait while the form is updated.";
      const values = Object.fromEntries(new FormData(formElement));
      const schema = readFields();
      const routingRules = readRoutingRules();
      const payload = {
        slug: values.slug,
        welcome_text: values.welcome_text,
        description_html: values.description_html || null,
        important_dates: readImportantDates(),
        ...schema,
        routing_rules: routingRules,
        opens_at_ms: toEpoch(values.opens_at),
        closes_at_ms: toEpoch(values.closes_at),
        submission_limit: values.submission_limit ? Number(values.submission_limit) : null,
        co_speaker_limit: Number(values.co_speaker_limit),
        success_title: values.success_title,
        success_message: values.success_message,
        redirect_to_portal: formElement.elements.redirect_to_portal.checked
      };
      payload.confirmation_subject = values.confirmation_subject;
      payload.confirmation_body = values.confirmation_body;
      if (updating) payload.version = state.publishedForm.version;
      const form = await api(
        updating
          ? `/api/v1/admin/events/${encodeURIComponent(state.context.event_id)}/cfp`
          : `/api/v1/admin/events/${encodeURIComponent(state.context.event_id)}/cfp/publish`,
        {
          method: updating ? "PATCH" : "POST",
          headers: updating ? admin() : { ...admin(), "idempotency-key": key() },
          body: JSON.stringify(payload)
        }
      );
      state.publishedForm = form;
      state.editing = false;
      state.dirty = false;
      sessionStorage.removeItem(draftKey());
      clearValidation();
      renderWorkspace();
      completed = true;
      submitButton.textContent = updating ? "Saved ✓" : "Published ✓";
      byId("publish-action-label").textContent = updating ? "Changes saved" : "CFP published";
      byId("publish-result").textContent = "Your form is up to date.";
      byId("cfp-saved-state").textContent = "Saved just now";
      byId("cfp-saved-state").hidden = false;
      setStatus(updating ? "Your CFP changes were saved." : "Your CFP was published successfully.", "success");
    } catch (error) {
      if (error.status === 401) {
        saveLocalDraft();
        location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname + location.search)}`);
        return;
      }
      const message = error.code === "slug_conflict"
        ? "That public URL is already taken. Add a year, city, or short code to make it unique."
        : error.code === "stale_conflict"
          ? "This CFP changed while you were editing it. Reload the page, review the latest version, and try again."
          : window.SessionBuddyApi.message(error);
      setStatus(message, true);
      if (error.code === "slug_conflict") {
        selectOutline("public-link", false);
        formElement.elements.slug.focus();
      }
    } finally {
      formElement.setAttribute("aria-busy", "false");
      submitButton.disabled = !state.context;
      if (!completed) submitButton.textContent = idleLabel;
      else window.setTimeout(() => {
        if (formElement.getAttribute("aria-busy") !== "true") submitButton.textContent = "Update live CFP";
      }, 1800);
    }
  });

  byId("edit-cfp").addEventListener("click", () => {
    state.editing = true;
    state.dirty = false;
    renderWorkspace();
    byId("cfp-builder-title").focus?.();
    byId("publish-settings").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  byId("cancel-cfp-edit").addEventListener("click", () => {
    if (!state.publishedForm) return;
    loadPublishedSettings(state.publishedForm);
    state.editing = false;
    state.dirty = false;
    sessionStorage.removeItem(draftKey());
    renderWorkspace();
    setStatus("No changes were made.");
    byId("cfp-link-title").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  let copyFeedbackTimer = null;
  async function copyCfpUrl(trigger) {
    const input = byId("cfp-url");
    const originalLabel = trigger.textContent;
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(input.value);
      else {
        input.select();
        if (!document.execCommand("copy")) throw new Error("Copy unavailable");
      }
      byId("copy-result").textContent = "Copied.";
      byId("cfp-copy-feedback").textContent = "Call for Proposals URL copied to clipboard.";
      trigger.textContent = "✓ Copied";
      trigger.classList.add("is-copied");
      clearTimeout(copyFeedbackTimer);
      copyFeedbackTimer = setTimeout(() => {
        trigger.textContent = originalLabel;
        trigger.classList.remove("is-copied");
      }, 2200);
    } catch (_) {
      input.focus();
      input.select();
      byId("copy-result").textContent = "Select the URL and copy it manually.";
      byId("cfp-copy-feedback").textContent = "Copy was unavailable. The URL is selected for manual copying.";
    }
  }

  byId("copy-cfp-url").addEventListener("click", (event) => copyCfpUrl(event.currentTarget));
  byId("copy-cfp-header").addEventListener("click", (event) => copyCfpUrl(event.currentTarget));

  function renderPreview() {
    const form = byId("publish-form");
    const values = Object.fromEntries(new FormData(form));
    const fields = readFields().fields;
    const content = byId("cfp-preview-content");
    content.replaceChildren();
    const header = document.createElement("header");
    const eventName = document.createElement("p");
    eventName.className = "eyebrow";
    eventName.textContent = state.eventName || "Event";
    const title = document.createElement("h1");
    title.textContent = "Call for Proposals";
    const welcome = document.createElement("p");
    welcome.className = "lede";
    welcome.textContent = String(values.welcome_text || "Add a welcome message.");
    header.append(eventName, title, welcome);
    const previewForm = document.createElement("div");
    previewForm.className = "cfp-preview-form";
    const visibleFields = fields.filter((field) => !identityFieldKeys.includes(field.key));
    visibleFields.forEach((field) => {
      const label = document.createElement("label");
      const labelText = document.createElement("span");
      labelText.textContent = `${field.label || "Untitled question"}${field.required ? " *" : ""}`;
      let control;
      if (field.type === "textarea") control = document.createElement("textarea");
      else if (["select", "multiselect"].includes(field.type)) {
        control = document.createElement("select");
        control.add(new Option("Choose an option", ""));
        (field.choices || []).forEach((choice) => control.add(new Option(choice, choice)));
      } else {
        control = document.createElement("input");
        control.type = ["email", "url", "phone"].includes(field.type) ? (field.type === "phone" ? "tel" : field.type) : "text";
      }
      control.disabled = true;
      control.placeholder = field.placeholder || "";
      label.append(labelText, control);
      if (field.help_text) {
        const help = document.createElement("small");
        help.textContent = field.help_text;
        label.append(help);
      }
      previewForm.append(label);
    });
    const submit = document.createElement("button");
    submit.type = "button";
    submit.disabled = true;
    submit.textContent = "Submit proposal";
    previewForm.append(submit);
    content.append(header, previewForm);
  }

  byId("preview-cfp").addEventListener("click", () => {
    const preview = byId("cfp-selection-preview");
    const opening = preview.hidden;
    if (opening) renderPreview();
    document.querySelectorAll(".cfp-editor-section").forEach((section) => { section.hidden = true; });
    byId("cfp-form-outline").hidden = opening;
    byId("cfp-selection-heading").hidden = opening;
    byId("cfp-editor-actions").hidden = opening;
    byId("cfp-notification-settings").hidden = opening;
    preview.hidden = !opening;
    byId("preview-cfp").textContent = opening ? "Back to editing" : "Preview form";
    byId("preview-cfp").setAttribute("aria-pressed", String(opening));
    if (!opening) selectOutline(state.selectedOutline, false);
    byId("preview-cfp").focus();
  });
  window.addEventListener("beforeunload", (event) => {
    if (!state.publishedForm || !state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  byId("edit-cfp").addEventListener("click", () => byId("cfp-share-dialog").close());
  function setLiveUrlEditing(editing) {
    byId("cfp-live-url").hidden = editing;
    byId("cfp-live-url-editor").hidden = !editing;
    byId("copy-cfp-header").hidden = editing;
    byId("configure-url-header").hidden = editing;
    byId("save-url-header").hidden = !editing;
    byId("cancel-url-header").hidden = !editing;
    if (editing) {
      byId("cfp-live-slug").value = state.publishedForm?.slug || "";
      byId("cfp-live-slug").focus();
      byId("cfp-live-slug").select();
    }
  }

  byId("configure-url-header").addEventListener("click", () => setLiveUrlEditing(true));
  byId("cancel-url-header").addEventListener("click", () => setLiveUrlEditing(false));
  byId("save-url-header").addEventListener("click", async () => {
    const input = byId("cfp-live-slug");
    if (!input.reportValidity() || !state.publishedForm) return;
    const button = byId("save-url-header");
    button.disabled = true;
    button.textContent = "Saving…";
    try {
      const current = state.publishedForm;
      const updated = await api(`/api/v1/admin/events/${encodeURIComponent(state.context.event_id)}/cfp`, {
        method: "PATCH",
        headers: admin(),
        body: JSON.stringify({
          version: current.version,
          slug: input.value.trim(),
          welcome_text: current.welcome_text,
          description_html: current.description_html || null,
          important_dates: current.important_dates || [],
          fields: current.fields,
          conditions: current.conditions || [],
          routing_rules: current.routing_rules || [],
          opens_at_ms: current.opens_at_ms,
          closes_at_ms: current.closes_at_ms,
          submission_limit: current.submission_limit,
          co_speaker_limit: current.co_speaker_limit ?? 1,
          success_title: current.success_title,
          success_message: current.success_message,
          redirect_to_portal: current.redirect_to_portal,
          confirmation_subject: current.confirmation_subject,
          confirmation_body: current.confirmation_body
        })
      });
      state.publishedForm = updated;
      byId("publish-form").elements.slug.value = updated.slug;
      setLiveUrlEditing(false);
      renderWorkspace();
      setStatus("");
    } catch (error) {
      setStatus(error.code === "slug_conflict"
        ? "That public URL is already taken. Add a year, city, or short code."
        : window.SessionBuddyApi.message(error), true);
      input.focus();
    } finally {
      button.disabled = false;
      button.textContent = "Save URL";
    }
  });

  installLinkDialog();
  installBuilder();
  renderWorkspace();
  restoreSession();
})();

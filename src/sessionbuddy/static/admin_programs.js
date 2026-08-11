(() => {
  "use strict";
  const coreFields = [
    { key: "speaker_name", type: "text", label: "Speaker name", required: true, choices: [] },
    { key: "speaker_email", type: "email", label: "Email", required: true, choices: [] },
    { key: "proposal_title", type: "text", label: "Proposal title", required: true, choices: [] },
    { key: "proposal_abstract", type: "textarea", label: "Proposal abstract", required: true, choices: [] }
  ];
  const state = { context: null, csrf: null, eventName: "", eventStatus: "", eventStartsAtMs: null, eventTimeZone: "", eventTracks: [], publishedForm: null, editing: false, fields: structuredClone(coreFields), routingRules: [] };
  const byId = (id) => document.getElementById(id);
  const jsonHeaders = () => ({ "content-type": "application/json" });
  const admin = () => ({ ...jsonHeaders(), "x-csrf-token": state.csrf });
  const key = () => `${crypto.randomUUID()}-${crypto.randomUUID()}`;

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

  function inputLabel(text, input) {
    const label = make("label", text);
    label.append(input);
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

  function cfpAvailability(form) {
    const now = Date.now();
    if (form.opens_at_ms && now < form.opens_at_ms) return "Scheduled";
    if (form.closes_at_ms && now > form.closes_at_ms) return "Closed";
    return "Live";
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
    publishButton.textContent = published ? "Save changes" : "Publish CFP";
    byId("publish-action-label").textContent = !eventIsActive
      ? "Event draft"
      : published ? "Published CFP" : "Ready to publish?";
    byId("published-note").hidden = !published;
    byId("publish-settings").hidden = Boolean(published) && !state.editing;
    byId("edit-cfp").hidden = !published || state.editing;
    byId("cancel-cfp-edit").hidden = !published;
    const savedState = byId("cfp-saved-state");
    savedState.hidden = !published || state.editing;
    if (published && !savedState.textContent) savedState.textContent = "Saved";
    byId("publish-result").textContent = !eventIsActive
      ? "Activate the event before publishing its CFP."
      : published
      ? "Published. Use the CFP link above, then review proposals as they arrive."
      : "Complete the form settings below, then publish.";

    const live = byId("cfp-link-live");
    const empty = byId("cfp-link-empty");
    const badge = byId("cfp-state");
    live.hidden = !published || !eventIsActive;
    empty.hidden = Boolean(published) && eventIsActive;
    badge.className = `badge${published ? " success" : ""}`;
    badge.textContent = !eventIsActive ? "Event draft" : published ? cfpAvailability(published) : "Not published";
    if (!eventIsActive) {
      empty.textContent = "Activate the event to make its CFP public.";
      return;
    }
    if (!published) {
      empty.textContent = "Configure and publish the proposal form below to get a shareable link.";
      return;
    }
    const publicUrl = `${location.origin}/cfp/${published.slug}`;
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
    state.routingRules = structuredClone(form.routing_rules || []);
    const editor = byId("publish-form");
    editor.elements.slug.value = form.slug;
    if (form.welcome_text) editor.elements.welcome_text.value = form.welcome_text;
    editor.elements.opens_at.value = toLocalInput(form.opens_at_ms);
    editor.elements.closes_at.value = toLocalInput(form.closes_at_ms);
    editor.elements.submission_limit.value = form.submission_limit || "";
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
      const core = index < coreFields.length;
      const card = make("fieldset");
      card.className = "question-card";
      card.dataset.index = String(index);
      const legend = make("legend", core ? `Required field: ${field.key}` : `Custom field ${index - 3}`);
      legend.className = "sr-only";
      const editor = make("details");
      editor.className = "question-editor";
      editor.open = !core;
      const editorSummary = make("summary");
      const summaryIdentity = make("span");
      summaryIdentity.append(make("strong", field.label));
      if (!core) summaryIdentity.append(make("small", field.key));
      const summaryMeta = make("span");
      summaryMeta.className = "question-editor__meta";
      if (!core) {
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
      editorSummary.append(summaryIdentity, summaryMeta);
      const editorBody = make("div");
      editorBody.className = "question-editor__body";
      const keyInput = textInput("field_key", field.key, true);
      keyInput.pattern = "[a-z][a-z0-9_]*";
      keyInput.readOnly = core;
      const type = document.createElement("select");
      type.name = "field_type";
      [
        ["Short text", "text"], ["Long text", "textarea"], ["Email", "email"],
        ["URL", "url"], ["Phone", "phone"], ["Single choice", "select"],
        ["Multiple choice", "multiselect"], ["Checkbox", "checkbox"],
        ["File upload", "file"], ["Image upload", "image"]
      ].forEach(([label, value]) => type.add(new Option(label, value)));
      type.value = field.type;
      if (core) type.disabled = true;
      const required = document.createElement("input");
      required.type = "checkbox";
      required.name = "field_required";
      required.checked = field.required;
      required.disabled = core;
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
      if (core) {
        keyInput.type = "hidden";
        choices.type = "hidden";
        editorBody.append(
          keyInput,
          choices,
          inputLabel("Question label", textInput("field_label", field.label, true)),
          inputLabel("Placeholder", textInput("field_placeholder", field.placeholder || "")),
          inputLabel("Help text", textInput("field_help", field.help_text || ""))
        );
      } else {
        editorBody.append(
          inputLabel("Field key", keyInput),
          inputLabel("Label", textInput("field_label", field.label, true)),
          inputLabel("Type", type),
          inputLabel("Required", required),
          inputLabel("Show to reviewers in blind rounds", blindVisible),
          inputLabel("Placeholder", textInput("field_placeholder", field.placeholder || "")),
          inputLabel("Help text", textInput("field_help", field.help_text || "")),
          inputLabel("Choices (comma separated)", choices)
        );
      }
      if (!core) {
        editorBody.append(
          make("p", "Optional display condition"),
          inputLabel("Show when field key", conditionSource),
          inputLabel("Comparison", operator),
          inputLabel("Value", conditionValue)
        );
        const remove = make("button", "Remove field");
        remove.type = "button";
        remove.className = "secondary";
        remove.addEventListener("click", () => {
          readFields();
          state.fields.splice(index, 1);
          renderFields();
        });
        editorBody.append(remove);
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
          make("strong", "Co-speakers"),
          make("small", "Name and email for each additional presenter")
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
            "Speakers may add up to 10 co-speakers. Each person receives an invitation to accept or decline and complete their own profile."
          )
        );
        coSpeakerDetails.append(coSpeakerSummary, coSpeakerBody);
        coSpeakers.append(coSpeakerDetails);
        list.append(coSpeakers);
      }
    });
  }

  function readFields() {
    const fields = [];
    const conditions = [];
    byId("form-fields").querySelectorAll("fieldset").forEach((card, index) => {
      const keyValue = card.elements.field_key.value.trim();
      const type = card.elements.field_type?.value || coreFields[index].type;
      const choices = card.elements.field_choices.value.split(",").map((value) => value.trim()).filter(Boolean);
      const field = {
        key: keyValue,
        type,
        label: card.elements.field_label.value.trim(),
        required: index < coreFields.length || card.elements.field_required.checked,
        help_text: card.elements.field_help.value.trim(),
        placeholder: card.elements.field_placeholder.value.trim(),
        choices: ["select", "multiselect"].includes(type) ? choices : [],
        blind_visible: Boolean(card.elements.field_blind_visible?.checked)
      };
      const source = card.elements.condition_source?.value.trim();
      const value = card.elements.condition_value?.value.trim();
      if (source && value) {
        field.condition = { source_key: source, operator: card.elements.condition_operator.value, value };
        conditions.push({ ...field.condition, target_key: keyValue });
      }
      fields.push(field);
    });
    state.fields = fields;
    return { fields: fields.map(({ condition, ...field }) => field), conditions };
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

  function validatePublishForm(form) {
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
    const keys = new Set(schema.fields.map((field) => field.key));
    byId("form-fields").querySelectorAll("fieldset").forEach((card, index) => {
      const field = schema.fields[index];
      const choices = card.elements.field_choices;
      choices.setCustomValidity("");
      if (["select", "multiselect"].includes(field.type)) {
        if (field.choices.length < 2) choices.setCustomValidity("Choice fields need at least two choices.");
        else if (new Set(field.choices).size !== field.choices.length) choices.setCustomValidity("Choices must be unique.");
      }
      const source = card.elements.condition_source;
      const value = card.elements.condition_value;
      if (source && value) {
        source.setCustomValidity(""); value.setCustomValidity("");
        if (Boolean(source.value.trim()) !== Boolean(value.value.trim())) {
          (source.value.trim() ? value : source).setCustomValidity("Complete both parts of the display condition.");
        } else if (source.value.trim() && !keys.has(source.value.trim())) {
          source.setCustomValidity("Use the key of an existing question.");
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
    return form.reportValidity();
  }

  function installBuilder() {
    const publish = byId("publish-form");
    publish.addEventListener("input", (event) => {
      event.target.setCustomValidity?.("");
      if (state.publishedForm) {
        byId("publish-action-label").textContent = "Unsaved changes";
        byId("publish-result").textContent = "Save when you are ready.";
      }
    });
    publish.elements.opens_at.addEventListener("input", () => syncAvailabilityLimits(publish));
    const add = byId("add-field");
    add.addEventListener("click", () => {
      readFields();
      state.fields.push({ key: `question_${state.fields.length - 3}`, type: "text", label: "New question", required: false, choices: [] });
      renderFields();
    });
    renderFields();
    byId("add-routing").textContent = "+ Add rule";
    byId("add-routing").addEventListener("click", () => {
      readRoutingRules();
      state.routingRules.push({ source_key: "", operator: "equals", value: "", category: "", track: "", review_queue: "" });
      renderRoutingRules();
    });
    renderRoutingRules();
  }

  async function restoreSession() {
    try {
      const session = await api("/api/v1/auth/session");
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
      byId("cfp-slug-prefix").textContent = `${location.host}/cfp/`;
      await loadEventTracks(eventId);
      state.publishedForm = workspace.published_form;
      state.editing = !state.publishedForm;
      if (state.publishedForm) loadPublishedSettings(state.publishedForm);
      else {
        const slug = byId("publish-form").elements.slug;
        slug.value = state.eventName.toLowerCase()
          .normalize("NFKD")
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "") || `event-${eventId.slice(0, 8)}`;
      }
      syncAvailabilityLimits(byId("publish-form"));
      byId("manage-access").href = `/admin/events/${encodeURIComponent(eventId)}/access`;
      byId("manage-access").hidden = false;
      renderWorkspace();
      setStatus(state.publishedForm ? "Your CFP is published and ready to share." : "Configure and publish the proposal form.");
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
    const idleLabel = updating ? "Save changes" : "Publish CFP";
    let completed = false;
    try {
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
        ...schema,
        routing_rules: routingRules,
        opens_at_ms: toEpoch(values.opens_at),
        closes_at_ms: toEpoch(values.closes_at),
        submission_limit: values.submission_limit ? Number(values.submission_limit) : null,
        success_title: values.success_title,
        success_message: values.success_message,
        redirect_to_portal: formElement.elements.redirect_to_portal.checked
      };
      if (updating) payload.version = state.publishedForm.version;
      else {
        payload.confirmation_subject = values.confirmation_subject;
        payload.confirmation_body = values.confirmation_body;
      }
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
      renderWorkspace();
      completed = true;
      submitButton.textContent = updating ? "Saved ✓" : "Published ✓";
      byId("publish-action-label").textContent = updating ? "Changes saved" : "CFP published";
      byId("publish-result").textContent = "Your form is up to date.";
      byId("cfp-saved-state").textContent = "Saved just now";
      byId("cfp-saved-state").hidden = false;
      setStatus(updating ? "Your CFP changes were saved." : "Your CFP was published successfully.", "success");
    } catch (error) {
      const message = error.code === "slug_conflict"
        ? "That public slug is already in use. Choose another."
        : error.code === "stale_conflict"
          ? "This CFP changed while you were editing it. Reload the page, review the latest version, and try again."
          : window.SessionBuddyApi.message(error);
      setStatus(message, true);
    } finally {
      formElement.setAttribute("aria-busy", "false");
      submitButton.disabled = !state.context;
      if (!completed) submitButton.textContent = idleLabel;
      else window.setTimeout(() => {
        if (formElement.getAttribute("aria-busy") !== "true") submitButton.textContent = "Save changes";
      }, 1800);
    }
  });

  byId("edit-cfp").addEventListener("click", () => {
    state.editing = true;
    renderWorkspace();
    byId("cfp-builder-title").focus?.();
    byId("publish-settings").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  byId("cancel-cfp-edit").addEventListener("click", () => {
    if (!state.publishedForm) return;
    loadPublishedSettings(state.publishedForm);
    state.editing = false;
    renderWorkspace();
    setStatus("No changes were made.");
    byId("cfp-link-title").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  byId("copy-cfp-url").addEventListener("click", async () => {
    const input = byId("cfp-url");
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(input.value);
      else {
        input.select();
        if (!document.execCommand("copy")) throw new Error("Copy unavailable");
      }
      byId("copy-result").textContent = "Copied.";
    } catch (_) {
      input.focus();
      input.select();
      byId("copy-result").textContent = "Select the URL and copy it manually.";
    }
  });

  installBuilder();
  renderWorkspace();
  restoreSession();
})();

(() => {
  "use strict";
  const coreFields = [
    { key: "speaker_name", type: "text", label: "Speaker name", required: true, choices: [] },
    { key: "speaker_email", type: "email", label: "Email", required: true, choices: [] },
    { key: "proposal_title", type: "text", label: "Proposal title", required: true, choices: [] },
    { key: "proposal_abstract", type: "textarea", label: "Proposal abstract", required: true, choices: [] }
  ];
  const state = { context: null, csrf: null, eventName: "", eventStartsAtMs: null, publishedForm: null, fields: structuredClone(coreFields), routingRules: [] };
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
    const path = `/api/v1/admin/events/${encodeURIComponent(eventId)}/cfp`;
    for (const delay of [0, 150, 350]) {
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      try {
        return await api(path);
      } catch (error) {
        if (error.status !== 404 || delay === 350) throw error;
      }
    }
    throw new Error("The event could not be loaded.");
  }

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
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

  function toEpoch(value) {
    if (!value) return null;
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? null : parsed.getTime();
  }

  function toLocalInput(value) {
    if (!value) return "";
    const date = new Date(value);
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }

  function earliestOpeningMs() {
    return Math.ceil(Date.now() / 60000) * 60000;
  }

  function syncAvailabilityLimits(form) {
    const opens = form.elements.opens_at;
    const closes = form.elements.closes_at;
    opens.min = toLocalInput(earliestOpeningMs());
    opens.max = state.eventStartsAtMs ? toLocalInput(state.eventStartsAtMs - 1) : "";
    closes.min = opens.value ? toLocalInput(toEpoch(opens.value) + 60000) : "";
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
    publishButton.disabled = !state.context;
    publishButton.textContent = published ? "Save changes" : "Publish form";
    byId("publish-settings").open = !published;
    byId("published-note").hidden = !published;
    byId("publish-result").textContent = published
      ? "Published. Use the CFP link above, then review proposals as they arrive."
      : "Complete the form settings below, then publish.";

    const live = byId("cfp-link-live");
    const empty = byId("cfp-link-empty");
    const badge = byId("cfp-state");
    live.hidden = !published;
    empty.hidden = Boolean(published);
    badge.className = `badge${published ? " success" : ""}`;
    badge.textContent = published ? "Live" : "Not published";
    if (!published) {
      empty.textContent = "Configure and publish the proposal form below to get a shareable link.";
      return;
    }
    const publicUrl = `${location.origin}/cfp/${published.slug}`;
    byId("cfp-url").value = publicUrl;
    byId("open-cfp-url").href = publicUrl;
    byId("review-submissions").href = `/admin/events/${encodeURIComponent(state.context.event_id)}/submissions`;
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
        choices: ["select", "multiselect"].includes(type) ? choices : []
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
      const source = document.createElement("select");
      source.name = "routing_source";
      source.required = true;
      source.add(new Option("Choose a question", ""));
      state.fields.forEach((field) => source.add(new Option(field.label, field.key)));
      source.value = rule.source_key || "";
      const operator = document.createElement("select");
      operator.name = "routing_operator";
      [["Equals", "equals"], ["Does not equal", "not_equals"], ["Contains", "contains"]]
        .forEach(([label, value]) => operator.add(new Option(label, value)));
      operator.value = rule.operator || "equals";
      card.append(
        make("legend", `Routing rule ${index + 1}`),
        inputLabel("When the answer to", source),
        inputLabel("Match", operator),
        inputLabel("Value", textInput("routing_value", rule.value || "", true)),
        inputLabel("Category", textInput("routing_category", rule.category || "")),
        inputLabel("Track", textInput("routing_track", rule.track || "")),
        inputLabel("Review queue", textInput("routing_queue", rule.review_queue || ""))
      );
      const remove = make("button", "Remove rule");
      remove.type = "button";
      remove.className = "secondary";
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
    state.routingRules = [...byId("routing-rules").querySelectorAll("fieldset")].map((card) => ({
      source_key: card.elements.routing_source.value.trim(),
      operator: card.elements.routing_operator.value,
      value: card.elements.routing_value.value.trim(),
      category: card.elements.routing_category.value.trim() || null,
      track: card.elements.routing_track.value.trim() || null,
      review_queue: card.elements.routing_queue.value.trim() || null
    }));
    return state.routingRules;
  }

  function validatePublishForm(form) {
    const opens = form.elements.opens_at;
    const closes = form.elements.closes_at;
    opens.setCustomValidity(""); closes.setCustomValidity("");
    syncAvailabilityLimits(form);
    const opensAt = toEpoch(opens.value);
    const closesAt = toEpoch(closes.value);
    if (opensAt !== null && opensAt < Date.now()) {
      opens.setCustomValidity("Opening time cannot be in the past.");
    } else if (opensAt !== null && opensAt >= state.eventStartsAtMs) {
      opens.setCustomValidity("The Call for Proposals must open before the event starts.");
    }
    if (opensAt !== null && closesAt !== null && closesAt <= opensAt) {
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
    [...byId("routing-rules").querySelectorAll("fieldset")].forEach((card, index) => {
      const rule = rules[index];
      card.elements.routing_source.setCustomValidity(keys.has(rule.source_key) ? "" : "Use the key of an existing question.");
      const hasDestination = Boolean(rule.category || rule.track || rule.review_queue);
      card.elements.routing_category.setCustomValidity(hasDestination ? "" : "Enter a category, track, or review queue.");
    });
    return form.reportValidity();
  }

  function installBuilder() {
    const publish = byId("publish-form");
    publish.addEventListener("input", (event) => event.target.setCustomValidity?.(""));
    publish.elements.opens_at.addEventListener("input", () => syncAvailabilityLimits(publish));
    const submit = publish.querySelector('button[type="submit"], button:not([type])');
    const heading = make("h3", "Form questions");
    const help = make("p", "Speaker and proposal details are required. Co-speakers are optional. Add event-specific questions below.");
    const fields = make("div");
    fields.id = "form-fields";
    const add = make("button", "Add question");
    add.id = "add-field";
    add.type = "button";
    add.className = "secondary";
    add.addEventListener("click", () => {
      readFields();
      state.fields.push({ key: `question_${state.fields.length - 3}`, type: "text", label: "New question", required: false, choices: [] });
      renderFields();
    });
    publish.insertBefore(heading, submit);
    publish.insertBefore(help, submit);
    publish.insertBefore(fields, submit);
    publish.insertBefore(add, submit);
    renderFields();
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
      state.publishedForm = workspace.published_form;
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
    try {
      if (!validatePublishForm(event.currentTarget)) return;
      const values = Object.fromEntries(new FormData(event.currentTarget));
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
        redirect_to_portal: event.currentTarget.elements.redirect_to_portal.checked
      };
      const updating = Boolean(state.publishedForm);
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
      renderWorkspace();
      setStatus(updating ? "Published form updated." : "Form published successfully.");
    } catch (error) {
      setStatus(error.status === 409 ? "That public slug is already in use. Choose another." : window.SessionBuddyApi.message(error), true);
    }
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

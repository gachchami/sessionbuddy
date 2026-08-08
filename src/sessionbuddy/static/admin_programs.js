(() => {
  "use strict";
  const coreFields = [
    { key: "speaker_name", type: "text", label: "Speaker name", required: true, choices: [] },
    { key: "speaker_email", type: "email", label: "Email", required: true, choices: [] },
    { key: "proposal_title", type: "text", label: "Proposal title", required: true, choices: [] },
    { key: "proposal_abstract", type: "textarea", label: "Proposal abstract", required: true, choices: [] }
  ];
  const state = { context: null, csrf: null, program: null, fields: structuredClone(coreFields) };
  const byId = (id) => document.getElementById(id);
  const json = () => ({ "content-type": "application/json" });
  const admin = () => ({ ...json(), "x-csrf-token": state.csrf });
  const key = () => `${crypto.randomUUID()}-${crypto.randomUUID()}`;

  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = {
      schema_version: 1,
      page_template: "/admin/programs",
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
    const response = await fetch(path, options);
    recordTelemetry(started, response);
    const body = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const error = new Error(body?.error?.message || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return body;
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

  function renderFields() {
    const list = byId("form-fields");
    list.replaceChildren();
    state.fields.forEach((field, index) => {
      const core = index < coreFields.length;
      const card = make("fieldset");
      card.dataset.index = String(index);
      const legend = make("legend", core ? `Required field: ${field.key}` : `Custom field ${index - 3}`);
      const keyInput = textInput("field_key", field.key, true);
      keyInput.pattern = "[a-z][a-z0-9_]*";
      keyInput.readOnly = core;
      const type = document.createElement("select");
      type.name = "field_type";
      ["text", "textarea", "email", "url", "select"].forEach((value) => type.add(new Option(value, value)));
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
      card.append(
        legend,
        inputLabel("Field key", keyInput),
        inputLabel("Label", textInput("field_label", field.label, true)),
        inputLabel("Type", type),
        inputLabel("Required", required),
        inputLabel("Select choices (comma separated)", choices)
      );
      if (!core) {
        card.append(
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
        card.append(remove);
      }
      list.append(card);
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
        help_text: "",
        choices: type === "select" ? choices : []
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

  function installBuilder() {
    const publish = byId("publish-form");
    const submit = publish.querySelector('button[type="submit"], button:not([type])');
    const heading = make("h3", "Form questions");
    const help = make("p", "The four identity/proposal fields are required. Add event-specific questions and optional conditions below.");
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
  }

  async function restoreSession() {
    try {
      const session = await api("/api/v1/auth/session");
      const requestedEvent = new URLSearchParams(location.search).get("event_id");
      const eventId = requestedEvent || session.event_id;
      if (!session.organization_id || !eventId) throw new Error("This account has no active event.");
      state.context = { organization_id: session.organization_id, event_id: eventId };
      state.csrf = session.csrf_token;
      byId("program-form").querySelector("button").disabled = false;
      byId("logout").disabled = false;
      byId("manage-access").href = `/admin/events/${encodeURIComponent(eventId)}/access`;
      byId("manage-access").hidden = false;
      setStatus("Signed in as organization administrator.");
    } catch (error) {
      if (error.status === 401) {
        location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname + location.search)}`);
        return;
      }
      setStatus(error.message, true);
    }
  }

  byId("sign-in").addEventListener("click", restoreSession);
  byId("program-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      state.program = await api("/api/v1/admin/programs", {
        method: "POST",
        headers: { ...admin(), "idempotency-key": key() },
        body: JSON.stringify({ ...state.context, ...values })
      });
      const slug = byId("publish-form").elements.slug;
      if (slug.value === "ai-engineer-summit") slug.value = `ai-engineer-summit-${state.program.id.slice(0, 8)}`;
      byId("program-result").textContent = `${state.program.name} · ${state.program.status}`;
      byId("publish-form").querySelector('button[type="submit"], button:not([type])').disabled = false;
      setStatus("Program created. Review and publish its public form.");
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  byId("publish-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const values = Object.fromEntries(new FormData(event.currentTarget));
      const schema = readFields();
      const form = await api(`/api/v1/admin/programs/${state.program.id}/forms/publish`, {
        method: "POST",
        headers: { ...admin(), "idempotency-key": key() },
        body: JSON.stringify({ slug: values.slug, welcome_text: values.welcome_text, ...schema })
      });
      const publicUrl = `${location.origin}/cfp/${form.slug}`;
      const wrap = byId("publish-result");
      wrap.replaceChildren();
      const link = make("a", publicUrl);
      link.href = publicUrl;
      const submissions = make("a", "Review submissions");
      submissions.href = `/admin/programs/${state.program.id}/submissions`;
      submissions.className = "button secondary";
      wrap.className = "public-link";
      wrap.append("Public form is live:", link, submissions);
      setStatus("Form published successfully.");
    } catch (error) {
      setStatus(error.status === 409 ? "That public slug is already in use. Choose another." : error.message, true);
    }
  });

  byId("logout").addEventListener("click", async () => {
    try {
      await api("/api/v1/session/logout", { method: "POST", headers: admin(), body: "{}" });
      state.context = null;
      state.csrf = null;
      state.program = null;
      byId("program-form").querySelector("button").disabled = true;
      byId("publish-form").querySelector('button[type="submit"], button:not([type])').disabled = true;
      byId("logout").disabled = true;
      setStatus("Signed out. The server-side session is revoked.");
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  installBuilder();
  restoreSession();
})();

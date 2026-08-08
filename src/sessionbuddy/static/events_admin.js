(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const state = { csrf: "", organizationId: "", organizations: new Map(), events: new Map() };

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const body = response.status === 204 ? null : await response.json();
    if (!response.ok) {
      const error = new Error(body?.error?.message || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function setStatus(message, error = false) {
    const status = byId("status");
    status.textContent = message;
    status.classList.toggle("error", error);
    if (error) status.focus();
  }

  function button(label, handler) {
    const control = document.createElement("button");
    control.type = "button";
    control.className = "secondary";
    control.textContent = label;
    control.addEventListener("click", handler);
    return control;
  }

  function link(label, href) {
    const control = document.createElement("a");
    control.textContent = label;
    control.href = href;
    control.className = "button secondary";
    return control;
  }

  function localDateTime(timestamp) {
    const date = new Date(timestamp);
    const local = new Date(timestamp - date.getTimezoneOffset() * 60_000);
    return local.toISOString().slice(0, 16);
  }

  function resetEventForm() {
    const form = byId("event-form");
    form.reset();
    form.elements.event_id.value = "";
    form.elements.version.value = "";
    form.elements.time_zone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    form.elements.delivery_mode.value = "";
    byId("event-form-heading").textContent = "Create an event";
    byId("save-event").textContent = "Create event";
    byId("event-status-label").hidden = true;
    byId("cancel-event-edit").hidden = true;
  }

  function editEvent(event) {
    const form = byId("event-form");
    form.elements.event_id.value = event.id;
    form.elements.version.value = String(event.version);
    form.elements.name.value = event.name;
    form.elements.starts_at.value = localDateTime(event.starts_at_ms);
    form.elements.ends_at.value = localDateTime(event.ends_at_ms);
    form.elements.time_zone.value = event.time_zone;
    form.elements.delivery_mode.value = event.delivery_mode;
    form.elements.location.value = event.location || "";
    form.elements.description.value = event.description || "";
    form.elements.status.value = event.status;
    byId("event-form-heading").textContent = `Edit ${event.name}`;
    byId("save-event").textContent = "Update event";
    byId("event-status-label").hidden = false;
    byId("cancel-event-edit").hidden = false;
    form.scrollIntoView({ behavior: "smooth", block: "start" });
    form.elements.name.focus();
  }

  function eventItem(event) {
    const item = document.createElement("li");
    const heading = document.createElement("strong");
    heading.textContent = event.name;
    const details = document.createElement("span");
    details.textContent = ` · ${event.status} · ${new Date(event.starts_at_ms).toLocaleString()}`;
    item.append(
      heading,
      details,
      " ",
      link("Programs", `/admin/programs?event_id=${encodeURIComponent(event.id)}`),
      " ",
      link("People", `/admin/events/${encodeURIComponent(event.id)}/access`),
      " ",
      link("Onboarding", `/admin/events/${encodeURIComponent(event.id)}/onboarding`),
      " ",
      link("Agenda", `/admin/events/${encodeURIComponent(event.id)}/agenda`),
      " ",
      link("Public schedule", `/events/${encodeURIComponent(event.id)}/schedule`),
      " ",
      button("Edit", () => editEvent(event))
    );
    return item;
  }

  function showOrganization() {
    const organization = state.organizations.get(state.organizationId);
    const form = byId("organization-form");
    form.elements.name.value = organization?.name || "";
    form.elements.version.value = organization ? String(organization.version) : "";
  }

  async function loadEvents(organizationId) {
    state.organizationId = organizationId;
    showOrganization();
    resetEventForm();
    const result = await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/events`);
    state.events = new Map(result.data.map((event) => [event.id, event]));
    const list = byId("event-list");
    list.replaceChildren();
    for (const event of result.data) list.append(eventItem(event));
    if (!result.data.length) {
      const empty = document.createElement("li");
      empty.textContent = "No events yet. Use the clearly labeled form above to create the first one.";
      list.append(empty);
    }
  }

  async function initialize() {
    const session = await api("/api/v1/auth/session");
    state.csrf = session.csrf_token;
    const result = await api("/api/v1/admin/organizations");
    state.organizations = new Map(result.data.map((organization) => [organization.id, organization]));
    const select = byId("organization");
    select.replaceChildren();
    for (const organization of result.data) select.add(new Option(organization.name, organization.id));
    if (!result.data.length) throw new Error("This account does not administer an organization.");
    if (session.organization_id && state.organizations.has(session.organization_id)) {
      select.value = session.organization_id;
    }
    await loadEvents(select.value);
    setStatus("Events are up to date.");
  }

  byId("organization").addEventListener("change", (event) => {
    loadEvents(event.currentTarget.value)
      .then(() => setStatus("Organization selected."))
      .catch((error) => setStatus(error.message, true));
  });

  byId("organization-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const organization = await api(
        `/api/v1/admin/organizations/${encodeURIComponent(values.organization_id)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
          body: JSON.stringify({ name: values.name, version: Number(values.version) })
        }
      );
      state.organizations.set(organization.id, organization);
      byId("organization").selectedOptions[0].textContent = organization.name;
      showOrganization();
      setStatus("Organization updated.");
    } catch (error) {
      setStatus(error.status === 409 ? "The organization changed elsewhere. Reload and try again." : error.message, true);
    }
  });

  byId("event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const eventId = values.event_id;
    const body = {
      name: values.name,
      starts_at_ms: new Date(values.starts_at).getTime(),
      ends_at_ms: new Date(values.ends_at).getTime(),
      time_zone: values.time_zone,
      delivery_mode: values.delivery_mode,
      location: values.location || null,
      description: values.description || null
    };
    if (eventId) {
      body.version = Number(values.version);
      body.status = values.status;
    }
    try {
      await api(
        eventId
          ? `/api/v1/admin/events/${encodeURIComponent(eventId)}`
          : `/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/events`,
        {
          method: eventId ? "PATCH" : "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
          body: JSON.stringify(body)
        }
      );
      setStatus(eventId ? "Event updated." : "Event created.");
      await loadEvents(state.organizationId);
    } catch (error) {
      setStatus(error.status === 409 ? "The event changed elsewhere. Reload and try again." : error.message, true);
    }
  });

  byId("cancel-event-edit").addEventListener("click", resetEventForm);

  initialize().catch((error) => {
    if (error.status === 401) {
      location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`);
    } else {
      setStatus(error.message, true);
    }
  });
})();

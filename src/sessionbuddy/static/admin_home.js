(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const state = { session: null, organizations: [], events: [] };
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }

  function cardLink(title, href, eyebrow, summary, badge) {
    const card = make("article", undefined, "entity-card");
    const top = make("div", undefined, "entity-card__top");
    top.append(make("span", eyebrow, "eyebrow"));
    if (badge) top.append(make("span", badge, "badge"));
    const heading = make("h3");
    const anchor = make("a", title);
    anchor.href = href;
    heading.append(anchor);
    card.append(top, heading, make("p", summary, "result"));
    const open = make("a", "Open →", "entity-card__action");
    open.href = href;
    open.setAttribute("aria-label", `Open ${title}`);
    card.append(open);
    return card;
  }

  function formatDate(event) {
    try {
      return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: event.time_zone })
        .format(new Date(event.starts_at_ms));
    } catch (_) { return "Date unavailable"; }
  }

  function localInputValue(date) {
    const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
    return local.toISOString().slice(0, 16);
  }

  function resetEventForm() {
    const form = byId("event-form");
    form.reset();
    byId("event-form-status").hidden = true;
    byId("event-form-status").className = "status status-compact";
    const start = new Date();
    start.setDate(start.getDate() + 1);
    start.setHours(9, 0, 0, 0);
    const end = new Date(start);
    end.setHours(17, 0, 0, 0);
    form.elements.starts_at.value = localInputValue(start);
    form.elements.ends_at.value = localInputValue(end);
    form.elements.starts_at.min = localInputValue(new Date());
    form.elements.ends_at.min = form.elements.starts_at.value;
    form.elements.time_zone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    form.elements.organization_id.replaceChildren(
      ...state.organizations.map((organization) => new Option(organization.name, organization.id))
    );
  }

  function openEventDialog() {
    resetEventForm();
    byId("event-dialog").showModal();
    byId("event-form").elements.name.focus();
  }

  function closeEventDialog() {
    if (byId("event-dialog").open) byId("event-dialog").close();
    resetEventForm();
  }

  async function loadDashboard() {
    const organizations = (await api("/api/v1/admin/organizations")).data;
    if (!organizations.length) throw new Error("This account does not manage an organization or event.");
    const manageableIds = new Set(
      (state.session.organization_access || [])
        .filter((access) => access.roles.includes("organization_admin"))
        .map((access) => access.organization_id)
    );
    state.organizations = organizations.filter((organization) => manageableIds.has(organization.id));
    byId("new-event").hidden = state.organizations.length === 0;

    const eventGroups = await Promise.all(organizations.map(async (organization) => ({
      organization,
      events: (await api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/events`)).data
    })));
    const events = eventGroups.flatMap(({ organization, events: items }) =>
      items.map((event) => ({ ...event, organization_name: organization.name }))
    );
    state.events = events;

    byId("metric-workspace").textContent = organizations.length === 1
      ? organizations[0].name
      : `${organizations.length} organizations`;

    const eventList = byId("event-list");
    if (events.length) {
      eventList.replaceChildren(...events.map((event) => cardLink(
        event.name,
        `/admin/events/${encodeURIComponent(event.id)}`,
        event.organization_name,
        `${formatDate(event)} · ${event.time_zone}`,
        event.status
      )));
    } else {
      const empty = make("p", "No events yet. Create the first event to begin.", "empty");
      if (state.organizations.length) {
        const create = make("button", "Create a new event");
        create.type = "button";
        create.addEventListener("click", openEventDialog);
        empty.append(document.createElement("br"), create);
      }
      eventList.replaceChildren(empty);
    }

    const speakerGroups = await Promise.all(events.map(async (event) => ({
      event,
      speakers: (await api(`/api/v1/admin/events/${encodeURIComponent(event.id)}/speaker-targets`)).data
    })));
    const speakers = speakerGroups.flatMap(({ event, speakers: items }) =>
      items.map((speaker) => ({ ...speaker, event }))
    );
    byId("metric-events").textContent = String(events.length);
    byId("metric-speakers").textContent = String(speakers.length);
    const speakerList = byId("speaker-list");
    if (speakers.length) {
      speakerList.replaceChildren(...speakers.slice(0, 6).map((speaker) => cardLink(
        speaker.display_name,
        `/admin/events/${encodeURIComponent(speaker.event.id)}/speakers/${encodeURIComponent(speaker.event_speaker_id)}`,
        speaker.event.name,
        speaker.proposal_title,
        speaker.selection_status
      )));
    } else {
      speakerList.replaceChildren(make("p", "Speakers appear here after proposals are submitted.", "empty"));
    }
    setStatus(`${organizations.length} organization${organizations.length === 1 ? "" : "s"}, ${events.length} event${events.length === 1 ? "" : "s"}, and ${speakers.length} speaker${speakers.length === 1 ? "" : "s"}.`);
  }

  byId("new-event").addEventListener("click", openEventDialog);
  byId("close-event-dialog").addEventListener("click", closeEventDialog);
  byId("cancel-event").addEventListener("click", closeEventDialog);
  byId("event-dialog").addEventListener("cancel", () => resetEventForm());
  byId("event-form").elements.starts_at.addEventListener("change", (event) => {
    event.currentTarget.form.elements.ends_at.min = event.currentTarget.value;
  });
  byId("event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    const startsAt = new Date(values.starts_at).getTime();
    const endsAt = new Date(values.ends_at).getTime();
    form.elements.starts_at.setCustomValidity(startsAt > Date.now() ? "" : "The event must start in the future.");
    form.elements.ends_at.setCustomValidity(endsAt > startsAt ? "" : "The event must end after it starts.");
    if (!form.reportValidity()) return;
    const submit = byId("create-event");
    submit.disabled = true;
    try {
      const created = await api(`/api/v1/admin/organizations/${encodeURIComponent(values.organization_id)}/events`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.session.csrf_token },
        body: JSON.stringify({
          name: values.name.trim(), starts_at_ms: startsAt, ends_at_ms: endsAt,
          time_zone: values.time_zone, delivery_mode: values.delivery_mode,
          location: values.location.trim(), description: values.description.trim()
        })
      });
      closeEventDialog();
      await loadDashboard();
      setStatus(`${created.name} was created. Open it from the event list when you are ready.`);
    } catch (error) {
      const status = byId("event-form-status");
      status.textContent = window.SessionBuddyApi.message(error, "The event could not be created. Try again.");
      status.classList.add("error");
      status.hidden = false;
      status.focus();
    } finally { submit.disabled = false; }
  });

  async function initialize() {
    state.session = await api("/api/v1/auth/session");
    await loadDashboard();
  }

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true);
  });
})();

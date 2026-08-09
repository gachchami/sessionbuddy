(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
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

  async function initialize() {
    await api("/api/v1/auth/session");
    const organizations = (await api("/api/v1/admin/organizations")).data;
    if (!organizations.length) throw new Error("This account does not manage an organization or event.");

    const eventGroups = await Promise.all(organizations.map(async (organization) => ({
      organization,
      events: (await api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/events`)).data
    })));
    const events = eventGroups.flatMap(({ organization, events: items }) =>
      items.map((event) => ({ ...event, organization_name: organization.name }))
    );

    const organizationList = byId("organization-list");
    organizationList.replaceChildren(...eventGroups.map(({ organization, events: items }) =>
      cardLink(
        organization.name,
        `/admin/events?organization_id=${encodeURIComponent(organization.id)}`,
        "Organization",
        `${items.length} event${items.length === 1 ? "" : "s"}`,
        organization.status
      )
    ));
    byId("organization-count").textContent = String(organizations.length);
    byId("metric-organizations").textContent = String(organizations.length);

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
      const create = make("a", "Create event", "button");
      create.href = "/admin/events#event-form";
      empty.append(document.createElement("br"), create);
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

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true);
  });
})();

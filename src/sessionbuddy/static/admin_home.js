(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const state = { session: null, organizations: [] };
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
    const card = make("article", undefined, "entity-card organizer-card organizer-speaker-card");
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

  function formatEventDateTime(event) {
    try {
      const formatter = new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: event.time_zone
      });
      const start = new Date(event.starts_at_ms);
      const end = new Date(event.ends_at_ms);
      return typeof formatter.formatRange === "function"
        ? formatter.formatRange(start, end)
        : `${formatter.format(start)} – ${formatter.format(end)}`;
    } catch (_) { return "Date and time unavailable"; }
  }

  function deliveryModeLabel(mode) {
    return { in_person: "In person", virtual: "Virtual", hybrid: "Hybrid" }[mode]
      || String(mode || "Event").replaceAll("_", " ");
  }

  function eventInitials(name) {
    return String(name || "Event")
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((word) => word[0])
      .join("")
      .toUpperCase();
  }

  function eventCard(event) {
    const href = `/admin/events/${encodeURIComponent(event.id)}`;
    const card = make("article", undefined, "event-visual-card organizer-card organizer-event-card");
    if (/^#[0-9a-f]{6}$/i.test(event.accent_color || "")) {
      card.style.setProperty("--event-accent", event.accent_color);
    }

    const media = make("div", undefined, "event-visual-card__media");
    const placeholder = make("span", eventInitials(event.name), "event-visual-card__placeholder");
    placeholder.setAttribute("aria-hidden", "true");
    media.append(placeholder);
    if (event.cover_image_url) {
      const cover = make("img", undefined, "event-visual-card__cover");
      cover.src = event.cover_image_url;
      cover.alt = "";
      cover.loading = "lazy";
      cover.decoding = "async";
      cover.addEventListener("error", () => cover.remove(), { once: true });
      media.append(cover);
    }
    if (event.logo_url) {
      const logo = make("img", undefined, "event-visual-card__logo");
      logo.src = event.logo_url;
      logo.alt = "";
      logo.loading = "lazy";
      logo.decoding = "async";
      logo.addEventListener("error", () => logo.remove(), { once: true });
      media.append(logo);
    }

    const body = make("div", undefined, "event-visual-card__body");
    const top = make("div", undefined, "event-visual-card__top");
    top.append(make("span", event.organization_name, "eyebrow"));
    top.append(make("span", event.status, "badge"));
    const heading = make("h3");
    const title = make("a", event.name);
    title.href = href;
    heading.append(title);

    const details = make("div", undefined, "event-visual-card__details");
    details.append(
      make("p", formatEventDateTime(event), "event-visual-card__date"),
      make("p", [event.location, deliveryModeLabel(event.delivery_mode)].filter(Boolean).join(" · "))
    );
    const zone = make("span", event.time_zone, "event-visual-card__zone");
    details.firstElementChild.append(" ", zone);

    const open = make("a", "Open event →", "event-visual-card__action");
    open.href = href;
    open.setAttribute("aria-label", `Open ${event.name}`);
    body.append(top, heading, details, open);
    card.append(media, body);
    return card;
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

    // The dashboard is an overview: only the most recent few events per
    // organization are fetched, and totals come from the aggregate metrics
    // endpoint rather than from paging the whole history.
    const [eventGroups, metricGroups] = await Promise.all([
      Promise.all(organizations.map(async (organization) => ({
        organization,
        page: await api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/events?limit=12`)
      }))),
      Promise.all(organizations.map((organization) =>
        api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/metrics`)
      ))
    ]);
    const totals = metricGroups.reduce(
      (sum, metrics) => ({
        events: sum.events + metrics.event_count,
        speakers: sum.speakers + metrics.speaker_count
      }),
      { events: 0, speakers: 0 }
    );
    const events = eventGroups.flatMap(({ organization, page }) =>
      page.data.map((event) => ({ ...event, organization_name: organization.name }))
    );
    byId("metric-workspace").textContent = organizations.length === 1
      ? organizations[0].name
      : `${organizations.length} organizations`;

    const eventList = byId("event-list");
    if (events.length) {
      eventList.replaceChildren(...events.map(eventCard));
    } else {
      const empty = make("p", "No events yet. Create the first event to begin.", "empty");
      if (state.organizations.length) {
        const create = make("a", "Create a new event", "button");
        create.href = "/admin/events#event-form";
        empty.append(document.createElement("br"), create);
      }
      eventList.replaceChildren(empty);
    }

    // Recent speakers come straight from the aggregate endpoint — no
    // per-event fan-out at all.
    const speakers = metricGroups
      .flatMap((metrics) => metrics.recent_speakers || [])
      .filter((speaker) => speaker.person_id && speaker.proposal_title !== "No proposal");
    byId("metric-events").textContent = String(totals.events);
    byId("metric-speakers").textContent = String(totals.speakers);
    const speakerList = byId("speaker-list");
    if (speakers.length) {
      speakerList.replaceChildren(...speakers.slice(0, 6).map((speaker) => cardLink(
        speaker.display_name,
        `/speakers/${encodeURIComponent(speaker.person_id)}`,
        speaker.event_name,
        speaker.proposal_title,
        speaker.selection_status
      )));
    } else {
      speakerList.replaceChildren(make("p", "Speakers appear here after proposals are submitted.", "empty"));
    }
    setStatus(`${organizations.length} organization${organizations.length === 1 ? "" : "s"}, ${totals.events} event${totals.events === 1 ? "" : "s"}, and ${totals.speakers} speaker${totals.speakers === 1 ? "" : "s"} with proposals.`);
  }

  async function initialize() {
    state.session = await api("/api/v1/auth/session");
    const name = state.session.display_name || state.session.email?.split("@")[0] || "there";
    byId("dashboard-greeting").textContent = `Welcome back, ${name}`;
    await loadDashboard();
  }

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true);
  });
})();

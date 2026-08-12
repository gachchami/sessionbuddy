(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const state = { session: null, organizations: [], organizationId: "", events: [], nextCursor: null };
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
    byId("status").classList.toggle("organizer-home-status--ready", !message);
  }

  function formatDate(event) {
    if (!event.starts_at_ms) return "Date not set";
    const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: event.time_zone || "UTC" });
    const start = new Date(event.starts_at_ms);
    const end = new Date(event.ends_at_ms || event.starts_at_ms);
    return typeof formatter.formatRange === "function" ? formatter.formatRange(start, end) : `${formatter.format(start)} – ${formatter.format(end)}`;
  }

  function formatDeliveryMode(value) {
    return String(value || "Event").replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
  }

  function eventRow(event) {
    const link = document.createElement("a");
    link.className = "organizer-home-event-row";
    link.href = `/admin/events/${encodeURIComponent(event.id)}`;
    const identity = document.createElement("span");
    identity.className = "organizer-home-event-identity";
    const marker = document.createElement("span");
    marker.className = `organizer-home-event-marker organizer-home-event-marker--${String(event.delivery_mode || "event").replaceAll("_", "-")}`;
    marker.setAttribute("aria-hidden", "true");
    const name = document.createElement("strong");
    name.textContent = event.name;
    const identityCopy = document.createElement("span");
    identityCopy.className = "organizer-home-event-identity-copy";
    const activity = document.createElement("small");
    const proposals = Number(event.proposal_count || 0);
    const reviews = Number(event.pending_review_count || 0);
    activity.textContent = `${proposals} proposal${proposals === 1 ? "" : "s"} · ${reviews} awaiting review`;
    identityCopy.append(name, activity);
    identity.append(marker, identityCopy);
    if (event.status === "draft") {
      const status = document.createElement("span");
      status.className = "organizer-home-event-status organizer-home-event-status--draft";
      status.textContent = "Draft";
      identityCopy.firstElementChild.after(status);
    }
    const date = document.createElement("time");
    date.className = "organizer-home-event-date";
    date.textContent = formatDate(event);
    const mode = document.createElement("span");
    mode.className = "organizer-home-event-mode";
    mode.textContent = formatDeliveryMode(event.delivery_mode);
    const venue = document.createElement("span");
    venue.className = "organizer-home-event-venue";
    venue.textContent = event.location || (event.delivery_mode === "virtual" ? "Online" : "Not set");
    venue.title = venue.textContent;
    const program = document.createElement("span");
    program.className = "organizer-home-event-program";
    const cfp = document.createElement("span");
    cfp.className = `organizer-home-event-cfp organizer-home-event-cfp--${event.cfp_status || "not_started"}`;
    cfp.textContent = `CFP ${({ not_started: "not set", draft: "draft", published: "published", closed: "closed" })[event.cfp_status] || "not set"}`;
    const schedule = document.createElement("small");
    schedule.className = `organizer-home-event-schedule organizer-home-event-schedule--${event.schedule_status || "not_started"}`;
    schedule.textContent = ({
      not_started: "No schedule",
      draft: "Draft schedule",
      ready: "Schedule ready",
      published: "Schedule published",
      updates_pending: "Schedule update ready"
    })[event.schedule_status] || "No schedule";
    schedule.title = schedule.textContent;
    program.append(cfp, schedule);
    const trailing = document.createElement("span");
    trailing.className = "organizer-home-event-trailing";
    const arrow = document.createElement("span");
    arrow.className = "organizer-home-event-arrow";
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "→";
    trailing.append(arrow);
    link.append(identity, date, venue, mode, program, trailing);
    return link;
  }

  function eventRows(events) {
    const rows = document.createElement("div");
    rows.className = "organizer-home-event-group__rows";
    const header = document.createElement("div");
    header.className = "organizer-home-event-columns";
    header.setAttribute("aria-hidden", "true");
    for (const label of ["Event", "Dates", "Venue", "Format", "Program", ""]) {
      const column = document.createElement("span");
      column.textContent = label;
      header.append(column);
    }
    rows.append(header, ...events.map(eventRow));
    return rows;
  }

  function eventGroup(title, events, collapsible = false) {
    const rows = eventRows(events);
    if (collapsible) {
      const group = document.createElement("details");
      group.className = "organizer-home-event-group organizer-home-event-group--archived";
      const summary = document.createElement("summary");
      summary.textContent = `${title} (${events.length})`;
      group.append(summary, rows);
      return group;
    }
    const group = document.createElement("section");
    group.className = "organizer-home-event-group";
    const heading = document.createElement("h3");
    heading.textContent = title;
    group.append(heading, rows);
    return group;
  }

  function renderEvents() {
    const list = document.querySelector(".organizer-home-event-list");
    if (state.events.length) {
      const current = state.events.filter((event) => event.status !== "archived");
      const archived = state.events.filter((event) => event.status === "archived");
      const groups = [];
      if (current.length) groups.push(eventGroup("Current events", current));
      if (archived.length) groups.push(eventGroup("Archived events", archived, true));
      list.replaceChildren(...groups);
    }
    else {
      const empty = document.createElement("p");
      empty.id = "event-list-empty";
      empty.className = "empty";
      empty.textContent = "No events yet. Create your first event.";
      list.replaceChildren(empty);
    }
    byId("event-count").textContent = `${state.events.length}${state.nextCursor ? "+" : ""}`;
    byId("event-count").setAttribute("aria-label", `${state.events.length}${state.nextCursor ? " or more" : ""} events shown`);
    byId("load-more-events").hidden = !state.nextCursor;
  }

  async function loadEventPage(cursor = null) {
    const params = new URLSearchParams({ view: "all", order: "upcoming" });
    if (cursor) params.set("cursor", cursor);
    const result = await api(`/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/events?${params}`);
    state.events = cursor ? [...state.events, ...result.data] : result.data;
    state.events.sort((left, right) => {
      const rank = { active: 0, draft: 1, archived: 2 };
      return (rank[left.status] ?? 1) - (rank[right.status] ?? 1)
        || (left.starts_at_ms || Number.MAX_SAFE_INTEGER) - (right.starts_at_ms || Number.MAX_SAFE_INTEGER);
    });
    state.nextCursor = result.next_cursor;
    renderEvents();
  }

  async function loadDashboard() {
    const organizations = (await api("/api/v1/admin/organizations")).data;
    if (!organizations.length) throw new Error("Your organization workspace is not available yet. Please try again or contact an administrator.");
    const organization = organizations[0];
    const manageableIds = new Set(
      (state.session.organization_access || [])
        .filter((access) => (access.permissions || []).some((permission) => ["owner", "manage"].includes(permission)))
        .map((access) => access.organization_id),
    );
    state.organizations = manageableIds.has(organization.id) ? [organization] : [];
    state.organizationId = organization.id;
    byId("new-event").hidden = state.organizations.length === 0;
    byId("organization-name").textContent = organization.name;

    await loadEventPage();
    setStatus("");
  }

  byId("load-more-events").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await loadEventPage(state.nextCursor); }
    catch (error) { setStatus(window.SessionBuddyApi.message(error), true); }
    finally { button.disabled = false; }
  });

  async function initialize() { state.session = await api("/api/v1/auth/session"); await loadDashboard(); }
  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      setStatus(`We couldn’t load this workspace. ${window.SessionBuddyApi.message(error)}`, true);
    }
  });
})();

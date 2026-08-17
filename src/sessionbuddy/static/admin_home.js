(() => {
  "use strict";

  window.__sessionbuddyTelemetryDraft = { ...(window.__sessionbuddyTelemetryDraft || {}), page_template: "/admin" };

  const byId = (id) => document.getElementById(id);
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  const VALID_VIEWS = new Set(["all", "active", "draft", "past"]);
  const VALID_ORDERS = new Set(["upcoming", "recent"]);
  const state = {
    session: null,
    organizations: [],
    organizationId: "",
    events: [],
    nextCursor: null,
    view: "all",
    query: "",
    order: "upcoming",
    eventsRequestId: 0,
    activityRequestId: 0,
    searchTimer: null,
  };

  function setStatus(message, error = false) {
    const status = byId("status");
    status.textContent = message;
    status.classList.toggle("error", error);
    status.classList.toggle("organizer-home-status--ready", !message);
  }

  function permissionsForOrganization(organizationId) {
    return (state.session.organization_access || []).find((access) => access.organization_id === organizationId)?.permissions || [];
  }

  function canManageOrganization(organizationId) {
    const shared = window.SessionBuddyAccess?.canManageOrganization;
    return shared
      ? shared(state.session, organizationId)
      : permissionsForOrganization(organizationId).some((permission) => ["owner", "manage"].includes(permission));
  }

  function canDuplicateEvent(event) {
    const shared = window.SessionBuddyAccess?.canDuplicateEvent;
    return shared ? shared(state.session, event) : canManageOrganization(event.organization_id);
  }

  function readUrlState() {
    const params = new URLSearchParams(location.search);
    const view = params.get("view");
    const order = params.get("order");
    state.view = VALID_VIEWS.has(view) ? view : "all";
    state.order = VALID_ORDERS.has(order) ? order : "upcoming";
    state.query = String(params.get("q") || "").trim().slice(0, 100);
    state.organizationId = params.get("organization_id") || "";
  }

  function syncUrl() {
    const params = new URLSearchParams();
    if (state.organizationId) params.set("organization_id", state.organizationId);
    if (state.view !== "all") params.set("view", state.view);
    if (state.query) params.set("q", state.query);
    if (state.order !== "upcoming") params.set("order", state.order);
    history.replaceState(null, "", `${location.pathname}${params.size ? `?${params}` : ""}`);
  }

  function syncControls() {
    document.querySelectorAll("[data-event-filter]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.eventFilter === state.view));
    });
    byId("event-search").value = state.query;
    byId("event-sort").value = state.order;
  }

  function formatDate(event) {
    const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: event.time_zone || "UTC" });
    const start = new Date(event.starts_at_ms);
    const end = new Date(event.ends_at_ms || event.starts_at_ms);
    return typeof formatter.formatRange === "function" && start.getTime() !== end.getTime()
      ? formatter.formatRange(start, end)
      : formatter.format(start);
  }

  function isoDate(event) {
    return new Date(event.starts_at_ms).toISOString();
  }

  function cell(headerId, label, className = "") {
    const node = document.createElement("div");
    node.setAttribute("role", "cell");
    node.setAttribute("aria-labelledby", headerId);
    node.dataset.label = label;
    if (className) node.className = className;
    return node;
  }

  function countLink(event, count, noun, suffix = "") {
    if (!count) return null;
    const link = document.createElement("a");
    link.href = `/admin/events/${encodeURIComponent(event.id)}/submissions${suffix}`;
    link.textContent = `${count} ${noun}${count === 1 ? "" : "s"}`;
    return link;
  }

  function programState(label, status, isPublic, isOffline) {
    const node = document.createElement("span");
    node.className = `organizer-home-program-state${isPublic ? " organizer-home-program-state--public" : ""}${status === "draft" || status === "ready" || status === "updates_pending" ? " organizer-home-program-state--pending" : ""}${isOffline ? " organizer-home-program-state--offline" : ""}`;
    node.textContent = `${label}${isOffline ? " · offline" : ""}`;
    return node;
  }

  function eventRow(event) {
    const row = document.createElement("div");
    row.className = "organizer-home-event-row";
    row.setAttribute("role", "row");
    row.dataset.eventId = event.id;

    const identity = cell("event-column-event", "Event", "organizer-home-event-identity");
    const nameLine = document.createElement("div");
    nameLine.className = "organizer-home-event-name-line";
    const name = document.createElement("a");
    name.className = "organizer-home-event-name";
    name.href = `/admin/events/${encodeURIComponent(event.id)}`;
    name.textContent = event.name;
    nameLine.append(name);
    if (event.status !== "active") {
      const status = document.createElement("span");
      status.className = `organizer-home-event-status organizer-home-event-status--${event.status}`;
      status.textContent = event.status === "draft" ? "Draft" : "Archived";
      nameLine.append(status);
    }
    identity.append(nameLine);
    const attention = document.createElement("div");
    attention.className = "organizer-home-event-attention";
    const proposals = countLink(event, Number(event.proposal_count || 0), "proposal");
    const reviews = countLink(event, Number(event.pending_review_count || 0), "awaiting review", "#rounds-title");
    if (proposals) attention.append(proposals);
    if (reviews) attention.append(reviews);
    if (attention.childElementCount) identity.append(attention);

    const date = cell("event-column-date", "Date", "organizer-home-event-date");
    const time = document.createElement("time");
    time.dateTime = isoDate(event);
    time.textContent = formatDate(event);
    date.append(time);

    const where = cell("event-column-where", "Where", "organizer-home-event-where");
    where.textContent = event.location || (event.delivery_mode === "virtual" ? "Online" : "Not set");

    const program = cell("event-column-program", "Program", "organizer-home-event-program");
    const inactive = event.status !== "active";
    const cfpLabels = { not_started: "CFP not set", draft: "CFP draft", published: "CFP published", closed: "CFP closed" };
    const scheduleLabels = { not_started: "No schedule", draft: "Schedule draft", ready: "Schedule ready", published: "Schedule published", updates_pending: "Schedule update ready" };
    const cfpStatus = event.cfp_status || "not_started";
    const scheduleStatus = event.schedule_status || "not_started";
    program.append(
      programState(cfpLabels[cfpStatus] || "CFP not set", cfpStatus, ["published", "closed"].includes(cfpStatus) && !inactive, inactive && ["published", "closed"].includes(cfpStatus)),
      programState(scheduleLabels[scheduleStatus] || "No schedule", scheduleStatus, ["published", "updates_pending"].includes(scheduleStatus) && !inactive, inactive && ["published", "updates_pending"].includes(scheduleStatus)),
    );

    const actions = cell("event-column-actions", "Actions", "organizer-home-event-actions");
    const settings = document.createElement("a");
    settings.className = "organizer-home-event-action";
    settings.href = `/admin/events/${encodeURIComponent(event.id)}/settings`;
    settings.textContent = "Settings";
    actions.append(settings);
    if (canDuplicateEvent(event)) {
      const duplicate = document.createElement("a");
      duplicate.className = "organizer-home-event-action";
      duplicate.href = `/admin/events/new?source=${encodeURIComponent(event.id)}`;
      duplicate.textContent = "Clone";
      actions.append(duplicate);
    }
    row.append(identity, date, where, program, actions);
    return row;
  }

  function renderEvents() {
    const table = byId("event-table");
    const list = byId("event-list");
    const empty = byId("event-list-empty");
    list.replaceChildren(...state.events.map(eventRow));
    table.hidden = state.events.length === 0;
    table.setAttribute("aria-busy", "false");
    empty.hidden = state.events.length > 0;
    if (!state.events.length) {
      empty.textContent = state.query
        ? `No events match “${state.query}”.`
        : state.view === "all" ? "No events yet. Create your first event." : `No ${state.view === "draft" ? "draft" : state.view} events.`;
    }
    byId("event-count").textContent = `${state.events.length}${state.nextCursor ? "+" : ""}`;
    byId("event-count").setAttribute("aria-label", `${state.events.length}${state.nextCursor ? " or more" : ""} events shown`);
    byId("load-more-events").hidden = !state.nextCursor;
  }

  async function loadEventPage(cursor = null, announce = false) {
    const requestId = cursor ? state.eventsRequestId : ++state.eventsRequestId;
    const params = new URLSearchParams({ view: state.view, order: state.order });
    if (state.query) params.set("q", state.query);
    if (cursor) params.set("cursor", cursor);
    if (!cursor) {
      byId("event-table").setAttribute("aria-busy", "true");
      byId("event-list-empty").hidden = true;
    }
    const result = await api(`/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/events?${params}`);
    if (requestId !== state.eventsRequestId) return false;
    state.events = cursor ? [...state.events, ...result.data] : result.data;
    state.nextCursor = result.next_cursor;
    renderEvents();
    if (announce) setStatus(`${state.events.length}${state.nextCursor ? " or more" : ""} events for ${byId("organization-name").textContent}.`);
    else setStatus("");
    return true;
  }

  async function refreshEvents(announce = false) {
    try {
      return await loadEventPage(null, announce);
    } catch (error) {
      byId("event-table").setAttribute("aria-busy", "false");
      if (!state.events.length) {
        byId("event-table").hidden = true;
        byId("event-list-empty").hidden = false;
        byId("event-list-empty").textContent = "Events are temporarily unavailable. Try again in a moment.";
      }
      setStatus(window.SessionBuddyApi.messageWithReference("Events could not be refreshed.", error), true);
      return false;
    }
  }

  function recentChangesSection() {
    const aside = document.createElement("aside");
    aside.id = "recent-changes";
    aside.className = "organizer-home-changes";
    aside.setAttribute("aria-labelledby", "recent-changes-title");
    aside.tabIndex = -1;
    aside.innerHTML = `<div class="organizer-home-changes__heading"><h2 id="recent-changes-title">Recent changes</h2><a href="/admin/organization#organization-activity">See all changes</a></div><p class="organizer-home-changes-state" role="status">Loading recent changes…</p>`;
    return aside;
  }

  function syncRecentChangesSkipLink(visible) {
    let link = byId("skip-recent-changes");
    if (!visible) {
      link?.remove();
      return;
    }
    if (link) return;
    link = document.createElement("a");
    link.id = "skip-recent-changes";
    link.className = "skip-link";
    link.href = "#recent-changes";
    link.textContent = "Skip to recent changes";
    document.querySelector(".skip-link").after(link);
  }

  async function loadRecentChanges() {
    const slot = byId("recent-changes-slot");
    const canManage = canManageOrganization(state.organizationId);
    syncRecentChangesSkipLink(canManage);
    if (!canManage) {
      slot.replaceChildren();
      return;
    }
    const requestId = ++state.activityRequestId;
    const aside = recentChangesSection();
    slot.replaceChildren(aside);
    try {
      const result = await api(`/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/activities`);
      if (requestId !== state.activityRequestId) return;
      const activities = result.data.filter((activity) => activity.operation !== "read").slice(0, 8);
      if (!activities.length) {
        aside.querySelector(".organizer-home-changes-state").textContent = "No recent changes.";
        return;
      }
      const list = document.createElement("ol");
      list.className = "organizer-home-changes-list";
      for (const activity of activities) {
        const item = document.createElement("li");
        item.className = "organizer-home-change";
        const sentence = document.createElement("strong");
        sentence.textContent = window.SessionBuddyActivityFormat.sentence(activity);
        const time = document.createElement("time");
        const date = new Date(activity.occurred_at_ms);
        time.dateTime = date.toISOString();
        time.title = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
        time.textContent = window.SessionBuddyActivityFormat.relativeTime(activity.occurred_at_ms);
        item.append(sentence, time);
        list.append(item);
      }
      aside.querySelector(".organizer-home-changes-state").replaceWith(list);
    } catch (error) {
      if (requestId !== state.activityRequestId) return;
      const status = aside.querySelector(".organizer-home-changes-state");
      status.classList.add("error");
      status.textContent = window.SessionBuddyApi.messageWithReference("Recent changes are temporarily unavailable.", error);
    }
  }

  function selectedOrganization() {
    return state.organizations.find((organization) => organization.id === state.organizationId);
  }

  function syncOrganizationHeader() {
    const organization = selectedOrganization();
    byId("organization-name").textContent = organization?.name || state.session.organization_name || "Events";
    const manager = canManageOrganization(state.organizationId);
    byId("new-event").hidden = !manager;
    byId("new-event").href = `/admin/events/new?organization_id=${encodeURIComponent(state.organizationId)}`;
    byId("organization-settings").hidden = !manager;
    byId("organization-picker").value = state.organizationId;
  }

  async function switchOrganization(organizationId, announce = false) {
    state.organizationId = organizationId;
    state.events = [];
    state.nextCursor = null;
    syncOrganizationHeader();
    syncUrl();
    await Promise.all([refreshEvents(announce), loadRecentChanges()]);
    if (announce) byId("events-title").focus({ preventScroll: true });
  }

  async function loadDashboard() {
    const organizations = (await api("/api/v1/admin/organizations")).data;
    if (!organizations.length) throw new Error("Your organization workspace is not available yet. Please try again or contact an administrator.");
    state.organizations = organizations;
    const validIds = new Set(organizations.map((organization) => organization.id));
    if (!validIds.has(state.organizationId)) {
      state.organizationId = validIds.has(state.session.organization_id) ? state.session.organization_id : organizations[0].id;
    }
    const picker = byId("organization-picker");
    picker.replaceChildren(...organizations.map((organization) => {
      const option = document.createElement("option");
      option.value = organization.id;
      option.textContent = organization.name;
      return option;
    }));
    byId("organization-picker-label").hidden = organizations.length < 2;
    syncControls();
    await switchOrganization(state.organizationId);
  }

  document.querySelectorAll("[data-event-filter]").forEach((button) => button.addEventListener("click", async () => {
    state.view = button.dataset.eventFilter;
    syncControls();
    syncUrl();
    await refreshEvents();
  }));

  byId("event-sort").addEventListener("change", async (event) => {
    state.order = event.currentTarget.value;
    syncUrl();
    await refreshEvents();
  });

  byId("event-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    clearTimeout(state.searchTimer);
    state.query = byId("event-search").value.trim().slice(0, 100);
    syncUrl();
    await refreshEvents();
  });

  byId("event-search").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => byId("event-search-form").requestSubmit(), 300);
  });

  byId("organization-picker").addEventListener("change", (event) => switchOrganization(event.currentTarget.value, true));

  byId("load-more-events").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "Loading…";
    try {
      await loadEventPage(state.nextCursor);
    } catch (error) {
      if (window.SessionBuddyApi.isStaleCursor(error)) {
        state.nextCursor = null;
        await loadEventPage();
        setStatus(window.SessionBuddyApi.messageWithReference("The event list changed. Showing the latest events from the beginning.", error));
      } else {
        setStatus(window.SessionBuddyApi.messageWithReference("More events could not be loaded.", error), true);
      }
    } finally {
      button.disabled = false;
      button.textContent = "Load more";
    }
  });

  async function initialize() {
    readUrlState();
    state.session = await api("/api/v1/auth/session");
    await loadDashboard();
    if (location.hash === "#event-form") {
      location.replace(`/admin/events/new?organization_id=${encodeURIComponent(state.organizationId)}`);
    }
  }

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      byId("event-table").setAttribute("aria-busy", "false");
      byId("event-table").hidden = true;
      byId("event-list-empty").hidden = false;
      byId("event-list-empty").textContent = "Events are temporarily unavailable. Reload the page to try again.";
      setStatus(window.SessionBuddyApi.messageWithReference("We couldn’t load this workspace.", error), true);
    }
  });
})();

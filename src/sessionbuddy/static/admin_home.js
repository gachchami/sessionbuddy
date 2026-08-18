(() => {
  "use strict";

  window.__sessionbuddyTelemetryDraft = { ...(window.__sessionbuddyTelemetryDraft || {}), page_template: "/admin" };

  const byId = (id) => document.getElementById(id);
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  const compactCreateLabel = matchMedia("(max-width: 32rem)");
  const VALID_VIEWS = new Set(["all", "active", "draft", "past"]);
  const VALID_ORDERS = new Set(["upcoming", "recent"]);
  const state = {
    session: null,
    organizations: [],
    organizationMetrics: new Map(),
    organizationQuery: "",
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
    const startValue = event.status === "draft" ? event.draft_starts_at_ms : event.starts_at_ms;
    const endValue = event.status === "draft" ? event.draft_ends_at_ms : event.ends_at_ms;
    if (startValue == null) return "Dates not set";
    const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: event.time_zone || "UTC" });
    const start = new Date(startValue);
    const end = new Date(endValue ?? startValue);
    return typeof formatter.formatRange === "function" && start.getTime() !== end.getTime()
      ? formatter.formatRange(start, end)
      : formatter.format(start);
  }

  function isoDate(event) {
    const startValue = event.status === "draft" ? event.draft_starts_at_ms : event.starts_at_ms;
    return startValue == null ? "" : new Date(startValue).toISOString();
  }

  function cell(headerId, label, className = "") {
    const node = document.createElement("div");
    node.setAttribute("role", "cell");
    node.setAttribute("aria-labelledby", headerId);
    node.dataset.label = label;
    if (className) node.className = className;
    return node;
  }

  function readinessLabel(label, tone = "neutral", href = "", accessibleLabel = "") {
    const node = document.createElement(href ? "a" : "span");
    node.className = `organizer-home-readiness organizer-home-readiness--${tone}${href ? " organizer-home-readiness--link" : ""}`;
    if (href) node.href = href;
    if (accessibleLabel) node.setAttribute("aria-label", accessibleLabel);
    node.textContent = label;
    return node;
  }

  function eventLink(label, href) {
    const link = document.createElement("a");
    link.className = "organizer-home-event-link";
    link.href = href;
    link.textContent = label;
    return link;
  }

  function eventStatusLabel(event) {
    return event.status === "active" ? "Active" : event.status === "draft" ? "Draft" : "Archived";
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
    identity.append(nameLine);
    const labels = document.createElement("div");
    labels.className = "organizer-home-event-labels";
    const eventId = encodeURIComponent(event.id);

    const statusCell = cell("event-column-status", "Location", "organizer-home-event-state");
    statusCell.textContent = event.location || (event.delivery_mode === "virtual" ? "Online" : "Not set");
    const status = document.createElement("span");
    status.className = `organizer-home-event-status organizer-home-event-status--${event.status}`;
    status.textContent = eventStatusLabel(event);
    labels.append(status);

    const date = cell("event-column-date", "Dates", "organizer-home-event-date");
    const time = document.createElement("time");
    const machineDate = isoDate(event);
    if (machineDate) time.dateTime = machineDate;
    time.textContent = formatDate(event);
    date.append(time);

    const program = cell("event-column-program", "Links", "organizer-home-event-program");
    const cfpStatus = event.cfp_status || "not_started";
    const scheduleStatus = event.schedule_status || "not_started";
    const publicCfpHref = event.status === "active" && event.cfp_public_path && ["published", "closed"].includes(cfpStatus)
      ? event.cfp_public_path
      : "";
    const publicAgendaHref = event.status === "active" && ["published", "updates_pending"].includes(scheduleStatus)
      ? `/events/${eventId}/schedule`
      : "";
    if (event.status === "archived") {
      labels.append(readinessLabel("CFP archived"), readinessLabel("Agenda archived"));
    } else {
      const proposalCount = Number(event.proposal_count || 0);
      const speakerCount = Number(event.speaker_count || 0);
      const cfpReady = event.status === "active" && ["published", "closed"].includes(cfpStatus);
      const agendaReady = event.status === "active" && ["ready", "published", "updates_pending"].includes(scheduleStatus);
      const cfpLabel = cfpReady
        ? `CFP ${cfpStatus === "closed" ? "closed" : "open"} · ${proposalCount} proposal${proposalCount === 1 ? "" : "s"}`
        : "CFP not configured";
      const agendaLabel = agendaReady
        ? `Agenda ${scheduleStatus === "published" ? "live" : "ready"} · ${speakerCount} speaker${speakerCount === 1 ? "" : "s"}`
        : "Agenda not built";
      labels.append(
        readinessLabel(cfpLabel, cfpReady ? "ready" : "warning", publicCfpHref, publicCfpHref ? `${cfpLabel} — view public CFP for ${event.name}` : ""),
        readinessLabel(agendaLabel, agendaReady ? "ready" : "warning", publicAgendaHref, publicAgendaHref ? `${agendaLabel} — view public agenda for ${event.name}` : ""),
      );
    }
    identity.append(labels);
    const links = document.createElement("nav");
    links.className = "organizer-home-event-links";
    links.setAttribute("aria-label", `${event.name} management`);
    links.append(
      eventLink("Manage CFP", `/admin/events/${eventId}/cfp`),
      eventLink("Manage agenda", `/admin/events/${eventId}/agenda`),
      eventLink("Speakers", `/admin/events/${eventId}/speakers`),
      eventLink("Reviewers", `/admin/events/${eventId}/reviewers`),
    );
    program.append(links);

    const actions = cell("event-column-actions", "Actions", "organizer-home-event-actions");
    const settings = document.createElement("a");
    settings.className = "organizer-home-event-action";
    settings.href = `/admin/events/${encodeURIComponent(event.id)}/settings`;
    settings.textContent = "Manage";
    actions.append(settings);
    if (canDuplicateEvent(event)) {
      const duplicate = document.createElement("a");
      duplicate.className = "organizer-home-event-action organizer-home-event-action--clone";
      duplicate.href = `/admin/events/new?source=${encodeURIComponent(event.id)}`;
      duplicate.textContent = "Clone";
      actions.append(duplicate);
    }
    row.append(identity, statusCell, date, program, actions);
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
    const countLabel = state.events.length === 1 ? "event" : "events";
    byId("event-count").textContent = `${state.events.length}${state.nextCursor ? "+" : ""} ${countLabel}`;
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
    if (announce) setStatus(`${state.events.length}${state.nextCursor ? " or more" : ""} events for ${byId("events-title").textContent}.`);
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
    aside.innerHTML = `<div class="organizer-home-changes__heading"><h2 id="recent-changes-title">Recent changes</h2><a href="/admin/organization#organization-activity">See all</a></div><p class="organizer-home-changes-state" role="status">Loading recent changes…</p>`;
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
      const activities = result.data.slice(0, 8);
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
    const organizationName = organization?.name || state.session.organization_name || "Events";
    byId("events-title").textContent = organizationName;
    const manager = canManageOrganization(state.organizationId);
    byId("new-event").hidden = !manager;
    byId("new-event").href = `/admin/events/new?organization_id=${encodeURIComponent(state.organizationId)}`;
    byId("new-event").setAttribute("aria-label", `Create event in ${organizationName}`);
    byId("new-event").textContent = compactCreateLabel.matches ? "Create event" : `Create event in ${organizationName}`;
    byId("organization-settings").hidden = !manager;
    byId("organization-settings").setAttribute("aria-label", `Organization settings for ${organizationName}`);
    byId("organization-picker").value = state.organizationId;
    document.querySelectorAll("[data-organization-id]").forEach((button) => {
      const selected = button.dataset.organizationId === state.organizationId;
      button.classList.toggle("organizer-home-organization-option--selected", selected);
      if (selected) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
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

  function renderOrganizationList() {
    const query = state.organizationQuery.toLocaleLowerCase();
    const organizations = state.organizations.filter((organization) => organization.name.toLocaleLowerCase().includes(query));
    byId("organization-list").replaceChildren(...organizations.map((organization) => {
      const metric = state.organizationMetrics.get(organization.id);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "organizer-home-organization-option";
      button.dataset.organizationId = organization.id;
      const name = document.createElement("span");
      name.textContent = organization.name;
      const counts = document.createElement("span");
      counts.className = "organizer-home-organization-option__counts";
      if (metric?.pending_review_count) {
        const attention = document.createElement("span");
        attention.className = "organizer-home-organization-attention";
        attention.textContent = String(metric.pending_review_count);
        attention.setAttribute("aria-label", `${metric.pending_review_count} proposals awaiting review`);
        counts.append(attention);
      }
      const events = document.createElement("span");
      events.textContent = metric ? String(metric.event_count) : "–";
      events.setAttribute("aria-label", metric ? `${metric.event_count} events` : "Event count unavailable");
      counts.append(events);
      button.append(name, counts);
      return button;
    }));
    syncOrganizationHeader();
  }

  async function loadDashboard() {
    const organizations = (await api("/api/v1/admin/organizations")).data;
    if (!organizations.length) throw new Error("Your organization workspace is not available yet. Please try again or contact an administrator.");
    state.organizations = organizations;
    state.organizationMetrics = new Map(organizations.map((organization) => [organization.id, organization]));
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
    document.body.classList.add("organizer-page--multi-organization");
    // The compact picker replaces the desktop rail. Keep it populated even for
    // one organization so responsive layouts never erase organization context.
    byId("organization-picker-label").hidden = false;
    byId("organization-rail").hidden = false;
    byId("organization-count").textContent = String(organizations.length);
    const totalEvents = organizations.reduce((total, organization) => total + organization.event_count, 0);
    const totalAttention = organizations.reduce((total, organization) => total + organization.pending_review_count, 0);
    byId("workspace-summary").textContent = `${organizations.length} organization${organizations.length === 1 ? "" : "s"} · ${totalEvents} event${totalEvents === 1 ? "" : "s"} · ${totalAttention} review item${totalAttention === 1 ? "" : "s"} need attention`;
    renderOrganizationList();
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
  byId("organization-search").addEventListener("input", (event) => {
    state.organizationQuery = event.currentTarget.value.trim().slice(0, 100);
    renderOrganizationList();
  });
  byId("organization-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-organization-id]");
    if (button && button.dataset.organizationId !== state.organizationId) switchOrganization(button.dataset.organizationId, true);
  });
  compactCreateLabel.addEventListener("change", syncOrganizationHeader);
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

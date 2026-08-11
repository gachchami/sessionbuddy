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
    byId("status").classList.toggle("organizer-home-status--ready", !message);
  }

  function formatEventDate(event) {
    try {
      const options = { month: "short", day: "numeric", year: "numeric", timeZone: event.time_zone };
      const formatter = new Intl.DateTimeFormat(undefined, options);
      const start = new Date(event.starts_at_ms);
      const end = new Date(event.ends_at_ms);
      return typeof formatter.formatRange === "function" ? formatter.formatRange(start, end) : `${formatter.format(start)} – ${formatter.format(end)}`;
    } catch (_) { return "Date unavailable"; }
  }

  function eventInitials(name) {
    return String(name || "Event").trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join("").toUpperCase();
  }

  function eventRow(event) {
    const href = `/admin/events/${encodeURIComponent(event.id)}`;
    const row = make("article", undefined, "organizer-home-event-row");
    const mark = make("span", eventInitials(event.name), "organizer-home-event-row__mark");
    mark.setAttribute("aria-hidden", "true");
    const copy = make("div", undefined, "organizer-home-event-row__copy");
    const heading = make("h3");
    const title = make("a", event.name);
    title.href = href;
    heading.append(title);
    copy.append(heading, make("p", event.location || ({ virtual: "Online" }[event.delivery_mode] || "Location to be announced")));
    row.append(mark, copy, make("time", formatEventDate(event), "organizer-home-event-row__date"), make("span", event.status, `badge badge--${event.status}`));
    const open = make("a", "•••", "organizer-home-event-row__open");
    open.href = href;
    open.setAttribute("aria-label", `Open ${event.name}`);
    row.append(open);
    return row;
  }

  function activityRow(speaker) {
    const row = make("article", undefined, "organizer-home-activity-row");
    const icon = make("span", "+", "organizer-home-activity-row__icon");
    icon.setAttribute("aria-hidden", "true");
    const copy = make("div");
    const heading = make("h3", "Speaker activity");
    const details = [speaker.display_name, speaker.proposal_title].filter(Boolean).join(" · ");
    copy.append(heading, make("p", details));
    row.append(icon, copy);
    if (speaker.selection_status) row.append(make("span", speaker.selection_status, "organizer-home-activity-row__status"));
    return row;
  }

  async function loadDashboard() {
    const organizations = (await api("/api/v1/admin/organizations")).data;
    if (!organizations.length) throw new Error("Your organization workspace is not available yet. Please try again or contact an administrator.");
    const organization = organizations[0];
    const manageableIds = new Set((state.session.organization_access || []).filter((access) => access.roles.includes("organization_admin")).map((access) => access.organization_id));
    state.organizations = manageableIds.has(organization.id) ? [organization] : [];
    byId("new-event").hidden = state.organizations.length === 0;
    byId("organization-name").textContent = organization.name;

    const [eventPage, metrics] = await Promise.all([
      api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/events?view=active&order=upcoming&limit=3`),
      api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/metrics`)
    ]);
    const events = eventPage.data.filter((event) => event.status !== "archived" && Number(event.ends_at_ms) >= Date.now()).sort((left, right) => Number(left.starts_at_ms) - Number(right.starts_at_ms));
    byId("metric-events").textContent = String(metrics.event_count ?? 0);
    byId("metric-speakers").textContent = String(metrics.speaker_count ?? 0);
    byId("metric-sessions").textContent = String(metrics.session_count ?? 0);
    byId("metric-proposals").textContent = String(metrics.proposal_count ?? 0);
    const upcomingLabel = eventPage.next_cursor ? `${events.length}+ upcoming` : `${events.length} upcoming`;
    byId("metric-events-detail").textContent = upcomingLabel;
    byId("metric-speakers-detail").textContent = `${metrics.speaker_count ?? 0} with proposals`;
    byId("metric-proposals-detail").textContent = `${metrics.pending_review_count ?? 0} pending review`;

    const allEventsArrow = make("span", "→");
    allEventsArrow.setAttribute("aria-hidden", "true");
    byId("all-events-link").replaceChildren(document.createTextNode("View all events "), allEventsArrow);
    if (events.length) byId("event-list").replaceChildren(...events.map(eventRow));
    else {
      const empty = make("p", "No upcoming events.", "empty");
      if (state.organizations.length) {
        const create = make("a", "Create your first event", "button");
        create.href = "/admin/events#event-form";
        empty.append(document.createElement("br"), create);
      }
      byId("event-list").replaceChildren(empty);
    }

    const speakers = (metrics.recent_speakers || []).filter((speaker) => speaker.person_id && speaker.proposal_title !== "No proposal");
    byId("activity-list").replaceChildren(...(speakers.length ? speakers.slice(0, 4).map(activityRow) : [make("p", "No recent activity yet. Speaker and proposal updates will appear here.", "empty organizer-home-activity-empty")]));
    setStatus("");
  }

  async function initialize() { state.session = await api("/api/v1/auth/session"); await loadDashboard(); }
  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      setStatus(`We couldn’t load this workspace. ${window.SessionBuddyApi.message(error)}`, true);
      byId("event-list").replaceChildren(make("p", "Events are temporarily unavailable. Refresh to try again.", "empty"));
      byId("activity-list").replaceChildren(make("p", "Activity is temporarily unavailable.", "empty"));
    }
  });
})();

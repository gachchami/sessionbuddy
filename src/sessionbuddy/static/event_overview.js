(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }

  async function api(path) {
    const response = await fetch(path, { credentials: "same-origin" });
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(body?.error?.message || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function tool(title, description, href) {
    const card = document.createElement("article");
    card.className = "entity-card tool-card";
    const heading = document.createElement("h3");
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.textContent = title;
    heading.append(anchor);
    const summary = document.createElement("p");
    summary.className = "result";
    summary.textContent = description;
    const open = document.createElement("a");
    open.href = href;
    open.className = "entity-card__action";
    open.textContent = "Open →";
    card.append(heading, summary, open);
    return card;
  }

  function formatRange(event) {
    try {
      const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: event.time_zone });
      return `${formatter.format(new Date(event.starts_at_ms))} – ${formatter.format(new Date(event.ends_at_ms))}`;
    } catch (_) { return "Event dates unavailable"; }
  }

  async function initialize() {
    if (!eventId) throw new Error("This event link is invalid.");
    await api("/api/v1/auth/session");
    const organizations = (await api("/api/v1/admin/organizations")).data;
    let selected = null;
    let organization = null;
    for (const item of organizations) {
      const events = (await api(`/api/v1/admin/organizations/${encodeURIComponent(item.id)}/events`)).data;
      selected = events.find((event) => event.id === eventId) || null;
      if (selected) { organization = item; break; }
    }
    if (!selected || !organization) throw new Error("This event is not available to your account.");
    const speakers = (await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-targets`)).data;
    document.title = `${selected.name} · SessionBuddy`;
    byId("organization-name").textContent = organization.name;
    byId("event-name").textContent = selected.name;
    byId("event-summary").textContent = `${formatRange(selected)} · ${selected.delivery_mode.replace("_", " ")}${selected.location ? ` · ${selected.location}` : ""}`;
    byId("event-status").textContent = selected.status;
    byId("speaker-count").textContent = String(speakers.length);
    byId("event-time-zone").textContent = selected.time_zone;
    byId("public-schedule").href = `/events/${encodeURIComponent(eventId)}/schedule`;
    const prefix = `/admin/events/${encodeURIComponent(eventId)}`;
    byId("event-tools").replaceChildren(
      tool("Call for speakers", "Build and publish the proposal form.", `/admin/programs?event_id=${encodeURIComponent(eventId)}`),
      tool("People & access", "Invite event administrators, reviewers, and speakers.", `${prefix}/access`),
      tool("Speakers", "Browse everyone connected to this event.", `${prefix}/speakers`),
      tool("Speaker onboarding", "Track missing profiles, files, and deadlines.", `${prefix}/onboarding`),
      tool("Resources & integrations", "Share portal resources and connect Accelevents.", `${prefix}/workspace`),
      tool("Reviews", "Evaluate proposals and record decisions.", "/reviews"),
      tool("Agenda", "Schedule accepted sessions and publish the program.", `${prefix}/agenda`),
      tool("Public pages", "Open the schedule and published speaker gallery.", `/events/${encodeURIComponent(eventId)}/schedule`)
    );
    byId("status").textContent = `${selected.name} is ready.`;
  }

  initialize().catch((error) => {
    if (error.status === 401) location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`);
    else { byId("status").textContent = error.message; byId("status").classList.add("error"); }
  });
})();

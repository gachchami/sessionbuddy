(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const pathMatch = location.pathname.match(/^\/admin\/events\/([^/]+)\/speakers(?:\/([^/]+))?$/);
  let selectedEventId = "";
  let selectedSpeakerId = "";
  try {
    selectedEventId = pathMatch ? decodeURIComponent(pathMatch[1]) : new URLSearchParams(location.search).get("event_id") || "";
    selectedSpeakerId = pathMatch?.[2] ? decodeURIComponent(pathMatch[2]) : "";
  } catch (_) { selectedEventId = ""; selectedSpeakerId = ""; }

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

  function speakerCard(item) {
    const card = document.createElement("article");
    card.className = "entity-card";
    const top = document.createElement("div");
    top.className = "entity-card__top";
    const event = document.createElement("span");
    event.className = "eyebrow";
    event.textContent = item.event.name;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = item.selection_status;
    top.append(event, badge);
    const heading = document.createElement("h3");
    const name = document.createElement("a");
    name.href = `/admin/events/${encodeURIComponent(item.event.id)}/speakers/${encodeURIComponent(item.event_speaker_id)}`;
    name.textContent = item.display_name;
    heading.append(name);
    const proposal = document.createElement("p");
    proposal.className = "result";
    proposal.textContent = item.proposal_title;
    const open = document.createElement("a");
    open.className = "entity-card__action";
    open.href = name.href;
    open.textContent = "View speaker →";
    card.append(top, heading, proposal, open);
    return card;
  }

  async function initialize() {
    await api("/api/v1/auth/session");
    const organizations = (await api("/api/v1/admin/organizations")).data;
    const eventGroups = await Promise.all(organizations.map(async (organization) => ({
      organization,
      events: (await api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/events`)).data
    })));
    let events = eventGroups.flatMap(({ organization, events: items }) => items.map((event) => ({ ...event, organization_name: organization.name })));
    if (selectedEventId) events = events.filter((event) => event.id === selectedEventId);
    if (selectedEventId && !events.length) throw new Error("This event is not available to your account.");
    const groups = await Promise.all(events.map(async (event) => ({
      event,
      speakers: (await api(`/api/v1/admin/events/${encodeURIComponent(event.id)}/speaker-targets`)).data
    })));
    const speakers = groups.flatMap(({ event, speakers: items }) => items.map((speaker) => ({ ...speaker, event })));
    const activeEvent = events.length === 1 ? events[0] : null;
    if (activeEvent) {
      byId("page-title").textContent = `${activeEvent.name} speakers`;
      byId("page-summary").textContent = `Browse speaker records for ${activeEvent.name}.`;
    }
    if (selectedSpeakerId) {
      const selected = speakers.find((speaker) => speaker.event_speaker_id === selectedSpeakerId);
      if (!selected) throw new Error("This speaker is not available in the selected event.");
      byId("speaker-detail").hidden = false;
      byId("speaker-event").textContent = `${selected.event.organization_name} · ${selected.event.name} · ${selected.selection_status}`;
      byId("speaker-name").textContent = selected.display_name;
      byId("speaker-proposal").textContent = selected.proposal_title;
      byId("speaker-onboarding").href = `/admin/events/${encodeURIComponent(selected.event.id)}/onboarding`;
      byId("speaker-directory").href = `/admin/events/${encodeURIComponent(selected.event.id)}/speakers`;
      document.title = `${selected.display_name} · SessionBuddy`;
    }
    const list = byId("speaker-list");
    list.replaceChildren();
    if (speakers.length) list.append(...speakers.map(speakerCard));
    else {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No speakers yet. They will appear after proposals are submitted.";
      list.append(empty);
    }
    byId("speaker-count").textContent = String(speakers.length);
    byId("status").textContent = `${speakers.length} speaker${speakers.length === 1 ? "" : "s"} shown.`;
  }

  initialize().catch((error) => {
    if (error.status === 401) location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname + location.search)}`);
    else { byId("status").textContent = error.message; byId("status").classList.add("error"); }
  });
})();

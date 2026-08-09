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
  let csrf = "";
  let selectedSpeaker = null;
  let allSpeakers = [];

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

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
    name.href = item.selection_status === "invited"
      ? `/admin/events/${encodeURIComponent(item.event.id)}/access`
      : `/admin/events/${encodeURIComponent(item.event.id)}/speakers/${encodeURIComponent(item.event_speaker_id)}`;
    name.textContent = item.display_name;
    heading.append(name);
    const proposal = document.createElement("p");
    proposal.className = "result";
    proposal.textContent = item.proposal_title;
    const identity = document.createElement("p");
    identity.className = "muted";
    identity.textContent = [item.job_title, item.company].filter(Boolean).join(" · ") || item.email;
    const open = document.createElement("a");
    open.className = "entity-card__action";
    open.href = name.href;
    open.textContent = item.selection_status === "invited" ? "Manage invitation →" : "View speaker →";
    card.append(top, heading, identity, proposal, open);
    return card;
  }

  function renderDirectory() {
    const query = byId("speaker-search").value.trim().toLowerCase();
    const selectionStatus = byId("speaker-status").value;
    const speakers = allSpeakers.filter((speaker) => {
      const searchable = [speaker.display_name, speaker.email, speaker.job_title, speaker.company, speaker.proposal_title, speaker.event.name]
        .join(" ").toLowerCase();
      return (!query || searchable.includes(query)) && (!selectionStatus || speaker.selection_status === selectionStatus);
    });
    const list = byId("speaker-list");
    list.replaceChildren();
    if (speakers.length) list.append(...speakers.map(speakerCard));
    else {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = allSpeakers.length
        ? "No speakers match these filters."
        : "No speakers yet. Invite one or publish a Call for Proposals.";
      list.append(empty);
    }
    byId("speaker-count").textContent = String(speakers.length);
    byId("status").classList.remove("error");
    byId("status").textContent = `${speakers.length} of ${allSpeakers.length} speaker${allSpeakers.length === 1 ? "" : "s"} shown.`;
  }

  async function initialize() {
    const session = await api("/api/v1/auth/session");
    csrf = session.csrf_token;
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
    allSpeakers = speakers;
    const activeEvent = events.length === 1 ? events[0] : null;
    if (activeEvent) {
      byId("page-title").textContent = `${activeEvent.name} speakers`;
      byId("page-summary").textContent = `Browse speaker records for ${activeEvent.name}.`;
      byId("invite-speaker").href = `/admin/events/${encodeURIComponent(activeEvent.id)}/access`;
    }
    if (selectedSpeakerId) {
      const selected = speakers.find((speaker) => speaker.event_speaker_id === selectedSpeakerId);
      if (!selected) throw new Error("This speaker is not available in the selected event.");
      byId("speaker-detail").hidden = false;
      byId("speaker-event").textContent = `${selected.event.organization_name} · ${selected.event.name} · ${selected.selection_status}`;
      byId("speaker-name").textContent = selected.display_name;
      byId("speaker-proposal").textContent = selected.proposal_title;
      selectedSpeaker = selected;
      const form = byId("speaker-form");
      ["display_name", "email", "job_title", "company", "location", "biography", "version"].forEach((name) => { form.elements[name].value = selected[name] ?? ""; });
      form.elements.links.value = (selected.links || []).join("\n");
      byId("speaker-onboarding").href = `/admin/events/${encodeURIComponent(selected.event.id)}/onboarding`;
      byId("speaker-directory").href = `/admin/events/${encodeURIComponent(selected.event.id)}/speakers`;
      document.title = `${selected.display_name} · SessionBuddy`;
    }
    renderDirectory();
  }

  byId("speaker-filters").addEventListener("input", renderDirectory);

  byId("speaker-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedSpeaker) return;
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    const links = String(values.links || "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    try {
      const updated = await api(`/api/v1/admin/events/${encodeURIComponent(selectedSpeaker.event.id)}/speakers/${encodeURIComponent(selectedSpeaker.event_speaker_id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ display_name: values.display_name, job_title: values.job_title, company: values.company, biography: values.biography, location: values.location, links, version: Number(values.version) })
      });
      selectedSpeaker = { ...selectedSpeaker, ...updated };
      form.elements.version.value = updated.version;
      byId("speaker-name").textContent = updated.display_name;
      byId("status").textContent = "Speaker details saved.";
    } catch (error) { byId("status").textContent = window.SessionBuddyApi.message(error); byId("status").classList.add("error"); }
  });

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
    }
  });
})();

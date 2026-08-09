(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const pathMatch = location.pathname.match(/^\/admin\/events\/([^/]+)\/speakers(?:\/([^/]+))?$/);
  const eventScoped = Boolean(pathMatch);
  let selectedEventId = "";
  let selectedSpeakerId = "";
  try {
    selectedEventId = pathMatch
      ? decodeURIComponent(pathMatch[1])
      : new URLSearchParams(location.search).get("event_id") || "";
    selectedSpeakerId = pathMatch?.[2] ? decodeURIComponent(pathMatch[2]) : "";
  } catch (_) {
    selectedEventId = "";
    selectedSpeakerId = "";
  }

  let csrf = "";
  let selectedSpeaker = null;
  let allSpeakers = [];
  let allEvents = [];

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function participationLink(participation) {
    if (participation.selection_status === "invited") {
      return `/admin/events/${encodeURIComponent(participation.event_id)}/access`;
    }
    return `/admin/events/${encodeURIComponent(participation.event_id)}/speakers/${encodeURIComponent(participation.event_speaker_id)}`;
  }

  function relevantParticipations(item) {
    const eventId = byId("speaker-event-filter").value;
    const selectionStatus = byId("speaker-status").value;
    return item.participations.filter((participation) => (
      (!eventId || participation.event_id === eventId)
      && (!selectionStatus || participation.selection_status === selectionStatus)
    ));
  }

  function speakerCard(item) {
    const participations = relevantParticipations(item);
    const card = document.createElement("article");
    card.className = "entity-card speaker-profile-card";

    const top = document.createElement("div");
    top.className = "entity-card__top";
    const organization = document.createElement("span");
    organization.className = "eyebrow";
    organization.textContent = item.organization_name;
    const count = document.createElement("span");
    count.className = "badge";
    count.textContent = `${participations.length} event${participations.length === 1 ? "" : "s"}`;
    top.append(organization, count);

    const heading = document.createElement("h3");
    heading.textContent = item.display_name;
    const identity = document.createElement("p");
    identity.className = "muted";
    identity.textContent = [item.job_title, item.company].filter(Boolean).join(" · ") || item.email;

    const participationList = document.createElement("ul");
    participationList.className = "speaker-participations";
    participationList.setAttribute("aria-label", `Event participation for ${item.display_name}`);
    for (const participation of participations) {
      const row = document.createElement("li");
      const eventLink = document.createElement("a");
      eventLink.href = participationLink(participation);
      eventLink.textContent = participation.event_name;
      const details = document.createElement("span");
      details.textContent = `${participation.selection_status} · ${participation.proposal_title}`;
      row.append(eventLink, details);
      participationList.append(row);
    }

    card.append(top, heading, identity, participationList);
    return card;
  }

  function renderDirectory() {
    const query = byId("speaker-search").value.trim().toLowerCase();
    const speakers = allSpeakers.filter((speaker) => {
      const searchable = [
        speaker.display_name,
        speaker.email,
        speaker.job_title,
        speaker.company,
        ...speaker.participations.flatMap((item) => [item.event_name, item.proposal_title]),
      ].join(" ").toLowerCase();
      return (!query || searchable.includes(query)) && relevantParticipations(speaker).length > 0;
    });

    const list = byId("speaker-list");
    list.replaceChildren();
    if (speakers.length) {
      list.append(...speakers.map(speakerCard));
    } else {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = allSpeakers.length
        ? "No speaker profiles match these filters."
        : "No speaker profiles yet. Invite someone to an event or publish a Call for Proposals.";
      list.append(empty);
    }
    byId("speaker-count").textContent = String(speakers.length);
    byId("status").classList.remove("error");
    byId("status").textContent = `${speakers.length} of ${allSpeakers.length} speaker profile${allSpeakers.length === 1 ? "" : "s"} shown.`;
  }

  function uniquePeople(items) {
    const people = new Map();
    for (const item of items) {
      const identity = `${item.organization_id}:${item.person_id || item.user_id || item.email.toLowerCase()}`;
      const existing = people.get(identity);
      if (!existing) {
        people.set(identity, { ...item, participations: [...item.participations] });
        continue;
      }
      const known = new Set(existing.participations.map((part) => part.event_speaker_id));
      existing.participations.push(
        ...item.participations.filter((part) => !known.has(part.event_speaker_id)),
      );
    }
    return [...people.values()];
  }

  function populateEventFilter() {
    const filter = byId("speaker-event-filter");
    const current = selectedEventId;
    const multipleOrganizations = new Set(allEvents.map((event) => event.organization_id)).size > 1;
    for (const event of allEvents) {
      const option = document.createElement("option");
      option.value = event.event_id;
      option.textContent = multipleOrganizations
        ? `${event.organization_name} — ${event.event_name}`
        : event.event_name;
      filter.append(option);
    }
    if (allEvents.some((event) => event.event_id === current)) filter.value = current;
  }

  function targetAsPerson(target, organization, event) {
    return {
      ...target,
      organization_id: organization.id,
      organization_name: organization.name,
      person_id: target.person_id || "",
      participations: [{
        event_id: event.id,
        event_name: event.name,
        event_speaker_id: target.event_speaker_id,
        selection_status: target.selection_status,
        proposal_title: target.proposal_title,
      }],
    };
  }

  async function loadEventScopedDirectory(organizations) {
    const eventGroups = await Promise.all(organizations.map(async (organization) => ({
      organization,
      events: (await api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/events`)).data,
    })));
    const matching = eventGroups.flatMap(({ organization, events }) => events
      .filter((event) => event.id === selectedEventId)
      .map((event) => ({ organization, event })));
    if (!matching.length) throw new Error("This event is not available to your account.");
    const { organization, event } = matching[0];
    const targets = (await api(`/api/v1/admin/events/${encodeURIComponent(event.id)}/speaker-targets`)).data;
    allEvents = [{
      event_id: event.id,
      event_name: event.name,
      organization_id: organization.id,
      organization_name: organization.name,
    }];
    allSpeakers = uniquePeople(targets.map((target) => targetAsPerson(target, organization, event)));
    return event;
  }

  async function loadOrganizationDirectory(organizations) {
    const directories = await Promise.all(organizations.map(async (organization) => ({
      organization,
      speakers: (await api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/speakers`)).data,
    })));
    const people = directories.flatMap(({ organization, speakers }) => speakers.map((speaker) => ({
      ...speaker,
      organization_id: organization.id,
      organization_name: organization.name,
      participations: speaker.participations || speaker.events || [],
    })));
    allSpeakers = uniquePeople(people);
    const eventMap = new Map();
    for (const person of allSpeakers) {
      for (const participation of person.participations) {
        if (!eventMap.has(participation.event_id)) {
          eventMap.set(participation.event_id, {
            ...participation,
            organization_id: person.organization_id,
            organization_name: person.organization_name,
          });
        }
      }
    }
    allEvents = [...eventMap.values()].sort((left, right) => (
      left.event_name.localeCompare(right.event_name)
    ));
  }

  function findEventSpeaker(eventSpeakerId) {
    for (const person of allSpeakers) {
      const participation = person.participations.find((item) => (
        item.event_speaker_id === eventSpeakerId
      ));
      if (participation) return { person, participation };
    }
    return null;
  }

  function showSpeakerDetail(person, participation) {
    selectedSpeaker = {
      ...person,
      event: {
        id: participation.event_id,
        name: participation.event_name,
        organization_name: person.organization_name,
      },
      event_speaker_id: participation.event_speaker_id,
      selection_status: participation.selection_status,
      proposal_title: participation.proposal_title,
    };
    byId("speaker-detail").hidden = false;
    byId("speaker-event").textContent = `${person.organization_name} · ${participation.event_name} · ${participation.selection_status}`;
    byId("speaker-name").textContent = person.display_name;
    byId("speaker-proposal").textContent = participation.proposal_title;
    const form = byId("speaker-form");
    ["display_name", "email", "job_title", "company", "location", "biography", "version"].forEach((name) => {
      form.elements[name].value = person[name] ?? "";
    });
    window.SessionBuddyApi.refreshCharacterCounters(form);
    form.elements.links.value = (person.links || []).join("\n");
    byId("speaker-onboarding").href = `/admin/events/${encodeURIComponent(participation.event_id)}/onboarding`;
    byId("speaker-directory").href = `/admin/events/${encodeURIComponent(participation.event_id)}/speakers`;
    document.title = `${person.display_name} · SessionBuddy`;
  }

  async function initialize() {
    const session = await api("/api/v1/auth/session");
    csrf = session.csrf_token;
    const organizations = (await api("/api/v1/admin/organizations")).data;
    let activeEvent = null;
    if (eventScoped) {
      activeEvent = await loadEventScopedDirectory(organizations);
    } else {
      await loadOrganizationDirectory(organizations);
    }
    populateEventFilter();

    if (eventScoped && activeEvent) {
      byId("page-title").textContent = `${activeEvent.name} speakers`;
      byId("page-summary").textContent = `Manage speaker participation in ${activeEvent.name}.`;
      byId("invite-speaker").href = `/admin/events/${encodeURIComponent(activeEvent.id)}/access`;
      byId("event-filter-field").hidden = true;
    }
    if (selectedSpeakerId) {
      const selection = findEventSpeaker(selectedSpeakerId);
      if (!selection) throw new Error("This speaker is not available in the selected event.");
      showSpeakerDetail(selection.person, selection.participation);
    }
    renderDirectory();
  }

  byId("speaker-filters").addEventListener("input", () => {
    const eventId = byId("speaker-event-filter").value;
    if (!eventScoped) {
      byId("invite-speaker").href = eventId
        ? `/admin/events/${encodeURIComponent(eventId)}/access`
        : "/admin/events";
    }
    renderDirectory();
  });

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
        body: JSON.stringify({
          display_name: values.display_name,
          job_title: values.job_title,
          company: values.company,
          biography: values.biography,
          location: values.location,
          links,
          version: Number(values.version),
        }),
      });
      selectedSpeaker = { ...selectedSpeaker, ...updated };
      form.elements.version.value = updated.version;
      byId("speaker-name").textContent = updated.display_name;
      byId("status").textContent = "Speaker details saved.";
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
    }
  });

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
    }
  });
})();

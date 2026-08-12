(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const pathMatch = location.pathname.match(/^\/admin\/events\/([^/]+)\/speakers(?:\/([^/]+))?$/);
  const profileMatch = location.pathname.match(/^\/speakers\/([^/]+)$/);
  const eventScoped = Boolean(pathMatch);
  const profileScoped = Boolean(profileMatch);
  let selectedPersonId = "";
  let selectedEventId = "";
  let selectedSpeakerId = "";
  try {
    selectedEventId = pathMatch
      ? decodeURIComponent(pathMatch[1])
      : new URLSearchParams(location.search).get("event_id") || "";
    selectedSpeakerId = pathMatch?.[2] ? decodeURIComponent(pathMatch[2]) : "";
    selectedPersonId = profileMatch?.[1] ? decodeURIComponent(profileMatch[1]) : "";
  } catch (_) {
    selectedEventId = "";
    selectedSpeakerId = "";
    selectedPersonId = "";
  }

  let csrf = "";
  let selectedSpeaker = null;
  let allSpeakers = [];
  let allEvents = [];
  let inviteEventId = "";

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function speakerNoteRow(note = { label: "", value: "" }) {
    const row = document.createElement("fieldset");
    row.className = "form-grid speaker-note-row";
    const label = document.createElement("label");
    label.textContent = "Field name";
    const labelInput = document.createElement("input");
    labelInput.dataset.noteLabel = "";
    labelInput.maxLength = 100;
    labelInput.required = true;
    labelInput.placeholder = "Travel and logistics";
    labelInput.value = note.label;
    label.append(labelInput);
    const value = document.createElement("label");
    value.textContent = "Private note";
    const valueInput = document.createElement("textarea");
    valueInput.dataset.noteValue = "";
    valueInput.maxLength = 5000;
    valueInput.rows = 3;
    valueInput.value = note.value;
    value.append(valueInput);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "secondary";
    remove.textContent = "Remove field";
    remove.addEventListener("click", () => row.remove());
    row.append(label, value, remove);
    return row;
  }

  async function loadSpeakerNotes(eventId, eventSpeakerId) {
    const form = byId("speaker-organizer-notes");
    const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speakers/${encodeURIComponent(eventSpeakerId)}/organizer-notes`);
    form.elements.version.value = result.version;
    byId("speaker-note-fields").replaceChildren(...result.data.map(speakerNoteRow));
    form.hidden = false;
  }

  function participationLink(participation) {
    if (participation.selection_status === "invited") {
      return `/admin/events/${encodeURIComponent(participation.event_id)}/speakers`;
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
  function relevantAssociations(item) {
    const eventId = byId("speaker-event-filter").value;
    return (item.event_associations || []).filter((item) => !eventId || item.event_id === eventId);
  }

  function eventCount(item) {
    return new Set([
      ...(item.event_associations || []).map((association) => association.event_id),
      ...item.participations.map((participation) => participation.event_id),
    ]).size;
  }

  function speakerCard(item) {
    const participations = relevantParticipations(item);
    const row = document.createElement("div");
    row.className = "people-table-row";
    row.setAttribute("role", "row");

    const person = document.createElement("div");
    person.className = "people-person-cell";
    person.setAttribute("role", "cell");
    const initials = document.createElement("span");
    initials.className = "people-monogram";
    initials.setAttribute("aria-hidden", "true");
    initials.textContent = item.display_name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "?";
    const identity = document.createElement("span");
    const heading = document.createElement("strong");
    if (eventScoped && participations.length && participations[0].selection_status !== "invited") {
      const profileLink = document.createElement("a");
      profileLink.href = participationLink(participations[0]);
      profileLink.textContent = item.display_name;
      profileLink.setAttribute("aria-label", `Edit ${item.display_name}'s speaker details`);
      heading.append(profileLink);
    } else if (item.user_id && item.public_profile_enabled) {
      const profileLink = document.createElement("a");
      profileLink.href = `/people/${encodeURIComponent(item.user_id)}`;
      profileLink.textContent = item.display_name;
      profileLink.setAttribute("aria-label", `View ${item.display_name}'s public profile`);
      heading.append(profileLink);
    } else {
      heading.textContent = item.display_name;
    }
    const detail = document.createElement("small");
    detail.textContent = [item.job_title, item.company].filter(Boolean).join(" · ");
    identity.append(heading);
    if (detail.textContent) identity.append(detail);
    person.append(initials, identity);

    const cell = (text, className = "") => {
      const node = document.createElement("span");
      node.className = className;
      node.setAttribute("role", "cell");
      node.textContent = text || "—";
      return node;
    };
    const roles = document.createElement("span");
    roles.className = "people-role-list";
    roles.setAttribute("role", "cell");
    for (const role of item.organization_roles || ["Speaker"]) {
      const chip = document.createElement("span");
      chip.className = `people-role people-role--${role.toLowerCase()}`;
      chip.textContent = role;
      roles.append(chip);
    }
    const count = eventScoped ? participations.length : eventCount(item);
    row.append(person, cell(item.email, "people-email"), cell(item.organization_name), roles,
      cell(`${count} event${count === 1 ? "" : "s"}`, "people-event-count"));
    return row;
  }

  function renderDirectory() {
    const query = byId("speaker-search").value;
    const searchField = byId("people-search-field").value;
    const organizationId = byId("people-organization-filter").value;
    const role = byId("people-role-filter").value;
    const speakers = allSpeakers.filter((speaker) => {
      const eventId = byId("speaker-event-filter").value;
      const organizationOnly = (speaker.organization_roles || []).includes("Organizer")
        && !speaker.participations.length && !(speaker.event_associations || []).length;
      return window.SessionBuddyPeopleSearch.matches(speaker, query, searchField)
        && (!organizationId || speaker.organization_id === organizationId)
        && (!role || (speaker.organization_roles || []).includes(role))
        && (relevantParticipations(speaker).length > 0
        || relevantAssociations(speaker).length > 0 || (!eventId && organizationOnly));
    });

    const list = byId("speaker-list");
    list.replaceChildren();
    if (speakers.length) {
      list.append(...speakers.map(speakerCard));
    } else {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = allSpeakers.length
        ? "No people match this search. Try another term or clear a filter."
        : "No people yet. People appear here when they join an organization or event.";
      list.append(empty);
    }
    byId("speaker-count").textContent = String(speakers.length);
    byId("speaker-count").setAttribute("aria-label", `${speakers.length} of ${allSpeakers.length} people shown`);
    byId("status").classList.remove("error");
    byId("status").textContent = "";
  }

  function uniquePeople(items) {
    const people = new Map();
    for (const item of items) {
      const identity = `${item.organization_id}:${item.user_id || item.email?.toLowerCase() || item.person_id}`;
      const existing = people.get(identity);
      if (!existing) {
        people.set(identity, {
          ...item,
          organization_roles: [...new Set(item.organization_roles || [])],
          event_associations: [...(item.event_associations || [])],
          participations: [...(item.participations || [])],
        });
        continue;
      }
      existing.organization_roles = [...new Set([
        ...(existing.organization_roles || []),
        ...(item.organization_roles || []),
      ])];
      const knownAssociations = new Set((existing.event_associations || []).map((association) => (
        `${association.event_id}:${association.role}`
      )));
      existing.event_associations.push(
        ...(item.event_associations || []).filter((association) => (
          !knownAssociations.has(`${association.event_id}:${association.role}`)
        )),
      );
      const known = new Set(existing.participations.map((part) => part.event_speaker_id));
      existing.participations.push(
        ...(item.participations || []).filter((part) => !known.has(part.event_speaker_id)),
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

  function populateOrganizationFilter(organizations) {
    const filter = byId("people-organization-filter");
    for (const organization of organizations) {
      const option = document.createElement("option");
      option.value = organization.id;
      option.textContent = organization.name;
      filter.append(option);
    }
    byId("organization-filter-field").hidden = organizations.length < 2;
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

  async function loadEventScopedDirectory(organizationsPromise) {
    let event, organizations, targetsResponse;
    try {
      [organizations, event, targetsResponse] = await Promise.all([
        organizationsPromise,
        api(`/api/v1/admin/events/${encodeURIComponent(selectedEventId)}`),
        api(`/api/v1/admin/events/${encodeURIComponent(selectedEventId)}/speaker-targets`),
      ]);
    } catch (error) {
      if (error.status === 404) throw new Error("This event is not available to your account.");
      throw error;
    }
    const organization = organizations.find((item) => item.id === event.organization_id);
    if (!organization) throw new Error("This event is not available to your account.");
    const targets = targetsResponse.data;
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
      speakers: (await api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/people`)).data,
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
      for (const participation of person.event_associations || person.participations) {
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
    const headshotForm = byId("speaker-headshot-form");
    headshotForm.hidden = !person.user_id;
    if (person.user_id) {
      const preview = byId("speaker-headshot-preview");
      preview.src = `/api/v1/admin/events/${encodeURIComponent(participation.event_id)}/speakers/${encodeURIComponent(participation.event_speaker_id)}/headshot?v=${person.version}`;
      preview.hidden = false;
      preview.addEventListener("load", () => {
        preview.hidden = false;
        byId("speaker-headshot-fallback").hidden = true;
      }, { once: true });
      preview.addEventListener("error", () => { preview.hidden = true; byId("speaker-headshot-fallback").hidden = false; }, { once: true });
    }
    if (participation.selection_status !== "invited") {
      loadSpeakerNotes(participation.event_id, participation.event_speaker_id).catch((error) => {
        byId("status").textContent = window.SessionBuddyApi.message(error);
        byId("status").classList.add("error");
      });
    } else {
      byId("speaker-organizer-notes").hidden = true;
    }
    document.title = `${person.display_name} · SessionBuddy`;
  }

  function showProfile(profile) {
    selectedSpeaker = profile;
    byId("speaker-detail").hidden = false;
    byId("speaker-directory-results").hidden = true;
    byId("page-title").textContent = profile.display_name;
    byId("page-summary").textContent = "Speaker profile";
    byId("invite-speaker").hidden = true;
    byId("speaker-event").textContent = "Speaker profile";
    byId("speaker-name").textContent = profile.display_name;
    byId("speaker-proposal").hidden = true;
    byId("speaker-profile-view").hidden = false;
    byId("speaker-role").textContent = [profile.job_title, profile.company].filter(Boolean).join(" · ") || "Speaker";
    byId("speaker-location").textContent = profile.location || "";
    byId("speaker-biography").textContent = profile.biography || "Biography not added yet.";
    const links = (profile.links || []).map((url) => {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = new URL(url).hostname;
      return anchor;
    });
    byId("speaker-links").replaceChildren(...links);
    byId("speaker-links").parentElement.hidden = links.length === 0;
    const participationNodes = profile.participations.map((participation) => {
      const item = document.createElement("li");
      const eventLink = document.createElement("a");
      eventLink.href = `/admin/events/${encodeURIComponent(participation.event_id)}`;
      eventLink.textContent = participation.event_name;
      const detail = document.createElement("span");
      detail.textContent = `${participation.selection_status} · ${participation.proposal_title}`;
      item.append(eventLink, detail);
      return item;
    });
    byId("speaker-events").replaceChildren(...participationNodes);
    const form = byId("speaker-form");
    form.hidden = !profile.can_edit;
    byId("speaker-onboarding").hidden = true;
    byId("speaker-directory").hidden = !sessionHasOrganizerAccess;
    if (profile.can_edit) {
      ["display_name", "email", "job_title", "company", "location", "biography", "version"].forEach((name) => {
        form.elements[name].value = profile[name] ?? "";
      });
      form.elements.links.value = (profile.links || []).join("\n");
      window.SessionBuddyApi.refreshCharacterCounters(form);
    }
    byId("status").textContent = profile.can_edit ? "This is your profile. You can edit it below." : "Speaker profile";
    document.title = `${profile.display_name} · SessionBuddy`;
  }

  let sessionHasOrganizerAccess = false;

  async function initialize() {
    const session = await api("/api/v1/auth/session");
    csrf = session.csrf_token;
    const canUseOrganizerResource = (item) => (item.permissions || []).some((permission) =>
      ["owner", "edit", "manage"].includes(permission));
    sessionHasOrganizerAccess = (session.organization_access || []).some(canUseOrganizerResource)
      || (session.event_access || []).some(canUseOrganizerResource);
    if (profileScoped) {
      const profile = await api(`/api/v1/speaker-profiles/${encodeURIComponent(selectedPersonId)}`);
      showProfile(profile);
      return;
    }
    const organizationsPromise = api("/api/v1/admin/organizations").then((response) => response.data);
    let activeEvent = null;
    if (eventScoped) {
      activeEvent = await loadEventScopedDirectory(organizationsPromise);
    } else {
      const organizations = await organizationsPromise;
      populateOrganizationFilter(organizations);
      await loadOrganizationDirectory(organizations);
    }
    populateEventFilter();

    if (eventScoped && activeEvent) {
      byId("page-title").textContent = "Speakers";
      byId("page-summary").textContent = `Speaker participation in ${activeEvent.name}.`;
      inviteEventId = activeEvent.id;
      byId("invite-speaker").hidden = false;
      byId("import-speakers").hidden = false;
      byId("event-filter-field").hidden = true;
      byId("status-filter-field").hidden = false;
      byId("organization-filter-field").hidden = true;
      byId("role-filter-field").hidden = true;
      byId("directory-title").textContent = "Speaker results";
      document.title = "Speakers · SessionBuddy";
    } else {
      byId("page-title").textContent = "People";
      byId("page-summary").textContent = "Find people across the organizations you manage.";
      document.title = "People · SessionBuddy";
    }
    if (selectedSpeakerId) {
      const selection = findEventSpeaker(selectedSpeakerId);
      if (!selection) throw new Error("This speaker is not available in the selected event.");
      showSpeakerDetail(selection.person, selection.participation);
    }
    renderDirectory();
  }

  byId("speaker-filters").addEventListener("input", () => {
    renderDirectory();
  });

  const inviteDialog = byId("invite-speaker-dialog");
  byId("invite-speaker").addEventListener("click", () => inviteDialog.showModal());
  byId("close-speaker-invite").addEventListener("click", () => inviteDialog.close());
  byId("cancel-speaker-invite").addEventListener("click", () => inviteDialog.close());
  byId("invite-speaker-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!inviteEventId || !form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    const values = Object.fromEntries(new FormData(form));
    button.disabled = true;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(inviteEventId)}/invitations`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ ...values, expires_in_days: Number(values.expires_in_days) })
      });
      form.reset();
      inviteDialog.close();
      byId("status").textContent = "Speaker invitation created and emailed.";
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").focus();
    } finally { button.disabled = false; }
  });

  function parseCsv(text) {
    const rows = [];
    let row = [], field = "", quoted = false;
    for (let index = 0; index < text.length; index += 1) {
      const character = text[index];
      if (quoted) {
        if (character === '"' && text[index + 1] === '"') { field += '"'; index += 1; }
        else if (character === '"') quoted = false;
        else field += character;
      } else if (character === '"' && field === "") quoted = true;
      else if (character === ",") { row.push(field); field = ""; }
      else if (character === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
      else if (character !== "\r") field += character;
    }
    if (quoted) throw new Error("The CSV contains an unclosed quoted field.");
    if (field || row.length) { row.push(field); rows.push(row); }
    return rows.filter((values) => values.some((value) => value.trim()));
  }

  function speakerInvitationsFromCsv(text) {
    const rows = parseCsv(text);
    if (!rows.length) throw new Error("The CSV file is empty.");
    const headers = rows.shift().map((value) => value.trim().toLowerCase());
    const displayNameHeader = headers.includes("display_name") ? "display_name" : "name";
    if (!headers.includes("email") || !headers.includes(displayNameHeader)) {
      throw new Error("CSV headers must include email and display_name (or name).");
    }
    if (rows.length > 500) throw new Error("Import no more than 500 speakers at a time.");
    const known = new Set();
    return rows.map((values, index) => {
      const value = (name) => String(values[headers.indexOf(name)] || "").trim();
      const email = value("email");
      const displayName = value(displayNameHeader);
      if (!email || !displayName) throw new Error(`Row ${index + 2} needs an email and display_name (or name).`);
      const normalized = email.toLowerCase();
      if (known.has(normalized)) throw new Error(`Row ${index + 2} repeats ${email}.`);
      known.add(normalized);
      return { email, display_name: displayName, job_title: value("job_title"), company: value("company"), role: "speaker", expires_in_days: 14 };
    });
  }

  const importDialog = byId("import-speakers-dialog");
  byId("import-speakers").addEventListener("click", () => importDialog.showModal());
  byId("close-speaker-import").addEventListener("click", () => importDialog.close());
  byId("cancel-speaker-import").addEventListener("click", () => importDialog.close());
  byId("import-speakers-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const file = form.elements.speaker_csv.files[0];
    const button = form.querySelector('button[type="submit"]');
    const importStatus = byId("speaker-import-status");
    if (!file || !form.reportValidity()) return;
    if (file.size > 1024 * 1024) { importStatus.textContent = "Choose a CSV file no larger than 1 MB."; return; }
    button.disabled = true;
    try {
      const invitations = speakerInvitationsFromCsv(await file.text());
      if (!invitations.length) throw new Error("The CSV has no speaker rows.");
      for (let index = 0; index < invitations.length; index += 1) {
        importStatus.textContent = `Sending invitation ${index + 1} of ${invitations.length}…`;
        await api(`/api/v1/admin/events/${encodeURIComponent(inviteEventId)}/invitations`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrf },
          body: JSON.stringify(invitations[index]),
        });
      }
      form.reset();
      importDialog.close();
      const organizations = await api("/api/v1/admin/organizations").then((response) => response.data);
      await loadEventScopedDirectory(Promise.resolve(organizations));
      renderDirectory();
      byId("status").textContent = `${invitations.length} speaker invitation${invitations.length === 1 ? "" : "s"} sent.`;
    } catch (error) {
      importStatus.textContent = error instanceof Error ? error.message : window.SessionBuddyApi.message(error);
    } finally { button.disabled = false; }
  });

  byId("speaker-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedSpeaker) return;
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    const links = String(values.links || "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    try {
      const profileEndpoint = profileScoped
        ? `/api/v1/speaker-profiles/${encodeURIComponent(selectedSpeaker.person_id)}`
        : `/api/v1/admin/events/${encodeURIComponent(selectedSpeaker.event.id)}/speakers/${encodeURIComponent(selectedSpeaker.event_speaker_id)}`;
      const updated = await api(profileEndpoint, {
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
      if (profileScoped) showProfile(selectedSpeaker);
      byId("status").textContent = "Speaker details saved.";
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
    }
  });

  byId("add-speaker-note").addEventListener("click", () => {
    byId("speaker-note-fields").append(speakerNoteRow());
  });

  byId("speaker-organizer-notes").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!selectedSpeaker?.event_speaker_id || !form.reportValidity()) return;
    const data = [...byId("speaker-note-fields").querySelectorAll(".speaker-note-row")].map((row) => ({
      label: row.querySelector("[data-note-label]").value,
      value: row.querySelector("[data-note-value]").value,
    }));
    try {
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(selectedSpeaker.event.id)}/speakers/${encodeURIComponent(selectedSpeaker.event_speaker_id)}/organizer-notes`, {
        method: "PUT",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ data, version: Number(form.elements.version.value) }),
      });
      form.elements.version.value = result.version;
      byId("status").textContent = "Organizer notes saved.";
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
    }
  });

  byId("speaker-headshot-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedSpeaker?.event_speaker_id) return;
    const form = event.currentTarget;
    const file = form.elements.headshot.files[0];
    if (!file || file.size <= 0 || file.size > 5 * 1024 * 1024) {
      byId("speaker-headshot-status").textContent = "Choose an image no larger than 5 MB.";
      return;
    }
    const endpoint = `/api/v1/admin/events/${encodeURIComponent(selectedSpeaker.event.id)}/speakers/${encodeURIComponent(selectedSpeaker.event_speaker_id)}/headshot`;
    try {
      await api(endpoint, { method: "PUT", headers: { "content-type": file.type, "x-csrf-token": csrf }, body: file });
      const preview = byId("speaker-headshot-preview");
      preview.src = `${endpoint}?v=${Date.now()}`;
      preview.hidden = false;
      byId("speaker-headshot-fallback").hidden = true;
      form.elements.headshot.value = "";
      byId("speaker-headshot-status").textContent = "Headshot saved.";
    } catch (error) {
      byId("speaker-headshot-status").textContent = window.SessionBuddyApi.message(error, "The headshot could not be saved.");
    }
  });

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
    }
  });
})();

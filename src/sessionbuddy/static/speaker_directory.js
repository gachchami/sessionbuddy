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
  let eventDirectoryContext = null;
  let speakerImportRows = null;
  let speakerImportBatchKey = "";

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  function idempotencyKey() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(24));
    return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  }

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
      && (!selectionStatus || participation.confirmation_status === selectionStatus)
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

    const cell = (text, className = "", label = "") => {
      const node = document.createElement("span");
      node.className = className;
      node.setAttribute("role", "cell");
      if (label) node.dataset.label = label;
      node.textContent = text || "—";
      return node;
    };
    const roles = document.createElement("span");
    roles.className = "people-role-list";
    roles.setAttribute("role", "cell");
    roles.dataset.label = "Roles";
    for (const role of item.organization_roles || ["Speaker"]) {
      const chip = document.createElement("span");
      chip.className = `people-role people-role--${role.toLowerCase()}`;
      chip.textContent = role;
      roles.append(chip);
    }
    const count = eventScoped ? participations.length : eventCount(item);
    row.append(person, cell(item.email, "people-email", "Email"), cell(item.organization_name, "", "Organization"), roles,
      cell(`${count} event${count === 1 ? "" : "s"}`, "people-event-count", "Events"));
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
        confirmation_status: target.confirmation_status,
        lifecycle_status: target.lifecycle_status,
        participation_version: target.participation_version,
        proposal_title: target.proposal_title,
      }],
    };
  }

  async function loadEventScopedDirectory(organizationsPromise) {
    if (!selectedEventId) {
      byId("status").textContent = "This event link is invalid. Open your active workspace and select an event.";
      byId("status").classList.add("error");
      byId("speaker-list").replaceChildren();
      return null;
    }
    const settle = (promise) => promise
      .then((value) => ({ ok: true, value }))
      .catch((error) => ({ ok: false, error }));
    // Event attribution and the roster/detail read are independent. Start both
    // before awaiting either so the nested scope check does not add a serial
    // Worker round trip to every directory navigation.
    const eventPromise = settle(api(`/api/v1/admin/events/${encodeURIComponent(selectedEventId)}`));
    const targetsPromise = settle(selectedSpeakerId
      ? api(`/api/v1/admin/events/${encodeURIComponent(selectedEventId)}/speakers/${encodeURIComponent(selectedSpeakerId)}`)
        .then((target) => [target])
      : api(`/api/v1/admin/events/${encodeURIComponent(selectedEventId)}/speaker-targets`)
        .then((result) => result.data));
    const eventResult = await eventPromise;
    if (!eventResult.ok) {
      const error = eventResult.error;
      const recoveryScope = window.SessionBuddyApi.recoveryScope.event(selectedEventId);
      if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error, recoveryScope)
          || window.SessionBuddyApi.redirectIfDocumentAccessChanged(error, recoveryScope)) return null;
      throw error;
    }
    const event = eventResult.value;
    const organizations = await organizationsPromise;
    const organization = organizations.find((item) => item.id === event.organization_id);
    if (!organization) throw new Error("This event is not available to your account.");
    const targetsResult = await targetsPromise;
    if (!targetsResult.ok) {
      const error = targetsResult.error;
      const recoveryScope = selectedSpeakerId
        ? window.SessionBuddyApi.recoveryScope.eventSpeaker(selectedEventId, selectedSpeakerId)
        : window.SessionBuddyApi.recoveryScope.event(selectedEventId);
      if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error, recoveryScope)
          || window.SessionBuddyApi.redirectIfDocumentAccessChanged(error, recoveryScope)) return null;
      throw error;
    }
    const targets = targetsResult.value;
    eventDirectoryContext = { event, organization };
    allEvents = [{
      event_id: event.id,
      event_name: event.name,
      organization_id: organization.id,
      organization_name: organization.name,
    }];
    allSpeakers = uniquePeople(targets.map((target) => targetAsPerson(target, organization, event)));
    return event;
  }

  async function refreshEventScopedRoster() {
    if (!eventDirectoryContext) return;
    const { event, organization } = eventDirectoryContext;
    const result = await api(`/api/v1/admin/events/${encodeURIComponent(event.id)}/speaker-targets`);
    allSpeakers = uniquePeople(result.data.map((target) => targetAsPerson(target, organization, event)));
    renderDirectory();
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

  function showHeadshotPreview(endpoint, cacheKey, successMessage = "", failureMessage = "") {
    const preview = byId("speaker-headshot-preview");
    const fallback = byId("speaker-headshot-fallback");
    const headshotStatus = byId("speaker-headshot-status");
    const speakerName = selectedSpeaker?.display_name || `${selectedSpeaker?.first_name || ""} ${selectedSpeaker?.last_name || ""}`.trim();
    preview.alt = speakerName ? `Headshot for ${speakerName}` : "Speaker headshot";
    preview.hidden = true;
    fallback.hidden = true;
    preview.onload = () => {
      preview.hidden = false;
      fallback.hidden = true;
      if (successMessage) headshotStatus.textContent = successMessage;
    };
    preview.onerror = () => {
      preview.hidden = true;
      fallback.hidden = false;
      if (failureMessage) headshotStatus.textContent = failureMessage;
    };
    preview.src = `${endpoint}?v=${encodeURIComponent(cacheKey)}`;
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
      confirmation_status: participation.confirmation_status,
      lifecycle_status: participation.lifecycle_status,
      participation_version: participation.participation_version,
      proposal_title: participation.proposal_title,
    };
    byId("speaker-detail").hidden = false;
    byId("speaker-event").textContent = `${person.organization_name} · ${participation.event_name} · ${participation.confirmation_status.replaceAll("_", " ")}`;
    byId("speaker-name").textContent = person.display_name;
    byId("speaker-proposal").textContent = participation.proposal_title;
    const form = byId("speaker-form");
    ["display_name", "email", "job_title", "company", "location", "version", "participation_version"].forEach((name) => {
      form.elements[name].value = person[name] ?? "";
    });
    form.elements.biography_override.value = person.biography_override ?? "";
    byId("speaker-biography-source").textContent = person.biography_source === "account"
      ? `Currently inherited from the account: ${person.biography || "No account biography."}`
      : "This event uses the organization biography below.";
    form.elements.confirmation_status.value = participation.confirmation_status;
    byId("speaker-confirmation-field").hidden = false;
    byId("restore-speaker").hidden = participation.lifecycle_status !== "withdrawn";
    window.SessionBuddyApi.refreshCharacterCounters(form);
    form.elements.links.value = (person.links || []).join("\n");
    byId("speaker-onboarding").href = `/admin/events/${encodeURIComponent(participation.event_id)}/onboarding`;
    byId("speaker-directory").href = `/admin/events/${encodeURIComponent(participation.event_id)}/speakers`;
    const headshotForm = byId("speaker-headshot-form");
    headshotForm.hidden = !person.user_id;
    byId("speaker-headshot-status").textContent = "";
    if (person.user_id) {
      const endpoint = `/api/v1/admin/events/${encodeURIComponent(participation.event_id)}/speakers/${encodeURIComponent(participation.event_speaker_id)}/headshot`;
      showHeadshotPreview(endpoint, person.version, "Current headshot is saved.");
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
    byId("speaker-confirmation-field").hidden = true;
    byId("speaker-onboarding").hidden = true;
    byId("restore-speaker").hidden = true;
    byId("speaker-directory").hidden = !sessionHasOrganizerAccess;
    if (profile.can_edit) {
      ["display_name", "email", "job_title", "company", "location", "version"].forEach((name) => {
        form.elements[name].value = profile[name] ?? "";
      });
      form.elements.participation_version.value = "";
      form.elements.biography_override.value = profile.biography_override ?? "";
      byId("speaker-biography-source").textContent = profile.biography_source === "account"
        ? `Currently inherited from your account: ${profile.biography || "No account biography."}`
        : "This organization biography overrides your account biography.";
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
      if (!activeEvent) return;
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
      // The singular endpoint is authoritative. A miss is handled as a
      // document-scope failure above rather than inferred from a capped roster.
      if (!selection) throw new Error("The speaker record could not be loaded.");
      showSpeakerDetail(selection.person, selection.participation);
    }
    byId("status").classList.remove("error");
    byId("status").textContent = "";
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
    let invitationCreated = false;
    button.disabled = true;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(inviteEventId)}/invitations`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ ...values, expires_in_days: Number(values.expires_in_days) })
      });
      invitationCreated = true;
      form.reset();
      inviteDialog.close();
      byId("status").textContent = "Speaker invitation created. Refreshing the roster…";
      await refreshEventScopedRoster();
      byId("status").textContent = "Speaker invitation created and emailed. The roster is up to date.";
    } catch (error) {
      byId("status").textContent = invitationCreated
        ? "Speaker invitation created and emailed, but the roster could not refresh. Reload this page to see the new invitation; do not send it again."
        : window.SessionBuddyApi.message(error);
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
    return rows;
  }

  function speakerInvitationsFromCsv(text) {
    const parsedRows = parseCsv(text);
    const headerIndex = parsedRows.findIndex((values) => values.some((value) => value.trim()));
    if (headerIndex < 0) throw new Error("The CSV file is empty.");
    const headers = parsedRows[headerIndex].map((value) => value.trim().toLowerCase());
    const displayNameHeader = headers.includes("display_name") ? "display_name" : "name";
    if (!headers.includes("email") || !headers.includes(displayNameHeader)) {
      throw new Error("CSV headers must include email and display_name (or name).");
    }
    const rows = parsedRows.slice(headerIndex + 1)
      .map((values, index) => ({ values, row_number: headerIndex + index + 2 }))
      .filter(({ values }) => values.some((value) => value.trim()));
    if (rows.length > 500) throw new Error("Import no more than 500 speakers at a time.");
    return rows.map(({ values, row_number }) => {
      const value = (name) => String(values[headers.indexOf(name)] || "").trim();
      return {
        row_number,
        email: value("email"),
        display_name: value(displayNameHeader),
        job_title: value("job_title"),
        company: value("company"),
        biography: value("biography"),
        disposition: "import",
      };
    });
  }

  function renderSpeakerImportResults(response) {
    const panel = byId("speaker-import-duplicates");
    const container = byId("speaker-import-duplicate-list");
    const labels = {
      ready: "Ready",
      created: "Imported",
      skipped: "Skipped",
      skipped_duplicate: "Duplicate",
      skipped_existing_speaker: "Already active",
      skipped_existing_invitation: "Already invited",
      needs_resolution: "Decision needed",
      rejected: "Invalid",
      failed: "Failed",
    };
    const readyCount = response.data.filter((result) => result.outcome === "ready").length;
    const summary = [
      [response.mode === "execute" ? "Imported" : "Ready", response.mode === "execute" ? response.created_count : readyCount],
      ["Skipped", response.skipped_count],
      ["Invalid", response.rejected_count],
      ["Failed", response.failed_count || 0],
      ["Decisions", response.needs_resolution_count],
    ];
    byId("speaker-import-summary").replaceChildren(...summary.map(([label, value]) => {
      const item = document.createElement("span");
      item.className = "speaker-import-summary__item";
      const count = document.createElement("strong");
      count.textContent = String(value);
      const caption = document.createElement("small");
      caption.textContent = String(label);
      item.append(count, caption);
      return item;
    }));
    container.replaceChildren(...response.data.map((result) => {
      const row = document.createElement("div");
      row.className = `speaker-import-result speaker-import-result--${result.outcome}`;
      row.setAttribute("role", "listitem");
      const marker = document.createElement("span");
      marker.className = "speaker-import-result__row";
      marker.textContent = String(result.row_number);
      marker.setAttribute("aria-label", `CSV row ${result.row_number}`);
      const identity = document.createElement("div");
      identity.className = "speaker-import-result__identity";
      const heading = document.createElement("strong");
      heading.textContent = result.display_name || "Missing name";
      const email = document.createElement("span");
      email.textContent = result.email || "Missing email";
      const reason = document.createElement("p");
      reason.textContent = result.reason || (result.outcome === "ready" ? "Ready to import." : result.outcome);
      identity.append(heading, email, reason);
      const outcome = document.createElement("span");
      outcome.className = `speaker-import-result__outcome speaker-import-result__outcome--${result.outcome}`;
      outcome.textContent = labels[result.outcome] || result.outcome;
      row.append(marker, identity, outcome);
      if (result.allowed_dispositions?.length) {
        const label = document.createElement("label");
        label.className = "speaker-import-result__action";
        label.textContent = "Action";
        const select = document.createElement("select");
        select.dataset.importDisposition = String(result.row_number);
        select.required = true;
        select.add(new Option("Choose…", ""));
        result.allowed_dispositions.forEach((value) => {
          const text = value === "separate_person" ? "Import as a separate person"
            : value === "import" ? "Import this row" : "Skip this row";
          select.add(new Option(text, value));
        });
        label.append(select);
        row.append(label);
      }
      return row;
    }));
    panel.hidden = false;
  }

  const importDialog = byId("import-speakers-dialog");
  byId("import-speakers").addEventListener("click", () => {
    const form = byId("import-speakers-form");
    form.reset();
    speakerImportRows = null;
    speakerImportBatchKey = "";
    byId("speaker-import-duplicates").hidden = true;
    byId("speaker-import-duplicate-list").replaceChildren();
    byId("speaker-import-summary").replaceChildren();
    byId("speaker-import-status").textContent = "Choose a CSV file to review before sending invitations.";
    const button = form.querySelector('button[type="submit"]');
    button.textContent = "Review import";
    button.disabled = false;
    importDialog.showModal();
  });
  byId("close-speaker-import").addEventListener("click", () => importDialog.close());
  byId("cancel-speaker-import").addEventListener("click", () => importDialog.close());
  byId("import-speakers-form").elements.speaker_csv.addEventListener("change", (event) => {
    const form = event.currentTarget.form;
    speakerImportRows = null;
    speakerImportBatchKey = "";
    byId("speaker-import-duplicates").hidden = true;
    byId("speaker-import-duplicate-list").replaceChildren();
    form.querySelector('button[type="submit"]').textContent = "Review import";
  });
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
      if (!speakerImportRows) {
        speakerImportRows = speakerInvitationsFromCsv(await file.text());
        if (!speakerImportRows.length) throw new Error("The CSV has no speaker rows.");
        const preview = await api(`/api/v1/admin/events/${encodeURIComponent(inviteEventId)}/speaker-invitations/import`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrf },
          body: JSON.stringify({ mode: "preview", rows: speakerImportRows }),
        });
        renderSpeakerImportResults(preview);
        const ready = preview.data.filter((result) => result.outcome === "ready").length;
        const conflicts = preview.needs_resolution_count;
        importStatus.textContent = `${ready} ready to import; ${preview.skipped_count} duplicate or existing; ${preview.rejected_count} invalid; ${conflicts} need a decision.`;
        button.textContent = ready || conflicts ? "Import reviewed rows" : "Nothing to import";
        button.disabled = !(ready || conflicts);
        if (conflicts) byId("speaker-import-duplicate-list").querySelector("[data-import-disposition]")?.focus();
        return;
      }
      const dispositions = new Map(
        [...byId("speaker-import-duplicate-list").querySelectorAll("[data-import-disposition]")]
          .map((select) => [Number(select.dataset.importDisposition), select.value])
      );
      const unresolved = [...dispositions.values()].some((value) => !value);
      if (unresolved) {
        const first = [...byId("speaker-import-duplicate-list").querySelectorAll("[data-import-disposition]")]
          .find((select) => !select.value);
        first?.focus();
        importStatus.textContent = "Choose Import or Skip for every identity conflict.";
        return;
      }
      const conflictsByEmail = new Map();
      speakerImportRows.forEach((row) => {
        const key = String(row.email || "").trim().toLowerCase();
        if (!key) return;
        const group = conflictsByEmail.get(key) || [];
        group.push(row);
        conflictsByEmail.set(key, group);
      });
      const invalidGroup = [...conflictsByEmail.values()].find((group) => {
        if (group.length < 2) return false;
        const selected = group.map((row) => dispositions.get(row.row_number) || row.disposition);
        const importCount = selected.filter((value) => value === "import").length;
        const hasVisibleDecision = group.some((row) => dispositions.has(row.row_number));
        return hasVisibleDecision && (importCount > 1
          || (importCount === 0 && selected.some((value) => value !== "skip")));
      });
      if (invalidGroup) {
        importStatus.textContent = "For each repeated email, import one row or skip the entire group.";
        byId("speaker-import-duplicate-list")
          .querySelector(`[data-import-disposition="${invalidGroup[0].row_number}"]`)?.focus();
        return;
      }
      const rows = speakerImportRows.map((row) => ({
        ...row,
        disposition: dispositions.get(row.row_number) || row.disposition,
      }));
      speakerImportRows = rows;
      speakerImportBatchKey ||= `speaker-csv-${idempotencyKey()}`;
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(inviteEventId)}/speaker-invitations/import`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-csrf-token": csrf,
          "idempotency-key": speakerImportBatchKey,
        },
        body: JSON.stringify({ mode: "execute", rows }),
      });
      renderSpeakerImportResults(result);
      const organizations = await api("/api/v1/admin/organizations").then((response) => response.data);
      await loadEventScopedDirectory(Promise.resolve(organizations));
      renderDirectory();
      importStatus.textContent = `${result.created_count} imported; ${result.skipped_count} skipped; ${result.rejected_count} rejected; ${result.failed_count || 0} failed; ${result.needs_resolution_count} still need a decision.`;
      button.textContent = result.needs_resolution_count ? "Apply remaining decisions" : "Import complete";
      button.disabled = !result.needs_resolution_count;
    } catch (error) {
      importStatus.textContent = error instanceof Error ? error.message : window.SessionBuddyApi.message(error);
    } finally {
      button.disabled = ["Nothing to import", "Import complete"].includes(button.textContent);
    }
  });

  byId("speaker-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedSpeaker) return;
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    const links = String(values.links || "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    const linkControl = form.elements.links;
    linkControl.setCustomValidity("");
    let linkError = "";
    if (links.length > 10) linkError = "Enter no more than 10 links.";
    else if (new Set(links).size !== links.length) linkError = "Each link must be unique.";
    else {
      const invalidIndex = links.findIndex((value) => {
        if (value.length > 2000) return true;
        try {
          const parsed = new URL(value);
          return !["http:", "https:"].includes(parsed.protocol) || !parsed.host
            || Boolean(parsed.username) || Boolean(parsed.password);
        } catch (_) { return true; }
      });
      if (invalidIndex >= 0) {
        linkError = `Link ${invalidIndex + 1} must be a full http:// or https:// URL without credentials.`;
      }
    }
    if (linkError) {
      linkControl.setCustomValidity(linkError);
      form.reportValidity();
      linkControl.focus();
      // The shared invalid handler has already rendered the durable inline
      // message. Do not leave a custom-validity latch behind: some touch
      // browsers run native constraint validation before dispatching the next
      // submit event, which otherwise makes a corrected form look inert.
      linkControl.setCustomValidity("");
      return;
    }
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
          biography_override: values.biography_override || null,
          location: values.location,
          links,
          version: Number(values.version),
          ...(profileScoped ? {} : {
            participation_version: Number(values.participation_version),
            confirmation_status: values.confirmation_status,
          }),
        }),
      });
      selectedSpeaker = { ...selectedSpeaker, ...updated };
      form.elements.version.value = updated.version;
      if (!profileScoped) form.elements.participation_version.value = updated.participation_version;
      byId("speaker-name").textContent = updated.display_name;
      if (profileScoped) showProfile(selectedSpeaker);
      else {
        const current = findEventSpeaker(selectedSpeaker.event_speaker_id);
        if (current) {
          Object.assign(current.person, updated);
          Object.assign(current.participation, {
            confirmation_status: updated.confirmation_status,
            lifecycle_status: updated.lifecycle_status,
            participation_version: updated.participation_version,
          });
          showSpeakerDetail(current.person, current.participation);
          renderDirectory();
        }
      }
      byId("status").textContent = "Speaker details saved.";
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
      const validationShown = window.SessionBuddyApi.showValidationErrors?.(form, error) ?? false;
      if (!validationShown) byId("status").focus();
    }
  });

  const clearLinkValidation = (event) => event.currentTarget.setCustomValidity("");
  // Clear the previous list-level error before native submit validation runs.
  // Capture makes this reliable on touch browsers, where the browser can test
  // the stale custom validity before a later bubble listener is observed.
  byId("speaker-form").elements.links.addEventListener("input", clearLinkValidation, true);
  byId("speaker-form").elements.links.addEventListener("change", clearLinkValidation, true);

  byId("restore-speaker").addEventListener("click", async (event) => {
    if (!selectedSpeaker?.event_speaker_id || selectedSpeaker.lifecycle_status !== "withdrawn") return;
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const restored = await api(
        `/api/v1/admin/events/${encodeURIComponent(selectedSpeaker.event.id)}/speakers/${encodeURIComponent(selectedSpeaker.event_speaker_id)}/restore`,
        {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrf },
          body: JSON.stringify({ participation_version: selectedSpeaker.participation_version }),
        },
      );
      selectedSpeaker.lifecycle_status = restored.status;
      selectedSpeaker.participation_version = restored.participation_version;
      const current = findEventSpeaker(selectedSpeaker.event_speaker_id);
      if (current) {
        current.participation.lifecycle_status = restored.status;
        current.participation.participation_version = restored.participation_version;
      }
      button.hidden = true;
      const sessionText = restored.reactivated_session_count === 0
        ? ""
        : restored.reactivated_session_count === 1
          ? " One accepted session was restored."
          : ` ${restored.reactivated_session_count} accepted sessions were restored.`;
      byId("status").textContent = `Speaker participation restored.${sessionText}`;
      byId("status").classList.remove("error");
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
      byId("status").focus();
    } finally {
      button.disabled = false;
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
      form.elements.headshot.value = "";
      byId("speaker-headshot-status").textContent = "Verifying saved headshot…";
      showHeadshotPreview(
        endpoint,
        Date.now(),
        "Headshot saved and verified.",
        "The upload finished, but the saved headshot could not be verified. Refresh and try again.",
      );
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

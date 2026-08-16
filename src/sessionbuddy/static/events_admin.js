(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const state = { csrf: "", userId: "", organizationId: "", organizations: new Map(), events: new Map(), eventFilter: "all", eventOrder: "upcoming", eventSearch: "", eventsRequestId: 0, submitting: false, editingDraft: false, submitTargetStatus: "active", createMutation: null, emailDefaults: null, draftTimer: null, adminEventIds: new Set(), archiveConfirmedEventId: "" };
  const logoRules = { "image/jpeg": 2 * 1024 * 1024, "image/png": 2 * 1024 * 1024, "image/webp": 2 * 1024 * 1024 };
  const timeZoneAliases = new Map([
    ["Asia/Calcutta", "Asia/Kolkata"],
    ["Europe/Kiev", "Europe/Kyiv"],
    ["America/Godthab", "America/Nuuk"],
    ["Asia/Saigon", "Asia/Ho_Chi_Minh"],
    ["Pacific/Enderbury", "Pacific/Kanton"],
    ["Pacific/Truk", "Pacific/Chuuk"],
    ["Pacific/Ponape", "Pacific/Pohnpei"],
    ["Etc/UTC", "UTC"]
  ]);

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  const eventDraftKey = () => `sessionbuddy:event-form-draft:${state.userId || "unknown"}`;
  const mutationToken = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  };

  function preserveEventDraft(form) {
    const values = {};
    for (const [name, value] of new FormData(form)) {
      if (typeof value === "string") values[name] = value;
    }
    sessionStorage.setItem(eventDraftKey(), JSON.stringify(values));
  }

  function queueEventDraft(form) {
    const editingActive = Boolean(form.elements.event_id.value) && !state.editingDraft;
    if (editingActive) {
      byId("event-autosave-state").textContent = "Live event changes are not autosaved";
      return;
    }
    byId("event-autosave-state").textContent = "Saving draft…";
    clearTimeout(state.draftTimer);
    state.draftTimer = setTimeout(() => {
      preserveEventDraft(form);
      byId("event-autosave-state").textContent = "Draft saved in this browser";
    }, 500);
  }

  function restoreEventDraft() {
    const raw = sessionStorage.getItem(eventDraftKey());
    if (!raw) return false;
    try {
      const values = JSON.parse(raw);
      const form = byId("event-form");
      for (const [name, value] of Object.entries(values)) {
        const field = form.elements[name];
        if (field && typeof value === "string") field.value = value;
      }
      byId("event-form-heading").textContent = values.event_id
        ? "Resume event changes"
        : values.duplicate_source_event_id ? "Resume event duplication" : "Resume event creation";
      state.editingDraft = Boolean(values.event_id && values.status === "draft");
      byId("save-event").textContent = values.event_id
        ? "Save changes"
        : "Create active event";
      byId("save-event-draft").hidden = Boolean(values.event_id && !state.editingDraft);
      byId("creation-action-note").hidden = Boolean(values.event_id && !state.editingDraft);
      byId("event-status-label").hidden = !values.event_id;
      updateDateTimePreview();
      openEventDialog();
      setDialogStatus("Your entries were restored after signing in again.");
      byId("event-autosave-state").textContent = "Draft restored from this browser";
      return true;
    } catch (_) {
      sessionStorage.removeItem(eventDraftKey());
      return false;
    }
  }

  function updateSaveAvailability() {
    const form = byId("event-form");
    const pending = Boolean(form.elements.logo_file.files[0] || form.elements.cover_file.files[0]);
    byId("save-event").disabled = pending || state.submitting;
    byId("save-event-draft").disabled = pending || state.submitting;
  }

  function setDialogStatus(message = "", error = false) {
    const status = byId("event-dialog-status");
    status.textContent = message;
    status.classList.toggle("error", error);
    status.setAttribute("role", error ? "alert" : "status");
    if (error) status.focus();
  }

  function setSubmitting(submitting, editing = false) {
    state.submitting = submitting;
    byId("event-form").setAttribute("aria-busy", String(submitting));
    byId("save-event-draft").textContent = submitting && state.submitTargetStatus === "draft" ? "Saving draft…" : "Save draft";
    byId("save-event").textContent = submitting
      ? (editing
          ? "Saving changes…"
          : state.submitTargetStatus === "active" ? "Creating event…" : "Create active event")
      : (editing ? "Save changes" : "Create active event");
    byId("cancel-event-edit").disabled = submitting;
    byId("close-event-dialog").disabled = submitting;
    updateSaveAvailability();
  }

  function setStatus(message, error = false) {
    const status = byId("status");
    status.textContent = message;
    status.classList.toggle("error", error);
    if (error) status.focus();
  }

  function button(label, handler) {
    const control = document.createElement("button");
    control.type = "button";
    control.className = "secondary";
    control.textContent = label;
    control.addEventListener("click", handler);
    return control;
  }

  function link(label, href) {
    const control = document.createElement("a");
    control.textContent = label;
    control.href = href;
    control.className = "button secondary";
    return control;
  }

  function normalizeTimeZone(value) {
    const trimmed = String(value || "").trim();
    return timeZoneAliases.get(trimmed) || trimmed;
  }

  function browserTimeZone() {
    return normalizeTimeZone(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  }

  function timeZoneIsValid(timeZone) {
    try {
      new Intl.DateTimeFormat(undefined, { timeZone }).format();
      return true;
    } catch (_) {
      return false;
    }
  }

  function securePublicUrl(value) {
    if (!value) return true;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && Boolean(url.hostname) && !url.username && !url.password;
    } catch (_) { return false; }
  }

  function populateTimeZones() {
    const detected = browserTimeZone();
    const values = new Set([detected, "UTC", ...timeZoneAliases.values()]);
    if (typeof Intl.supportedValuesOf === "function") {
      for (const value of Intl.supportedValuesOf("timeZone")) values.add(normalizeTimeZone(value));
    }
    const options = [...values].sort().map((value) => new Option(value, value));
    byId("time-zone-options").replaceChildren(...options);
    byId("detected-time-zone").textContent = `Detected from your browser: ${detected}.`;
  }

  function partsInTimeZone(timestamp, timeZone) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    }).formatToParts(new Date(timestamp));
    return Object.fromEntries(
      parts.filter(({ type }) => type !== "literal").map(({ type, value }) => [type, Number(value)])
    );
  }

  function eventLocalDateTime(timestamp, timeZone) {
    const parts = partsInTimeZone(timestamp, timeZone);
    const pad = (value) => String(value).padStart(2, "0");
    return {
      date: `${parts.year}-${pad(parts.month)}-${pad(parts.day)}`,
      time: `${pad(parts.hour)}:${pad(parts.minute)}`
    };
  }

  function zonedDateTimeToMillis(dateValue, timeValue, timeZone) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateValue) || !/^\d{2}:\d{2}$/.test(timeValue)) {
      throw new Error("Choose complete start and end dates and times.");
    }
    if (!timeZoneIsValid(timeZone)) throw new Error("Enter a valid IANA time-zone name.");
    const [year, month, day] = dateValue.split("-").map(Number);
    const [hour, minute] = timeValue.split(":").map(Number);
    const intended = Date.UTC(year, month - 1, day, hour, minute);
    let timestamp = intended;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const actual = partsInTimeZone(timestamp, timeZone);
      const actualAsUtc = Date.UTC(
        actual.year, actual.month - 1, actual.day, actual.hour, actual.minute
      );
      const adjustment = intended - actualAsUtc;
      timestamp += adjustment;
      if (adjustment === 0) break;
    }
    const resolved = eventLocalDateTime(timestamp, timeZone);
    if (resolved.date !== dateValue || resolved.time !== timeValue) {
      throw new Error(`That local time does not exist in ${timeZone}. Choose another time.`);
    }
    return timestamp;
  }

  function formatEventDateTime(timestamp, timeZone) {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone
    }).format(new Date(timestamp));
  }

  function updateDateTimePreview() {
    const form = byId("event-form");
    const preview = byId("date-time-preview");
    const timeZone = normalizeTimeZone(form.elements.time_zone.value);
    form.elements.end_date.setCustomValidity("");
    form.elements.time_zone.setCustomValidity("");
    if (!form.elements.start_date.value || !form.elements.end_date.value) {
      preview.textContent = "Choose a start and end date to preview the event.";
      return;
    }
    try {
      const startsAt = zonedDateTimeToMillis(
        form.elements.start_date.value, form.elements.start_time.value, timeZone
      );
      const endsAt = zonedDateTimeToMillis(
        form.elements.end_date.value, form.elements.end_time.value, timeZone
      );
      if (endsAt <= startsAt) {
        form.elements.end_date.setCustomValidity("The event must end after it starts.");
        preview.textContent = "The event must end after it starts.";
        return;
      }
      preview.textContent = `${formatEventDateTime(startsAt, timeZone)} – ${formatEventDateTime(endsAt, timeZone)} · ${timeZone}`;
    } catch (error) {
      if (!timeZoneIsValid(timeZone)) {
        form.elements.time_zone.setCustomValidity(error.message);
      }
      preview.textContent = error.message;
    }
  }

  function setPublicPreviewImage(kind, source = "") {
    const image = byId(`public-brand-preview-${kind}`);
    const empty = byId(`public-brand-preview-${kind}-empty`);
    if (source) {
      image.src = source;
      image.hidden = false;
      if (empty) empty.hidden = true;
    } else {
      image.removeAttribute("src");
      image.hidden = true;
      if (empty) empty.hidden = false;
    }
  }

  function updatePublicBrandPreview() {
    const form = byId("event-form");
    const name = form.elements.name.value.trim();
    const accent = form.elements.accent_color.value || "#3159d9";
    const delivery = form.elements.delivery_mode.value.replace("_", " ");
    const location = form.elements.location.value.trim();
    const website = form.elements.website_url.value.trim();
    const card = byId("public-brand-preview-card");
    card.style.setProperty("--event-preview-accent", accent);
    byId("public-brand-preview-title").textContent = name || "Your event name";
    byId("public-brand-preview-monogram").textContent = (name || "AI").slice(0, 2).toUpperCase();
    const dateText = byId("date-time-preview").textContent;
    byId("public-brand-preview-date").textContent = dateText.startsWith("Choose")
      ? "Choose event dates"
      : dateText;
    byId("public-brand-preview-location").textContent = [location, delivery].filter(Boolean).join(" · ")
      || "Add a location or meeting URL";
    const websitePreview = byId("public-brand-preview-website");
    try {
      websitePreview.textContent = website ? new URL(website).hostname : "";
      websitePreview.hidden = !website;
    } catch {
      websitePreview.textContent = website;
      websitePreview.hidden = !website;
    }
  }

  function resetEventForm() {
    const form = byId("event-form");
    form.reset();
    state.submitting = false;
    state.createMutation = null;
    state.editingDraft = false;
    state.submitTargetStatus = "active";
    setDialogStatus();
    form.classList.remove("validation-attempted");
    for (const field of form.querySelectorAll('[aria-invalid="true"]')) field.removeAttribute("aria-invalid");
    byId("event-form-help").className = "result";
    byId("event-form-help").innerHTML = 'Required <span class="required-marker" aria-hidden="true">*</span>';
    form.elements.event_id.value = "";
    form.elements.version.value = "";
    form.elements.duplicate_source_event_id.value = "";
    form.elements.duplicate_source_version.value = "";
    form.elements.retain_source_logo.value = "false";
    form.elements.retain_source_cover.value = "false";
    form.elements.time_zone.value = browserTimeZone();
    form.elements.start_date.value = "";
    form.elements.start_time.value = "09:00";
    form.elements.end_date.value = "";
    form.elements.end_time.value = "17:00";
    form.elements.start_date.removeAttribute("min");
    form.elements.end_date.removeAttribute("min");
    form.elements.delivery_mode.value = "";
    form.elements.accent_color.value = "#3159d9";
    form.elements.logo_url.value = "";
    byId("event-logo-preview").removeAttribute("src");
    byId("event-logo-preview-frame").hidden = true;
    byId("event-logo-filename").textContent = "";
    byId("event-logo-status").textContent = "";
    byId("event-logo-status").className = "image-upload__status";
    byId("upload-event-logo").disabled = true;
    form.elements.cover_image_url.value = "";
    byId("event-cover-preview").removeAttribute("src");
    byId("event-cover-preview-frame").hidden = true;
    byId("event-cover-filename").textContent = "";
    byId("event-cover-status").textContent = "";
    byId("event-cover-status").className = "image-upload__status";
    byId("upload-event-cover").disabled = true;
    byId("save-event").disabled = false;
    byId("save-event-draft").disabled = false;
    byId("cancel-event-edit").disabled = false;
    byId("close-event-dialog").disabled = false;
    byId("event-form-heading").textContent = "Create an event";
    byId("save-event").textContent = "Create active event";
    byId("save-event-draft").hidden = false;
    byId("save-event-draft").textContent = "Save draft";
    byId("creation-action-note").hidden = false;
    byId("event-autosave-state").textContent = "Drafts autosave in this browser";
    byId("event-status-label").hidden = true;
    const advanced = form.querySelector(".advanced-settings");
    if (advanced) advanced.open = false;
    updateDateTimePreview();
    setPublicPreviewImage("logo");
    setPublicPreviewImage("cover");
    updatePublicBrandPreview();
  }

  function openEventDialog() {
    const dialog = byId("event-dialog");
    if (!dialog.open) dialog.showModal();
  }

  function closeEventDialog() {
    if (state.submitting) return;
    const dialog = byId("event-dialog");
    if (dialog.open) dialog.close();
    resetEventForm();
  }

  function editEvent(event) {
    const form = byId("event-form");
    form.elements.event_id.value = event.id;
    form.elements.version.value = String(event.version);
    form.elements.name.value = event.name;
    const timeZone = normalizeTimeZone(event.time_zone);
    const startsAt = eventLocalDateTime(event.starts_at_ms, timeZone);
    const endsAt = eventLocalDateTime(event.ends_at_ms, timeZone);
    form.elements.start_date.value = startsAt.date;
    form.elements.start_time.value = startsAt.time;
    form.elements.end_date.value = endsAt.date;
    form.elements.end_time.value = endsAt.time;
    form.elements.start_date.removeAttribute("min");
    form.elements.end_date.removeAttribute("min");
    form.elements.time_zone.value = timeZone;
    form.elements.delivery_mode.value = event.delivery_mode;
    form.elements.location.value = event.location || "";
    form.elements.description.value = event.description || "";
    window.SessionBuddyApi.refreshCharacterCounters(form);
    form.elements.email_sender_name.value = event.email_sender_name || "";
    form.elements.email_reply_to.value = event.email_reply_to || "";
    form.elements.accent_color.value = event.accent_color || "#3159d9";
    form.elements.logo_url.value = event.logo_url || "";
    if (event.logo_url) {
      byId("event-logo-preview").src = event.logo_url;
      byId("event-logo-preview-frame").hidden = false;
      byId("event-logo-filename").textContent = "Current logo";
      byId("event-logo-status").textContent = "Uploaded and saved.";
      byId("event-logo-status").className = "image-upload__status success";
    }
    form.elements.cover_image_url.value = event.cover_image_url || "";
    if (event.cover_image_url) {
      byId("event-cover-preview").src = event.cover_image_url;
      byId("event-cover-preview-frame").hidden = false;
      byId("event-cover-filename").textContent = "Current cover";
      byId("event-cover-status").textContent = "Uploaded and saved.";
      byId("event-cover-status").className = "image-upload__status success";
    }
    form.elements.website_url.value = event.website_url || "";
    form.elements.status.value = event.status;
    const archiveOption = form.elements.status.querySelector('option[value="archived"]');
    if (archiveOption) archiveOption.disabled = !state.adminEventIds.has(event.id);
    state.editingDraft = event.status === "draft";
    byId("event-form-heading").textContent = `Edit ${event.name}`;
    byId("save-event").textContent = "Save changes";
    byId("save-event-draft").hidden = !state.editingDraft;
    byId("creation-action-note").hidden = !state.editingDraft;
    byId("event-status-label").hidden = false;
    byId("event-autosave-state").textContent = state.editingDraft
      ? "Drafts autosave in this browser"
      : "Live event changes are not autosaved";
    updateDateTimePreview();
    setPublicPreviewImage("logo", event.logo_url || "");
    setPublicPreviewImage("cover", event.cover_image_url || "");
    updatePublicBrandPreview();
    openEventDialog();
    form.elements.name.focus();
  }

  function duplicateEvent(event) {
    resetEventForm();
    const form = byId("event-form");
    form.elements.duplicate_source_event_id.value = event.id;
    form.elements.duplicate_source_version.value = String(event.version);
    form.elements.name.value = `${event.name} copy`;
    const timeZone = normalizeTimeZone(event.time_zone);
    const startsAt = eventLocalDateTime(event.starts_at_ms, timeZone);
    const endsAt = eventLocalDateTime(event.ends_at_ms, timeZone);
    form.elements.start_date.value = startsAt.date;
    form.elements.start_time.value = startsAt.time;
    form.elements.end_date.value = endsAt.date;
    form.elements.end_time.value = endsAt.time;
    form.elements.time_zone.value = timeZone;
    form.elements.delivery_mode.value = event.delivery_mode;
    form.elements.location.value = event.location || "";
    form.elements.description.value = event.description || "";
    form.elements.email_sender_name.value = event.email_sender_name || "";
    form.elements.email_reply_to.value = event.email_reply_to || "";
    form.elements.accent_color.value = event.accent_color || "#3159d9";
    form.elements.website_url.value = event.website_url || "";
    if (event.logo_url) {
      form.elements.retain_source_logo.value = "true";
      byId("event-logo-filename").textContent = "Current logo";
      byId("event-logo-status").textContent = "Will be copied to the new event.";
    }
    if (event.cover_image_url) {
      form.elements.retain_source_cover.value = "true";
      byId("event-cover-filename").textContent = "Current cover";
      byId("event-cover-status").textContent = "Will be copied to the new event.";
    }
    window.SessionBuddyApi.refreshCharacterCounters(form);
    byId("event-form-heading").textContent = `Duplicate ${event.name}`;
    byId("save-event").textContent = "Create active event";
    byId("save-event-draft").hidden = false;
    byId("creation-action-note").hidden = false;
    updateDateTimePreview();
    setPublicPreviewImage("logo", event.logo_url || "");
    setPublicPreviewImage("cover", event.cover_image_url || "");
    updatePublicBrandPreview();
    openEventDialog();
    form.elements.name.focus();
  }

  function eventItem(event) {
    const item = document.createElement("div");
    item.className = "event-table-row";
    item.setAttribute("role", "row");
    const timeZone = normalizeTimeZone(event.time_zone);
    const eventDate = eventLocalDateTime(event.starts_at_ms, timeZone).date;
    const [year, month, day] = eventDate.split("-").map(Number);
    const monthLabel = new Intl.DateTimeFormat(undefined, { month: "short", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
    const identity = document.createElement("div");
    identity.className = "event-table-cell event-table-event";
    identity.setAttribute("role", "cell");
    const monogram = document.createElement("span");
    monogram.className = "event-monogram";
    monogram.textContent = event.name.trim().slice(0, 2).toUpperCase();
    monogram.setAttribute("aria-hidden", "true");
    const content = document.createElement("div");
    const heading = document.createElement("h3");
    const overview = document.createElement("a");
    overview.textContent = event.name;
    overview.href = `/admin/events/${encodeURIComponent(event.id)}`;
    heading.append(overview);
    const details = document.createElement("p");
    details.textContent = [event.location, event.delivery_mode?.replace("_", " ")].filter(Boolean).join(" · ");
    content.append(heading, details);
    identity.append(monogram, content);
    const date = document.createElement("div");
    date.className = "event-table-cell event-table-date";
    date.setAttribute("role", "cell");
    const datePrimary = document.createElement("strong");
    datePrimary.textContent = `${monthLabel} ${day}, ${year}`;
    const dateSecondary = document.createElement("small");
    dateSecondary.textContent = timeZone;
    date.append(datePrimary, dateSecondary);
    const statusCell = document.createElement("div");
    statusCell.className = "event-table-cell event-table-status";
    statusCell.setAttribute("role", "cell");
    const badge = document.createElement("span");
    badge.className = `event-status event-status--${event.status}`;
    badge.textContent = event.status;
    statusCell.append(badge);
    const cfpCell = document.createElement("div");
    cfpCell.className = "event-table-cell event-table-cfp";
    cfpCell.setAttribute("role", "cell");
    const proposalCount = document.createElement("span");
    const proposals = Number.isInteger(event.proposal_count) ? event.proposal_count : 0;
    proposalCount.textContent = `${proposals} ${proposals === 1 ? "proposal" : "proposals"}`;
    const cfp = link("Manage CFP", `/admin/events/${encodeURIComponent(event.id)}/cfp`);
    cfp.classList.add("event-action--cfp");
    cfpCell.append(cfp, proposalCount);
    const actions = document.createElement("div");
    actions.className = "event-table-cell event-table-actions";
    actions.setAttribute("role", "cell");
    const duplicate = button("Duplicate", () => duplicateEvent(event));
    duplicate.setAttribute("aria-label", `Duplicate ${event.name} as a draft`);
    duplicate.hidden = !state.adminOrganizationIds?.has(event.organization_id);
    const more = document.createElement("details");
    more.className = "event-row-more";
    more.hidden = duplicate.hidden;
    const moreTrigger = document.createElement("summary");
    moreTrigger.setAttribute("aria-label", `More actions for ${event.name}`);
    moreTrigger.textContent = "•••";
    const moreMenu = document.createElement("div");
    moreMenu.className = "event-row-more__menu";
    const edit = button("Edit", () => {
      more.open = false;
      editEvent(event);
    });
    moreMenu.append(duplicate);
    more.append(moreTrigger, moreMenu);
    actions.append(
      edit,
      more
    );
    item.append(identity, date, statusCell, cfpCell, actions);
    return item;
  }

  function visibleEvents() {
    // The loaded set already reflects the server-side view filter and search
    // query; re-filtering locally could only hide rows the server returned.
    return [...state.events.values()];
  }

  function renderEventList() {
    const events = visibleEvents();
    const list = byId("event-list");
    const viewLabels = { active: "Active", draft: "Draft", past: "Past", all: "All" };
    byId("event-list-title").textContent = `${viewLabels[state.eventFilter] || "All"} events`;
    list.replaceChildren(...events.map(eventItem));
    byId("event-count").textContent = `${events.length}${state.nextCursor ? "+" : ""}`;
    byId("load-more-events").hidden = !state.nextCursor;
    const empty = byId("event-list-empty");
    empty.hidden = events.length > 0;
    if (!events.length) {
      if (state.eventSearch) empty.textContent = `No events match “${state.eventSearch}”. Try another search.`;
      else if (state.eventFilter !== "all") empty.textContent = `No ${state.eventFilter === "draft" ? "draft" : state.eventFilter} events in this view.`;
      else empty.textContent = "No events yet. Create your first event.";
    }
  }

  function showOrganization() {
    const organization = state.organizations.get(state.organizationId);
    byId("organization-title").textContent = organization?.name || "Organization";
  }

  function fetchEventsPage(organizationId, cursor) {
    // Filters and search run on the SERVER so older events beyond the loaded
    // pages can never look "missing" from a filtered view.
    const params = new URLSearchParams();
    params.set("view", state.eventFilter);
    params.set("order", state.eventOrder);
    if (state.eventSearch) params.set("q", state.eventSearch);
    if (cursor) params.set("cursor", cursor);
    return api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/events?${params}`);
  }


  async function loadEvents(organizationId) {
    const requestId = ++state.eventsRequestId;
    state.organizationId = organizationId;
    showOrganization();
    resetEventForm();
    // One page at a time: the server paginates and the user asks for more.
    let result;
    try {
      result = await fetchEventsPage(organizationId, null);
    } catch (error) {
      if (requestId !== state.eventsRequestId) return false;
      throw error;
    }
    if (requestId !== state.eventsRequestId) return false;
    state.events = new Map(result.data.map((event) => [event.id, event]));
    state.nextCursor = result.next_cursor;
    renderEventList();
    return true;
  }

  async function loadMoreEvents() {
    if (!state.nextCursor) return;
    const requestId = state.eventsRequestId;
    const cursor = state.nextCursor;
    const button = byId("load-more-events");
    button.disabled = true;
    try {
      const result = await fetchEventsPage(state.organizationId, cursor);
      if (requestId !== state.eventsRequestId) return;
      for (const event of result.data) state.events.set(event.id, event);
      state.nextCursor = result.next_cursor;
      renderEventList();
    } catch (error) {
      if (window.SessionBuddyApi.isStaleCursor(error)) {
        state.nextCursor = null;
        await loadEvents(state.organizationId);
        setStatus(window.SessionBuddyApi.messageWithReference("The event list changed while you were away. Showing the latest events from the beginning.", error));
      } else setStatus(window.SessionBuddyApi.message(error), true);
    } finally {
      button.disabled = false;
    }
  }

  async function initialize() {
    const session = await api("/api/v1/auth/session");
    state.userId = session.user_id;
    state.csrf = session.csrf_token;
    state.emailDefaults = {
      name: session.default_email_sender_name || "SessionBuddy",
      address: session.default_email_address || "events@example.test"
    };
    const defaultIdentity = `${state.emailDefaults.name} <${state.emailDefaults.address}>`;
    byId("event-form").elements.email_sender_name.placeholder = state.emailDefaults.name;
    byId("event-form").elements.email_reply_to.placeholder = state.emailDefaults.address;
    byId("event-email-default").textContent = `Blank uses ${defaultIdentity}.`;
    // Creating events needs exact management of the selected organization.
    // Resource permissions are separate from account personas and do not
    // cascade from any other organization.
    state.adminOrganizationIds = new Set(
      (session.organization_access || [])
        .filter((item) => (item.permissions || []).some((permission) =>
          ["owner", "manage"].includes(permission)))
        .map((item) => item.organization_id)
    );
    state.adminEventIds = new Set(
      (session.event_access || [])
        .filter((item) => (item.permissions || []).some((permission) =>
          ["owner", "manage"].includes(permission)))
        .map((item) => item.event_id)
    );
    const result = await api("/api/v1/admin/organizations");
    state.organizations = new Map(result.data.map((organization) => [organization.id, organization]));
    const select = byId("organization");
    select.replaceChildren();
    for (const organization of result.data) select.add(new Option(organization.name, organization.id));
    if (!result.data.length) throw new Error("This account does not administer an organization.");
    const requestedOrganization = new URLSearchParams(location.search).get("organization_id");
    if (requestedOrganization && state.organizations.has(requestedOrganization)) select.value = requestedOrganization;
    else if (session.organization_id && state.organizations.has(session.organization_id)) select.value = session.organization_id;
    byId("organization-picker").hidden = result.data.length === 1;
    updateCreateAccess(select.value);
    await loadEvents(select.value);
    setStatus("");
    if (location.hash === "#event-form" && !byId("new-event").hidden) {
      resetEventForm();
      openEventDialog();
    }
    restoreEventDraft();
  }

  byId("new-event").addEventListener("click", () => {
    resetEventForm();
    openEventDialog();
    byId("event-form").elements.name.focus();
  });

  function updateCreateAccess(organizationId) {
    byId("new-event").hidden = !state.adminOrganizationIds?.has(organizationId);
  }

  byId("load-more-events").addEventListener("click", () => { loadMoreEvents(); });

  byId("organization").addEventListener("change", (event) => {
    updateCreateAccess(event.currentTarget.value);
    loadEvents(event.currentTarget.value)
      .then(() => setStatus(""))
      .catch((error) => setStatus(error.message, true));
  });
  function requeryEvents() {
    loadEvents(state.organizationId)
      .then((applied) => { if (applied) setStatus(""); })
      .catch((error) => setStatus(error.message, true));
  }
  document.querySelectorAll("[data-event-filter]").forEach((button) => button.addEventListener("click", () => {
    state.eventFilter = button.dataset.eventFilter;
    document.querySelectorAll("[data-event-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    requeryEvents();
  }));
  let searchDebounce = 0;
  byId("event-search").addEventListener("input", (event) => {
    state.eventSearch = event.currentTarget.value.trim();
    // Invalidate an in-flight response immediately, before the debounce fires.
    ++state.eventsRequestId;
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(requeryEvents, 250);
  });
  byId("event-sort").addEventListener("change", (event) => {
    state.eventOrder = event.currentTarget.value;
    requeryEvents();
  });

  byId("event-form").addEventListener("input", (event) => {
    event.target.setCustomValidity?.("");
    if (event.target.validity?.valid) event.target.removeAttribute("aria-invalid");
    updateDateTimePreview();
    updatePublicBrandPreview();
    queueEventDraft(event.currentTarget);
  });
  byId("event-form").addEventListener("invalid", (event) => {
    event.target.setAttribute("aria-invalid", "true");
  }, true);
  byId("save-event").addEventListener("click", () => {
    const form = byId("event-form");
    form.classList.add("validation-attempted");
    if (form.checkValidity()) return;
    const help = byId("event-form-help");
    help.className = "status error event-form-error";
    help.textContent = `Complete the highlighted fields before ${form.elements.event_id.value ? "saving changes" : "creating the event"}.`;
    const firstInvalid = form.querySelector(":invalid");
    firstInvalid?.focus();
    firstInvalid?.reportValidity();
  });
  byId("event-form").elements.start_date.addEventListener("change", (event) => {
    const form = event.currentTarget.form;
    if (!form.elements.end_date.value) form.elements.end_date.value = event.currentTarget.value;
    updateDateTimePreview();
    updatePublicBrandPreview();
  });
  byId("event-form").elements.time_zone.addEventListener("change", (event) => {
    event.currentTarget.value = normalizeTimeZone(event.currentTarget.value);
    updateDateTimePreview();
    updatePublicBrandPreview();
  });

  for (const kind of ["logo", "cover"]) {
    byId(`public-brand-preview-${kind}`).addEventListener("error", () => setPublicPreviewImage(kind));
  }

  byId("event-form").elements.logo_file.addEventListener("change", (event) => {
    const input = event.currentTarget;
    const file = input.files[0];
    const message = file && !logoRules[file.type]
      ? "Choose a PNG, JPEG, or WebP image."
      : file && file.size > logoRules[file.type]
        ? "Choose an image no larger than 2 MB."
        : "";
    input.setCustomValidity(message);
    byId("upload-event-logo").disabled = !file || Boolean(message);
    event.currentTarget.form.elements.logo_url.value = "";
    byId("event-logo-status").textContent = message || (file ? "Ready to upload." : "");
    byId("event-logo-status").className = `image-upload__status${message ? " error" : ""}`;
    updateSaveAvailability();
    if (!file || message) return;
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const source = String(reader.result || "");
      byId("event-logo-preview").src = source;
      byId("event-logo-preview-frame").hidden = false;
      byId("event-logo-filename").textContent = file.name;
      setPublicPreviewImage("logo", source);
    }, { once: true });
    reader.readAsDataURL(file);
  });

  byId("event-form").elements.cover_file.addEventListener("change", (event) => {
    const input = event.currentTarget;
    const file = input.files[0];
    const message = file && !logoRules[file.type]
      ? "Choose a PNG, JPEG, or WebP image."
      : file && file.size > logoRules[file.type]
        ? "Choose an image no larger than 2 MB."
        : "";
    input.setCustomValidity(message);
    byId("upload-event-cover").disabled = !file || Boolean(message);
    event.currentTarget.form.elements.cover_image_url.value = "";
    byId("event-cover-status").textContent = message || (file ? "Ready to upload." : "");
    byId("event-cover-status").className = `image-upload__status${message ? " error" : ""}`;
    updateSaveAvailability();
    if (!file || message) return;
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const source = String(reader.result || "");
      byId("event-cover-preview").src = source;
      byId("event-cover-preview-frame").hidden = false;
      byId("event-cover-filename").textContent = file.name;
      setPublicPreviewImage("cover", source);
    }, { once: true });
    reader.readAsDataURL(file);
  });

  async function uploadEventAsset(kind, file) {
    return api(`/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/event-assets/${kind}`, {
      method: "POST",
      headers: { "content-type": file.type, "x-csrf-token": state.csrf },
      body: file
    });
  }

  async function uploadSelectedAsset(kind) {
    const form = byId("event-form");
    const isLogo = kind === "logo";
    const input = form.elements[isLogo ? "logo_file" : "cover_file"];
    const button = byId(isLogo ? "upload-event-logo" : "upload-event-cover");
    const status = byId(isLogo ? "event-logo-status" : "event-cover-status");
    const file = input.files[0];
    if (!file || !input.checkValidity()) return;
    button.disabled = true;
    status.textContent = "Uploading…";
    status.className = "image-upload__status";
    try {
      const uploaded = await uploadEventAsset(kind, file);
      form.elements[isLogo ? "logo_url" : "cover_image_url"].value = uploaded.asset_url;
      form.elements[isLogo ? "retain_source_logo" : "retain_source_cover"].value = "false";
      input.value = "";
      updateSaveAvailability();
      status.textContent = "Uploaded.";
      status.className = "image-upload__status success";
    } catch (error) {
      button.disabled = false;
      status.textContent = error.message;
      status.className = "image-upload__status error";
    }
  }

  byId("upload-event-logo").addEventListener("click", () => uploadSelectedAsset("logo"));
  byId("upload-event-cover").addEventListener("click", () => uploadSelectedAsset("cover"));

  byId("event-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const eventId = values.event_id;
    const duplicateSourceId = values.duplicate_source_event_id;
    const createStatus = event.submitter?.value === "draft" ? "draft" : "active";
    const intendedStatus = eventId
      ? state.editingDraft
        ? values.status === "archived" ? "archived" : createStatus
        : values.status || state.events.get(eventId)?.status
      : createStatus;
    const currentEvent = eventId ? state.events.get(eventId) : null;
    if (currentEvent?.status === "active" && intendedStatus === "archived" && state.archiveConfirmedEventId !== eventId) {
      byId("event-archive-title").textContent = `Archive ${currentEvent.name}?`;
      byId("event-archive-dialog").dataset.eventId = eventId;
      byId("event-archive-dialog").showModal();
      return;
    }
    state.archiveConfirmedEventId = "";
    state.submitTargetStatus = intendedStatus;
    if (state.submitting) return;
    try {
      for (const name of ["website_url"]) {
        const input = form.elements[name];
        input.setCustomValidity(securePublicUrl(values[name]) ? "" : "Use a complete HTTPS URL without embedded credentials.");
        if (!input.reportValidity()) return;
      }
      const timeZone = normalizeTimeZone(values.time_zone);
      const startsAt = zonedDateTimeToMillis(values.start_date, values.start_time, timeZone);
      const endsAt = zonedDateTimeToMillis(values.end_date, values.end_time, timeZone);
      if (endsAt <= startsAt) {
        form.elements.end_date.setCustomValidity("The event must end after it starts.");
        form.elements.end_date.reportValidity();
        updateDateTimePreview();
        return;
      }
      if (intendedStatus === "active" && endsAt <= Date.now()) {
        form.elements.end_date.setCustomValidity("Update the event dates before activating.");
        form.elements.end_date.setAttribute("aria-invalid", "true");
        form.elements.end_date.reportValidity();
        return;
      }
      const body = {
        name: values.name,
        starts_at_ms: startsAt,
        ends_at_ms: endsAt,
        time_zone: timeZone,
        delivery_mode: values.delivery_mode,
        location: values.location.trim(),
        description: values.description.trim(),
        email_sender_name: values.email_sender_name || null,
        email_reply_to: values.email_reply_to || null,
        accent_color: values.accent_color || "#3159d9",
        logo_url: values.logo_url || null,
        cover_image_url: values.cover_image_url || null,
        website_url: values.website_url || null
      };
      if (duplicateSourceId) {
        body.source_version = Number(values.duplicate_source_version);
        body.retain_source_logo = values.retain_source_logo === "true";
        body.retain_source_cover = values.retain_source_cover === "true";
      }
      if (!eventId) body.status = createStatus;
      if (eventId) {
        body.version = Number(values.version);
        body.status = intendedStatus;
      }
      if (form.elements.logo_file.files[0] || form.elements.cover_file.files[0]) {
        setDialogStatus(`Upload the selected logo or cover before ${eventId ? "saving changes" : "creating the event"}.`, true);
        return;
      }
      setDialogStatus(eventId && !state.editingDraft ? "Saving changes…" : createStatus === "draft" ? "Saving draft…" : "Creating event…");
      setSubmitting(true, Boolean(eventId));
      const fingerprint = JSON.stringify(body);
      if (!eventId && (!state.createMutation || state.createMutation.fingerprint !== fingerprint)) {
        state.createMutation = { fingerprint, key: mutationToken() };
      }
      const headers = { "content-type": "application/json", "x-csrf-token": state.csrf };
      if (!eventId) headers["idempotency-key"] = state.createMutation.key;
      await api(
        eventId
          ? `/api/v1/admin/events/${encodeURIComponent(eventId)}`
          : duplicateSourceId
            ? `/api/v1/admin/events/${encodeURIComponent(duplicateSourceId)}/duplicate`
          : `/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/events`,
        {
          method: eventId ? "PATCH" : "POST",
          headers,
          body: JSON.stringify(body)
        }
      );
      sessionStorage.removeItem(eventDraftKey());
      setStatus(eventId && currentEvent?.status !== "archived" && intendedStatus === "archived"
        ? "Event archived."
        : eventId && currentEvent?.status === "archived" && intendedStatus === "active"
          ? "Event activated."
        : eventId && !state.editingDraft
          ? "Event updated."
          : createStatus === "draft" ? "Draft saved." : "Event created.");
      await loadEvents(state.organizationId);
      closeEventDialog();
    } catch (error) {
      if (!timeZoneIsValid(normalizeTimeZone(values.time_zone))) {
        form.elements.time_zone.setCustomValidity(error.message);
        form.elements.time_zone.reportValidity();
        updateDateTimePreview();
        return;
      }
      if (error.status === 401) {
        preserveEventDraft(form);
        window.SessionBuddyApi.redirectIfSignedOut(error);
      } else {
        const message = error.status === 409
          ? eventId
            ? "This event changed elsewhere. Reload the page before saving again so you do not overwrite someone else's work."
            : duplicateSourceId
              ? "The source event changed while this form was open. Close it and choose Duplicate again to review the latest details."
              : "Event creation is still being processed. Try again to safely check the same request."
          : error.status === 403
            ? "Your access changed while this form was open. Ask an administrator to restore event management access."
            : error.message;
        setDialogStatus(message, true);
      }
    } finally {
      setSubmitting(false, Boolean(eventId));
    }
  });

  byId("cancel-event-edit").addEventListener("click", closeEventDialog);
  byId("close-event-dialog").addEventListener("click", closeEventDialog);
  byId("event-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeEventDialog();
  });
  byId("event-archive-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const dialog = byId("event-archive-dialog");
    state.archiveConfirmedEventId = dialog.dataset.eventId || "";
    dialog.close();
    byId("event-form").requestSubmit(byId("save-event"));
  });
  byId("cancel-event-archive").addEventListener("click", () => {
    state.archiveConfirmedEventId = "";
    byId("event-archive-dialog").close();
  });
  byId("event-archive-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    state.archiveConfirmedEventId = "";
    byId("event-archive-dialog").close();
  });

  populateTimeZones();
  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true);
  });
})();

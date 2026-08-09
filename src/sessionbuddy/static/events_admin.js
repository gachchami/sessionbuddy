(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const state = { csrf: "", organizationId: "", organizations: new Map(), events: new Map() };
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

  function updateSaveAvailability() {
    const form = byId("event-form");
    const pending = Boolean(form.elements.logo_file.files[0] || form.elements.cover_file.files[0]);
    byId("save-event").disabled = pending;
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

  function resetEventForm() {
    const form = byId("event-form");
    form.reset();
    form.classList.remove("validation-attempted");
    for (const field of form.querySelectorAll('[aria-invalid="true"]')) field.removeAttribute("aria-invalid");
    byId("event-form-help").className = "result";
    byId("event-form-help").innerHTML = 'Fields marked <span class="required-marker" aria-hidden="true">*</span> are required.';
    form.elements.event_id.value = "";
    form.elements.version.value = "";
    form.elements.time_zone.value = browserTimeZone();
    form.elements.start_date.value = "";
    form.elements.start_time.value = "09:00";
    form.elements.end_date.value = "";
    form.elements.end_time.value = "17:00";
    const minimumDate = eventLocalDateTime(Date.now(), form.elements.time_zone.value).date;
    form.elements.start_date.min = minimumDate;
    form.elements.end_date.min = minimumDate;
    form.elements.delivery_mode.value = "";
    form.elements.accent_color.value = "#3159d9";
    form.elements.logo_url.value = "";
    byId("event-logo-preview").removeAttribute("src");
    byId("event-logo-preview-frame").hidden = true;
    byId("event-logo-filename").textContent = "";
    byId("event-logo-status").textContent = "No file selected.";
    byId("event-logo-status").className = "image-upload__status";
    byId("upload-event-logo").disabled = true;
    form.elements.cover_image_url.value = "";
    byId("event-cover-preview").removeAttribute("src");
    byId("event-cover-preview-frame").hidden = true;
    byId("event-cover-filename").textContent = "";
    byId("event-cover-status").textContent = "No file selected.";
    byId("event-cover-status").className = "image-upload__status";
    byId("upload-event-cover").disabled = true;
    byId("save-event").disabled = false;
    byId("event-form-heading").textContent = "Create an event";
    byId("save-event").textContent = "Create event";
    byId("event-status-label").hidden = true;
    const advanced = form.querySelector(".advanced-settings");
    if (advanced) advanced.open = false;
    updateDateTimePreview();
  }

  function openEventDialog() {
    const dialog = byId("event-dialog");
    if (!dialog.open) dialog.showModal();
  }

  function closeEventDialog() {
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
    byId("event-form-heading").textContent = `Edit ${event.name}`;
    byId("save-event").textContent = "Update event";
    byId("event-status-label").hidden = false;
    updateDateTimePreview();
    openEventDialog();
    form.elements.name.focus();
  }

  function eventItem(event) {
    const item = document.createElement("article");
    item.className = "entity-card";
    const top = document.createElement("div");
    top.className = "entity-card__top";
    const kind = document.createElement("span");
    kind.className = "eyebrow";
    kind.textContent = "Event";
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = event.status;
    top.append(kind, badge);
    const heading = document.createElement("h3");
    const overview = document.createElement("a");
    overview.textContent = event.name;
    overview.href = `/admin/events/${encodeURIComponent(event.id)}`;
    heading.append(overview);
    const details = document.createElement("p");
    details.className = "result";
    const timeZone = normalizeTimeZone(event.time_zone);
    details.textContent = `${formatEventDateTime(event.starts_at_ms, timeZone)} · ${timeZone}`;
    const actions = document.createElement("div");
    actions.className = "actions entity-card__action";
    actions.append(
      link("Open", `/admin/events/${encodeURIComponent(event.id)}`),
      button("Edit", () => editEvent(event))
    );
    item.append(top, heading, details, actions);
    return item;
  }

  function showOrganization() {
    const organization = state.organizations.get(state.organizationId);
    const form = byId("organization-form");
    byId("organization-title").textContent = organization?.name || "Organization";
    form.elements.organization_id.value = organization?.id || "";
    form.elements.name.value = organization?.name || "";
    form.elements.version.value = organization ? String(organization.version) : "";
  }

  async function loadEvents(organizationId) {
    state.organizationId = organizationId;
    showOrganization();
    resetEventForm();
    const result = await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/events`);
    state.events = new Map(result.data.map((event) => [event.id, event]));
    const list = byId("event-list");
    list.replaceChildren();
    for (const event of result.data) list.append(eventItem(event));
    byId("event-count").textContent = String(result.data.length);
    if (!result.data.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No events yet. Create your first event.";
      list.append(empty);
    }
  }

  async function initialize() {
    const session = await api("/api/v1/auth/session");
    state.csrf = session.csrf_token;
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
    await loadEvents(select.value);
    setStatus("Events are up to date.");
    if (location.hash === "#event-form") {
      resetEventForm();
      openEventDialog();
    }
  }

  byId("new-event").addEventListener("click", () => {
    resetEventForm();
    openEventDialog();
    byId("event-form").elements.name.focus();
  });

  byId("edit-organization").addEventListener("click", () => {
    showOrganization();
    byId("organization-dialog").showModal();
    byId("organization-form").elements.name.focus();
  });

  const closeOrganizationDialog = () => {
    if (byId("organization-dialog").open) byId("organization-dialog").close();
  };
  byId("close-organization").addEventListener("click", closeOrganizationDialog);
  byId("cancel-organization").addEventListener("click", closeOrganizationDialog);

  byId("organization").addEventListener("change", (event) => {
    loadEvents(event.currentTarget.value)
      .then(() => setStatus("Organization selected."))
      .catch((error) => setStatus(error.message, true));
  });

  byId("organization-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const organization = await api(
        `/api/v1/admin/organizations/${encodeURIComponent(values.organization_id)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
          body: JSON.stringify({ name: values.name, version: Number(values.version) })
        }
      );
      state.organizations.set(organization.id, organization);
      byId("organization").selectedOptions[0].textContent = organization.name;
      showOrganization();
      closeOrganizationDialog();
      setStatus("Organization updated.");
    } catch (error) {
      setStatus(error.status === 409 ? "The organization changed elsewhere. Reload and try again." : error.message, true);
    }
  });

  byId("event-form").addEventListener("input", (event) => {
    event.target.setCustomValidity?.("");
    if (event.target.validity?.valid) event.target.removeAttribute("aria-invalid");
    updateDateTimePreview();
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
    help.textContent = "Complete the highlighted fields before creating the event.";
    const firstInvalid = form.querySelector(":invalid");
    firstInvalid?.focus();
    firstInvalid?.reportValidity();
  });
  byId("event-form").elements.start_date.addEventListener("change", (event) => {
    const form = event.currentTarget.form;
    if (!form.elements.end_date.value) form.elements.end_date.value = event.currentTarget.value;
    updateDateTimePreview();
  });
  byId("event-form").elements.time_zone.addEventListener("change", (event) => {
    event.currentTarget.value = normalizeTimeZone(event.currentTarget.value);
    if (!event.currentTarget.form.elements.event_id.value) {
      const minimumDate = eventLocalDateTime(Date.now(), event.currentTarget.value).date;
      event.currentTarget.form.elements.start_date.min = minimumDate;
      event.currentTarget.form.elements.end_date.min = minimumDate;
    }
    updateDateTimePreview();
  });

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
    byId("event-logo-status").textContent = message || (file ? "Preview ready. Click Upload to store this logo." : "No file selected.");
    byId("event-logo-status").className = `image-upload__status${message ? " error" : ""}`;
    updateSaveAvailability();
    if (!file || message) return;
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      byId("event-logo-preview").src = String(reader.result || "");
      byId("event-logo-preview-frame").hidden = false;
      byId("event-logo-filename").textContent = file.name;
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
    byId("event-cover-status").textContent = message || (file ? "Preview ready. Click Upload to store this cover." : "No file selected.");
    byId("event-cover-status").className = `image-upload__status${message ? " error" : ""}`;
    updateSaveAvailability();
    if (!file || message) return;
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      byId("event-cover-preview").src = String(reader.result || "");
      byId("event-cover-preview-frame").hidden = false;
      byId("event-cover-filename").textContent = file.name;
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
      input.value = "";
      updateSaveAvailability();
      status.textContent = "Uploaded. This image will be saved with the event.";
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
    try {
      for (const name of ["website_url"]) {
        const input = form.elements[name];
        input.setCustomValidity(securePublicUrl(values[name]) ? "" : "Use a complete HTTPS URL without embedded credentials.");
        if (!input.reportValidity()) return;
      }
      const timeZone = normalizeTimeZone(values.time_zone);
      const startsAt = zonedDateTimeToMillis(values.start_date, values.start_time, timeZone);
      const endsAt = zonedDateTimeToMillis(values.end_date, values.end_time, timeZone);
      if (!eventId && startsAt <= Date.now()) {
        form.elements.start_date.setCustomValidity("A new event must start in the future.");
        form.elements.start_date.setAttribute("aria-invalid", "true");
        form.elements.start_date.reportValidity();
        return;
      }
      if (endsAt <= startsAt) {
        form.elements.end_date.setCustomValidity("The event must end after it starts.");
        form.elements.end_date.reportValidity();
        updateDateTimePreview();
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
      if (eventId) {
        body.version = Number(values.version);
        body.status = values.status;
      }
      if (form.elements.logo_file.files[0] || form.elements.cover_file.files[0]) {
        setStatus("Upload the selected logo or cover before creating the event.", true);
        return;
      }
      await api(
        eventId
          ? `/api/v1/admin/events/${encodeURIComponent(eventId)}`
          : `/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/events`,
        {
          method: eventId ? "PATCH" : "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
          body: JSON.stringify(body)
        }
      );
      setStatus(eventId ? "Event updated." : "Event created.");
      await loadEvents(state.organizationId);
      closeEventDialog();
    } catch (error) {
      if (!timeZoneIsValid(normalizeTimeZone(values.time_zone))) {
        form.elements.time_zone.setCustomValidity(error.message);
        form.elements.time_zone.reportValidity();
        updateDateTimePreview();
        return;
      }
      setStatus(error.status === 409 ? "The event changed elsewhere. Reload and try again." : error.message, true);
    }
  });

  byId("cancel-event-edit").addEventListener("click", closeEventDialog);
  byId("close-event-dialog").addEventListener("click", closeEventDialog);
  byId("event-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeEventDialog();
  });

  populateTimeZones();
  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true);
  });
})();

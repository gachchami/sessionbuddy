(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  const form = byId("event-editor-form");
  const fields = ["name", "delivery_mode", "location", "description", "time_zone", "start_date", "start_time", "end_date", "end_time", "accent_color", "website_url", "logo_url", "cover_image_url", "email_sender_name", "email_reply_to"];
  const labels = { name: "Event name", delivery_mode: "Attendance format", location: "Location", description: "Description", time_zone: "Time zone", start_date: "Start date", start_time: "Start time", end_date: "End date", end_time: "End time", accent_color: "Accent color", website_url: "Event website", logo_url: "Event logo", cover_image_url: "Event cover", email_sender_name: "Sender name", email_reply_to: "Reply-to email" };
  const aliases = new Map([["Asia/Calcutta", "Asia/Kolkata"], ["Europe/Kiev", "Europe/Kyiv"], ["America/Godthab", "America/Nuuk"], ["Asia/Saigon", "Asia/Ho_Chi_Minh"], ["Pacific/Enderbury", "Pacific/Kanton"], ["Pacific/Truk", "Pacific/Chuuk"], ["Pacific/Ponape", "Pacific/Pohnpei"], ["Etc/UTC", "UTC"]]);
  const state = { mode: "create", session: null, event: null, source: null, organizationId: "", baseline: {}, latest: {}, conflicts: new Map(), dirty: false, busy: false, readOnly: false, mutation: null, intent: "save", unsavedUploads: new Set() };

  window.__sessionbuddyTelemetryDraft = window.__sessionbuddyTelemetryDraft || {};
  window.__sessionbuddyTelemetryDraft.page_template = location.pathname.endsWith("/settings") ? "/admin/events/{event_id}/settings" : "/admin/events/new";

  function normalizeTimeZone(value) { const trimmed = String(value || "").trim(); return aliases.get(trimmed) || trimmed; }
  function browserTimeZone() { return normalizeTimeZone(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"); }
  // The time zone control stays a flat <select>: grouped options vanish from
  // accessibility snapshots (tests/release_readiness/test_select_options_stay_snapshot_visible.py),
  // so the region order and "Region/City" labels carry the grouping instead.
  const REGION_ORDER = ["UTC", "Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific", "Other"];
  const SECTION_IDS = ["general", "date-time", "branding", "email", "lifecycle"];
  const STATUS_LABELS = { draft: "Draft", active: "Active", archived: "Archived" };
  const DELIVERY_LABELS = { in_person: "In person", virtual: "Virtual", hybrid: "Hybrid" };
  const pad2 = (value) => String(value).padStart(2, "0");
  let zoneCatalog = [];
  const zoneOffsets = new Map();
  function zoneRegion(zone) { if (zone === "UTC") return "UTC"; const head = zone.split("/")[0]; return zone.includes("/") && REGION_ORDER.includes(head) ? head : "Other"; }
  function populateTimeZones() {
    const supported = typeof Intl.supportedValuesOf === "function"
      ? Intl.supportedValuesOf("timeZone")
      : ["UTC", "America/Los_Angeles", "America/New_York", "Europe/London", "Europe/Paris", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney"];
    const zones = [...new Set(["UTC", ...supported.map(normalizeTimeZone)])];
    zoneCatalog = zones
      .map((zone) => ({ value: zone, label: zone.replaceAll("_", " "), region: zoneRegion(zone), search: zone.toLowerCase().replaceAll("_", " ").replaceAll("/", " ") }))
      .sort((a, b) => REGION_ORDER.indexOf(a.region) - REGION_ORDER.indexOf(b.region) || a.label.localeCompare(b.label));
    renderZoneOptions("");
  }
  // Rebuilds the option list for a filter query. The selected value is always
  // kept, and so is every zone a discard, reconcile, or "Use latest" may assign
  // (baseline, latest server value, conflict), so those direct assignments can
  // never land on a missing option. The select's value never changes here, so
  // filtering neither dirties the form nor fires events.
  function renderZoneOptions(query = "") {
    const select = form.elements.time_zone; const current = select.value;
    const q = String(query || "").trim().toLowerCase().replaceAll("_", " ").replaceAll("/", " ");
    const matches = q ? zoneCatalog.filter((zone) => zone.search.includes(q) || offsetMatches(zone.value, q)) : zoneCatalog;
    const options = matches.map((zone) => new Option(zone.label, zone.value));
    const keep = new Set([current, state.baseline.time_zone, state.latest.time_zone, state.conflicts.get("time_zone")?.latest].filter(Boolean));
    for (const value of keep) if (!options.some((option) => option.value === value)) options.push(new Option(value.replaceAll("_", " "), value));
    select.replaceChildren(...options); if (current) select.value = current;
    byId("time-zone-filter-status").textContent = q ? (matches.length === 1 ? `1 of ${zoneCatalog.length} zones match · Enter selects it` : matches.length ? `${matches.length} of ${zoneCatalog.length} zones match` : "No zones match. Clear the search to see every zone.") : "";
    return matches;
  }
  function resetZoneFilter() { const filter = byId("time-zone-filter"); if (!filter.value) return; filter.value = ""; renderZoneOptions(""); }
  function parseOffsetQuery(q) {
    const match = /^(?:utc|gmt)?\s*([+\-−])(\d{1,2})(?::?(\d{2}))?$/.exec(q); if (!match) return null;
    const hours = Number(match[2]); const minutes = Number(match[3] || 0); if (hours > 14 || minutes > 59) return null;
    return `${match[1] === "+" ? "+" : "-"}${pad2(hours)}:${pad2(minutes)}`;
  }
  // Offsets are computed lazily and cached per zone only for offset-style
  // queries; a plain keystroke never formats every zone.
  function offsetKey(zone) {
    if (!zoneOffsets.has(zone)) { const minutes = zoneOffsetMinutes(zone, Date.now()); zoneOffsets.set(zone, `${minutes < 0 ? "-" : "+"}${pad2(Math.floor(Math.abs(minutes) / 60))}:${pad2(Math.abs(minutes) % 60)}`); }
    return zoneOffsets.get(zone);
  }
  function offsetMatches(zone, q) { if (!/^(utc|gmt)?\s*[+\-−]\d/.test(q)) return false; const wanted = parseOffsetQuery(q); return Boolean(wanted) && offsetKey(zone) === wanted; }
  function validTimeZone(value) { try { new Intl.DateTimeFormat(undefined, { timeZone: value }).format(); return true; } catch (_) { return false; } }
  function partsInTimeZone(timestamp, timeZone) {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(timestamp));
    return Object.fromEntries(parts.filter(({ type }) => type !== "literal").map(({ type, value }) => [type, Number(value)]));
  }
  function eventLocalDateTime(timestamp, timeZone) {
    const parts = partsInTimeZone(timestamp, timeZone); const pad = (value) => String(value).padStart(2, "0");
    return { date: `${parts.year}-${pad(parts.month)}-${pad(parts.day)}`, time: `${pad(parts.hour)}:${pad(parts.minute)}` };
  }
  function zonedDateTimeToMillis(dateValue, timeValue, timeZone) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(dateValue) || !/^\d{2}:\d{2}$/.test(timeValue)) throw new Error("Choose complete start and end dates and times.");
    if (!validTimeZone(timeZone)) throw new Error("Enter a valid IANA time-zone name.");
    const [year, month, day] = dateValue.split("-").map(Number); const [hour, minute] = timeValue.split(":").map(Number);
    const intended = Date.UTC(year, month - 1, day, hour, minute); let timestamp = intended;
    for (let attempt = 0; attempt < 4; attempt += 1) { const actual = partsInTimeZone(timestamp, timeZone); const adjustment = intended - Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute); timestamp += adjustment; if (!adjustment) break; }
    const resolved = eventLocalDateTime(timestamp, timeZone);
    if (resolved.date !== dateValue || resolved.time !== timeValue) throw new Error(`That local time does not exist in ${timeZone}. Choose another time.`);
    return timestamp;
  }
  function formatDateTime(timestamp, timeZone) { return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone }).format(new Date(timestamp)); }
  function zoneOffsetMinutes(zone, now) { const p = partsInTimeZone(now, zone); return Math.round((Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute) - Math.floor(now / 60000) * 60000) / 60000); }
  function formatOffset(minutes) { return `UTC${minutes < 0 ? "−" : "+"}${pad2(Math.floor(Math.abs(minutes) / 60))}:${pad2(Math.abs(minutes) % 60)}`; }
  // The offset and clock are for this instant and say so; #date-time-preview
  // remains the authority for the event's own instants.
  function updateTimeZoneContext() {
    const node = byId("event-time-zone-context"); const zone = normalizeTimeZone(form.elements.time_zone.value);
    if (!zone || !validTimeZone(zone)) { node.textContent = "Choose the event’s local time zone."; return; }
    const now = Date.now(); const here = browserTimeZone();
    const local = new Intl.DateTimeFormat(undefined, { timeZone: zone, hour: "numeric", minute: "2-digit" }).format(now);
    node.textContent = `Dates and times below are in ${zone.replaceAll("_", " ")} (${formatOffset(zoneOffsetMinutes(zone, now))}). It is ${local} there now.${here !== zone ? ` Your browser is on ${here.replaceAll("_", " ")} (${formatOffset(zoneOffsetMinutes(here, now))}).` : ""}`;
  }
  function mutationToken() { const bytes = crypto.getRandomValues(new Uint8Array(32)); return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join(""); }
  function editorEventId() { const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/settings\/?$/); return match ? decodeURIComponent(match[1]) : ""; }
  function draftKey() { return `sessionbuddy:event-editor:${state.mode}:${state.event?.id || editorEventId() || state.source?.id || new URLSearchParams(location.search).get("source") || state.organizationId || "new"}`; }
  function setStatus(message = "", error = false, focus = false) { const status = byId("editor-status"); status.textContent = message; status.classList.toggle("error", error); status.setAttribute("role", error ? "alert" : "status"); if (focus) status.focus(); }
  function setUnavailable(title, copy) { byId("editor-unavailable-title").textContent = title; byId("editor-unavailable-copy").textContent = copy; byId("editor-unavailable").hidden = false; form.hidden = true; setStatus(""); }
  function secureUrl(value) { if (!value) return true; try { const parsed = new URL(value); return parsed.protocol === "https:" && !parsed.username && !parsed.password; } catch (_) { return false; } }

  function snapshot() { const values = {}; for (const name of fields) values[name] = String(form.elements[name]?.value || ""); return values; }
  function applySnapshot(values) { for (const name of fields) if (form.elements[name] && Object.hasOwn(values, name)) { const value = values[name] ?? ""; if (name === "time_zone" && value && ![...form.elements[name].options].some((option) => option.value === value)) form.elements[name].add(new Option(String(value).replaceAll("_", " "), value)); form.elements[name].value = value; } updatePreview(); updateImages(); window.SessionBuddyApi.refreshCharacterCounters?.(form); }
  function eventSnapshot(event) {
    const timeZone = normalizeTimeZone(event.time_zone);
    const startValue = event.status === "draft" ? event.draft_starts_at_ms : event.starts_at_ms;
    const endValue = event.status === "draft" ? event.draft_ends_at_ms : event.ends_at_ms;
    const start = startValue == null ? null : eventLocalDateTime(startValue, timeZone);
    const end = endValue == null ? null : eventLocalDateTime(endValue, timeZone);
    const delivery = event.status === "draft" ? event.draft_delivery_mode : event.delivery_mode;
    return { name: event.name || "", delivery_mode: delivery || "", location: event.location || "", description: event.description || "", time_zone: timeZone, start_date: start?.date || "", start_time: start?.time || "09:00", end_date: end?.date || "", end_time: end?.time || "17:00", accent_color: event.accent_color || "#0969da", website_url: event.website_url || "", logo_url: event.logo_url || "", cover_image_url: event.cover_image_url || "", email_sender_name: event.email_sender_name || "", email_reply_to: event.email_reply_to || "" };
  }
  function defaultSnapshot(timeZone = browserTimeZone()) { return { name: "", delivery_mode: "", location: "", description: "", time_zone: normalizeTimeZone(timeZone) || browserTimeZone(), start_date: "", start_time: "09:00", end_date: "", end_time: "17:00", accent_color: "#0969da", website_url: "", logo_url: "", cover_image_url: "", email_sender_name: "", email_reply_to: "" }; }
  function same(a, b) { return String(a ?? "") === String(b ?? ""); }
  function updateDirty() {
    const local = snapshot(); state.dirty = fields.some((name) => !same(local[name], state.baseline[name]));
    byId("save-state").textContent = state.dirty ? "Unsaved changes" : state.mode === "edit" ? "All changes saved" : "Not saved yet";
    byId("discard-event").disabled = !state.dirty || state.busy || state.readOnly;
    byId("save-event").disabled = !state.dirty || state.busy || state.readOnly || state.conflicts.size > 0;
    if (state.mode === "create" || state.mode === "duplicate") { byId("save-event").disabled = state.busy || state.readOnly; byId("save-draft").disabled = state.busy || state.readOnly; }
    if (state.mode === "edit" && state.event?.status === "draft") byId("activate-event").textContent = state.dirty ? "Save and activate" : "Activate event";
    updateLifecycle();
    // A save that starts while the zone list is narrowed restores the full
    // list first, so a reconcile can always select the latest server zone.
    if (state.busy) resetZoneFilter();
    const dates = describeDates(local); updateSummary(local, dates); syncSectionNav(local, dates);
  }
  // The status is unknown on the read-only recovery path (the event could not
  // be loaded); the summary row and the Lifecycle link hide rather than guess.
  function statusUnknown() { return state.mode === "edit" && !state.event; }
  function statusLabel() { return STATUS_LABELS[state.event?.status] || (statusUnknown() ? "Unknown" : "Draft"); }
  // session.organization_name is the first administered organization, which
  // may not be this event's; only the matching access row can name it.
  function organizationName() {
    const access = (state.session?.organization_access || []).find((row) => row.organization_id === state.organizationId);
    return access?.organization_name || (state.session?.organization_id === state.organizationId ? state.session.organization_name : "") || "";
  }
  function updateSummary(values = snapshot(), dates = describeDates(values)) {
    byId("summary-name").textContent = values.name.trim() || "Untitled event";
    byId("summary-status").textContent = statusLabel(); byId("summary-status-row").hidden = statusUnknown();
    byId("summary-dates").textContent = dates.hasDates ? dates.text : "Dates not set";
    byId("summary-location").textContent = `${values.location.trim() || "Not set"}${DELIVERY_LABELS[values.delivery_mode] ? ` · ${DELIVERY_LABELS[values.delivery_mode]}` : ""}`;
    const organization = organizationName(); byId("summary-organization").textContent = organization; byId("summary-organization-row").hidden = !organization;
  }
  // Nav states are never empty so no link's text collapses to its section name.
  function syncSectionNav(values = snapshot(), dates = describeDates(values)) {
    byId("nav-state-general").textContent = ["name", "delivery_mode", "location", "description"].every((name) => values[name].trim()) ? "Complete" : "Needs details";
    byId("nav-state-date-time").textContent = dates.hasDates ? `${dates.days} day${dates.days === 1 ? "" : "s"}` : "Not scheduled";
    const logo = values.logo_url; const cover = values.cover_image_url;
    byId("nav-state-branding").textContent = logo && cover ? "Logo and cover" : logo ? "Logo set" : cover ? "Cover set" : values.website_url || values.accent_color.toLowerCase() !== "#0969da" ? "Customized" : "Optional";
    byId("nav-state-email").textContent = values.email_sender_name.trim() || values.email_reply_to.trim() ? "Custom sender" : "Optional";
    byId("nav-state-lifecycle").textContent = statusLabel(); byId("nav-lifecycle").hidden = state.mode !== "edit" || statusUnknown();
  }
  function preserveDraft() { try { sessionStorage.setItem(draftKey(), JSON.stringify({ values: snapshot(), unsavedUploads: [...state.unsavedUploads], organizationId: state.organizationId, eventName: state.event?.name || state.source?.name || "Event settings" })); } catch (_) {} }
  function savedDraft() { try { return JSON.parse(sessionStorage.getItem(draftKey()) || "null"); } catch (_) { return null; } }
  function removeDraft() { try { sessionStorage.removeItem(draftKey()); } catch (_) {} }
  // The handoff banner is a nicety; storage denial must not block navigation after a successful save.
  function handoff(message) { try { sessionStorage.setItem("sessionbuddy:handoff-status", message); } catch (_) {} }
  function syncUploadButtons() { for (const kind of ["logo", "cover"]) { const input = byId(`${kind}-file`); byId(`upload-${kind}`).disabled = state.busy || state.readOnly || !input.files[0] || !input.checkValidity(); } }
  function setBusy(busy, message = "") { state.busy = busy; form.setAttribute("aria-busy", String(busy)); for (const control of form.querySelectorAll("button, input[type=file], #time-zone-filter")) if (!control.closest("dialog")) control.disabled = busy || (state.readOnly && control.type !== "hidden"); if (message) setStatus(message); updateDirty(); syncUploadButtons(); }

  // One description of the event's dates feeds the preview, the summary and
  // the nav state. `days` counts calendar days in the event zone, inclusive.
  function describeDates(values) {
    const zone = normalizeTimeZone(values.time_zone);
    if (!values.start_date || !values.end_date) return { text: "Choose a start and end date.", hasDates: false };
    try {
      const start = zonedDateTimeToMillis(values.start_date, values.start_time, zone); const end = zonedDateTimeToMillis(values.end_date, values.end_time, zone);
      if (end <= start) return { text: "The event must end after it starts.", hasDates: false };
      const day = (value) => { const [year, month, date] = value.split("-").map(Number); return Date.UTC(year, month - 1, date); };
      return { text: `${formatDateTime(start, zone)} – ${formatDateTime(end, zone)} · ${zone}`, hasDates: true, days: Math.round((day(values.end_date) - day(values.start_date)) / 86_400_000) + 1 };
    } catch (error) { return { text: error.message, hasDates: false }; }
  }
  function updatePreview() {
    updateBrandPreview();
    byId("date-time-preview").textContent = describeDates(snapshot()).text;
    updateTimeZoneContext();
  }
  function updateImages() {
    for (const kind of ["logo", "cover"]) { const image = byId(`${kind}-preview`); const value = form.elements[kind === "logo" ? "logo_url" : "cover_image_url"].value; image.hidden = !value; if (value) image.src = value; else image.removeAttribute("src"); }
    updateBrandPreview();
  }
  function updateBrandPreview() {
    const values = snapshot();
    // Admin controls do not consume --event-accent; public-page token remapping
    // is deliberately scoped, so this custom property only paints the preview.
    document.documentElement.style.setProperty("--event-accent", values.accent_color || "#0969da");
    window.SessionBuddyPublicEventMasthead.render(document.querySelector("[data-public-event-masthead]"), {
      event: { id: state.event?.id || state.source?.id || "preview", name: values.name || "Event name", accentColor: values.accent_color, logoUrl: values.logo_url, coverUrl: values.cover_image_url },
      active: null, embedded: false, navigation: false,
    });
  }

  function validate(intendedStatus) {
    for (const input of form.querySelectorAll("input, select, textarea")) { input.setCustomValidity(""); input.removeAttribute("aria-invalid"); }
    const values = snapshot();
    if (intendedStatus === "active" && !form.reportValidity()) return null;
    if (intendedStatus === "draft" && !values.name.trim()) { form.elements.name.setCustomValidity("Enter an event name to save this draft."); form.elements.name.reportValidity(); return null; }
    if (!secureUrl(values.website_url)) { form.elements.website_url.setCustomValidity("Use a complete HTTPS URL without embedded credentials."); form.elements.website_url.setAttribute("aria-invalid", "true"); form.elements.website_url.reportValidity(); return null; }
    try {
      const timeZone = normalizeTimeZone(values.time_zone || browserTimeZone());
      const startsAt = values.start_date ? zonedDateTimeToMillis(values.start_date, values.start_time, timeZone) : null;
      const endsAt = values.end_date ? zonedDateTimeToMillis(values.end_date, values.end_time, timeZone) : null;
      if (startsAt != null && endsAt != null && endsAt <= startsAt) { form.elements.end_date.setCustomValidity("The event must end after it starts."); form.elements.end_date.setAttribute("aria-invalid", "true"); form.elements.end_date.reportValidity(); return null; }
      if (intendedStatus === "active" && (endsAt == null || endsAt <= Date.now())) { form.elements.end_date.setCustomValidity("Update the event dates before activating."); form.elements.end_date.setAttribute("aria-invalid", "true"); form.elements.end_date.reportValidity(); return null; }
      if (form.elements.logo_file.files[0] || form.elements.cover_file.files[0]) { setStatus("Upload the selected image or clear it before saving.", true, true); return null; }
      return { name: values.name.trim(), starts_at_ms: startsAt, ends_at_ms: endsAt, time_zone: timeZone, delivery_mode: values.delivery_mode || null, location: values.location.trim(), description: values.description.trim(), email_sender_name: values.email_sender_name.trim() || null, email_reply_to: values.email_reply_to.trim() || null, accent_color: values.accent_color || "#0969da", logo_url: values.logo_url || null, cover_image_url: values.cover_image_url || null, website_url: values.website_url || null };
    } catch (error) { form.elements.time_zone.setCustomValidity(error.message); form.elements.time_zone.setAttribute("aria-invalid", "true"); form.elements.time_zone.reportValidity(); return null; }
  }

  function updateLifecycle() {
    if (state.mode !== "edit" || !state.event) return;
    const status = state.event.status; byId("lifecycle-state").textContent = status;
    const canManage = window.SessionBuddyAccess?.canManageLifecycle(state.session, state.event) !== false;
    byId("activate-event").hidden = status !== "draft" || state.readOnly;
    byId("archive-event").hidden = status !== "active" || !canManage || state.readOnly;
    byId("restore-draft").hidden = status !== "archived" || !canManage || state.readOnly;
    byId("restore-active").hidden = status !== "archived" || !canManage || state.readOnly;
    byId("duplicate-event").hidden = !(window.SessionBuddyAccess?.canDuplicateEvent(state.session, state.event)) || state.readOnly;
    const copy = status === "draft" ? "The event becomes eligible to publish its call for proposals and schedule. Each is published separately." : status === "active" ? "Archiving takes the event and its published program offline without deleting its records." : "Restore this event as a private draft, or activate it to return published surfaces to public view.";
    byId("lifecycle-consequence").textContent = copy;
  }

  function setReadOnly() {
    if (state.readOnly) return; state.readOnly = true; preserveDraft(); window.removeEventListener("beforeunload", beforeUnload);
    form.classList.add("is-read-only");
    for (const control of form.elements) { if (control.type === "hidden") continue; if (control.matches("input:not([type=file]), textarea")) control.readOnly = true; else control.disabled = true; }
    for (const kind of state.unsavedUploads) byId(`${kind}-status`).textContent = "Not saved — you no longer have access to save this event";
    byId("access-banner").hidden = false; byId("access-banner").focus(); updateDirty();
  }

  function conflictChoice(name, useLatest) {
    const conflict = state.conflicts.get(name); if (!conflict) return;
    if (useLatest) form.elements[name].value = conflict.latest ?? "";
    state.conflicts.delete(name); renderConflicts(); updateDirty();
  }
  function renderConflicts() {
    const list = byId("conflict-list"); list.replaceChildren();
    for (const [name, conflict] of state.conflicts) { const row = document.createElement("div"); row.className = "event-editor__conflict"; const copy = document.createElement("p"); const heading = document.createElement("strong"); heading.textContent = `${labels[name]} changed elsewhere`; copy.append(heading, document.createElement("br"), `Latest: ${String(conflict.latest || "Not set")}`); const mine = document.createElement("button"); mine.type = "button"; mine.className = "secondary"; mine.textContent = "Keep mine"; mine.addEventListener("click", () => conflictChoice(name, false)); const latest = document.createElement("button"); latest.type = "button"; latest.className = "secondary"; latest.textContent = "Use latest"; latest.addEventListener("click", () => conflictChoice(name, true)); row.append(copy, mine, latest); list.append(row); }
    byId("conflict-panel").hidden = !state.conflicts.size;
    if (state.conflicts.size) byId("conflict-title").focus();
  }
  async function reconcile() {
    const local = snapshot(); const latestEvent = await api(`/api/v1/admin/events/${encodeURIComponent(state.event.id)}`); const latest = eventSnapshot(latestEvent); state.conflicts.clear();
    for (const name of fields) { const changedLocal = !same(local[name], state.baseline[name]); const changedLatest = !same(latest[name], state.baseline[name]); if (!changedLocal) form.elements[name].value = latest[name]; else if (changedLatest && !same(local[name], latest[name])) state.conflicts.set(name, { mine: local[name], latest: latest[name] }); }
    state.event = latestEvent; state.baseline = latest; state.latest = latest; renderConflicts(); updatePreview(); updateImages(); updateDirty();
  }

  async function save(status, lifecycleMessage = "") {
    const body = validate(status); if (!body) return null; body.status = status;
    if (state.mode === "edit") body.version = state.event.version;
    if (state.mode === "duplicate") { body.source_version = state.source.version; body.retain_source_logo = Boolean(state.source.logo_url && !body.logo_url); body.retain_source_cover = Boolean(state.source.cover_image_url && !body.cover_image_url); }
    const fingerprint = JSON.stringify(body); if (state.mode !== "edit" && (!state.mutation || state.mutation.fingerprint !== fingerprint)) state.mutation = { fingerprint, key: mutationToken() };
    const headers = { "content-type": "application/json", "x-csrf-token": state.session.csrf_token }; if (state.mode !== "edit") headers["idempotency-key"] = state.mutation.key;
    const path = state.mode === "edit" ? `/api/v1/admin/events/${encodeURIComponent(state.event.id)}` : state.mode === "duplicate" ? `/api/v1/admin/events/${encodeURIComponent(state.source.id)}/duplicate` : `/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/events`;
    setBusy(true, lifecycleMessage || (state.mode === "edit" ? "Saving changes…" : status === "draft" ? "Saving draft…" : "Creating event…"));
    try {
      const saved = await api(path, { method: state.mode === "edit" ? "PATCH" : "POST", headers, body: JSON.stringify(body) }); removeDraft(); state.unsavedUploads.clear();
      if (state.mode === "create") { state.readOnly = true; state.dirty = false; window.removeEventListener("beforeunload", beforeUnload); handoff(status === "draft" ? "Draft saved." : "Event created."); location.assign(`/admin/events/${encodeURIComponent(saved.id)}`); return saved; }
      if (state.mode === "duplicate") { state.readOnly = true; state.dirty = false; window.removeEventListener("beforeunload", beforeUnload); handoff(status === "draft" ? "Draft saved. You are now editing the copy." : "Event created. You are now editing the copy."); location.assign(`/admin/events/${encodeURIComponent(saved.id)}/settings`); return saved; }
      const previous = state.event.status; state.event = saved; state.baseline = eventSnapshot(saved); applySnapshot(state.baseline); state.conflicts.clear(); renderConflicts();
      const message = previous !== saved.status ? saved.status === "archived" ? "Event archived." : previous === "archived" && saved.status === "draft" ? "Event restored as a draft." : previous === "archived" ? "Event restored." : "Event activated." : "Event saved.";
      setStatus(message, false, true); updateDirty(); return saved;
    } catch (error) {
      if (error.status === 401) { preserveDraft(); window.SessionBuddyApi.redirectIfSignedOut(error); return null; }
      if (error.status === 403 || (error.status === 404 && state.mode === "edit")) { setReadOnly(); return null; }
      if (error.status === 409 && error.code === "event_timezone_has_agenda_conflict") { setStatus(window.SessionBuddyApi.message(error), true, true); return null; }
      if (error.status === 409 && state.mode === "edit") { setStatus("Someone else saved this event while you were editing.", true); try { await reconcile(); } catch (reloadError) { setStatus(window.SessionBuddyApi.message(reloadError), true, true); } return null; }
      if (error.status === 409 && state.mode === "duplicate") { setStatus("The source event changed. Reload to clone its latest version.", true, true); byId("reload-source").hidden = false; return null; }
      if (error.status === 409) { setStatus("Event creation is still being processed. Try again to safely check the same request.", true, true); return null; }
      window.SessionBuddyApi.showValidationErrors?.(form, error); const message = window.SessionBuddyApi.message(error); setStatus(error.status === 422 && /invalid event (logo|cover) asset/i.test(message) ? "Upload the image again, then save the event." : message, true, true); return null;
    } finally { setBusy(false); }
  }

  async function upload(kind) {
    const fileInput = byId(`${kind}-file`); const file = fileInput.files[0]; if (!file) return;
    const status = byId(`${kind}-status`); const button = byId(`upload-${kind}`); button.disabled = true; status.textContent = "Uploading…";
    try { const uploaded = await api(`/api/v1/admin/organizations/${encodeURIComponent(state.organizationId)}/event-assets/${kind}`, { method: "POST", headers: { "content-type": file.type, "x-csrf-token": state.session.csrf_token }, body: file }); form.elements[kind === "logo" ? "logo_url" : "cover_image_url"].value = uploaded.asset_url; fileInput.value = ""; state.unsavedUploads.add(kind); status.textContent = "Upload complete · Save changes to use this image."; updateImages(); updateDirty(); }
    catch (error) { status.textContent = window.SessionBuddyApi.message(error); }
    finally { button.disabled = state.readOnly || !fileInput.files[0]; }
  }

  function configureMode() {
    const sourceId = new URLSearchParams(location.search).get("source"); const eventId = editorEventId(); state.mode = eventId ? "edit" : sourceId ? "duplicate" : "create";
    byId("lifecycle").hidden = state.mode !== "edit"; byId("setup-guide").hidden = state.mode === "edit"; byId("save-draft").hidden = state.mode === "edit"; byId("back-to-event").hidden = state.mode !== "edit";
    byId("event-summary-panel").hidden = state.mode !== "edit"; byId("nav-lifecycle").hidden = state.mode !== "edit";
    if (state.mode === "create") { document.title = "Create event · SessionBuddy"; byId("page-title").textContent = "Create event"; byId("page-lede").textContent = "Drafts stay private. An active event is eligible to publish its call for proposals and schedule; each is published separately."; byId("save-draft").textContent = "Save draft"; byId("save-event").textContent = "Create event and continue setup"; }
    if (state.mode === "duplicate") { document.title = "Clone event · SessionBuddy"; byId("page-title").textContent = "Clone event"; byId("page-lede").textContent = "Review the copied setup before creating a separate event."; byId("duplicate-context").hidden = false; byId("save-draft").className = ""; byId("save-event").className = "secondary"; byId("save-event").textContent = "Create event and continue setup"; byId("save-draft").textContent = "Save draft"; byId("save-draft").parentElement.insertBefore(byId("save-event"), byId("save-draft")); }
    syncSectionNav();
  }

  // --- Section navigation -------------------------------------------------
  const SPY_PAUSE_MS = 800;
  let spyPause = 0; let spyPinned = ""; let spyTimer = 0;
  const inView = new Set();
  function markCurrent(id) { for (const link of byId("event-editor-nav").querySelectorAll("a[data-section-link]")) { if (link.dataset.sectionLink === id) link.setAttribute("aria-current", "location"); else link.removeAttribute("aria-current"); } }
  // The band's top-most section is current; right after a nav click the chosen
  // section keeps that role for as long as it is still in the band.
  function syncCurrentSection() {
    const top = spyPinned && inView.has(spyPinned) ? spyPinned : SECTION_IDS.find((id) => inView.has(id));
    spyPinned = ""; if (top) markCurrent(top);
  }
  function goToSection(id, { focus = true } = {}) {
    const section = byId(id); if (!section || section.hidden) return false;
    if (section instanceof HTMLDetailsElement) section.open = true;
    // Keep the shell's history stamp on this entry so Back/Forward stays precise.
    history.replaceState(history.state, "", `#${id}`);
    // Hold the scroll spy while the programmatic scroll settles so the chosen
    // section stays current instead of whichever neighbour crosses the band,
    // then re-sync once so the nav is truthful after the pause.
    spyPause = Date.now() + SPY_PAUSE_MS; spyPinned = id;
    clearTimeout(spyTimer); spyTimer = setTimeout(syncCurrentSection, SPY_PAUSE_MS);
    section.scrollIntoView({ block: "start" });
    if (focus) (section.querySelector("summary") || section.querySelector("h2"))?.focus({ preventScroll: true });
    markCurrent(id); return true;
  }
  function startSectionSpy() {
    if (!("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver((entries) => {
      // Every callback updates the set, including during the pause, so a
      // boundary crossed while paused is not lost.
      for (const entry of entries) { if (entry.isIntersecting) inView.add(entry.target.id); else inView.delete(entry.target.id); }
      if (Date.now() < spyPause) return;
      syncCurrentSection();
    }, { rootMargin: "-35% 0px -55% 0px", threshold: 0 });
    for (const id of SECTION_IDS) observer.observe(byId(id));
    window.SessionBuddyPage?.onLeave(() => { observer.disconnect(); clearTimeout(spyTimer); });
  }

  async function initialize() {
    configureMode(); populateTimeZones(); state.session = await api("/api/v1/auth/session");
    const configuredSender = state.session.default_email_address
      ? `${state.session.default_email_sender_name || "SessionBuddy"} <${state.session.default_email_address}>`
      : "";
    byId("email-default").textContent = configuredSender
      ? `Blank uses ${configuredSender}.`
      : "No default sender address is configured for this installation. Reply-to can still be set here; an administrator must configure outbound email before messages can be delivered.";
    byId("email-summary-note").textContent = configuredSender ? `Optional · uses ${configuredSender}` : "Optional";
    let profileTimeZone = "";
    if (state.mode === "create") {
      try { profileTimeZone = (await api("/api/v1/account/profile")).time_zone || ""; } catch (_) { /* Browser zone remains a safe fallback. */ }
    }
    let values = defaultSnapshot(profileTimeZone);
    if (state.mode === "edit") {
      const eventId = editorEventId();
      try { state.event = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`); } catch (error) { if ([403, 404].includes(error.status)) { const draft = savedDraft(); if (draft?.values) { state.organizationId = draft.organizationId || ""; state.baseline = { ...draft.values }; state.latest = { ...draft.values }; state.unsavedUploads = new Set(draft.unsavedUploads || []); applySnapshot(draft.values); byId("page-title").textContent = draft.eventName || "Event settings"; form.hidden = false; setReadOnly(); setStatus(""); return; } setUnavailable("Event settings unavailable", "This event is missing or you no longer have access to it."); return; } throw error; }
      state.organizationId = state.event.organization_id; values = eventSnapshot(state.event); byId("page-title").textContent = state.event.name; byId("back-to-event").href = `/admin/events/${encodeURIComponent(state.event.id)}`; byId("duplicate-event").href = `/admin/events/new?source=${encodeURIComponent(state.event.id)}`;
    } else if (state.mode === "duplicate") {
      const sourceId = new URLSearchParams(location.search).get("source");
      try { state.source = await api(`/api/v1/admin/events/${encodeURIComponent(sourceId)}`); } catch (error) { if ([403, 404].includes(error.status)) { setUnavailable("Event unavailable", "That event is no longer available to clone."); return; } throw error; }
      state.organizationId = state.source.organization_id; values = eventSnapshot(state.source); values.name = `${state.source.name} copy`; values.logo_url = ""; values.cover_image_url = ""; byId("source-name").textContent = state.source.name;
      const sourceStart = state.source.status === "draft" ? state.source.draft_starts_at_ms : state.source.starts_at_ms;
      byId("source-date").textContent = sourceStart == null
        ? "Dates not set"
        : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: state.source.time_zone }).format(new Date(sourceStart));
      byId("source-link").href = `/admin/events/${encodeURIComponent(state.source.id)}`;
      if (state.source.logo_url) { byId("logo-status").textContent = "Will be copied to the new event."; byId("logo-preview").src = state.source.logo_url; byId("logo-preview").hidden = false; } if (state.source.cover_image_url) { byId("cover-status").textContent = "Will be copied to the new event."; byId("cover-preview").src = state.source.cover_image_url; byId("cover-preview").hidden = false; }
    } else {
      const organizations = await api("/api/v1/admin/organizations"); const manageable = (organizations.data || []).filter((org) => window.SessionBuddyAccess?.canManageOrganization(state.session, org.id) !== false);
      if (!manageable.length) { setUnavailable("No organization access", "This account does not administer an organization."); return; }
      const requested = new URLSearchParams(location.search).get("organization_id"); state.organizationId = manageable.some((org) => org.id === requested) ? requested : manageable.some((org) => org.id === state.session.organization_id) ? state.session.organization_id : manageable[0].id;
      const select = form.elements.organization_id; for (const org of manageable) select.add(new Option(org.name, org.id)); select.value = state.organizationId; byId("organization-field").hidden = manageable.length < 2;
    }
    state.baseline = { ...values }; state.latest = { ...values }; const draft = savedDraft(); if (draft?.values) { state.unsavedUploads = new Set(draft.unsavedUploads || []); applySnapshot(draft.values); } else applySnapshot(values);
    // Only an existing event with branding opens the disclosure; create and clone start closed.
    if (state.mode === "edit") { const current = snapshot(); byId("branding").open = Boolean(current.logo_url || current.cover_image_url || current.website_url); }
    if (state.mode === "duplicate") {
      if (state.source.logo_url && !form.elements.logo_url.value) { byId("logo-preview").src = state.source.logo_url; byId("logo-preview").hidden = false; }
      if (state.source.cover_image_url && !form.elements.cover_image_url.value) { byId("cover-preview").src = state.source.cover_image_url; byId("cover-preview").hidden = false; }
    }
    form.hidden = false; startSectionSpy(); setStatus(""); updateLifecycle(); updateDirty();
    const target = location.hash.slice(1); if (!SECTION_IDS.includes(target) || !goToSection(target)) form.elements.name.focus();
  }

  function beforeUnload(event) { if (!state.dirty || state.readOnly) return; event.preventDefault(); event.returnValue = ""; }
  window.addEventListener("beforeunload", beforeUnload, { signal: window.SessionBuddyPage?.signal });
  // Unsaved edits turn an in-place workspace navigation into a document one so the native leave prompt applies.
  window.addEventListener("sessionbuddy:navigate", (event) => { if (state.dirty && !state.readOnly) event.preventDefault(); }, { signal: window.SessionBuddyPage?.signal });
  form.addEventListener("input", (event) => { if (event.target.id === "time-zone-filter") return; if (event.target.name === "time_zone" || event.target.type === "date" || event.target.type === "time") updatePreview(); else updateBrandPreview(); updateDirty(); if (state.dirty) preserveDraft(); });
  form.addEventListener("change", (event) => { if (event.target.id === "time-zone-filter") return; if (event.target.name === "organization_id") state.organizationId = event.target.value; if (event.target.name === "start_date" && event.target.value && !form.elements.end_date.value) { form.elements.end_date.value = event.target.value; updatePreview(); } updateBrandPreview(); updateDirty(); });
  form.addEventListener("submit", (event) => { event.preventDefault(); const status = event.submitter?.value === "draft" ? "draft" : state.mode === "edit" ? state.event.status : "active"; save(status); });
  form.addEventListener("keydown", (event) => { if (event.key !== "Enter" || event.target.matches("textarea, button")) return; event.preventDefault(); form.requestSubmit(state.mode === "duplicate" ? byId("save-draft") : byId("save-event")); });
  byId("discard-event").addEventListener("click", () => { applySnapshot(state.baseline); state.conflicts.clear(); state.unsavedUploads.clear(); for (const kind of ["logo", "cover"]) byId(`${kind}-status`).textContent = ""; renderConflicts(); removeDraft(); setStatus("Changes discarded."); updateDirty(); });
  for (const kind of ["logo", "cover"]) { byId(`${kind}-file`).addEventListener("change", (event) => { const file = event.target.files[0]; const valid = !file || (["image/jpeg", "image/png", "image/webp"].includes(file.type) && file.size <= 2 * 1024 * 1024); event.target.setCustomValidity(valid ? "" : "Choose a PNG, JPG, or WebP image no larger than 2 MB."); byId(`upload-${kind}`).disabled = !file || !valid || state.readOnly; if (file && !valid) event.target.reportValidity(); updateDirty(); }); byId(`upload-${kind}`).addEventListener("click", () => upload(kind)); }
  byId("activate-event").addEventListener("click", () => save("active", state.dirty ? "Saving and activating…" : "Activating…"));
  byId("archive-event").addEventListener("click", () => { if (state.dirty) { byId("lifecycle-note").textContent = "Save your changes before archiving."; return; } byId("archive-title").textContent = `Archive ${state.event.name}?`; byId("archive-dialog").showModal(); });
  byId("archive-dialog").addEventListener("close", () => { if (byId("archive-dialog").returnValue === "confirm") save("archived", "Archiving…"); });
  byId("restore-draft").addEventListener("click", () => { if (state.dirty) { byId("lifecycle-note").textContent = "Save your changes before restoring."; return; } save("draft", "Restoring…"); });
  byId("restore-active").addEventListener("click", () => { if (state.dirty) { byId("lifecycle-note").textContent = "Save your changes before restoring."; return; } save("active", "Restoring…"); });
  byId("reload-source").addEventListener("click", () => location.reload());
  byId("time-zone-filter").addEventListener("input", (event) => renderZoneOptions(event.target.value));
  byId("time-zone-filter").addEventListener("keydown", (event) => {
    // Enter moves to the select instead of reaching the form's Enter-to-submit
    // handler; when the search leaves exactly one zone, Enter selects it first.
    if (event.key === "Enter" || event.key === "ArrowDown") {
      event.preventDefault(); event.stopPropagation();
      const select = form.elements.time_zone; const matches = renderZoneOptions(event.currentTarget.value);
      if (event.key === "Enter" && matches.length === 1 && select.value !== matches[0].value) { select.value = matches[0].value; select.dispatchEvent(new Event("input", { bubbles: true })); select.dispatchEvent(new Event("change", { bubbles: true })); }
      form.elements.time_zone.focus(); return;
    }
    if (event.key === "Escape") { event.preventDefault(); event.currentTarget.value = ""; renderZoneOptions(""); }
  });
  // The filter hands focus to the select, so Enter there must not reach the
  // form's Enter-to-submit handler either: implicit submission is for text fields.
  form.elements.time_zone.addEventListener("keydown", (event) => { if (event.key === "Enter") event.stopPropagation(); });
  byId("event-editor-nav").addEventListener("click", (event) => { const link = event.target.closest("a[data-section-link]"); if (!link) return; event.preventDefault(); goToSection(link.dataset.sectionLink); });
  const tick = setInterval(() => { zoneOffsets.clear(); updateTimeZoneContext(); }, 60_000);
  window.SessionBuddyPage?.onLeave(() => clearInterval(tick));

  initialize().catch((error) => setStatus(window.SessionBuddyApi.message(error), true, true));
})();

(() => {
  "use strict";
  const match = location.pathname.match(/^\/(?:embeds\/)?events\/([^/]+)\/(?:schedule|sessions|itinerary)$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(eventId)) eventId = "";
  const embedded = location.pathname.startsWith("/embeds/");
  const sessionsOnly = location.pathname.endsWith("/sessions");
  const storageKey = `sessionbuddy:itinerary:${eventId}`;
  const state = { model: null, view: "list", query: "", track: "", format: "", room: "", day: "", itinerary: new Set(), selected: null };
  const expandedDescriptions = new Set();
  try { state.itinerary = new Set(JSON.parse(localStorage.getItem(storageKey) || "[]")); } catch (_) { state.itinerary = new Set(); }
  const byId = (id) => document.getElementById(id);
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
  const trackPalette = ["#5b4bdb", "#087e8b", "#b54708", "#b4236a", "#2563a8", "#527a1f", "#8a3ffc", "#a63d40"];
  function trackColor(item) {
    const name = item.track_name || "No track";
    let hash = 0;
    for (const character of name) hash = ((hash * 31) + character.codePointAt(0)) >>> 0;
    return trackPalette[hash % trackPalette.length];
  }
  function trackChip(item) {
    const chip = make("span", item.track_name || "No track", "track-chip");
    chip.style.setProperty("--track-color", trackColor(item));
    return chip;
  }
  function format(value, options) { try { return new Intl.DateTimeFormat(undefined, { timeZone: state.model.event.time_zone, ...options }).format(new Date(value)); } catch (_) { return "Date unavailable"; } }
  function day(item) { return format(item.start_at_ms, { weekday: "long", month: "long", day: "numeric" }); }
  function dayKey(item) { return format(item.start_at_ms, { year: "numeric", month: "2-digit", day: "2-digit" }); }
  function group(item) { if (state.view === "room") return item.room_name; if (state.view === "track") return item.track_name || "No track"; if (["day", "week"].includes(state.view)) return day(item); if (state.view === "mine") return "My itinerary"; return "All sessions"; }
  function saveItinerary() { localStorage.setItem(storageKey, JSON.stringify([...state.itinerary])); byId("itinerary-count").textContent = String(state.itinerary.size); }
  function icsText(items) {
    const escape = (value) => String(value || "").replaceAll("\\", "\\\\").replaceAll("\n", "\\n").replaceAll(",", "\\,").replaceAll(";", "\\;");
    const stamp = (value) => new Date(value).toISOString().replaceAll("-", "").replaceAll(":", "").replace(".000", "");
    const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SessionBuddy//Itinerary//EN", "CALSCALE:GREGORIAN"];
    items.forEach((item) => lines.push(
      "BEGIN:VEVENT",
      `UID:${escape(item.id)}@sessionbuddy`,
      `DTSTAMP:${stamp(Date.now())}`,
      `DTSTART:${stamp(item.start_at_ms)}`,
      `DTEND:${stamp(item.end_at_ms)}`,
      `SUMMARY:${escape(item.title)}`,
      `LOCATION:${escape(item.room_name)}`,
      `DESCRIPTION:${escape(item.description)}`,
      "END:VEVENT",
    ));
    lines.push("END:VCALENDAR");
    return `${lines.join("\r\n")}\r\n`;
  }
  function downloadCalendar() {
    const selected = state.model.items.filter((item) => state.itinerary.has(item.id));
    if (!selected.length) return;
    const blob = new Blob([icsText(selected)], { type: "text/calendar;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${state.model.event.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "event"}-itinerary.ics`;
    link.click();
    URL.revokeObjectURL(link.href);
    byId("status").textContent = `Downloaded ${selected.length} session${selected.length === 1 ? "" : "s"} as a calendar file.`;
  }
  function toggleItinerary(id) { if (state.itinerary.has(id)) state.itinerary.delete(id); else state.itinerary.add(id); saveItinerary(); render(); }
  function matches(item) {
    const haystack = [item.title, item.description, item.speaker_names, item.speaker_details, item.format_name, item.room_name, item.track_name, ...(item.labels || []).map((label) => label.name)].join(" ").toLowerCase();
    return (!state.query || haystack.includes(state.query))
      && (!state.track || (item.track_name || "No track") === state.track)
      && (!state.format || item.format_name === state.format)
      && (!state.room || item.room_name === state.room)
      && (!state.day || dayKey(item) === state.day);
  }
  function labelChips(item) {
    const labels = make("div", undefined, "schedule-labels");
    (item.labels || []).forEach((label) => {
      const chip = make("span", label.name, "schedule-label");
      chip.style.setProperty("--label-color", label.color);
      labels.append(chip);
    });
    return labels;
  }
  function openDetail(item) {
    state.selected = item;
    const kicker = byId("session-detail-kicker");
    kicker.replaceChildren(make("span", item.format_name, "session-format-name"), trackChip(item));
    byId("session-detail").style.setProperty("--track-color", trackColor(item));
    byId("session-detail-title").textContent = item.title;
    byId("session-detail-speaker").textContent = item.speaker_names || "Speaker TBA";
    if (item.speaker_details) byId("session-detail-speaker").append(make("small", item.speaker_details));
    byId("session-detail-meta").textContent = `${format(item.start_at_ms, { weekday: "long", month: "long", day: "numeric", hour: "numeric", minute: "2-digit" })}–${format(item.end_at_ms, { hour: "numeric", minute: "2-digit" })} · ${item.room_name}`;
    byId("session-detail-description").textContent = item.description || "No session description is available.";
    byId("session-detail-labels").replaceChildren(...labelChips(item).childNodes);
    const itinerary = byId("session-detail-itinerary");
    itinerary.textContent = state.itinerary.has(item.id) ? "Remove from my itinerary" : "Add to my itinerary";
    byId("session-detail").showModal();
  }
  function sessionCard(item) {
    const row = make("li", undefined, "schedule-item");
    row.style.setProperty("--track-color", trackColor(item));
    const time = make("time", `${format(item.start_at_ms, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}–${format(item.end_at_ms, { hour: "numeric", minute: "2-digit" })}`); time.dateTime = new Date(item.start_at_ms).toISOString();
    const details = document.createElement("div");
    const classification = make("p", undefined, "session-classification");
    classification.append(make("span", item.format_name, "session-format-name"), trackChip(item));
    details.append(classification, make("h3", item.title));
    const description = String(item.description || "");
    if (description) {
      const preview = make("p", undefined, "session-description");
      preview.id = `session-description-${item.id}`;
      const long = description.length > 220;
      const toggle = make("button", "Show more", "secondary");
      toggle.type = "button";
      toggle.setAttribute("aria-controls", preview.id);
      const updateDescription = () => {
        const expanded = expandedDescriptions.has(item.id);
        preview.textContent = long && !expanded ? `${description.slice(0, 220).trimEnd()}…` : description;
        toggle.textContent = expanded ? "Show less" : "Show more";
        toggle.setAttribute("aria-expanded", String(expanded));
      };
      toggle.addEventListener("click", () => {
        if (expandedDescriptions.has(item.id)) expandedDescriptions.delete(item.id);
        else expandedDescriptions.add(item.id);
        updateDescription();
      });
      updateDescription();
      details.append(preview);
      if (long) details.append(toggle);
    }
    const speaker = make("p", item.speaker_names || "Speaker TBA", "session-speaker");
    if (item.speaker_details) speaker.append(make("small", item.speaker_details));
    details.append(speaker, make("p", item.room_name, "session-room"), labelChips(item));
    const show = make("button", "Show details", "session-more secondary"); show.type = "button"; show.addEventListener("click", () => openDetail(item));
    const add = make("button", state.itinerary.has(item.id) ? "✓" : "+", "itinerary-button"); add.type = "button"; add.setAttribute("aria-pressed", String(state.itinerary.has(item.id))); add.setAttribute("aria-label", `${state.itinerary.has(item.id) ? "Remove" : "Add"} ${item.title} ${state.itinerary.has(item.id) ? "from" : "to"} my itinerary`); add.addEventListener("click", () => toggleItinerary(item.id));
    const actions = make("div", undefined, "session-actions"); actions.append(show); if (!sessionsOnly) actions.append(add);
    row.append(time, details, actions);
    return row;
  }
  function renderGrid(items) {
    const root = byId("schedule");
    const rooms = [...new Set(items.map((item) => item.room_name))];
    const times = [...new Set(items.map((item) => item.start_at_ms))].sort((a, b) => a - b);
    const board = make("div", undefined, "stage-board");
    board.style.setProperty("--room-count", String(Math.max(1, rooms.length)));
    board.append(make("div", "Time", "stage-board__corner"), ...rooms.map((room) => make("div", room, "stage-board__room")));
    times.forEach((time) => {
      board.append(make("time", format(time, { hour: "numeric", minute: "2-digit" }), "stage-board__time"));
      rooms.forEach((room) => {
        const cell = make("div", undefined, "stage-board__cell");
        items.filter((item) => item.room_name === room && item.start_at_ms === time).forEach((item) => {
          const button = make("button", undefined, "stage-session"); button.type = "button";
          button.style.setProperty("--track-color", trackColor(item));
          button.append(trackChip(item), make("strong", item.title), make("span", item.speaker_names || "Speaker TBA"), make("small", item.format_name));
          button.addEventListener("click", () => openDetail(item)); cell.append(button);
        });
        board.append(cell);
      });
    });
    root.append(board);
  }
  function render() {
    const source = state.view === "mine" ? state.model.items.filter((item) => state.itinerary.has(item.id)) : state.model.items;
    const visible = source.filter(matches);
    const groups = new Map();
    visible.forEach((item) => { const key = group(item); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(item); });
    const root = byId("schedule"); root.replaceChildren(); root.classList.toggle("week-view", state.view === "week");
    byId("results-summary").textContent = state.query || state.view === "mine"
      ? `${visible.length} of ${source.length} session${source.length === 1 ? "" : "s"} shown.`
      : `${visible.length} published session${visible.length === 1 ? "" : "s"}.`;
    byId("empty").hidden = visible.length !== 0;
    byId("download-calendar").disabled = state.itinerary.size === 0;
    byId("empty").querySelector("strong").textContent = state.view === "mine" ? "Choose + on a session to build your itinerary." : "No sessions are published yet.";
    if (state.view === "grid") { renderGrid(visible); return; }
    [...groups].forEach(([name, items]) => {
      const section = make("section", undefined, "schedule-group"); section.append(make("h2", name));
      const list = make("ol", undefined, "schedule-list");
      items.sort((a, b) => a.start_at_ms - b.start_at_ms).forEach((item) => {
        list.append(sessionCard(item));
      }); section.append(list); root.append(section);
    });
  }
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => { state.view = button.dataset.view; document.querySelectorAll("[data-view]").forEach((item) => { const active = item === button; item.setAttribute("aria-pressed", String(active)); item.classList.toggle("secondary", !active); }); render(); }));
  byId("schedule-search").addEventListener("input", (event) => { state.query = event.currentTarget.value.trim().toLowerCase(); render(); });
  [["track-filter", "track"], ["format-filter", "format"], ["room-filter", "room"]].forEach(([id, key]) => byId(id).addEventListener("change", (event) => { state[key] = event.currentTarget.value; render(); }));
  byId("download-calendar").addEventListener("click", downloadCalendar);
  byId("close-session-detail").addEventListener("click", () => byId("session-detail").close());
  byId("session-detail-itinerary").addEventListener("click", () => { if (state.selected) toggleItinerary(state.selected.id); byId("session-detail").close(); });
  function populateSelect(id, values) {
    const select = byId(id);
    values.filter(Boolean).sort((a, b) => a.localeCompare(b)).forEach((value) => select.append(new Option(value, value)));
  }
  function populateDiscovery() {
    populateSelect("track-filter", [...new Set(state.model.items.map((item) => item.track_name || "No track"))]);
    populateSelect("format-filter", [...new Set(state.model.items.map((item) => item.format_name))]);
    populateSelect("room-filter", [...new Set(state.model.items.map((item) => item.room_name))]);
    const days = [...new Map(state.model.items.map((item) => [dayKey(item), day(item)])).entries()];
    const tabs = byId("day-tabs"); tabs.hidden = days.length < 2;
    const all = make("button", "All days", "secondary"); all.type = "button"; all.setAttribute("aria-pressed", "true");
    all.addEventListener("click", () => { state.day = ""; [...tabs.children].forEach((button) => button.setAttribute("aria-pressed", String(button === all))); render(); });
    tabs.append(all);
    days.forEach(([key, label]) => {
      const button = make("button", label, "secondary"); button.type = "button"; button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => { state.day = key; [...tabs.children].forEach((item) => item.setAttribute("aria-pressed", String(item === button))); render(); });
      tabs.append(button);
    });
  }
  async function load() {
    if (!eventId) throw new Error("Invalid schedule link");
    let body;
    try {
      body = await window.SessionBuddyApi.request(`/api/v1/public/events/${encodeURIComponent(eventId)}/schedule${location.search}`);
    } catch (error) {
      const filters = new URLSearchParams(location.search);
      if (![401, 403].includes(error.status) || filters.has("track_id") || filters.has("track")) throw error;
      body = await window.SessionBuddyApi.request(`/api/v1/events/${encodeURIComponent(eventId)}/schedule`);
    }
    state.model = body; document.body.classList.toggle("embedded", embedded);
    if (location.pathname.endsWith("/itinerary")) state.view = "mine";
    const hasPublishedSchedule = Boolean(body.revision);
    document.querySelector(".schedule-discovery").hidden = !hasPublishedSchedule;
    document.querySelector(".schedule-filters").hidden = sessionsOnly || !hasPublishedSchedule;
    byId("results-summary").hidden = !hasPublishedSchedule;
    const eventName = body.event.name;
    const monogram = eventName.trim().slice(0, 2).toUpperCase() || "EV";
    byId("title").textContent = sessionsOnly ? "Sessions" : "Schedule";
    const timezone = byId("timezone");
    if (body.revision) {
      let timezoneName = body.event.time_zone;
      try {
        timezoneName = new Intl.DateTimeFormat(undefined, {
          timeZone: body.event.time_zone,
          timeZoneName: "long",
        }).formatToParts(new Date()).find((part) => part.type === "timeZoneName")?.value || timezoneName;
      } catch (_) { /* Keep the configured label when Intl cannot resolve it. */ }
      timezone.textContent = `All session times use ${timezoneName}.`;
      timezone.hidden = false;
    } else {
      timezone.textContent = "";
      timezone.hidden = true;
    }
    byId("status").textContent = body.revision
      ? `${body.items.length} published session${body.items.length === 1 ? "" : "s"}.`
      : "Schedule coming soon.";
    if (body.event.accent_color) document.documentElement.style.setProperty("--event-accent", body.event.accent_color);
    window.SessionBuddyPublicEventMasthead.render(document.querySelector("[data-public-event-masthead]"), {
      event: { id: body.event.id, name: eventName, accentColor: body.event.accent_color, logoUrl: body.event.logo_url, coverUrl: body.event.cover_image_url, cfpUrl: body.event.cfp_url },
      active: "schedule", embedded,
    });
    populateDiscovery(); saveItinerary(); render();
    const requestedParams = new URLSearchParams(location.search);
    const requestedSearch = requestedParams.get("search") || "";
    if (requestedSearch) { state.query = requestedSearch.trim().toLowerCase(); byId("schedule-search").value = requestedSearch; render(); }
  }
  load().catch((error) => { byId("status").textContent = window.SessionBuddyApi.message(error, "The schedule is unavailable. Try again."); byId("status").classList.add("error"); });
})();

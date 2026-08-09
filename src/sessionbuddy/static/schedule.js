(() => {
  "use strict";
  const match = location.pathname.match(/^\/(?:embeds\/)?events\/([^/]+)\/(?:schedule|sessions)$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(eventId)) eventId = "";
  const embedded = location.pathname.startsWith("/embeds/");
  const sessionsOnly = location.pathname.endsWith("/sessions");
  const storageKey = `sessionbuddy:itinerary:${eventId}`;
  const state = { model: null, view: "list", query: "", itinerary: new Set() };
  try { state.itinerary = new Set(JSON.parse(localStorage.getItem(storageKey) || "[]")); } catch (_) { state.itinerary = new Set(); }
  const byId = (id) => document.getElementById(id);
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
  function format(value, options) { try { return new Intl.DateTimeFormat(undefined, { timeZone: state.model.event.time_zone, ...options }).format(new Date(value)); } catch (_) { return "Date unavailable"; } }
  function day(item) { return format(item.start_at_ms, { weekday: "long", month: "long", day: "numeric" }); }
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
  function render() {
    const source = state.view === "mine" ? state.model.items.filter((item) => state.itinerary.has(item.id)) : state.model.items;
    const visible = state.query ? source.filter((item) => [item.title, item.description, item.speaker_names, item.room_name, item.track_name].join(" ").toLowerCase().includes(state.query)) : source;
    const groups = new Map();
    visible.forEach((item) => { const key = group(item); if (!groups.has(key)) groups.set(key, []); groups.get(key).push(item); });
    const root = byId("schedule"); root.replaceChildren(); root.classList.toggle("week-view", state.view === "week");
    byId("results-summary").textContent = state.query || state.view === "mine"
      ? `${visible.length} of ${source.length} session${source.length === 1 ? "" : "s"} shown.`
      : `${visible.length} published session${visible.length === 1 ? "" : "s"}.`;
    byId("empty").hidden = visible.length !== 0;
    byId("download-calendar").disabled = state.itinerary.size === 0;
    byId("empty").querySelector("strong").textContent = state.view === "mine" ? "Choose + on a session to build your itinerary." : "No sessions are published yet.";
    [...groups].forEach(([name, items]) => {
      const section = make("section", undefined, "schedule-group"); section.append(make("h2", name));
      const list = make("ol", undefined, "schedule-list");
      items.sort((a, b) => a.start_at_ms - b.start_at_ms).forEach((item) => {
        const row = make("li", undefined, "schedule-item");
        const time = make("time", `${format(item.start_at_ms, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}–${format(item.end_at_ms, { hour: "numeric", minute: "2-digit" })}`); time.dateTime = new Date(item.start_at_ms).toISOString();
        const details = document.createElement("div"); details.append(make("h3", item.title), make("p", `${item.speaker_names || "Speaker TBA"} · ${item.room_name}${item.track_name ? ` · ${item.track_name}` : ""}`));
        if (item.description) details.append(make("p", item.description, "description"));
        const add = make("button", state.itinerary.has(item.id) ? "✓" : "+", "itinerary-button"); add.type = "button"; add.setAttribute("aria-pressed", String(state.itinerary.has(item.id))); add.setAttribute("aria-label", `${state.itinerary.has(item.id) ? "Remove" : "Add"} ${item.title} ${state.itinerary.has(item.id) ? "from" : "to"} my itinerary`); add.addEventListener("click", () => toggleItinerary(item.id));
        row.append(time, details);
        if (!sessionsOnly) row.append(add);
        list.append(row);
      }); section.append(list); root.append(section);
    });
  }
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => { state.view = button.dataset.view; document.querySelectorAll("[data-view]").forEach((item) => { const active = item === button; item.setAttribute("aria-pressed", String(active)); item.classList.toggle("secondary", !active); }); render(); }));
  byId("schedule-search").addEventListener("input", (event) => { state.query = event.currentTarget.value.trim().toLowerCase(); render(); });
  byId("download-calendar").addEventListener("click", downloadCalendar);
  async function load() {
    if (!eventId) throw new Error("Invalid schedule link");
    let body;
    try {
      body = await window.SessionBuddyApi.request(`/api/v1/public/events/${encodeURIComponent(eventId)}/schedule`);
    } catch (error) {
      if (![401, 403].includes(error.status)) throw error;
      body = await window.SessionBuddyApi.request(`/api/v1/events/${encodeURIComponent(eventId)}/schedule`);
    }
    state.model = body; document.body.classList.toggle("embedded", embedded);
    if (sessionsOnly) document.querySelector(".schedule-filters").hidden = true;
    byId("title").textContent = sessionsOnly ? `${body.event.name} sessions` : body.event.name; byId("speakers-link").href = `/events/${encodeURIComponent(eventId)}/speakers`;
    byId("timezone").textContent = body.revision
      ? `Times shown in ${body.event.time_zone}. Published revision ${body.revision.revision_number}.`
      : `Times will be shown in ${body.event.time_zone}.`;
    byId("status").textContent = body.revision
      ? `${body.items.length} published session${body.items.length === 1 ? "" : "s"}.`
      : "The organizer has not published the schedule yet.";
    if (body.event.accent_color) document.documentElement.style.setProperty("--blue", body.event.accent_color);
    if (body.event.logo_url) { byId("event-logo").src = body.event.logo_url; byId("event-logo").hidden = false; }
    if (body.event.cover_image_url) { byId("event-cover").src = body.event.cover_image_url; byId("event-cover").alt = `${body.event.name} cover`; byId("event-cover").hidden = false; }
    saveItinerary(); render();
  }
  load().catch((error) => { byId("status").textContent = window.SessionBuddyApi.message(error, "The schedule is unavailable. Try again."); byId("status").classList.add("error"); });
})();

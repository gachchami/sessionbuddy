(() => {
  "use strict";
  const REFRESH_MS = 5000;
  const ALLOWED_STATES = new Set(["", "open", "completed", "overdue", "due_soon"]);
  const ALLOWED_TASK_TYPES = new Set(["", "biography", "profile", "headshot", "slides", "supporting_document"]);
  const routeMatch = location.pathname.match(/^\/admin\/events\/([^/]+)\/onboarding$/);
  let eventId = "";
  try { eventId = routeMatch ? decodeURIComponent(routeMatch[1]) : ""; } catch (_) { eventId = ""; }
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(eventId)) eventId = "";
  const state = { timer: null, loading: false, cursor: null, rows: [], lastSuccess: null, timeZone: "UTC", csrf: "", channel: null };
  const byId = (id) => document.getElementById(id);
  const make = (tag, value, className) => { const node = document.createElement(tag); if (value !== undefined) node.textContent = value; if (className) node.className = className; return node; };

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }

  function setConnection(kind, label) {
    byId("connection-dot").className = kind;
    byId("connection-state").textContent = label;
    byId("last-refreshed").textContent = state.lastSuccess
      ? `Last refreshed ${state.lastSuccess.toLocaleTimeString()}` : "Not refreshed yet";
  }

  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = {
      schema_version: 1, page_template: "/admin/events/{event_id}/onboarding",
      navigation_type: navigation?.type || "unknown",
      device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop",
      sampled: false, lcp_ms: null, inp_ms: null, cls: null,
      ttfb_ms: navigation?.responseStart ?? null, fcp_ms: null, route_transition_ms: null,
      critical_api_ms: Math.max(0, performance.now() - started),
      api_request_id: response.headers.get("x-request-id")
    };
  }

  async function api(path, options = {}) {
    const started = performance.now();
    return window.SessionBuddyApi.request(path, options, {
      onResponse: (response) => recordTelemetry(started, response)
    });
  }

  function selectedFilters() {
    const selectedState = byId("state").value;
    const taskType = byId("task-type").value;
    return {
      state: ALLOWED_STATES.has(selectedState) ? selectedState : "",
      task_type: ALLOWED_TASK_TYPES.has(taskType) ? taskType : ""
    };
  }

  function query(cursor = null) {
    const params = new URLSearchParams();
    const filters = selectedFilters();
    if (filters.state) params.set("state", filters.state);
    if (filters.task_type) params.set("task_type", filters.task_type);
    if (cursor) params.set("cursor", cursor);
    return params.toString();
  }

  function preserveFilters() {
    const params = new URLSearchParams();
    const filters = selectedFilters();
    if (filters.state) params.set("state", filters.state);
    if (filters.task_type) params.set("task_type", filters.task_type);
    history.replaceState(null, "", `${location.pathname}${params.size ? `?${params}` : ""}`);
  }

  function formatDate(value) {
    if (value === null || value === undefined) return "No due date";
    try {
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short", timeZone: state.timeZone
      }).format(new Date(value));
    } catch (_) { return "Date unavailable"; }
  }

  function isOverdue(row) { return row.state === "overdue"; }

  function addTableRow(row) {
    const tr = document.createElement("tr");
    const speaker = document.createElement("td");
    const link = make("a", row.display_name); link.href = speakerLink(row); speaker.append(link);
    tr.append(speaker);
    [row.proposal_title, row.task_title, formatDate(row.due_at_ms), formatDate(row.last_activity_at_ms)].forEach((value, index) => {
      const td = make("td", value, index === 1 ? "task-name" : index === 2 && isOverdue(row) ? "overdue" : ""); tr.append(td);
    });
    const action = document.createElement("td");
    const button = make("button", "Send reminder", "compact secondary"); button.type = "button";
    button.addEventListener("click", () => sendReminder(row, button)); action.append(button); tr.append(action);
    byId("onboarding-rows").append(tr);
  }

  function addCard(row) {
    const item = document.createElement("li");
    const heading = document.createElement("h3");
    const speaker = make("a", row.display_name); speaker.href = speakerLink(row); heading.append(speaker); item.append(heading);
    const list = document.createElement("dl");
    [["Session", row.proposal_title], ["Missing", row.task_title], ["Due", formatDate(row.due_at_ms)], ["Last activity", formatDate(row.last_activity_at_ms)]].forEach(([label, value]) => {
      list.append(make("dt", label), make("dd", value, label === "Due" && isOverdue(row) ? "overdue" : ""));
    });
    const reminder = make("button", "Send reminder", "compact secondary"); reminder.type = "button";
    reminder.addEventListener("click", () => sendReminder(row, reminder));
    item.append(list, reminder); byId("onboarding-cards").append(item);
  }

  function speakerLink(row) {
    return `/admin/events/${encodeURIComponent(eventId)}/speakers/${encodeURIComponent(row.event_speaker_id)}`;
  }

  async function sendReminder(row, button) {
    button.disabled = true;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-tasks/${encodeURIComponent(row.task_id)}/reminders`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` },
        body: "{}"
      });
      setStatus(`Reminder queued for ${row.display_name}.`);
    } catch (error) {
      setStatus(error.status === 404
        ? "Reminder delivery is not connected in this environment yet. No message was sent."
        : "The reminder could not be queued. No message was sent.", true);
    } finally { button.disabled = false; }
  }

  function render(data, append) {
    const summary = data.summary;
    state.timeZone = data.time_zone;
    byId("event-name").textContent = `${data.event_name} · Dates shown in ${data.time_zone}`;
    byId("count-complete").textContent = summary.complete;
    byId("count-incomplete").textContent = summary.incomplete;
    byId("count-overdue").textContent = summary.overdue;
    byId("count-due-soon").textContent = summary.due_soon;
    const incoming = data.data;
    if (!append) {
      state.rows = [];
      byId("onboarding-rows").replaceChildren();
      byId("onboarding-cards").replaceChildren();
    }
    state.rows.push(...incoming);
    incoming.forEach((row) => { addTableRow(row); addCard(row); });
    byId("result-count").textContent = String(state.rows.length);
    byId("empty").hidden = state.rows.length !== 0;
    byId("load-more").hidden = !data.next_cursor;
    state.cursor = data.next_cursor || null;
  }

  async function refresh({ append = false, announce = false } = {}) {
    if (state.loading || document.hidden) return;
    state.loading = true;
    byId("results-panel").setAttribute("aria-busy", "true");
    setConnection("", state.lastSuccess ? "Refreshing" : "Connecting");
    try {
      const filterQuery = query(append ? state.cursor : null);
      const endpoint = `/api/v1/admin/events/${encodeURIComponent(eventId)}/onboarding`;
      const data = await api(`${endpoint}${filterQuery ? `?${filterQuery}` : ""}`);
      render(data, append);
      state.lastSuccess = new Date(data.generated_at_ms);
      setConnection("live", "Live snapshot");
      setStatus(announce ? "Onboarding snapshot refreshed." : `${state.rows.length} speaker record${state.rows.length === 1 ? "" : "s"} shown.`);
    } catch (error) {
      const authMessage = error.status === 401 || error.status === 403
        ? "Your account cannot access this event."
        : "Live refresh failed. Showing the last successful snapshot while reconnecting.";
      setStatus(authMessage, true);
      setConnection("stale", error.status === 401 || error.status === 403 ? "Access unavailable" : "Reconnecting");
    } finally {
      state.loading = false;
      byId("results-panel").setAttribute("aria-busy", "false");
    }
  }

  function startPolling() {
    clearInterval(state.timer);
    if (!document.hidden) state.timer = setInterval(() => refresh(), REFRESH_MS);
  }

  function connectInvalidations() {
    if (!("BroadcastChannel" in window)) return;
    state.channel?.close();
    state.channel = new BroadcastChannel(`sessionbuddy:onboarding:${eventId}`);
    state.channel.addEventListener("message", () => refresh());
  }

  function initializeFilters() {
    const params = new URLSearchParams(location.search);
    const selectedState = params.get("state") || "";
    const taskType = params.get("task_type") || "";
    byId("state").value = ALLOWED_STATES.has(selectedState) ? selectedState : "";
    byId("task-type").value = ALLOWED_TASK_TYPES.has(taskType) ? taskType : "";
    preserveFilters();
  }

  byId("filters").addEventListener("submit", (event) => { event.preventDefault(); preserveFilters(); refresh({ announce: true }); });
  byId("clear-filters").addEventListener("click", () => { byId("filters").reset(); preserveFilters(); refresh({ announce: true }); });
  byId("refresh").addEventListener("click", () => refresh({ announce: true }));
  byId("load-more").addEventListener("click", () => refresh({ append: true, announce: true }));
  document.addEventListener("visibilitychange", () => {
    startPolling();
    if (document.hidden) setConnection("", "Updates paused");
    else refresh({ announce: true });
  });
  window.addEventListener("online", () => refresh({ announce: true }));
  window.addEventListener("offline", () => setConnection("stale", "Offline · snapshot retained"));
  window.addEventListener("pageshow", (event) => { if (event.persisted) refresh({ announce: true }); });
  window.addEventListener("sessionbuddy:onboarding-invalidated", () => refresh());

  async function initialize() {
    if (!eventId) {
      setStatus("This onboarding link is invalid. Return to Programs and choose an event.", true);
      setConnection("stale", "Invalid event link");
      return;
    }
    initializeFilters();
    try {
      const session = await api("/api/v1/session");
      state.csrf = session.csrf_token;
      await refresh();
      connectInvalidations();
      startPolling();
    } catch (error) {
      setStatus(error.status === 401 ? "Sign in as an event administrator to continue." : "We couldn’t verify your admin session.", true);
      setConnection("stale", "Access unavailable");
    }
  }
  initialize();
})();

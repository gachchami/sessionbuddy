(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/workspace$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const state = { csrf: "" };

  function idempotencyKey() {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }
  const api = (path, options = {}, behavior = {}) => window.SessionBuddyApi.request(path, options, behavior);
  function mutationHeaders() { return { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() }; }

  byId("token-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget;
    const label = new FormData(form).get("label");
    try {
      const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/integrations/accelevents/tokens`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ label }) });
      byId("token-value").textContent = body.token; byId("token-result").hidden = false; form.reset(); setStatus("Read-only integration token generated.");
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), true); }
  });
  function renderEmbed() {
    const type = byId("embed-type").value;
    const enabled = byId("embed-enabled").checked;
    const title = byId("embed-title").value.trim() || "Event schedule";
    const height = Math.max(320, Math.min(1600, Number(byId("embed-height").value) || 640));
    const encoded = encodeURIComponent(eventId);
    const publicUrl = `${location.origin}/events/${encoded}/${type}`;
    const embedUrl = `${location.origin}/embeds/events/${encoded}/${type}`;
    byId("embed-url").value = publicUrl;
    byId("open-embed").href = publicUrl;
    byId("embed-code").value = enabled
      ? `<iframe src="${embedUrl}" title="${title.replaceAll('"', '&quot;')}" loading="lazy" style="width:100%;min-height:${height}px;border:0"></iframe>`
      : "Embed disabled. Enable it to generate code.";
    byId("copy-embed").disabled = !enabled;
    localStorage.setItem(`sessionbuddy:embed:${eventId}`, JSON.stringify({ type, enabled, title, height }));
  }
  byId("embed-builder").addEventListener("input", renderEmbed);
  byId("copy-embed").addEventListener("click", async () => {
    const field = byId("embed-code");
    try { await navigator.clipboard.writeText(field.value); setStatus("Embed code copied."); }
    catch (_) { field.focus(); field.select(); setStatus("Copy the selected embed code."); }
  });
  async function initialize() {
    if (!eventId) throw new Error("Invalid event link.");
    const session = await api("/api/v1/auth/session"); state.csrf = session.csrf_token;
    try {
      const saved = JSON.parse(localStorage.getItem(`sessionbuddy:embed:${eventId}`) || "null");
      if (saved) { byId("embed-type").value = saved.type || "schedule"; byId("embed-enabled").checked = saved.enabled !== false; byId("embed-title").value = saved.title || "Event schedule"; byId("embed-height").value = saved.height || 640; }
    } catch (_) { /* use defaults */ }
    renderEmbed();
    byId("event-id").textContent = eventId; byId("api-base").textContent = `${location.origin}/v1`;
    setStatus("Publishing tools ready.");
  }
  initialize().catch((error) => { if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true); });
})();

(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/workspace$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const state = { csrf: "", tokenMutation: null, embeds: [] };

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
  function mutationHeaders(key = idempotencyKey()) { return { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": key }; }

  byId("token-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = event.currentTarget;
    const label = new FormData(form).get("label");
    const payload = JSON.stringify({ label });
    if (!state.tokenMutation || state.tokenMutation.payload !== payload) {
      state.tokenMutation = { payload, key: idempotencyKey() };
    }
    try {
      const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/integrations/accelevents/tokens`, { method: "POST", headers: mutationHeaders(state.tokenMutation.key), body: payload });
      state.tokenMutation = null;
      byId("token-value").textContent = body.token; byId("token-result").hidden = false; form.reset(); setStatus("Read-only integration token generated.");
    } catch (error) {
      setStatus(window.SessionBuddyApi.message(error), true);
    }
  });
  function renderEmbed() {
    const type = byId("embed-type").value;
    const title = byId("embed-title").value.trim() || "Event schedule";
    const height = Math.max(320, Math.min(1600, Number(byId("embed-height").value) || 640));
    const output = byId("embed-output").value;
    const track = byId("embed-track").value.trim();
    const customCss = byId("embed-css").value.trim();
    const encoded = encodeURIComponent(eventId);
    byId("publish-agenda").href = `/admin/events/${encoded}/agenda`;
    const publicUrl = `${location.origin}/events/${encoded}/${type === "itinerary" ? "schedule" : type}`;
    const query = track ? `?track=${encodeURIComponent(track)}` : "";
    const embedUrl = `${location.origin}/embeds/events/${encoded}/${type}${query}`;
    const feedBase = `${location.origin}/api/v1/public/events/${encoded}/schedule`;
    byId("embed-url").value = publicUrl;
    byId("open-embed").href = publicUrl;
    const outputs = {
      iframe: `<iframe src="${embedUrl}" title="${title.replaceAll('"', '&quot;')}" loading="lazy" style="width:100%;min-height:${height}px;border:0"></iframe>${customCss ? `\n<style>${customCss}</style>` : ""}`,
      html: `<a href="${publicUrl}${query}">${title.replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</a>`,
      json: feedBase,
      xml: `${feedBase}.xml`,
      ical: `${feedBase}.ics`,
    };
    byId("embed-code").value = outputs[output];
  }
  byId("embed-builder").addEventListener("input", renderEmbed);
  byId("copy-embed").addEventListener("click", async () => {
    const field = byId("embed-code");
    try { await navigator.clipboard.writeText(field.value); setStatus("Embed code copied."); }
    catch (_) { field.focus(); field.select(); setStatus("Copy the selected embed code."); }
  });
  function saveRegistry() { localStorage.setItem(`sessionbuddy:embeds:${eventId}`, JSON.stringify(state.embeds)); }
  function renderRegistry() {
    const root = byId("embed-registry"); root.replaceChildren();
    if (!state.embeds.length) { root.append(Object.assign(document.createElement("p"), { className: "help", textContent: "No saved presets yet." })); return; }
    state.embeds.forEach((preset) => {
      const row = document.createElement("article"); row.className = "item-row";
      const content = document.createElement("div");
      const title = document.createElement("strong"); title.textContent = preset.name;
      const meta = document.createElement("p"); meta.className = "help"; meta.textContent = `${preset.type} · ${preset.output} · ${preset.enabled ? "enabled" : "disabled"}`;
      content.append(title, meta);
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "secondary"; remove.textContent = "Remove";
      remove.addEventListener("click", () => { state.embeds = state.embeds.filter((item) => item.id !== preset.id); saveRegistry(); renderRegistry(); });
      row.append(content, remove); root.append(row);
    });
  }
  byId("save-embed").addEventListener("click", () => {
    const preset = { id: crypto.randomUUID(), name: byId("embed-name").value.trim() || "Untitled preset", type: byId("embed-type").value, output: byId("embed-output").value, enabled: byId("embed-enabled").checked, track: byId("embed-track").value.trim(), custom_css: byId("embed-css").value.trim(), code: byId("embed-code").value };
    state.embeds.unshift(preset); saveRegistry(); renderRegistry(); setStatus(`Saved “${preset.name}”.`);
  });
  async function initialize() {
    if (!eventId) throw new Error("Invalid event link.");
    try {
      state.embeds = JSON.parse(localStorage.getItem(`sessionbuddy:embeds:${eventId}`) || "[]");
    } catch (_) { /* use defaults */ }
    renderEmbed(); renderRegistry();
    byId("event-id").textContent = eventId; byId("api-base").textContent = `${location.origin}/v1`;
    setStatus("Sharing tools ready. Checking integration access…");
    const session = await api("/api/v1/auth/session"); state.csrf = session.csrf_token;
    byId("generate-token").disabled = false;
    setStatus("Sharing and integration tools ready.");
  }
  initialize().catch((error) => {
    if (window.SessionBuddyApi.redirectIfSignedOut(error)) return;
    if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error) || window.SessionBuddyApi.redirectIfDocumentAccessChanged(error)) return;
    setStatus(window.SessionBuddyApi.message(error), true);
  });
})();

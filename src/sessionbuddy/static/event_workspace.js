(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/workspace$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const state = { csrf: "" };

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  function slug(value) { return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80); }
  function mutationHeaders() { return { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` }; }
  function approvedEmbed(value) {
    if (!value) return true;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password && new Set([
        "www.youtube.com", "youtube.com", "www.youtube-nocookie.com", "player.vimeo.com",
        "docs.google.com", "drive.google.com", "calendar.google.com"
      ]).has(url.hostname.toLowerCase());
    } catch (_) { return false; }
  }

  async function loadResources() {
    const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/resources`);
    byId("resource-count").textContent = body.data.length;
    const nodes = body.data.map((item) => {
      const li = document.createElement("li");
      const title = document.createElement("strong"); title.textContent = item.title;
      const meta = document.createElement("span"); meta.className = "muted"; meta.textContent = `${item.status} · /${item.slug}`;
      li.append(title, meta); return li;
    });
    byId("resource-list").replaceChildren(...nodes);
  }
  async function loadTargets() {
    const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-targets`);
    const accepted = body.data.filter((item) => item.selection_status === "accepted");
    const options = accepted.map((item) => { const option = new Option(`${item.display_name} — ${item.proposal_title}`, item.event_speaker_id); return option; });
    byId("speaker-target").append(...options);
  }
  byId("resource-form").elements.title.addEventListener("input", (event) => {
    const slugInput = event.currentTarget.form.elements.slug;
    if (!slugInput.dataset.edited) slugInput.value = slug(event.currentTarget.value);
  });
  byId("resource-form").elements.slug.addEventListener("input", (event) => { event.currentTarget.dataset.edited = "true"; });
  byId("resource-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  byId("resource-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget));
    const embed = event.currentTarget.elements.embed_url;
    embed.setCustomValidity(approvedEmbed(values.embed_url) ? "" : "Use an approved HTTPS Google, YouTube, or Vimeo URL.");
    if (!event.currentTarget.reportValidity()) return;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/resources`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ title: values.title, slug: values.slug, summary: values.summary, body_text: values.body_text, embed_url: values.embed_url || null, status: values.status, sort_order: Number(values.sort_order) }) });
      event.currentTarget.reset(); delete event.currentTarget.elements.slug.dataset.edited; event.currentTarget.elements.sort_order.value = "0"; setStatus("Resource published to the speaker portal."); await loadResources();
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), true); }
  });
  byId("task-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget));
    const label = event.currentTarget.elements.field_label;
    label.setCustomValidity(values.field_required && !String(values.field_label || "").trim() ? "Enter the required response question." : "");
    if (!event.currentTarget.reportValidity()) return;
    const fields = values.field_label ? [{ key: "response", label: values.field_label, type: values.field_type, required: Boolean(values.field_required), choices: [] }] : [];
    const due = values.due_at ? new Date(values.due_at).getTime() : null;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-tasks`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ event_speaker_id: values.event_speaker_id, submission_id: null, title: values.title, help_text: values.help_text, due_at_ms: due, fields }) });
      event.currentTarget.reset(); setStatus("Custom task assigned to the speaker.");
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), true); }
  });
  byId("task-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  byId("token-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const label = new FormData(event.currentTarget).get("label");
    try {
      const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/integrations/accelevents/tokens`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ label }) });
      byId("token-value").textContent = body.token; byId("token-result").hidden = false; event.currentTarget.reset(); setStatus("Read-only integration token generated.");
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), true); }
  });
  async function initialize() {
    if (!eventId) throw new Error("Invalid event link.");
    const session = await api("/api/v1/session"); state.csrf = session.csrf_token;
    byId("event-id").textContent = eventId; byId("api-base").textContent = `${location.origin}/v1`;
    byId("onboarding-link").href = `/admin/events/${encodeURIComponent(eventId)}/onboarding`;
    await Promise.all([loadResources(), loadTargets()]); setStatus("Resources ready.");
  }
  initialize().catch((error) => { if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true); });
})();

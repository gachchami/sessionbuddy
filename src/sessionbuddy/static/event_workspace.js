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
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const body = await response.json().catch(() => null);
    if (!response.ok) throw Object.assign(new Error(body?.error?.message || `Request failed (${response.status})`), { status: response.status });
    return body;
  }
  function slug(value) { return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80); }
  function mutationHeaders() { return { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` }; }

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
  byId("resource-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/resources`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ title: values.title, slug: values.slug, summary: values.summary, body_text: values.body_text, embed_url: values.embed_url || null, status: values.status, sort_order: Number(values.sort_order) }) });
      event.currentTarget.reset(); event.currentTarget.elements.sort_order.value = "0"; setStatus("Resource published to the speaker portal."); await loadResources();
    } catch (error) { setStatus(error.message, true); }
  });
  byId("task-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget));
    const fields = values.field_label ? [{ key: "response", label: values.field_label, type: values.field_type, required: Boolean(values.field_required), choices: [] }] : [];
    const due = values.due_at ? new Date(values.due_at).getTime() : null;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-tasks`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ event_speaker_id: values.event_speaker_id, submission_id: null, title: values.title, help_text: values.help_text, due_at_ms: due, fields }) });
      event.currentTarget.reset(); setStatus("Custom task assigned to the speaker.");
    } catch (error) { setStatus(error.message, true); }
  });
  byId("token-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const label = new FormData(event.currentTarget).get("label");
    try {
      const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/integrations/accelevents/tokens`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ label }) });
      byId("token-value").textContent = body.token; byId("token-result").hidden = false; setStatus("Read-only integration token generated.");
    } catch (error) { setStatus(error.message, true); }
  });
  async function initialize() {
    if (!eventId) throw new Error("Invalid event workspace link.");
    const session = await api("/api/v1/session"); state.csrf = session.csrf_token;
    byId("event-id").textContent = eventId; byId("api-base").textContent = `${location.origin}/v1`;
    byId("onboarding-link").href = `/admin/events/${encodeURIComponent(eventId)}/onboarding`;
    await Promise.all([loadResources(), loadTargets()]); setStatus("Workspace ready.");
  }
  initialize().catch((error) => { if (error.status === 401) location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`); else setStatus(error.message, true); });
})();

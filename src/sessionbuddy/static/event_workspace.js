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
  const api = (path, options = {}, behavior = {}) => window.SessionBuddyApi.request(path, options, behavior);
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
    if (!nodes.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "No resources yet.";
      nodes.push(empty);
    }
    byId("resource-list").replaceChildren(...nodes);
  }
  function fileSize(value) {
    if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }
  async function downloadAsset(asset, button) {
    button.disabled = true;
    try {
      const grant = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/assets/${encodeURIComponent(asset.id)}/download-grants`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
        body: "{}"
      });
      let response;
      await api("/api/v1/assets/download", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
        body: JSON.stringify({ token: grant.token })
      }, {
        expectJson: false,
        onResponse: (received) => { response = received; }
      });
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = asset.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus(`${asset.filename} downloaded.`);
    } catch (error) {
      setStatus(window.SessionBuddyApi.message(error, "The file could not be downloaded."), true);
    } finally { button.disabled = false; }
  }
  async function loadAssets() {
    const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/assets`);
    byId("file-count").textContent = body.data.length;
    const nodes = body.data.map((asset) => {
      const item = document.createElement("li");
      const title = document.createElement("strong"); title.textContent = asset.filename;
      const meta = document.createElement("span"); meta.className = "muted";
      meta.textContent = `${asset.speaker_name} · ${asset.kind.replaceAll("_", " ")} · ${fileSize(asset.byte_size)} · ${asset.version_count} version${asset.version_count === 1 ? "" : "s"}`;
      const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = "Download";
      button.addEventListener("click", () => downloadAsset(asset, button));
      item.append(title, meta, button); return item;
    });
    if (!nodes.length) {
      const empty = document.createElement("li"); empty.className = "empty";
      empty.textContent = "No safety-checked speaker files yet."; nodes.push(empty);
    }
    byId("file-list").replaceChildren(...nodes);
  }
  async function loadTargets() {
    const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-targets`);
    const accepted = body.data.filter((item) => item.selection_status === "accepted");
    const options = accepted.map((item) => { const option = new Option(`${item.display_name} — ${item.proposal_title}`, item.event_speaker_id); return option; });
    byId("speaker-target").replaceChildren(...options);
    if (!accepted.length) {
      const option = new Option("No accepted speakers yet", "");
      option.disabled = true;
      byId("speaker-target").append(option);
    }
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
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const values = Object.fromEntries(data);
    const speakerIds = data.getAll("event_speaker_id").filter(Boolean);
    const label = event.currentTarget.elements.field_label;
    label.setCustomValidity(values.field_required && !String(values.field_label || "").trim() ? "Enter the required response question." : "");
    if (!event.currentTarget.reportValidity()) return;
    const fields = values.field_label ? [{ key: "response", label: values.field_label, type: values.field_type, required: Boolean(values.field_required), choices: [] }] : [];
    const due = values.due_at ? new Date(values.due_at).getTime() : null;
    try {
      await Promise.all(speakerIds.map((eventSpeakerId) =>
        api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-tasks`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ event_speaker_id: eventSpeakerId, submission_id: null, title: values.title, help_text: values.help_text, due_at_ms: due, fields }) })
      ));
      event.currentTarget.reset();
      setStatus(`Task assigned to ${speakerIds.length} speaker${speakerIds.length === 1 ? "" : "s"}.`);
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
  document.querySelectorAll(".copy-share").forEach((button) => button.addEventListener("click", async () => {
    const field = byId(button.dataset.copy);
    try { await navigator.clipboard.writeText(field.value); setStatus("Embed code copied."); }
    catch (_) { field.focus(); field.select(); setStatus("Copy the selected embed code."); }
  }));
  async function initialize() {
    if (!eventId) throw new Error("Invalid event link.");
    const session = await api("/api/v1/auth/session"); state.csrf = session.csrf_token;
    const encoded = encodeURIComponent(eventId);
    const scheduleUrl = `${location.origin}/events/${encoded}/schedule`;
    const speakersUrl = `${location.origin}/events/${encoded}/speakers`;
    byId("schedule-url").value = scheduleUrl; byId("speakers-url").value = speakersUrl;
    byId("schedule-embed").value = `<iframe src="${location.origin}/embeds/events/${encoded}/schedule" title="Event schedule" loading="lazy" style="width:100%;min-height:640px;border:0"></iframe>`;
    byId("speakers-embed").value = `<iframe src="${location.origin}/embeds/events/${encoded}/speakers" title="Event speakers" loading="lazy" style="width:100%;min-height:640px;border:0"></iframe>`;
    byId("open-schedule").href = scheduleUrl; byId("open-speakers").href = speakersUrl;
    byId("event-id").textContent = eventId; byId("api-base").textContent = `${location.origin}/v1`;
    byId("onboarding-link").href = `/admin/events/${encodeURIComponent(eventId)}/onboarding`;
    const results = await Promise.allSettled([loadResources(), loadTargets(), loadAssets()]);
    if (results.some((result) => result.status === "rejected")) {
      setStatus("Some workspace information could not be loaded. Refresh to try again.", true);
    } else setStatus("Workspace ready.");
  }
  initialize().catch((error) => { if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true); });
})();

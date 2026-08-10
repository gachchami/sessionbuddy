(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/workspace$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const state = { csrf: "", timeZone: "", taskMutation: null };

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }
  const api = (path, options = {}, behavior = {}) => window.SessionBuddyApi.request(path, options, behavior);
  async function loadEventTimeZone() {
    const event = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
    if (!event?.time_zone) throw new Error("The event time zone could not be loaded.");
    return event.time_zone;
  }
  function partsInTimeZone(value) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: state.timeZone,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23"
    }).formatToParts(new Date(value));
    return Object.fromEntries(parts
      .filter(({ type }) => type !== "literal")
      .map(({ type, value: part }) => [type, Number(part)]));
  }
  function inputMillis(value) {
    if (!value) return null;
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!match) return Number.NaN;
    const [, year, month, day, hour, minute] = match.map(Number);
    const intended = Date.UTC(year, month - 1, day, hour, minute);
    let timestamp = intended;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const actual = partsInTimeZone(timestamp);
      const actualAsUtc = Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute);
      const adjustment = intended - actualAsUtc;
      timestamp += adjustment;
      if (adjustment === 0) break;
    }
    const actual = partsInTimeZone(timestamp);
    return Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute) === intended
      ? timestamp
      : Number.NaN;
  }
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
  async function downloadAsset(asset, version, button) {
    button.disabled = true;
    try {
      const grant = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/assets/${encodeURIComponent(asset.id)}/versions/${encodeURIComponent(version.id)}/download-grants`, {
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
      link.href = url; link.download = version.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus(`${version.filename} downloaded.`);
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
      const currentComment = document.createElement("p"); currentComment.className = "help"; currentComment.textContent = asset.version_comment;
      const history = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = `${asset.versions.length} saved versions`;
      const versions = document.createElement("ol");
      asset.versions.forEach((version) => {
        const versionItem = document.createElement("li");
        const versionTitle = document.createElement("strong"); versionTitle.textContent = `Version ${version.generation} · ${version.filename}`;
        const versionMeta = document.createElement("span"); versionMeta.className = "muted"; versionMeta.textContent = `${version.state} · ${fileSize(version.byte_size)} · ${new Date(version.uploaded_at_ms).toLocaleString()}`;
        const comment = document.createElement("p"); comment.className = "help"; comment.textContent = version.version_comment;
        const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = "Download";
        button.addEventListener("click", () => downloadAsset(asset, version, button));
        versionItem.append(versionTitle, versionMeta, comment, button); versions.append(versionItem);
      });
      history.append(summary, versions);
      item.append(title, meta, currentComment, history); return item;
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
    const dueInput = event.currentTarget.elements.due_at;
    const due = inputMillis(values.due_at);
    dueInput.setCustomValidity(Number.isNaN(due) ? `Choose a valid local time in ${state.timeZone}.` : "");
    if (!event.currentTarget.reportValidity()) return;
    const fields = values.field_label ? [{ key: "response", label: values.field_label, type: values.field_type, required: Boolean(values.field_required), choices: [] }] : [];
    const payloads = speakerIds.map((eventSpeakerId) => ({ event_speaker_id: eventSpeakerId, submission_id: null, title: values.title, help_text: values.help_text, due_at_ms: due, fields }));
    const fingerprint = JSON.stringify(payloads);
    if (!state.taskMutation || state.taskMutation.fingerprint !== fingerprint) {
      state.taskMutation = {
        fingerprint,
        keys: new Map(speakerIds.map((eventSpeakerId) => [eventSpeakerId, `${crypto.randomUUID()}-${crypto.randomUUID()}`]))
      };
    }
    const button = event.currentTarget.querySelector('button[type="submit"], button:not([type])');
    button.disabled = true;
    try {
      await Promise.all(payloads.map((payload) =>
        api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-tasks`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": state.taskMutation.keys.get(payload.event_speaker_id) },
          body: JSON.stringify(payload)
        })
      ));
      event.currentTarget.reset();
      state.taskMutation = null;
      setStatus(`Task assigned to ${speakerIds.length} speaker${speakerIds.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(`${window.SessionBuddyApi.message(error)} Retry Assign task; the same request will not be duplicated.`, true);
    } finally { button.disabled = false; }
  });
  byId("task-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  byId("token-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const label = new FormData(event.currentTarget).get("label");
    try {
      const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/integrations/accelevents/tokens`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ label }) });
      byId("token-value").textContent = body.token; byId("token-result").hidden = false; event.currentTarget.reset(); setStatus("Read-only integration token generated.");
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
    state.timeZone = await loadEventTimeZone();
    byId("task-time-zone").textContent = state.timeZone;
    try {
      const saved = JSON.parse(localStorage.getItem(`sessionbuddy:embed:${eventId}`) || "null");
      if (saved) { byId("embed-type").value = saved.type || "schedule"; byId("embed-enabled").checked = saved.enabled !== false; byId("embed-title").value = saved.title || "Event schedule"; byId("embed-height").value = saved.height || 640; }
    } catch (_) { /* use defaults */ }
    renderEmbed();
    byId("event-id").textContent = eventId; byId("api-base").textContent = `${location.origin}/v1`;
    byId("onboarding-link").href = `/admin/events/${encodeURIComponent(eventId)}/onboarding`;
    const results = await Promise.allSettled([loadResources(), loadTargets(), loadAssets()]);
    if (results.some((result) => result.status === "rejected")) {
      setStatus("Some workspace information could not be loaded. Refresh to try again.", true);
    } else setStatus("Workspace ready.");
  }
  initialize().catch((error) => { if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true); });
})();

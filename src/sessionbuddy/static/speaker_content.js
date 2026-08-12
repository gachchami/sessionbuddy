(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/speaker-content$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const state = { csrf: "", timeZone: "", taskMutation: null };

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
  function eventTime(value) {
    try {
      const formatted = new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short", timeZone: state.timeZone
      }).format(new Date(value));
      return `${formatted} · Event time (${state.timeZone})`;
    } catch (_) { return "Date unavailable"; }
  }
  function slug(value) { return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80); }
  function mutationHeaders() { return { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() }; }
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
        const versionMeta = document.createElement("span"); versionMeta.className = "muted"; versionMeta.textContent = `${version.state} · ${fileSize(version.byte_size)} · ${eventTime(version.uploaded_at_ms)}`;
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
    const available = body.data.filter((item) => ["invited", "submitted", "accepted"].includes(item.selection_status));
    const options = available.map((item) => {
      const label = document.createElement("label"); label.className = "speaker-picker__option";
      const input = document.createElement("input"); input.type = "checkbox"; input.name = "event_speaker_id"; input.value = item.event_speaker_id;
      const copy = document.createElement("span");
      const name = document.createElement("strong"); name.textContent = item.display_name;
      const stateLabel = { invited: "Invitation pending", submitted: "Registered", accepted: "Accepted" }[item.selection_status];
      const proposal = document.createElement("small"); proposal.textContent = `${stateLabel} · ${item.proposal_title}`;
      copy.append(name, proposal); label.append(input, copy); return label;
    });
    byId("speaker-target").replaceChildren(...options);
    if (!available.length) {
      const empty = document.createElement("p"); empty.className = "empty";
      empty.textContent = "No invited, registered, or accepted speakers yet.";
      byId("speaker-target").append(empty);
    }
  }
  byId("resource-form").elements.title.addEventListener("input", (event) => {
    const slugInput = event.currentTarget.form.elements.slug;
    if (!slugInput.dataset.edited) slugInput.value = slug(event.currentTarget.value);
  });
  byId("resource-form").elements.slug.addEventListener("input", (event) => { event.currentTarget.dataset.edited = "true"; });
  byId("resource-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  byId("resource-form").addEventListener("submit", async (event) => {
    // event.currentTarget is null after any await; capture the form up front.
    event.preventDefault(); const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    const embed = form.elements.embed_url;
    embed.setCustomValidity(approvedEmbed(values.embed_url) ? "" : "Use an approved HTTPS Google, YouTube, or Vimeo URL.");
    if (!form.reportValidity()) return;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/resources`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ title: values.title, slug: values.slug, summary: values.summary, body_text: values.body_text, embed_url: values.embed_url || null, status: values.status, sort_order: Number(values.sort_order) }) });
      form.reset(); delete form.elements.slug.dataset.edited; form.elements.sort_order.value = "0"; setStatus("Resource published to the speaker portal."); await loadResources();
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), true); }
  });
  byId("task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    // event.currentTarget is null after any await; capture the form up front.
    // Using it after the awaited task creation threw and turned a SUCCESSFUL
    // assignment into a failure banner (the eval run's SPK-15 sighting).
    const form = event.currentTarget;
    const data = new FormData(form);
    const values = Object.fromEntries(data);
    const speakerIds = data.getAll("event_speaker_id").filter(Boolean);
    if (!speakerIds.length) {
      setStatus("Choose at least one invited, registered, or accepted speaker.", true);
      byId("speaker-target").focus();
      return;
    }
    const label = form.elements.field_label;
    label.setCustomValidity(values.field_required && !String(values.field_label || "").trim() ? "Enter the required response question." : "");
    const dueInput = form.elements.due_at;
    const due = inputMillis(values.due_at);
    dueInput.setCustomValidity(Number.isNaN(due) ? `Choose a valid local time in ${state.timeZone}.` : "");
    if (!form.reportValidity()) return;
    const fields = values.field_label ? [{ key: "response", label: values.field_label, type: values.field_type, required: Boolean(values.field_required), choices: [] }] : [];
    const payloads = speakerIds.map((eventSpeakerId) => ({ event_speaker_id: eventSpeakerId, submission_id: null, title: values.title, help_text: values.help_text, due_at_ms: due, fields }));
    const fingerprint = JSON.stringify(payloads);
    if (!state.taskMutation || state.taskMutation.fingerprint !== fingerprint) {
      state.taskMutation = {
        fingerprint,
        keys: new Map(speakerIds.map((eventSpeakerId) => [eventSpeakerId, idempotencyKey()]))
      };
    }
    const button = form.querySelector('button[type="submit"], button:not([type])');
    button.disabled = true;
    try {
      await Promise.all(payloads.map((payload) =>
        api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-tasks`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": state.taskMutation.keys.get(payload.event_speaker_id) },
          body: JSON.stringify(payload)
        })
      ));
      form.reset();
      state.taskMutation = null;
      setStatus(`Task assigned to ${speakerIds.length} speaker${speakerIds.length === 1 ? "" : "s"}.`);
    } catch (error) {
      setStatus(`${window.SessionBuddyApi.message(error)} Retry Assign task; the same request will not be duplicated.`, true);
    } finally { button.disabled = false; }
  });
  byId("task-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  async function initialize() {
    if (!eventId) throw new Error("Invalid event link.");
    const session = await api("/api/v1/auth/session"); state.csrf = session.csrf_token;
    state.timeZone = await loadEventTimeZone();
    byId("task-time-zone").textContent = state.timeZone;
    const results = await Promise.allSettled([loadResources(), loadTargets(), loadAssets()]);
    if (results.some((result) => result.status === "rejected")) {
      setStatus("Some speaker information could not be loaded. Refresh to try again.", true);
    } else if (byId("status").textContent === "Loading…") {
      // Only claim readiness if nothing else spoke meanwhile: a fast user's
      // action feedback must not be stomped by slow initialization.
      setStatus("Speaker tasks and files ready.");
    }
  }
  initialize().catch((error) => { if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true); });
})();

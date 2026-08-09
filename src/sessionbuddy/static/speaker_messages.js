(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/messages$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  let csrf = "";
  let speakers = [];
  let visibleSpeakers = [];
  let previewedMessage = null;
  let messageMutation = null;
  let messageCursor = null;
  let mergeTarget = null;
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }

  function selectedIds() {
    return [...document.querySelectorAll('input[name="speaker_recipient"]:checked')].map((input) => input.value);
  }

  function invalidatePreview() {
    previewedMessage = null;
    messageMutation = null;
    byId("send-message").disabled = true;
    byId("message-preview").hidden = true;
    byId("recipient-count").textContent = `${selectedIds().length} selected`;
  }

  function recipientRow(speaker) {
    const label = document.createElement("label");
    label.className = "recipient-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "speaker_recipient";
    checkbox.value = speaker.event_speaker_id;
    const identity = document.createElement("span");
    const name = document.createElement("strong"); name.textContent = speaker.display_name;
    const detail = document.createElement("small"); detail.textContent = `${speaker.email} · ${speaker.proposal_title || "No session title"}`;
    identity.append(name, detail);
    const status = document.createElement("span"); status.className = "badge"; status.textContent = speaker.selection_status;
    label.append(checkbox, identity, status);
    return label;
  }

  function renderRecipients() {
    const query = byId("recipient-search").value.trim().toLowerCase();
    visibleSpeakers = speakers.filter((speaker) => !query || [speaker.display_name, speaker.email, speaker.company, speaker.proposal_title].join(" ").toLowerCase().includes(query));
    const list = byId("recipient-list");
    const selected = new Set(selectedIds());
    list.replaceChildren();
    if (!visibleSpeakers.length) {
      const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = speakers.length ? "No recipients match your search." : "No active speakers are available yet."; list.append(empty);
      return;
    }
    list.append(...visibleSpeakers.map((speaker) => {
      const row = recipientRow(speaker);
      row.querySelector("input").checked = selected.has(speaker.event_speaker_id);
      return row;
    }));
  }

  async function loadMessageHistory(append = false) {
    const parameters = new URLSearchParams({ limit: "25" });
    if (append && messageCursor) parameters.set("cursor", messageCursor);
    const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/communications?${parameters}`);
    const body = byId("message-history");
    if (!append) body.replaceChildren();
    if (!result.data.length && !append) {
      const row = document.createElement("tr");
      const cell = document.createElement("td"); cell.colSpan = 4; cell.textContent = "No messages sent for this event yet.";
      row.append(cell); body.append(row);
    }
    result.data.forEach((message) => {
      const row = document.createElement("tr");
      [message.recipient_email, message.subject, message.status, new Date(message.updated_at_ms).toLocaleString()].forEach((value) => {
        const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
      });
      body.append(row);
    });
    messageCursor = result.next_cursor;
    byId("load-older-messages").hidden = !messageCursor;
    return result.data;
  }

  function payload() {
    const form = byId("message-form");
    const eventSpeakerIds = selectedIds();
    if (!eventSpeakerIds.length) { setStatus("Select at least one recipient.", true); return null; }
    if (!form.reportValidity()) return null;
    const values = Object.fromEntries(new FormData(form));
    return { event_speaker_ids: eventSpeakerIds, subject: values.subject, body_text: values.body_text };
  }

  byId("recipient-search").addEventListener("input", renderRecipients);
  byId("recipient-list").addEventListener("change", invalidatePreview);
  byId("select-visible").addEventListener("click", () => {
    const visibleIds = new Set(visibleSpeakers.map((speaker) => speaker.event_speaker_id));
    const boxes = [...document.querySelectorAll('input[name="speaker_recipient"]')].filter((input) => visibleIds.has(input.value));
    const select = boxes.some((input) => !input.checked);
    boxes.forEach((input) => { input.checked = select; });
    byId("select-visible").textContent = select ? "Clear visible" : "Select visible";
    invalidatePreview();
  });
  byId("message-template").addEventListener("change", (event) => {
    const templates = {
      welcome: { subject: "Welcome to {{event.name}}", body: "Hi {{speaker.name}},\n\nWe’re excited to have you present {{submission.title}}. Complete your next steps at {{portal.link}}." },
      deadline: { subject: "Next steps for {{event.name}}", body: "Hi {{speaker.name}},\n\nPlease review your outstanding speaker tasks at {{portal.link}}." }
    };
    const selected = templates[event.target.value];
    if (!selected) return;
    const form = byId("message-form");
    form.elements.subject.value = selected.subject;
    form.elements.body_text.value = selected.body;
    invalidatePreview();
  });
  byId("message-form").addEventListener("focusin", (event) => {
    if (["subject", "body_text"].includes(event.target.name)) mergeTarget = event.target;
  });
  document.querySelector(".personalization-toolbar").addEventListener("click", (event) => {
    const button = event.target.closest("[data-merge-field]");
    if (!button) return;
    const field = mergeTarget || byId("message-form").elements.body_text;
    const start = field.selectionStart ?? field.value.length;
    const end = field.selectionEnd ?? start;
    field.setRangeText(button.dataset.mergeField, start, end, "end");
    field.focus();
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
  byId("message-form").addEventListener("input", invalidatePreview);
  byId("preview-message").addEventListener("click", async () => {
    const body = payload();
    if (!body) return;
    try {
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/communications/speakers/preview`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify(body) });
      previewedMessage = body;
      messageMutation = { fingerprint: JSON.stringify(body), key: `${crypto.randomUUID()}-${crypto.randomUUID()}` };
      const preview = byId("message-preview-content"); preview.replaceChildren();
      result.recipients.forEach((recipient) => {
        const card = document.createElement("article");
        const heading = document.createElement("strong"); heading.textContent = `${recipient.display_name} · ${recipient.email}`;
        const subject = document.createElement("p"); subject.textContent = `Subject: ${recipient.subject}`;
        const message = document.createElement("p");
        message.textContent = new DOMParser().parseFromString(recipient.html_body.replace(/<br\s*\/?>/gi, "\n"), "text/html").body.textContent || "";
        card.append(heading, subject, message); preview.append(card);
      });
      byId("message-preview").hidden = false;
      byId("send-message").disabled = false;
      setStatus(`Preview ready for ${result.recipients.length} recipient${result.recipients.length === 1 ? "" : "s"}.`);
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), true); }
  });
  byId("message-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!previewedMessage) return;
    const button = byId("send-message"); button.disabled = true;
    try {
      const fingerprint = JSON.stringify(previewedMessage);
      if (!messageMutation || messageMutation.fingerprint !== fingerprint) messageMutation = { fingerprint, key: `${crypto.randomUUID()}-${crypto.randomUUID()}` };
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/communications/speakers/send`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf, "idempotency-key": messageMutation.key }, body: JSON.stringify({ ...previewedMessage, confirmed: true }) });
      event.currentTarget.reset();
      document.querySelectorAll('input[name="speaker_recipient"]').forEach((input) => { input.checked = false; });
      invalidatePreview();
      await loadMessageHistory();
      setStatus(`${result.message_ids.length} message${result.message_ids.length === 1 ? "" : "s"} queued.`);
    } catch (error) {
      try {
        const history = await loadMessageHistory();
        const selected = new Set(previewedMessage.event_speaker_ids);
        const expectedEmails = new Set(speakers.filter((speaker) => selected.has(speaker.event_speaker_id)).map((speaker) => speaker.email));
        const delivered = history.filter((message) => message.subject === previewedMessage.subject && expectedEmails.has(message.recipient_email));
        if (expectedEmails.size && new Set(delivered.map((message) => message.recipient_email)).size === expectedEmails.size) {
          event.currentTarget.reset();
          document.querySelectorAll('input[name="speaker_recipient"]').forEach((input) => { input.checked = false; });
          invalidatePreview();
          setStatus(`${expectedEmails.size} message${expectedEmails.size === 1 ? "" : "s"} queued. Delivery was confirmed from message history.`);
          return;
        }
      } catch (_) { /* Keep the original delivery error below. */ }
      setStatus(`${window.SessionBuddyApi.message(error)} You can safely retry; already queued recipients will not be duplicated.`, true);
      button.disabled = false;
    }
  });
  byId("load-older-messages").addEventListener("click", async (event) => {
    event.currentTarget.disabled = true;
    try { await loadMessageHistory(true); } finally { event.currentTarget.disabled = false; }
  });

  async function initialize() {
    if (!eventId) throw new Error("Invalid event link.");
    const session = await api("/api/v1/auth/session"); csrf = session.csrf_token;
    byId("speaker-directory").href = `/admin/events/${encodeURIComponent(eventId)}/speakers`;
    speakers = (await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-targets`)).data.filter((speaker) => speaker.selection_status !== "invited");
    renderRecipients();
    await loadMessageHistory();
    setStatus(`${speakers.length} available recipient${speakers.length === 1 ? "" : "s"}.`);
  }

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true);
  });
})();

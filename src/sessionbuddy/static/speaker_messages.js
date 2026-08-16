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
  let eventTimeZone = "";
  let historyMessages = [];
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function idempotencyKey() {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }

  function clearComposeError() {
    const error = byId("message-compose-error");
    if (!error.textContent) return;
    // Do not remove the focused node from the accessibility tree. User edits
    // normally move focus first; synthetic changes get a stable fallback.
    const restorePreviewFocus = document.activeElement === error;
    error.textContent = "";
    error.classList.remove("error");
    if (restorePreviewFocus) byId("preview-message").focus();
  }

  function showComposeError(message) {
    const error = byId("message-compose-error");
    error.textContent = message;
    error.classList.add("error");
    error.focus();
  }

  function eventTime(value) {
    try {
      const formatted = new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short", timeZone: eventTimeZone
      }).format(new Date(value));
      return formatted;
    } catch (_) { return "Date unavailable"; }
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
    clearComposeError();
  }

  // One vocabulary for where a recipient stands in the program, used by the
  // list, the preview, and the confirmation. Everyone who is not invited used
  // to read "Active speaker", which hid rejected submitters from an organizer
  // sending mail straight after a decision round.
  const selectionLabels = {
    invited: "Invited — awaiting acceptance",
    submitted: "Proposal submitted — no decision yet",
    accepted: "Accepted speaker",
    rejected: "Proposal not selected"
  };

  function selectionLabel(status) {
    return selectionLabels[status] || "Speaker";
  }

  function recipientRow(speaker) {
    const label = document.createElement("label");
    label.className = "recipient-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.name = "speaker_recipient";
    checkbox.value = speaker.event_speaker_id;
    const identity = document.createElement("span");
    identity.className = "recipient-row__identity";
    const heading = document.createElement("span");
    heading.className = "recipient-row__heading";
    const name = document.createElement("strong"); name.textContent = speaker.display_name;
    const recipientState = selectionLabel(speaker.selection_status);
    const detail = document.createElement("small"); detail.textContent = `${speaker.email} · ${speaker.proposal_title || recipientState}`;
    const status = document.createElement("span"); status.className = "badge"; status.textContent = recipientState;
    heading.append(name, status);
    identity.append(heading, detail);
    label.append(checkbox, identity);
    return label;
  }

  function renderRecipients() {
    const query = byId("recipient-search").value.trim().toLowerCase();
    const status = byId("recipient-status").value;
    visibleSpeakers = speakers.filter((speaker) => (
      (status === "all" || speaker.selection_status === status)
      && (!query || [speaker.display_name, speaker.email, speaker.company, speaker.proposal_title].join(" ").toLowerCase().includes(query))
    ));
    const list = byId("recipient-list");
    const selected = new Set(selectedIds());
    list.replaceChildren();
    if (!visibleSpeakers.length) {
      const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = speakers.length ? "No recipients match this search or status." : "No speakers, submitters, or invitations are available yet."; list.append(empty);
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
    historyMessages = append ? historyMessages.concat(result.data) : result.data;
    renderMessageHistory();
    messageCursor = result.next_cursor;
    byId("load-older-messages").hidden = !messageCursor;
    return result.data;
  }

  function renderMessageHistory() {
    const list = byId("message-history");
    const filter = byId("message-history-filter").value;
    const messages = historyMessages.filter((message) => filter === "all" ||
      (filter === "attention" ? ["failed", "cancelled"].includes(message.status) : message.category === filter));
    list.replaceChildren();
    if (!messages.length) {
      const empty = document.createElement("p"); empty.className = "workflow-empty-state";
      empty.textContent = historyMessages.length ? "No messages match this filter." : "No event messages have been sent yet.";
      list.append(empty); return;
    }
    messages.forEach((message) => {
      const details = document.createElement("details"); details.className = "message-history__item";
      const summary = document.createElement("summary");
      const rail = document.createElement("span"); rail.className = `message-history__rail message-history__rail--${message.category}`; rail.setAttribute("aria-hidden", "true");
      const main = document.createElement("span"); main.className = "message-history__main";
      const subject = document.createElement("strong"); subject.textContent = message.subject;
      const recipient = document.createElement("span"); recipient.className = "message-history__recipient"; recipient.textContent = message.recipient_email;
      main.append(subject, recipient);
      const meta = document.createElement("span"); meta.className = "message-history__meta";
      const category = document.createElement("span"); category.className = "message-history__category"; category.textContent = message.category;
      const status = document.createElement("span"); status.className = `message-history__status message-history__status--${message.status}`; status.textContent = message.status;
      const time = document.createElement("time"); time.dateTime = new Date(message.updated_at_ms).toISOString(); time.textContent = eventTime(message.updated_at_ms);
      meta.append(category, status, time); summary.append(rail, main, meta);
      const detail = document.createElement("div"); detail.className = "message-history__detail";
      const body = document.createElement("p"); body.textContent = message.body_preview || "No message preview available.";
      const delivery = document.createElement("p"); delivery.className = "message-history__delivery";
      delivery.textContent = message.last_error_code ? `Delivery error: ${message.last_error_code}` : `Delivery attempts: ${message.attempt_count}`;
      detail.append(body, delivery); details.append(summary, detail); list.append(details);
    });
  }

  function payload() {
    const form = byId("message-form");
    const eventSpeakerIds = selectedIds();
    if (!eventSpeakerIds.length) {
      showComposeError("Select at least one recipient.");
      return null;
    }
    if (!form.reportValidity()) return null;
    const values = Object.fromEntries(new FormData(form));
    return { event_speaker_ids: eventSpeakerIds, subject: values.subject, body_text: values.body_text };
  }

  byId("recipient-search").addEventListener("input", renderRecipients);
  byId("recipient-status").addEventListener("change", renderRecipients);
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
      invitation: { subject: "Your invitation to {{event.name}}", body: "Hi {{speaker.name}},\n\nYour speaker invitation is waiting for you. Please use the secure access link in your original invitation email to accept." },
      welcome: { subject: "Welcome to {{event.name}}", body: "Hi {{speaker.name}},\n\nWe’re excited to have you present {{submission.title}}. Complete your next steps at {{portal.link}}." },
      deadline: { subject: "Next steps for {{event.name}}", body: "Hi {{speaker.name}},\n\nPlease review your outstanding speaker tasks at {{portal.link}}." }
    };
    const selected = templates[event.target.value];
    if (!selected) return;
    const form = byId("message-form");
    form.elements.subject.value = selected.subject;
    form.elements.body_text.value = selected.body;
    window.SessionBuddyApi.refreshCharacterCounters(form);
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
    clearComposeError();
    try {
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/communications/speakers/preview`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify(body) });
      previewedMessage = body;
      messageMutation = { fingerprint: JSON.stringify(body), key: idempotencyKey() };
      const preview = byId("message-preview-content"); preview.replaceChildren();
      result.recipients.forEach((recipient) => {
        const card = document.createElement("article");
        const state = selectionLabel(recipient.selection_status || recipient.recipient_state);
        const heading = document.createElement("strong"); heading.textContent = `${recipient.display_name} · ${recipient.email} · ${state}`;
        const subject = document.createElement("p"); subject.textContent = `Subject: ${recipient.subject}`;
        const message = document.createElement("p");
        message.textContent = new DOMParser().parseFromString(recipient.html_body.replace(/<br\s*\/?>/gi, "\n"), "text/html").body.textContent || "";
        card.append(heading, subject, message); preview.append(card);
      });
      byId("message-preview").hidden = false;
      byId("send-message").disabled = false;
      setStatus(`Preview ready for ${result.recipients.length} recipient${result.recipients.length === 1 ? "" : "s"}.`);
    } catch (error) { showComposeError(window.SessionBuddyApi.message(error)); }
  });
  async function sendPreviewedMessage() {
    const form = byId("message-form");
    const button = byId("send-message");
    const message = previewedMessage;
    if (!message) return;
    button.disabled = true;
    try {
      const fingerprint = JSON.stringify(message);
      if (!messageMutation || messageMutation.fingerprint !== fingerprint) messageMutation = { fingerprint, key: idempotencyKey() };
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/communications/speakers/send`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf, "idempotency-key": messageMutation.key }, body: JSON.stringify({ ...message, confirmed: true }) });
      form.reset();
      document.querySelectorAll('input[name="speaker_recipient"]').forEach((input) => { input.checked = false; });
      invalidatePreview();
      try { await loadMessageHistory(); } catch (_) { /* The accepted send remains successful. */ }
      setStatus(`${result.message_ids.length} message${result.message_ids.length === 1 ? "" : "s"} queued.`);
    } catch (error) {
      try {
        const history = await loadMessageHistory();
        const selected = new Set(message.event_speaker_ids);
        const expectedEmails = new Set(speakers.filter((speaker) => selected.has(speaker.event_speaker_id)).map((speaker) => speaker.email));
        const delivered = history.filter((item) => item.subject === message.subject && expectedEmails.has(item.recipient_email));
        if (expectedEmails.size && new Set(delivered.map((message) => message.recipient_email)).size === expectedEmails.size) {
          form.reset();
          document.querySelectorAll('input[name="speaker_recipient"]').forEach((input) => { input.checked = false; });
          invalidatePreview();
          setStatus(`${expectedEmails.size} message${expectedEmails.size === 1 ? "" : "s"} queued. Delivery was confirmed from message history.`);
          return;
        }
      } catch (_) { /* Keep the original delivery error below. */ }
      setStatus(`${window.SessionBuddyApi.message(error)} You can safely retry; already queued recipients will not be duplicated.`, true);
      button.disabled = false;
    }
  }

  byId("message-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!previewedMessage) return;
    const count = previewedMessage.event_speaker_ids.length;
    byId("confirm-message-count").textContent = `${count} personalized email${count === 1 ? "" : "s"} will be queued.`;
    byId("confirm-message-subject").textContent = `Subject: ${previewedMessage.subject}`;
    byId("confirm-message-send").showModal();
  });
  byId("cancel-message-send").addEventListener("click", () => byId("confirm-message-send").close("cancel"));
  byId("confirm-message-send").addEventListener("close", () => byId("send-message").focus());
  byId("confirm-message-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const confirm = byId("confirm-message-send-button");
    confirm.disabled = true;
    byId("confirm-message-send").close("confirmed");
    try { await sendPreviewedMessage(); } finally { confirm.disabled = false; }
  });
  byId("load-older-messages").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try { await loadMessageHistory(true); } finally { button.disabled = false; }
  });
  byId("message-history-filter").addEventListener("change", renderMessageHistory);

  async function initialize() {
    if (!eventId) throw new Error("Invalid event link.");
    const session = await api("/api/v1/auth/session"); csrf = session.csrf_token;
    let event;
    try {
      event = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
    } catch (error) {
      const recoveryScope = `event:${eventId}`;
      if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error, recoveryScope)
          || window.SessionBuddyApi.redirectIfDocumentAccessChanged(error, recoveryScope)) return;
      throw error;
    }
    if (!event?.time_zone) throw new Error("The event time zone could not be loaded.");
    eventTimeZone = event.time_zone;
    byId("message-time-zone").textContent = eventTimeZone;
    byId("speaker-directory").href = `/admin/events/${encodeURIComponent(eventId)}/speakers`;
    speakers = (await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-targets`)).data;
    renderRecipients();
    await loadMessageHistory();
    if (byId("status").textContent === "Loading speakers\u2026") {
      // Same guard as the workspace: initialization must not overwrite a
      // faster user action's feedback.
      setStatus(`${speakers.length} available recipient${speakers.length === 1 ? "" : "s"}.`);
    }
  }

  initialize().catch((error) => {
    if (window.SessionBuddyApi.redirectIfSignedOut(error)) return;
    setStatus(window.SessionBuddyApi.message(error), true);
  });
})();

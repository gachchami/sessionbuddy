(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const pathMatch = location.pathname.match(/^\/admin\/events\/([^/]+)\/speakers(?:\/([^/]+))?$/);
  let selectedEventId = "";
  let selectedSpeakerId = "";
  try {
    selectedEventId = pathMatch ? decodeURIComponent(pathMatch[1]) : new URLSearchParams(location.search).get("event_id") || "";
    selectedSpeakerId = pathMatch?.[2] ? decodeURIComponent(pathMatch[2]) : "";
  } catch (_) { selectedEventId = ""; selectedSpeakerId = ""; }
  let csrf = "";
  let selectedSpeaker = null;
  let previewedMessage = null;
  let messageMutation = null;
  let allSpeakers = [];

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  async function loadMessageHistory() {
    if (!selectedEventId) return;
    const result = await api(`/api/v1/admin/events/${encodeURIComponent(selectedEventId)}/communications`);
    const body = byId("message-history");
    body.replaceChildren();
    if (!result.data.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td"); cell.colSpan = 4; cell.textContent = "No messages sent for this event yet.";
      row.append(cell); body.append(row); return;
    }
    for (const message of result.data) {
      const row = document.createElement("tr");
      [message.recipient_email, message.subject, message.status, new Date(message.updated_at_ms).toLocaleString()].forEach((value) => {
        const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
      });
      body.append(row);
    }
  }

  function speakerCard(item) {
    const card = document.createElement("article");
    card.className = "entity-card";
    const top = document.createElement("div");
    top.className = "entity-card__top";
    const event = document.createElement("span");
    event.className = "eyebrow";
    event.textContent = item.event.name;
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = item.selection_status;
    top.append(event, badge);
    const heading = document.createElement("h3");
    const name = document.createElement("a");
    name.href = `/admin/events/${encodeURIComponent(item.event.id)}/speakers/${encodeURIComponent(item.event_speaker_id)}`;
    name.textContent = item.display_name;
    heading.append(name);
    const proposal = document.createElement("p");
    proposal.className = "result";
    proposal.textContent = item.proposal_title;
    const identity = document.createElement("p");
    identity.className = "muted";
    identity.textContent = [item.job_title, item.company].filter(Boolean).join(" · ") || item.email;
    const open = document.createElement("a");
    open.className = "entity-card__action";
    open.href = name.href;
    open.textContent = "View speaker →";
    card.append(top, heading, identity, proposal, open);
    const select = document.createElement("label"); select.className = "check-label entity-card__action";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.name = "speaker_recipient"; checkbox.value = item.event_speaker_id;
    select.append(checkbox, document.createTextNode(" Select for message")); card.append(select);
    return card;
  }

  function renderDirectory() {
    const query = byId("speaker-search").value.trim().toLowerCase();
    const selectionStatus = byId("speaker-status").value;
    const speakers = allSpeakers.filter((speaker) => {
      const searchable = [speaker.display_name, speaker.email, speaker.job_title, speaker.company, speaker.proposal_title, speaker.event.name]
        .join(" ").toLowerCase();
      return (!query || searchable.includes(query)) && (!selectionStatus || speaker.selection_status === selectionStatus);
    });
    const list = byId("speaker-list");
    list.replaceChildren();
    if (speakers.length) list.append(...speakers.map(speakerCard));
    else {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = allSpeakers.length
        ? "No speakers match these filters."
        : "No speakers yet. Invite one or publish a call for speakers.";
      list.append(empty);
    }
    byId("speaker-count").textContent = String(speakers.length);
    byId("status").classList.remove("error");
    byId("status").textContent = `${speakers.length} of ${allSpeakers.length} speaker${allSpeakers.length === 1 ? "" : "s"} shown.`;
  }

  async function initialize() {
    const session = await api("/api/v1/auth/session");
    csrf = session.csrf_token;
    const organizations = (await api("/api/v1/admin/organizations")).data;
    const eventGroups = await Promise.all(organizations.map(async (organization) => ({
      organization,
      events: (await api(`/api/v1/admin/organizations/${encodeURIComponent(organization.id)}/events`)).data
    })));
    let events = eventGroups.flatMap(({ organization, events: items }) => items.map((event) => ({ ...event, organization_name: organization.name })));
    if (selectedEventId) events = events.filter((event) => event.id === selectedEventId);
    if (selectedEventId && !events.length) throw new Error("This event is not available to your account.");
    const groups = await Promise.all(events.map(async (event) => ({
      event,
      speakers: (await api(`/api/v1/admin/events/${encodeURIComponent(event.id)}/speaker-targets`)).data
    })));
    const speakers = groups.flatMap(({ event, speakers: items }) => items.map((speaker) => ({ ...speaker, event })));
    allSpeakers = speakers;
    const activeEvent = events.length === 1 ? events[0] : null;
    if (activeEvent) {
      byId("page-title").textContent = `${activeEvent.name} speakers`;
      byId("page-summary").textContent = `Browse speaker records for ${activeEvent.name}.`;
      byId("invite-speaker").href = `/admin/events/${encodeURIComponent(activeEvent.id)}/access`;
      byId("message-panel").hidden = false;
      await loadMessageHistory();
    }
    if (selectedSpeakerId) {
      const selected = speakers.find((speaker) => speaker.event_speaker_id === selectedSpeakerId);
      if (!selected) throw new Error("This speaker is not available in the selected event.");
      byId("speaker-detail").hidden = false;
      byId("speaker-event").textContent = `${selected.event.organization_name} · ${selected.event.name} · ${selected.selection_status}`;
      byId("speaker-name").textContent = selected.display_name;
      byId("speaker-proposal").textContent = selected.proposal_title;
      selectedSpeaker = selected;
      const form = byId("speaker-form");
      ["display_name", "email", "job_title", "company", "location", "biography", "version"].forEach((name) => { form.elements[name].value = selected[name] ?? ""; });
      form.elements.links.value = (selected.links || []).join("\n");
      byId("speaker-onboarding").href = `/admin/events/${encodeURIComponent(selected.event.id)}/onboarding`;
      byId("speaker-directory").href = `/admin/events/${encodeURIComponent(selected.event.id)}/speakers`;
      document.title = `${selected.display_name} · SessionBuddy`;
    }
    renderDirectory();
  }

  byId("speaker-filters").addEventListener("input", renderDirectory);

  const templateSelect = document.createElement("select");
  templateSelect.id = "message-template";
  templateSelect.append(
    new Option("Write from scratch", ""),
    new Option("Speaker welcome", "welcome"),
    new Option("Deadline reminder", "deadline")
  );
  const templateLabel = document.createElement("label");
  templateLabel.append("Template ", templateSelect);
  const mergeHelp = document.createElement("p");
  mergeHelp.className = "help";
  mergeHelp.textContent = "Merge fields: {{speaker.first_name}}, {{speaker.name}}, {{submission.title}}, {{event.name}}, {{portal.link}}";
  byId("message-form").prepend(templateLabel, mergeHelp);
  templateSelect.addEventListener("change", () => {
    const form = byId("message-form");
    const templates = {
      welcome: {
        subject: "Welcome to {{event.name}}",
        body: "Hi {{speaker.first_name}},\n\nWe’re excited to have you present {{submission.title}}. Complete your next steps at {{portal.link}}."
      },
      deadline: {
        subject: "Next steps for {{event.name}}",
        body: "Hi {{speaker.first_name}},\n\nPlease review your outstanding speaker tasks at {{portal.link}}."
      }
    };
    const selected = templates[templateSelect.value];
    if (!selected) return;
    form.elements.subject.value = selected.subject;
    form.elements.body_text.value = selected.body;
    form.dispatchEvent(new Event("input", { bubbles: true }));
  });

  byId("speaker-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!selectedSpeaker) return;
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    const links = String(values.links || "").split(/\r?\n/).map((value) => value.trim()).filter(Boolean);
    try {
      const updated = await api(`/api/v1/admin/events/${encodeURIComponent(selectedSpeaker.event.id)}/speakers/${encodeURIComponent(selectedSpeaker.event_speaker_id)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ display_name: values.display_name, job_title: values.job_title, company: values.company, biography: values.biography, location: values.location, links, version: Number(values.version) })
      });
      selectedSpeaker = { ...selectedSpeaker, ...updated };
      form.elements.version.value = updated.version;
      byId("speaker-name").textContent = updated.display_name;
      byId("status").textContent = "Speaker details saved.";
    } catch (error) { byId("status").textContent = window.SessionBuddyApi.message(error); byId("status").classList.add("error"); }
  });

  function messagePayload() {
    const form = byId("message-form");
    const values = Object.fromEntries(new FormData(form));
    const eventSpeakerIds = [...document.querySelectorAll('input[name="speaker_recipient"]:checked')].map((input) => input.value);
    if (!eventSpeakerIds.length) { byId("status").textContent = "Select at least one speaker to message."; byId("status").classList.add("error"); return null; }
    if (!form.reportValidity()) return null;
    return { event_speaker_ids: eventSpeakerIds, subject: values.subject, body_text: values.body_text };
  }

  byId("message-form").addEventListener("input", () => { previewedMessage = null; messageMutation = null; byId("send-message").disabled = true; byId("message-preview").hidden = true; });
  document.addEventListener("change", (event) => { if (event.target.matches?.('input[name="speaker_recipient"]')) { previewedMessage = null; messageMutation = null; byId("send-message").disabled = true; byId("message-preview").hidden = true; } });
  byId("preview-message").addEventListener("click", async () => {
    const payload = messagePayload();
    if (!payload || !selectedEventId) return;
    try {
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(selectedEventId)}/communications/speakers/preview`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify(payload) });
      previewedMessage = payload;
      messageMutation = { fingerprint: JSON.stringify(payload), key: `${crypto.randomUUID()}-${crypto.randomUUID()}` };
      const preview = byId("message-preview");
      preview.replaceChildren();
      preview.append(document.createTextNode(`Ready for ${result.recipients.length} recipient${result.recipients.length === 1 ? "" : "s"}.`));
      result.recipients.forEach((recipient) => {
        const card = document.createElement("article");
        const heading = document.createElement("strong");
        heading.textContent = `${recipient.display_name} · ${recipient.email}`;
        const subject = document.createElement("p");
        subject.textContent = `Subject: ${recipient.subject}`;
        const body = document.createElement("p");
        const previewDocument = new DOMParser().parseFromString(
          recipient.html_body.replace(/<br\s*\/?>/gi, "\n"),
          "text/html"
        );
        body.textContent = previewDocument.body.textContent || "";
        card.append(heading, subject, body); preview.append(card);
      });
      byId("message-preview").hidden = false;
      byId("send-message").disabled = false;
      byId("status").classList.remove("error"); byId("status").textContent = "Preview ready. Confirm to queue delivery.";
    } catch (error) { byId("status").textContent = window.SessionBuddyApi.message(error); byId("status").classList.add("error"); }
  });
  byId("message-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!previewedMessage || !selectedEventId) return;
    const button = byId("send-message"); button.disabled = true;
    try {
      const fingerprint = JSON.stringify(previewedMessage);
      if (!messageMutation || messageMutation.fingerprint !== fingerprint) {
        messageMutation = { fingerprint, key: `${crypto.randomUUID()}-${crypto.randomUUID()}` };
      }
      const result = await api(`/api/v1/admin/events/${encodeURIComponent(selectedEventId)}/communications/speakers/send`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf, "idempotency-key": messageMutation.key }, body: JSON.stringify({ ...previewedMessage, confirmed: true }) });
      event.currentTarget.reset(); document.querySelectorAll('input[name="speaker_recipient"]').forEach((input) => { input.checked = false; });
      previewedMessage = null; messageMutation = null; byId("message-preview").hidden = true;
      byId("status").classList.remove("error"); byId("status").textContent = `${result.message_ids.length} message${result.message_ids.length === 1 ? "" : "s"} queued.`;
      try {
        await loadMessageHistory();
      } catch (_) {
        byId("status").textContent = `${result.message_ids.length} message${result.message_ids.length === 1 ? "" : "s"} queued. Delivery history could not be refreshed yet.`;
      }
    } catch (error) { byId("status").textContent = `${window.SessionBuddyApi.message(error)} Retry Send; already queued recipients will not be duplicated.`; byId("status").classList.add("error"); button.disabled = false; }
  });

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").classList.add("error");
    }
  });
})();

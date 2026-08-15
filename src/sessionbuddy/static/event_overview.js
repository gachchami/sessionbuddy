(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }

  const api = (path) => window.SessionBuddyApi.request(path);

  function showEventUnavailable() {
    document.body.classList.add("event-resource-unavailable");
    const hideEventNavigation = () => document.querySelector(".sb-event-nav")?.setAttribute("hidden", "");
    hideEventNavigation();
    new MutationObserver(hideEventNavigation).observe(document.querySelector("[data-auth-shell]"), { childList: true, subtree: true });
    document.body.classList.remove("is-loading");
    byId("event-public-header").hidden = true;
    document.querySelector(".event-overview-now").hidden = true;
    const status = byId("status");
    status.classList.add("error");
    const message = document.createElement("strong");
    message.textContent = "This event does not exist or is unavailable to your account.";
    const back = document.createElement("a");
    back.className = "button secondary";
    back.href = "/admin";
    back.textContent = "Back to events";
    status.replaceChildren(message, document.createTextNode(" "), back);
    document.title = "Event unavailable · SessionBuddy";
  }

  function formatRange(event) {
    try {
      const date = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: event.time_zone });
      const time = new Intl.DateTimeFormat(undefined, { timeStyle: "short", timeZone: event.time_zone });
      const start = new Date(event.starts_at_ms);
      const end = new Date(event.ends_at_ms);
      const startDate = date.format(start);
      const endDate = date.format(end);
      return startDate === endDate
        ? `${startDate} · ${time.format(start)}–${time.format(end)}`
        : `${startDate}, ${time.format(start)} – ${endDate}, ${time.format(end)}`;
    } catch (_) { return "Event dates unavailable"; }
  }

  async function initialize() {
    if (!eventId) {
      showEventUnavailable();
      return;
    }
    await api("/api/v1/auth/session");
    let selected;
    try {
      selected = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
    } catch (error) {
      if ([403, 404, 422].includes(error.status)) {
        showEventUnavailable();
        return;
      }
      throw error;
    }
    if (!selected) return;
    const settle = (promise) => promise
      .then((value) => ({ ok: true, value }))
      .catch((error) => ({ ok: false, status: Number(error && error.status) || 0 }));
    const [speakersResult, cfp, submissionsState, roundState, agendaState] = await Promise.all([
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-targets`),
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/cfp`),
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions`)),
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds/current`)),
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda`))
    ]);
    const speakers = speakersResult.data;
    const cfpPublished = Boolean(cfp.published_form);
    // Availability is decided by the API (sessionbuddy/cfp/availability.py) and
    // arrives as availability_state. Publishing a form is not the same as an
    // open call: this tile used to claim "Open · Accepting proposals" for the
    // rest of the event's life, including after the public form had closed.
    const cfpState = cfp.published_form?.availability_state || "";
    const cfpStateLabel = { scheduled: "Scheduled", open: "Open", closed: "Closed" }[cfpState] || "Open";
    const cfpStateNote = {
      scheduled: "Opens on schedule",
      open: "Accepting proposals",
      closed: "Closed to new proposals"
    }[cfpState] || "Accepting proposals";
    const bannerUrl = cfp.published_form?.cover_image_url || "";
    const logoUrl = cfp.published_form?.logo_url || "";
    const accentColor = cfp.published_form?.accent_color || "#3159d9";
    const submissionCount = submissionsState.ok
      ? Number(submissionsState.value.total ?? submissionsState.value.data.length)
      : 0;
    // A missing agenda is a real state (404); any other failure is not knowledge.
    const agendaMissing = !agendaState.ok && agendaState.status === 404;
    const agendaFailed = !agendaState.ok && agendaState.status !== 404;
    const agenda = agendaState.ok ? agendaState.value : null;
    const agendaItems = agenda && Array.isArray(agenda.items) ? agenda.items.length : 0;
    const agendaPublished = Boolean(agenda && agenda.revision && agenda.revision.status === "published");
    const degraded = !submissionsState.ok || !roundState.ok || agendaFailed;
    document.title = `${selected.name} · SessionBuddy`;
    byId("event-name").textContent = selected.name;
    byId("event-monogram").textContent = selected.name.slice(0, 2).toUpperCase();
    byId("event-public-header").style.setProperty("--event-preview-accent", accentColor);
    if (bannerUrl) {
      byId("event-banner").src = bannerUrl;
      byId("event-banner").alt = `${selected.name} banner`;
      byId("event-banner").hidden = false;
      byId("event-cover-empty").hidden = true;
    }
    if (logoUrl) {
      byId("event-logo").src = logoUrl;
      byId("event-logo").alt = `${selected.name} logo`;
      byId("event-logo").hidden = false;
    }
    const mode = selected.delivery_mode.replace("_", " ");
    byId("event-summary").textContent = `${formatRange(selected)} · ${mode}${selected.location ? ` · ${selected.location}` : ""}`;
    byId("cfp-action").href = `/admin/events/${encodeURIComponent(eventId)}/cfp`;
    byId("public-schedule").href = `/events/${encodeURIComponent(eventId)}/schedule`;
    const prefix = `/admin/events/${encodeURIComponent(eventId)}`;
    byId("proposals-link").href = `${prefix}/submissions`;
    byId("speakers-link").href = `${prefix}/speakers`;
    byId("agenda-link").href = `${prefix}/agenda`;
    byId("cfp-link").href = `${prefix}/cfp`;
    byId("event-actions").hidden = false;
    byId("next-step").hidden = cfpPublished;
    byId("next-step-action").href = `${prefix}/cfp`;
    byId("proposal-count").textContent = submissionsState.ok ? `${submissionCount} submitted` : "Unavailable";
    byId("proposal-note").textContent = submissionsState.ok ? `${submissionCount} total proposal${submissionCount === 1 ? "" : "s"}` : "Refresh to try again";
    byId("speaker-count").textContent = `${speakers.length} confirmed`;
    byId("speaker-note").textContent = `${speakers.length} speaker${speakers.length === 1 ? "" : "s"} in this event`;
    byId("agenda-count").textContent = agendaFailed ? "Unavailable" : `${agendaItems} session${agendaItems === 1 ? "" : "s"}`;
    byId("agenda-note").textContent = agendaFailed ? "Unavailable" : agendaPublished ? "Published" : agendaMissing ? "Not started" : "Draft";
    byId("cfp-state").textContent = cfpPublished ? cfpStateLabel : "Draft";
    byId("cfp-note").textContent = cfpPublished ? cfpStateNote : "Publish before sharing";
    document.body.classList.remove("is-loading");
    byId("status").textContent = degraded
      ? "Some program information is unavailable. Refresh to try again."
      : "";
  }

  initialize().catch((error) => {
    document.body.classList.remove("is-loading");
    if (window.SessionBuddyApi.redirectIfSignedOut(error)) return;
    byId("status").textContent = window.SessionBuddyApi.message(
      error,
      "The event could not be loaded. Try again.",
    );
    byId("status").classList.add("error");
  });
})();

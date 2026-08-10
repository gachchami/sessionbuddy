(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }

  const api = (path) => window.SessionBuddyApi.request(path);

  function tool(number, title, description, href, state = "Open") {
    const card = document.createElement("article");
    card.className = "workflow-card organizer-card organizer-workflow-card";
    const meta = document.createElement("div");
    meta.className = "workflow-card__meta";
    const step = document.createElement("span"); step.textContent = String(number).padStart(2, "0");
    const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = state;
    meta.append(step, badge);
    const heading = document.createElement("h3");
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.textContent = title;
    heading.append(anchor);
    const summary = document.createElement("p");
    summary.className = "result";
    summary.textContent = description;
    const open = document.createElement("a");
    open.href = href;
    open.className = "entity-card__action";
    open.textContent = "Open →";
    card.append(meta, heading, summary, open);
    return card;
  }

  function formatRange(event) {
    try {
      const formatter = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: event.time_zone });
      return `${formatter.format(new Date(event.starts_at_ms))} – ${formatter.format(new Date(event.ends_at_ms))}`;
    } catch (_) { return "Event dates unavailable"; }
  }

  async function initialize() {
    if (!eventId) throw new Error("This event link is invalid.");
    await api("/api/v1/auth/session");
    const organizations = (await api("/api/v1/admin/organizations")).data;
    let selected = null;
    let organization = null;
    for (const item of organizations) {
      const events = (await api(`/api/v1/admin/organizations/${encodeURIComponent(item.id)}/events`)).data;
      selected = events.find((event) => event.id === eventId) || null;
      if (selected) { organization = item; break; }
    }
    if (!selected || !organization) throw new Error("This event is not available to your account.");
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
    const reviewPath = `/admin/events/${encodeURIComponent(eventId)}/submissions`;
    const cfpLive = Boolean(cfp.published_form);
    const plural = (count, noun) => `${count} ${noun}${count === 1 ? "" : "s"}`;
    const submissionCount = submissionsState.ok
      ? Number(submissionsState.value.total ?? submissionsState.value.data.length)
      : 0;
    const reviewBadge = roundState.ok && roundState.value
      ? "Round open"
      : !submissionsState.ok || !roundState.ok
        ? "Unavailable"
        : submissionCount > 0
          ? plural(submissionCount, "submission")
          : cfpLive
            ? "No submissions yet"
            : "Waiting";
    // A missing agenda is a real state (404); any other failure is not knowledge.
    const agendaMissing = !agendaState.ok && agendaState.status === 404;
    const agendaFailed = !agendaState.ok && agendaState.status !== 404;
    const agenda = agendaState.ok ? agendaState.value : null;
    const agendaItems = agenda && Array.isArray(agenda.items) ? agenda.items.length : 0;
    const agendaPublished = Boolean(agenda && agenda.revision && agenda.revision.status === "published");
    const agendaBadge = agendaFailed
      ? "Unavailable"
      : agendaMissing
        ? "Not started"
        : agendaPublished
          ? "Published"
          : agendaItems > 0
            ? `Draft · ${plural(agendaItems, "session")}`
            : "Draft · no sessions yet";
    const degraded = !submissionsState.ok || !roundState.ok || agendaFailed;
    document.title = `${selected.name} · SessionBuddy`;
    byId("organization-name").textContent = organization.name;
    byId("event-name").textContent = selected.name;
    byId("event-summary").textContent = `${formatRange(selected)} · ${selected.delivery_mode.replace("_", " ")}${selected.location ? ` · ${selected.location}` : ""}`;
    byId("event-status").textContent = selected.status === "active" ? "In progress" : selected.status;
    byId("speaker-count").textContent = String(speakers.length);
    byId("event-time-zone").textContent = selected.time_zone;
    byId("public-schedule").href = `/events/${encodeURIComponent(eventId)}/schedule`;
    const prefix = `/admin/events/${encodeURIComponent(eventId)}`;
    const nextHref = cfpLive ? reviewPath : `${prefix}/cfp`;
    byId("event-actions").hidden = false;
    byId("next-step-title").textContent = cfpLive ? "Review incoming proposals" : "Publish your Call for Proposals";
    byId("next-step-summary").textContent = cfpLive
      ? "Your form is live. Review proposals and prepare the evaluation round."
      : "Create the public form that speakers will use to send proposals.";
    byId("next-step-action").href = nextHref;
    byId("next-step-action").textContent = cfpLive ? "Open submissions" : "Set up the form";
    byId("event-tools").replaceChildren(
      tool(1, "Call for Proposals", "Manage the form and its public link.", `${prefix}/cfp`, cfpLive ? "Live" : "Not published"),
      tool(2, "Review & decide", "Evaluate submissions and choose the program.", reviewPath, reviewBadge),
      tool(3, "Prepare speakers", "Invite people and track onboarding work.", `${prefix}/onboarding`, plural(speakers.length, "speaker")),
      tool(4, "Build the agenda", "Place accepted sessions and resolve conflicts.", `${prefix}/agenda`, agendaBadge),
      tool(5, "Publish", "Share the schedule, speaker pages, and embeds.", `${prefix}/workspace`, agendaFailed ? "Unavailable" : agendaPublished ? "Live" : "Waiting")
    );
    byId("status").textContent = degraded
      ? `${selected.name} loaded, but some live counts are unavailable right now. Refresh to retry.`
      : `${selected.name} is ready.`;
  }

  initialize().catch((error) => {
    if (window.SessionBuddyApi.redirectIfSignedOut(error)) return;
    byId("status").textContent = window.SessionBuddyApi.message(
      error,
      "The event could not be loaded. Try again.",
    );
    byId("status").classList.add("error");
  });
})();

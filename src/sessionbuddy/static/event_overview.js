(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }

  const api = (path) => window.SessionBuddyApi.request(path);

  function tool(title, href, state = "Open", phase = "upcoming") {
    const card = document.createElement("li");
    card.className = `event-stage event-stage--${phase}${state === "Unavailable" ? " event-stage--unavailable" : ""}`;
    const step = document.createElement("span");
    step.className = "event-stage__marker";
    step.setAttribute("aria-hidden", "true");
    const heading = document.createElement("h3");
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.textContent = title;
    heading.append(anchor);
    const badge = document.createElement("span");
    badge.className = "event-stage__state";
    badge.textContent = state;
    card.append(step, heading, badge);
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
    let selected;
    try {
      selected = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
    } catch (error) {
      if (error.status === 404) throw new Error("This event is not available to your account.");
      throw error;
    }
    const organizations = (await api("/api/v1/admin/organizations")).data;
    const organization = organizations.find((item) => item.id === selected.organization_id);
    if (!organization) throw new Error("This event is not available to your account.");
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
    byId("event-status").textContent = selected.status === "active" ? "Active event" : `${selected.status} event`;
    byId("speaker-count").textContent = String(speakers.length);
    byId("event-time-zone").textContent = selected.time_zone;
    byId("public-schedule").href = `/events/${encodeURIComponent(eventId)}/schedule`;
    const prefix = `/admin/events/${encodeURIComponent(eventId)}`;
    const nextHref = !cfpLive || submissionCount === 0 ? `${prefix}/cfp` : reviewPath;
    byId("event-actions").hidden = false;
    byId("next-step-title").textContent = !cfpLive
      ? "Publish your Call for Proposals"
      : submissionCount === 0
        ? "Bring in the first proposal"
        : "Review incoming proposals";
    byId("next-step-summary").textContent = !cfpLive
      ? "Finish the public form so speakers can start submitting."
      : submissionCount === 0
        ? "Your call is live and ready to share. No proposals have arrived yet."
        : `${plural(submissionCount, "proposal")} ${submissionCount === 1 ? "is" : "are"} ready for review and evaluation.`;
    byId("next-step-action").href = nextHref;
    byId("next-step-action").textContent = !cfpLive ? "Set up the form" : submissionCount === 0 ? "Open Call for Proposals" : "Open submissions";
    byId("proposal-count").textContent = submissionsState.ok ? String(submissionCount) : "—";
    byId("proposal-note").textContent = submissionsState.ok ? (cfpLive ? "Call is live" : "Call not published") : "Unavailable";
    byId("agenda-count").textContent = agendaFailed ? "—" : String(agendaItems);
    byId("agenda-note").textContent = agendaFailed ? "Unavailable" : agendaPublished ? "Published" : agendaMissing ? "Not started" : "Draft";
    const currentStep = !cfpLive || submissionCount === 0 ? 1 : !roundState.ok || !roundState.value ? 2 : speakers.length === 0 ? 3 : agendaMissing || (agenda && agendaItems === 0) ? 4 : 5;
    const phase = (step) => step < currentStep ? "complete" : step === currentStep ? "current" : "upcoming";
    // The stage list mirrors the event sidebar: same five program pages, same
    // canonical names, same order.
    byId("event-tools").replaceChildren(
      tool("Call for Proposals", `${prefix}/cfp`, cfpLive ? "Live" : "Not published", phase(1)),
      tool("Submissions", reviewPath, reviewBadge, phase(2)),
      tool("Speakers", `${prefix}/speakers`, plural(speakers.length, "speaker"), phase(3)),
      tool("Agenda", `${prefix}/agenda`, agendaBadge, phase(4)),
      tool("Publish", `${prefix}/workspace`, agendaFailed ? "Unavailable" : agendaPublished ? "Live" : "Waiting", phase(5))
    );
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

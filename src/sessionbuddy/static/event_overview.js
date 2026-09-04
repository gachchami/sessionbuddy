(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }

  const api = (path) => window.SessionBuddyApi.request(path);

  // A draft keeps its working values in draft_* fields while the released
  // columns hold placeholders, so every reader of the schedule must choose the
  // fields by event status. This is the one place that choice is made.
  function effectiveSchedule(event) {
    const draft = event.status === "draft";
    return {
      draft,
      startsAt: draft ? event.draft_starts_at_ms : event.starts_at_ms,
      endsAt: draft ? event.draft_ends_at_ms : event.ends_at_ms,
      delivery: draft ? event.draft_delivery_mode : event.delivery_mode,
    };
  }

  function formatRange(event) {
    try {
      const { startsAt: startValue, endsAt: endValue } = effectiveSchedule(event);
      if (startValue == null) return "Dates not set";
      const date = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: event.time_zone });
      const time = new Intl.DateTimeFormat(undefined, { timeStyle: "short", timeZone: event.time_zone });
      const start = new Date(startValue);
      const end = new Date(endValue ?? startValue);
      const startDate = date.format(start);
      const endDate = date.format(end);
      return startDate === endDate
        ? `${startDate} · ${time.format(start)}–${time.format(end)}`
        : `${startDate}, ${time.format(start)} – ${endDate}, ${time.format(end)}`;
    } catch (_) { return "Event dates unavailable"; }
  }

  function renderEventActivity(result) {
    const state = byId("event-activity-state");
    const list = byId("event-activity-list");
    const activities = result.ok && Array.isArray(result.value?.data) ? result.value.data : [];
    if (!result.ok) {
      state.textContent = "Recent activity is temporarily unavailable. Refresh to try again.";
      state.classList.add("error");
      return;
    }
    if (!activities.length) {
      state.textContent = "No recent activity.";
      return;
    }
    const items = activities.slice(0, 8).map((activity) => {
      const item = document.createElement("li");
      item.className = "event-activity-item";
      const sentence = document.createElement("strong");
      sentence.textContent = window.SessionBuddyActivityFormat.sentence(activity);
      const time = document.createElement("time");
      const occurredAt = new Date(Number(activity.occurred_at_ms));
      time.dateTime = occurredAt.toISOString();
      time.title = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(occurredAt);
      time.textContent = window.SessionBuddyActivityFormat.relativeTime(activity.occurred_at_ms);
      item.append(sentence, time);
      return item;
    });
    list.replaceChildren(...items);
    list.hidden = false;
    state.hidden = true;
  }

  async function initialize() {
    if (!eventId) {
      document.body.classList.remove("is-loading");
      byId("status").textContent = "This event link is invalid. Open your active workspace and select an event.";
      byId("status").classList.add("error");
      return;
    }
    // Start the event read alongside the session check; failures are still
    // handled in the original order below.
    const sessionPromise = api("/api/v1/auth/session");
    const eventPromise = api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
    eventPromise.catch(() => {});
    await sessionPromise;
    let selected;
    try {
      selected = await eventPromise;
    } catch (error) {
      const recoveryScope = window.SessionBuddyApi.recoveryScope.event(eventId);
      if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error, recoveryScope) || window.SessionBuddyApi.redirectIfDocumentAccessChanged(error, recoveryScope)) return;
      throw error;
    }
    const settle = (promise) => promise
      .then((value) => ({ ok: true, value }))
      .catch((error) => ({ ok: false, status: Number(error && error.status) || 0 }));
    const [speakersState, cfpStateResult, submissionsState, roundState, roundListState, agendaState, activityState] = await Promise.all([
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-targets`)),
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/cfp`)),
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions`)),
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds/current`)),
      // The current-round read answers only "is a round open"; drafted and
      // closed rounds are visible only in the round list, so both are loaded.
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluation-rounds`)),
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda`)),
      settle(api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/activities`))
    ]);
    const speakers = speakersState.ok ? speakersState.value.data : [];
    // The roster lists every target, pending invitations included
    // (competition/router.py _SPEAKER_TARGET_LIST_SQL), so "confirmed" must
    // count confirmation_status rather than rows.
    const confirmedSpeakers = speakers.filter((speaker) => speaker.confirmation_status === "confirmed").length;
    const cfp = cfpStateResult.ok ? cfpStateResult.value : {};
    const cfpPublished = Boolean(cfp.published_form);
    // Availability is decided by the API (sessionbuddy/cfp/availability.py) and
    // arrives as availability_state. Publishing a form is not the same as an
    // open call: this tile used to claim "Open · Accepting proposals" for the
    // rest of the event's life, including after the public form had closed.
    const cfpState = cfp.published_form?.availability_state || "";
    const cfpStateLabel = { scheduled: "Scheduled", open: "Open", closed: "Closed" }[cfpState] || "Open";
    const cfpStateNote = {
      scheduled: "Opens on schedule",
      open: "Accepting submissions",
      closed: "Closed to submissions"
    }[cfpState] || "Accepting proposals";
    const accentColor = cfp.published_form?.accent_color || "#0969da";
    const submissionCount = submissionsState.ok
      ? Number(submissionsState.value.total ?? submissionsState.value.data.length)
      : 0;
    // A missing agenda is a real state (404); any other failure is not knowledge.
    const agendaMissing = !agendaState.ok && agendaState.status === 404;
    const agendaFailed = !agendaState.ok && agendaState.status !== 404;
    const agenda = agendaState.ok ? agendaState.value : null;
    const agendaItems = agenda && Array.isArray(agenda.items) ? agenda.items.length : 0;
    // The agenda read always returns the working draft as `revision`, even
    // right after a publish (scheduling/router.py _agenda_model, publish);
    // publication is carried only by published_revision.
    const agendaPublished = Boolean(agenda && agenda.published_revision);
    const degraded = !speakersState.ok || !cfpStateResult.ok || !submissionsState.ok || !roundState.ok || !roundListState.ok || agendaFailed || !activityState.ok;
    document.title = `${selected.name} · SessionBuddy`;
    byId("event-name").textContent = selected.name;
    document.documentElement.style.setProperty("--event-accent", accentColor);
    const { delivery } = effectiveSchedule(selected);
    const summaryParts = [formatRange(selected)];
    if (delivery) summaryParts.push(delivery.replace("_", " "));
    if (selected.location) summaryParts.push(selected.location);
    byId("event-summary").textContent = summaryParts.join(" · ");
    byId("edit-event").href = `/admin/events/${encodeURIComponent(eventId)}/settings`;
    byId("cfp-action").href = `/admin/events/${encodeURIComponent(eventId)}/cfp`;
    byId("cfp-action").hidden = !cfpPublished;
    byId("public-schedule").href = `/events/${encodeURIComponent(eventId)}/schedule`;
    const prefix = `/admin/events/${encodeURIComponent(eventId)}`;
    byId("event-actions").hidden = false;
    byId("next-step").hidden = cfpPublished;
    byId("next-step-action").href = `${prefix}/cfp`;
    renderLaunchReadiness({
      prefix,
      event: selected,
      cfp: { ok: cfpStateResult.ok, published: cfpPublished, stateLabel: cfpStateLabel, hasDraft: Boolean(cfp.draft_form) },
      round: roundState,
      rounds: roundListState,
      agenda: { failed: agendaFailed, missing: agendaMissing, published: agendaPublished, items: agendaItems },
      speakers: { ok: speakersState.ok, count: confirmedSpeakers, total: speakers.length },
      submissions: { ok: submissionsState.ok, count: submissionCount },
    });
    // A draft event has no public page yet; say so instead of linking to a 404.
    const publicPage = byId("public-schedule");
    if (selected.status === "active") {
      publicPage.textContent = "Public event page";
      publicPage.removeAttribute("aria-disabled");
      publicPage.removeAttribute("title");
    } else {
      publicPage.textContent = "Public page not yet available";
      publicPage.setAttribute("aria-disabled", "true");
      publicPage.title = selected.status === "draft" ? "Activate the event to publish its public page." : "Archived events have no public page.";
      publicPage.removeAttribute("href");
    }
    renderEventActivity(activityState);
    document.body.classList.remove("is-loading");
    byId("status").textContent = degraded
      ? "Some program information is unavailable. Refresh to try again."
      : "";
    byId("status").classList.toggle("event-overview-status--warning", degraded);
  }

  // One compact readiness model for the whole launch: five parts, each with
  // words for its state and a route to where it is done. Derived only from
  // data this page already loads, so it never claims more than the API knows.
  // Event details are done only once the event is activated: every other part
  // publishes only from an active event (cfp/router.py refuses to publish a
  // CFP for a draft), so a filled-in draft is still the next step. The wording
  // names what activation needs, mirroring the server's two gates
  // (platform/auth/access.py): _event_storage_values requires name, both
  // dates, location, attendance format, and description, and
  // _validate_event_can_activate requires the end date to be in the future.
  function eventDetailsState(event) {
    const { draft, startsAt, endsAt, delivery } = effectiveSchedule(event);
    const missing = [];
    if (!String(event.name || "").trim()) missing.push("name");
    if (startsAt == null || endsAt == null) missing.push("dates");
    if (!String(event.location || "").trim()) missing.push("location");
    if (!delivery) missing.push("attendance format");
    if (!String(event.description || "").trim()) missing.push("description");
    if (missing.length) return { done: false, state: `Missing: ${missing.join(", ")}` };
    if (draft && Number(endsAt) <= Date.now()) return { done: false, state: "Dates have passed · update before activating" };
    if (draft) return { done: false, state: "Details complete · activate to publish" };
    const activation = { active: "active", archived: "archived" }[event.status] || String(event.status || "");
    return { done: true, state: `Complete · ${activation}` };
  }

  // Review setup is complete only when a round exists and is past draft.
  // Round statuses are draft, open, and closed (evaluation/models.py). The
  // current-round endpoint returns the open round or null, so a null there
  // cannot distinguish "drafted" from "none"; the round list decides that.
  function reviewSetupState(current, rounds) {
    const openRound = current.ok && current.value && typeof current.value === "object" && current.value.id ? current.value : null;
    const statuses = rounds.ok && Array.isArray(rounds.value?.data)
      ? rounds.value.data.map((item) => String(item.status || ""))
      : null;
    if (openRound || statuses?.includes("open")) return { done: true, state: "Round open" };
    if (statuses?.includes("closed")) return { done: true, state: "Round closed" };
    if (statuses?.includes("draft")) return { done: false, state: "Round drafted, not opened" };
    if (statuses && !statuses.length) return { done: false, state: "No review round yet" };
    if (statuses) return { done: false, state: "Round status unavailable" };
    if (current.ok) return { done: false, state: "No open round" };
    return { done: false, state: "Unavailable" };
  }

  function renderLaunchReadiness({ prefix, event, cfp, round, rounds, agenda, speakers, submissions }) {
    const eventDetails = eventDetailsState(event);
    const reviewSetup = reviewSetupState(round, rounds);
    const steps = [
      {
        label: "Event details",
        href: `${prefix}/settings`,
        done: eventDetails.done,
        state: eventDetails.state,
      },
      {
        label: "Call for proposals",
        href: `${prefix}/cfp`,
        done: cfp.published,
        state: !cfp.ok ? "Unavailable" : cfp.published ? `Published · ${cfp.stateLabel}${submissions.ok ? ` · ${submissions.count} received` : ""}` : cfp.hasDraft ? "Draft saved, not published" : "Not set up",
      },
      {
        label: "Review setup",
        href: `${prefix}/submissions`,
        done: reviewSetup.done,
        state: reviewSetup.state,
      },
      {
        label: "Agenda",
        href: `${prefix}/agenda`,
        done: agenda.published,
        state: agenda.failed ? "Unavailable" : agenda.missing ? "Not started" : `${agenda.published ? "Published" : "Draft"} · ${agenda.items} session${agenda.items === 1 ? "" : "s"}`,
      },
      {
        label: "Speakers and sharing",
        href: `${prefix}/speakers`,
        done: speakers.ok && speakers.count > 0 && agenda.published,
        state: speakers.ok ? `${speakers.count} confirmed of ${speakers.total} invited${agenda.published ? " · program shareable" : ""}` : "Unavailable",
      },
    ];
    const list = byId("launch-readiness-steps");
    list.replaceChildren();
    const currentIndex = steps.findIndex((step) => !step.done);
    steps.forEach((step, index) => {
      const item = document.createElement("li");
      item.className = `event-readiness__step${step.done ? " is-done" : ""}${index === currentIndex ? " is-current" : ""}`;
      const marker = document.createElement("span");
      marker.className = "event-readiness__marker";
      marker.setAttribute("aria-hidden", "true");
      if (step.done) {
        marker.innerHTML = '<svg viewBox="0 0 16 16" width="12" height="12" focusable="false"><path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      } else {
        marker.textContent = String(index + 1);
      }
      const link = document.createElement("a");
      link.className = "event-readiness__label";
      link.href = step.href;
      link.textContent = step.label;
      const state = document.createElement("span");
      state.className = "event-readiness__state";
      state.textContent = step.state;
      const srState = document.createElement("span");
      srState.className = "sr-only";
      srState.textContent = step.done ? "Complete: " : index === currentIndex ? "Next: " : "";
      item.append(marker, srState, link, state);
      list.append(item);
    });
    const done = steps.filter((step) => step.done).length;
    byId("launch-readiness-progress").textContent = `${done} of ${steps.length} complete`;
    byId("launch-readiness").hidden = false;
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

(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/agenda$/);
  let eventId = "";
  try {
    eventId = match ? decodeURIComponent(match[1]) : "";
  } catch (_) {
    eventId = "";
  }
  if (!/^[A-Za-z0-9_.-]{1,128}$/.test(eventId)) eventId = "";
  const state = {
    csrf: "",
    model: null,
    selected: null,
    previewTimer: null,
    previewToken: 0,
    pendingArchive: null,
    pendingDeleteSession: null,
    publishMutation: null,
  };
  const byId = (id) => document.getElementById(id);
  const make = (tag, value, className) => {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    if (className) node.className = className;
    return node;
  };
  const key = () => `${crypto.randomUUID()}-${crypto.randomUUID()}`;

  function labelChips(labels = []) {
    const list = make("div", undefined, "label-chip-list");
    labels.forEach((label) => {
      const chip = make("span", label.name, "label-chip");
      chip.style.setProperty("--label-color", label.color);
      list.append(chip);
    });
    return list;
  }

  async function api(path, options = {}) {
    return window.SessionBuddyApi.request(path, options);
  }
  function status(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }
  function format(value, options = {}) {
    try {
      return new Intl.DateTimeFormat(undefined, {
        timeZone: state.model.event.time_zone,
        ...options,
      }).format(new Date(value));
    } catch (_) {
      return "Date unavailable";
    }
  }
  function partsInTimeZone(value) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: state.model.event.time_zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(value));
    return Object.fromEntries(
      parts.filter(({ type }) => type !== "literal").map((
        { type, value: part },
      ) => [type, Number(part)]),
    );
  }
  function localInput(value) {
    const parts = partsInTimeZone(value);
    const pad = (part) => String(part).padStart(2, "0");
    return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${
      pad(parts.hour)
    }:${pad(parts.minute)}`;
  }
  function inputMillis(value) {
    const match = String(value).match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/,
    );
    if (!match) throw new Error("Choose a complete date and time.");
    const [, year, month, day, hour, minute] = match.map(Number);
    const intended = Date.UTC(year, month - 1, day, hour, minute);
    let timestamp = intended;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const actual = partsInTimeZone(timestamp);
      const actualAsUtc = Date.UTC(
        actual.year,
        actual.month - 1,
        actual.day,
        actual.hour,
        actual.minute,
      );
      const adjustment = intended - actualAsUtc;
      timestamp += adjustment;
      if (adjustment === 0) break;
    }
    if (localInput(timestamp) !== value) {
      throw new Error(
        `That local time does not exist in ${state.model.event.time_zone}.`,
      );
    }
    return timestamp;
  }
  function eventDate(value) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: state.model.event.time_zone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(new Date(value));
    const fields = Object.fromEntries(
      parts.map((part) => [part.type, part.value]),
    );
    return `${fields.year}-${fields.month}-${fields.day}`;
  }
  function currentView() {
    return document.querySelector('input[name="view"]:checked').value;
  }
  function eventDays() {
    const { starts_at_ms: start, ends_at_ms: end } = state.model.event;
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return [];
    try {
      // Enumerate calendar dates, not 24-hour steps through local instants:
      // daylight-saving transitions can make an event day 23 or 25 hours.
      const first = Date.parse(`${eventDate(start)}T00:00:00Z`);
      const last = Date.parse(`${eventDate(end)}T00:00:00Z`);
      const count = (last - first) / 86400000 + 1;
      // A corrupt or unusually long range must not create an unbounded board.
      // Existing scheduled days are always added independently below.
      if (!Number.isInteger(count) || count < 1 || count > 366) return [];
      return Array.from({ length: count }, (_, index) =>
        new Date(first + index * 86400000).toISOString().slice(0, 10)
      );
    } catch (_) {
      return [];
    }
  }
  function dayLabel(date) {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: "UTC", weekday: "long", month: "short", day: "numeric",
    }).format(new Date(`${date}T00:00:00Z`));
  }
  function groupKey(item, view) {
    if (view === "room") return item.room_name || "Unassigned room";
    if (view === "track") return item.track_name || "No track";
    if (view === "day") {
      return eventDate(item.start_at_ms);
    }
    if (view === "week") {
      const date = new Date(item.start_at_ms);
      date.setUTCDate(date.getUTCDate() - date.getUTCDay());
      return `Week of ${
        format(date.getTime(), {
          month: "short",
          day: "numeric",
          year: "numeric",
        })
      }`;
    }
    return "All sessions";
  }
  function card(item, scheduled = true) {
    const node = make("li", undefined, "session-card");
    node.dataset.itemId = item.id || "";
    node.dataset.sessionId = item.session_id;
    node.draggable = true;
    if (item.id?.startsWith("optimistic-")) node.classList.add("optimistic");
    node.setAttribute(
      "aria-label",
      `${item.title}. ${
        scheduled
          ? "Scheduled session; drag to move or use Edit session."
          : "Unscheduled session; drag to schedule or use Schedule session."
      }`,
    );
    node.append(make("h3", item.title));
    if (item.labels?.length) node.append(labelChips(item.labels));
    if (scheduled) {
      node.append(
        make(
          "p",
          `${
            format(item.start_at_ms, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })
          }–${
            format(item.end_at_ms, { hour: "numeric", minute: "2-digit" })
          } · ${item.room_name}`,
          "scheduled-meta",
        ),
      );
    }
    node.append(make("p", item.content_status === "approved" ? "Public content approved" : "Content draft", "help"));
    if (item.source_type === "organizer_created" && item.participants?.length > 1) {
      const people = make("details", undefined, "session-participants");
      people.append(make("summary", `Participants (${item.participants.length})`));
      item.participants.forEach((participant) => {
        const removeParticipant = make("button", `Remove ${participant.display_name}`, "tertiary danger-text");
        removeParticipant.type = "button";
        removeParticipant.addEventListener("click", async () => {
          removeParticipant.disabled = true;
          try {
            await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/sessions/${encodeURIComponent(item.session_id)}/participants/${encodeURIComponent(participant.id)}`, {
              method: "DELETE",
              headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
            });
            await load(false);
            status(`${participant.display_name} removed from the session.`);
          } catch (error) {
            status(window.SessionBuddyApi.message(error, "The participant could not be removed."), true);
            removeParticipant.disabled = false;
          }
        });
        people.append(removeParticipant);
      });
      node.append(people);
    }
    const edit = make("button", scheduled ? "Edit session" : "Schedule session", "secondary");
    edit.type = "button";
    edit.setAttribute(
      "aria-label",
      `${scheduled ? "Edit session" : "Schedule session"}: ${item.title}`,
    );
    edit.addEventListener("click", () => openEditor(item));
    node.append(edit);
    if (!scheduled && item.source_type === "organizer_created") {
      const remove = make("button", "Delete session", "tertiary danger-text");
      remove.type = "button";
      remove.addEventListener("click", () => {
        state.pendingDeleteSession = item;
        byId("delete-session-summary").textContent = `“${item.title}” will be deleted.`;
        byId("delete-session-dialog").showModal();
      });
      node.append(remove);
    }
    node.addEventListener("dragstart", (event) => {
      state.selected = item;
      node.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", item.id || item.session_id);
    });
    node.addEventListener("dragend", () => {
      node.classList.remove("dragging");
      document.querySelectorAll(".agenda-group").forEach((group) =>
        group.classList.remove("preview-ok", "preview-conflict")
      );
    });
    return node;
  }
  function renderResources() {
    const participantSelect = byId("manual-session-form").elements.participant_ids;
    participantSelect.replaceChildren();
    (state.model.session_participants || []).forEach((participant) => {
      const option = make(
        "option",
        participant.recipient_state === "invited"
          ? `${participant.display_name} — invited, awaiting acceptance`
          : participant.display_name,
      );
      option.value = participant.id;
      participantSelect.append(option);
    });
    const renderList = (kind, values, archivedValues = []) => {
      const list = byId(`${kind}-list`);
      list.replaceChildren();
      if (!values.length) {
        list.append(make("li", kind === "room" ? "No rooms" : "No tracks", "help"));
      } else values.forEach((value) => {
        const item = make("li");
        item.append(make("span", value.name));
        if (state.model.can_manage_resource_lifecycle) {
          const archive = make("button", "Archive", "secondary");
          archive.type = "button";
          archive.setAttribute("aria-label", `Archive ${value.name}`);
          archive.addEventListener("click", () => openResourceArchive(kind, value));
          item.append(archive);
        }
        list.append(item);
      });
      const archivedSection = byId(`archived-${kind}-section`);
      const archivedList = byId(`archived-${kind}-list`);
      archivedSection.hidden = !archivedValues.length;
      archivedList.replaceChildren();
      archivedValues.forEach((value) => {
        const item = make("li");
        item.append(make("span", value.name));
        if (state.model.can_manage_resource_lifecycle) {
          const restore = make("button", "Restore", "secondary");
          restore.type = "button";
          restore.setAttribute("aria-label", `Restore ${value.name}`);
          restore.addEventListener("click", () => updateResource(kind, value, "active"));
          item.append(restore);
        }
        archivedList.append(item);
      });
    };
    renderList("room", state.model.rooms, state.model.archived_rooms || []);
    renderList("track", state.model.tracks, state.model.archived_tracks || []);
    const labelList = byId("label-list");
    labelList.replaceChildren();
    if (!state.model.labels.length) {
      labelList.append(make("li", "No labels", "help"));
    }
    state.model.labels.forEach((label) => {
      const item = make("li");
      const identity = make("span", undefined, "label-resource");
      const swatch = make("span", undefined, "label-swatch");
      swatch.style.setProperty("--label-color", label.color);
      identity.append(swatch, make("span", label.name));
      item.append(identity);
      if (label.can_manage) {
        const actions = make("span", undefined, "resource-actions");
        const edit = make("button", "Edit", "secondary");
        edit.type = "button";
        edit.setAttribute("aria-label", `Edit ${label.name}`);
        edit.addEventListener("click", () => openLabelEditor(label));
        actions.append(edit);
        if (state.model.can_manage_resource_lifecycle) {
          const archive = make("button", "Archive", "secondary");
          archive.type = "button";
          archive.setAttribute("aria-label", `Archive ${label.name}`);
          archive.addEventListener("click", () => openResourceArchive("label", label));
          actions.append(archive);
        }
        item.append(actions);
      }
      labelList.append(item);
    });
    const archivedLabels = state.model.archived_labels || [];
    const archivedLabelSection = byId("archived-label-section");
    const archivedLabelList = byId("archived-label-list");
    archivedLabelSection.hidden = !archivedLabels.length;
    archivedLabelList.replaceChildren();
    archivedLabels.forEach((label) => {
      const item = make("li");
      const identity = make("span", undefined, "label-resource");
      const swatch = make("span", undefined, "label-swatch");
      swatch.style.setProperty("--label-color", label.color);
      identity.append(swatch, make("span", label.name));
      item.append(identity);
      if (state.model.can_manage_resource_lifecycle) {
        const restore = make("button", "Restore", "secondary");
        restore.type = "button";
        restore.setAttribute("aria-label", `Restore ${label.name}`);
        restore.addEventListener("click", () => saveLabel(label, "active"));
        item.append(restore);
      }
      archivedLabelList.append(item);
    });
    const autoForm = byId("auto-schedule-form");
    if (!autoForm.elements.start_at.value) {
      autoForm.elements.start_at.value = localInput(state.model.event.starts_at_ms);
    }
    byId("auto-schedule").disabled = state.model.unscheduled_sessions.length === 0;
  }
  function render() {
    const model = state.model;
    const view = currentView();
    byId("agenda-setup").hidden = true;
    byId("agenda-workspace").hidden = false;
    byId("revision-card").hidden = false;
    byId("event-title").textContent = "Agenda";
    byId("timezone").textContent = `${model.event.name} · ${model.event.time_zone}`;
    document.querySelectorAll("[data-event-time-zone]").forEach((node) => {
      node.textContent = model.event.time_zone;
    });
    byId("revision").textContent = model.published_revision
      ? `Draft v${model.revision.version} · latest published revision ${model.published_revision.revision_number}`
      : `v${model.revision.version} · ${model.revision.state}`;
    byId("published-link").href = `/events/${
      encodeURIComponent(eventId)
    }/schedule`;
    byId("item-count").textContent = String(model.items.length);
    byId("publish").disabled = model.items.length === 0;
    renderResources();
    const unscheduled = byId("unscheduled");
    unscheduled.replaceChildren();
    if (!model.unscheduled_sessions.length) {
      unscheduled.append(
        make(
          "li",
          model.items.length
            ? "All accepted sessions are scheduled."
            : "No sessions are ready yet. Accept a proposal or create a session under Schedule tools.",
          "empty",
        ),
      );
    }
    model.unscheduled_sessions.forEach((item) =>
      unscheduled.append(card(item, false))
    );
    const groups = new Map();
    if (view === "day") eventDays().forEach((day) => groups.set(day, []));
    model.items.forEach((item) => {
      const name = groupKey(item, view);
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(item);
    });
    const board = byId("board");
    board.replaceChildren();
    byId("empty").hidden = model.items.length !== 0 || groups.size !== 0;
    byId("empty").querySelector("p").textContent = model.unscheduled_sessions.length
      ? "Add an accepted session from the unscheduled list."
      : "Accepted proposals and organizer-created sessions will appear here.";
    [...groups].sort(([left], [right]) => left.localeCompare(right)).forEach(
      ([name, items]) => {
        const section = make("section", undefined, "agenda-group");
        section.dataset.group = name;
        section.append(make("h3", view === "day" ? dayLabel(name) : name));
        const list = make("ul", undefined, "agenda-group-list");
        if (!items.length) list.append(make("li", "No sessions scheduled for this day.", "empty"));
        items.sort((a, b) => a.start_at_ms - b.start_at_ms).forEach((item) =>
          list.append(card(item))
        );
        section.append(list);
        section.addEventListener("dragover", (event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
        });
        section.addEventListener(
          "dragenter",
          () => previewDrop(section, name, view),
        );
        section.addEventListener("drop", (event) => {
          event.preventDefault();
          commitDrop(section, name, view);
        });
        board.append(section);
      },
    );
    byId("agenda").setAttribute("aria-busy", "false");
  }
  function candidateForGroup(item, name, view) {
    const candidate = {
      item_id: item.id || null,
      session_id: item.session_id,
      start_at_ms: item.start_at_ms,
      end_at_ms: item.end_at_ms,
      room_id: item.room_id || state.model.rooms[0]?.id,
      track_id: item.track_id || null,
      version: item.version || 0,
      event_date: eventDate(item.start_at_ms),
    };
    if (view === "room") {
      candidate.room_id = state.model.rooms.find((room) =>
        room.name === name
      )?.id || candidate.room_id;
    }
    if (view === "track") {
      candidate.track_id = state.model.tracks.find((track) =>
        track.name === name
      )?.id || null;
    }
    return candidate;
  }
  async function preview(candidate) {
    return api(
      `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/preview`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-csrf-token": state.csrf,
        },
        body: JSON.stringify(candidate),
      },
    );
  }
  async function previewDrop(section, name, view) {
    if (!state.selected?.start_at_ms) return;
    try {
      const result = await preview(
        candidateForGroup(state.selected, name, view),
      );
      section.classList.toggle("preview-ok", result.valid);
      section.classList.toggle("preview-conflict", !result.valid);
      showConflicts(result.conflicts || []);
    } catch (_) {
      section.classList.add("preview-conflict");
    }
  }
  async function commitDrop(section, name, view) {
    if (!state.selected?.start_at_ms) {
      openEditor(state.selected);
      return;
    }
    const candidate = candidateForGroup(state.selected, name, view);
    const before = structuredClone(state.model);
    try {
      const check = await preview(candidate);
      if (!check.valid) {
        showConflicts(check.conflicts);
        section.classList.add("preview-conflict");
        return;
      }
      optimistic(candidate);
      render();
      await save(candidate);
      status("Agenda change saved.");
    } catch (error) {
      state.model = before;
      render();
      const restored = document.querySelector(
        `[data-item-id="${CSS.escape(candidate.item_id || "")}"]`,
      );
      restored?.classList.add("rollback");
      status(
        error.status === 409
          ? "Schedule changed elsewhere or conflicts. Your move was rolled back."
          : "The move could not be saved and was rolled back.",
        true,
      );
    }
  }
  function optimistic(candidate) {
    let item = state.model.items.find((value) =>
      value.id === candidate.item_id
    );
    if (!item) {
      item = {
        ...state.selected,
        id: `optimistic-${candidate.session_id}`,
        version: 0,
      };
      state.model.items.push(item);
      state.model.unscheduled_sessions = state.model.unscheduled_sessions
        .filter((value) => value.session_id !== candidate.session_id);
    }
    Object.assign(item, candidate);
    item.room_name =
      state.model.rooms.find((room) => room.id === candidate.room_id)?.name ||
      item.room_name;
    item.track_name =
      state.model.tracks.find((track) => track.id === candidate.track_id)
        ?.name || null;
  }
  async function save(candidate) {
    const creating = !candidate.item_id;
    const path = creating
      ? `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/items`
      : `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/items/${
        encodeURIComponent(candidate.item_id)
      }`;
    const saved = await api(path, {
      method: creating ? "POST" : "PATCH",
      headers: {
        "content-type": "application/json",
        "x-csrf-token": state.csrf,
        "idempotency-key": key(),
      },
      body: JSON.stringify(candidate),
    });
    await load(false);
    return saved;
  }
  function options(select, values, selected, empty) {
    select.replaceChildren();
    if (empty) select.add(new Option(empty, ""));
    values.forEach((value) => select.add(new Option(value.name, value.id)));
    select.value = selected || "";
  }
  function renderLabelChoices(item) {
    const root = byId("session-labels");
    root.replaceChildren();
    if (!state.model.labels.length) {
      root.append(make("p", "No labels exist for this event yet.", "help"));
      return;
    }
    const selected = new Set(item.label_ids || []);
    state.model.labels.forEach((label) => {
      const wrapper = make("label", undefined, "label-choice");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.name = "session_label";
      input.value = label.id;
      input.checked = selected.has(label.id);
      const swatch = make("span", undefined, "label-swatch");
      swatch.style.setProperty("--label-color", label.color);
      wrapper.append(input, swatch, make("span", label.name));
      root.append(wrapper);
    });
  }
  async function loadContentHistory(item) {
    const list = byId("content-history");
    list.replaceChildren(make("li", "Loading history…", "help"));
    try {
      const content = await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/sessions/${encodeURIComponent(item.session_id)}/content`,
      );
      Object.assign(item, {
        title: content.title,
        abstract: content.abstract,
        content_status: content.content_status,
        content_version: content.version,
      });
      const form = byId("editor-form");
      form.elements.title.value = content.title;
      form.elements.abstract.value = content.abstract;
      window.SessionBuddyApi.refreshCharacterCounters(form);
      form.elements.content_status.value = content.content_status;
      form.elements.content_version.value = content.version;
      list.replaceChildren();
      if (!content.history.length) {
        list.append(make("li", "No changes recorded yet.", "help"));
        return;
      }
      content.history.forEach((entry) => {
        const row = make("li", undefined, "content-history-entry");
        const copy = make("div", undefined, "content-history-entry__copy");
        copy.append(
          make("strong", `v${entry.version} · ${entry.title}`),
          make(
            "span",
            `${entry.content_status} · ${format(entry.created_at_ms, { dateStyle: "medium", timeStyle: "short" })} · ${entry.changed_by}`,
            "help",
          ),
          make("p", entry.abstract, "content-history-entry__abstract"),
        );
        row.append(copy);
        if (entry.version !== content.version) {
          const restore = make("button", "Restore", "secondary");
          restore.type = "button";
          restore.addEventListener("click", () => restoreContent(item, entry.version, restore));
          row.append(restore);
        }
        list.append(row);
      });
    } catch (error) {
      list.replaceChildren(make("li", window.SessionBuddyApi.message(error), "help"));
    }
  }
  async function restoreContent(item, historyVersion, button) {
    const saveButton = byId("save-item");
    button.disabled = true;
    saveButton.disabled = true;
    status("Restoring session content…");
    try {
      const content = await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/sessions/${encodeURIComponent(item.session_id)}/content/restore`,
        {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
          body: JSON.stringify({
            history_version: historyVersion,
            current_version: Number(byId("editor-form").elements.content_version.value),
          }),
        },
      );
      Object.assign(item, { title: content.title, abstract: content.abstract, content_status: content.content_status, content_version: content.version });
      const form = byId("editor-form");
      // Apply the authoritative response immediately. A refresh below replaces
      // the agenda model, but the editor remains open and can otherwise submit
      // its pre-restore version while that refresh is in flight.
      form.elements.title.value = content.title;
      form.elements.abstract.value = content.abstract;
      form.elements.content_status.value = content.content_status;
      form.elements.content_version.value = content.version;
      await load(false);
      state.selected = state.model.items.find((value) => value.session_id === item.session_id)
        || state.model.unscheduled_sessions.find((value) => value.session_id === item.session_id)
        || item;
      await loadContentHistory(state.selected);
      status(`Content restored from version ${historyVersion}.`);
    } catch (error) {
      status(window.SessionBuddyApi.message(error), true);
    } finally {
      button.disabled = false;
      saveButton.disabled = false;
    }
  }
  function openEditor(item) {
    if (!item) return;
    state.selected = item;
    const form = byId("editor-form");
    form.elements.item_id.value = item.id || "";
    form.elements.session_id.value = item.session_id;
    form.elements.version.value = item.version || 0;
    form.elements.content_version.value = item.content_version || 1;
    form.elements.label_version.value = item.label_version || 1;
    form.elements.title.value = item.title;
    form.elements.abstract.value = item.abstract || "";
    byId("editor-speakers").textContent = item.speaker_names || "Not provided";
    window.SessionBuddyApi.refreshCharacterCounters(form);
    form.elements.content_status.value = item.content_status || "draft";
    renderLabelChoices(item);
    form.elements.start_at.value = item.start_at_ms
      ? localInput(item.start_at_ms)
      : "";
    form.elements.end_at.value = item.end_at_ms
      ? localInput(item.end_at_ms)
      : "";
    options(
      form.elements.room_id,
      state.model.rooms,
      item.room_id,
      "Choose room",
    );
    options(
      form.elements.track_id,
      state.model.tracks,
      item.track_id,
      "No track",
    );
    byId("unschedule-item").hidden = !item.id;
    byId("editor-title").textContent = item.id
      ? `Edit session: ${item.title}`
      : `Schedule session: ${item.title}`;
    clearConflicts();
    byId("preview-state").textContent = "Change a field to check conflicts.";
    byId("editor").showModal();
    (item.id ? form.elements.start_at : form.elements.title).focus();
    loadContentHistory(item);
  }
  async function saveContent(item) {
    const form = byId("editor-form");
    const payload = {
      title: form.elements.title.value,
      abstract: form.elements.abstract.value,
      content_status: form.elements.content_status.value,
      version: Number(form.elements.content_version.value),
    };
    if (
      payload.title === item.title && payload.abstract === (item.abstract || "")
      && payload.content_status === (item.content_status || "draft")
    ) return;
    const content = await api(
      `/api/v1/admin/events/${encodeURIComponent(eventId)}/sessions/${encodeURIComponent(item.session_id)}/content`,
      {
        method: "PATCH",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
        body: JSON.stringify(payload),
      },
    );
    Object.assign(item, {
      title: content.title,
      abstract: content.abstract,
      content_status: content.content_status,
      content_version: content.version,
    });
  }
  async function saveLabels(item) {
    const form = byId("editor-form");
    const labelIds = [...form.querySelectorAll('input[name="session_label"]:checked')]
      .map((input) => input.value);
    const before = [...(item.label_ids || [])].sort();
    const after = [...labelIds].sort();
    if (before.length === after.length && before.every((value, index) => value === after[index])) {
      return;
    }
    const result = await api(
      `/api/v1/admin/events/${encodeURIComponent(eventId)}/sessions/${encodeURIComponent(item.session_id)}/labels`,
      {
        method: "PUT",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
        body: JSON.stringify({
          label_ids: labelIds,
          version: Number(form.elements.label_version.value),
        }),
      },
    );
    Object.assign(item, {
      labels: result.labels,
      label_ids: result.labels.map((label) => label.id),
      label_version: result.version,
    });
  }
  function formCandidate() {
    const form = byId("editor-form");
    const start = inputMillis(form.elements.start_at.value);
    return {
      item_id: form.elements.item_id.value || null,
      session_id: form.elements.session_id.value,
      start_at_ms: start,
      end_at_ms: inputMillis(form.elements.end_at.value),
      room_id: form.elements.room_id.value,
      track_id: form.elements.track_id.value || null,
      version: Number(form.elements.version.value),
      event_date: eventDate(start),
    };
  }
  function clearConflicts() {
    byId("conflict-banner").hidden = true;
    byId("editor-errors").hidden = true;
  }
  function showConflicts(conflicts, editor = false) {
    const panel = byId(editor ? "editor-errors" : "conflict-banner");
    const list = panel.querySelector("ul");
    list.replaceChildren();
    conflicts.forEach((conflict) => list.append(make("li", conflict.message)));
    panel.hidden = conflicts.length === 0;
    if (conflicts.length) panel.focus();
  }
  function schedulePreview() {
    clearTimeout(state.previewTimer);
    const token = ++state.previewToken;
    state.previewTimer = setTimeout(async () => {
      let candidate;
      try {
        candidate = formCandidate();
      } catch (error) {
        byId("preview-state").textContent = error.message;
        byId("preview-state").className = "preview-state error";
        byId("save-item").disabled = true;
        return;
      }
      if (
        !Number.isFinite(candidate.start_at_ms) ||
        !Number.isFinite(candidate.end_at_ms) ||
        candidate.start_at_ms >= candidate.end_at_ms || !candidate.room_id
      ) {
        byId("preview-state").textContent =
          "Enter a valid start, end, and room.";
        byId("preview-state").className = "preview-state error";
        byId("save-item").disabled = true;
        return;
      }
      try {
        const result = await preview(candidate);
        if (token !== state.previewToken) return;
        showConflicts(result.conflicts || [], true);
        byId("preview-state").textContent = result.valid
          ? "No room or speaker conflicts found."
          : "Conflict found. Adjust the schedule before saving.";
        byId("preview-state").className = `preview-state ${
          result.valid ? "ok" : "error"
        }`;
        byId("save-item").disabled = !result.valid;
      } catch (_) {
        byId("preview-state").textContent =
          "Conflict preview unavailable. Saving is disabled.";
        byId("preview-state").className = "preview-state error";
        byId("save-item").disabled = true;
      }
    }, 150);
  }
  async function load(announce = true) {
    const model = await api(
      `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda`,
    );
    state.model = model;
    render();
    if (announce) status("Draft agenda loaded.");
  }
  function names(value) {
    return String(value || "").split(",").map((name) => name.trim()).filter(
      Boolean,
    );
  }
  function showSetup() {
    byId("agenda-setup").hidden = false;
    byId("agenda-workspace").hidden = true;
    byId("revision-card").hidden = true;
    byId("agenda").setAttribute("aria-busy", "false");
    byId("timezone").textContent = "No agenda yet";
    status("Add rooms to create this event’s first agenda.");
  }
  function openResourceArchive(kind, resource) {
    const descriptions = {
      room: "This room will no longer be available for scheduling. Remove or move every scheduled session from it before archiving.",
      track: "This track will no longer be available for scheduling. Remove it from every scheduled session before archiving.",
      label: "This label will no longer be available for sessions. Remove it from every session before archiving.",
    };
    state.pendingArchive = { kind, resource };
    byId("resource-archive-title").textContent = `Archive ${resource.name}?`;
    byId("resource-archive-consequence").textContent = descriptions[kind];
    byId("resource-archive-dialog").showModal();
  }
  async function updateResource(kind, resource, nextStatus = "archived") {
    const label = kind === "room" ? "room" : "track";
    try {
      state.model = await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/${kind}s/${encodeURIComponent(resource.id)}`,
        {
          method: "PATCH",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
          },
          body: JSON.stringify({ status: nextStatus, version: resource.version }),
        },
      );
      render();
      showConflicts([]);
      status(`${label[0].toUpperCase()}${label.slice(1)} ${nextStatus === "archived" ? "archived" : "restored"}.`);
    } catch (error) {
      status(error.message || `The ${label} could not be ${nextStatus === "archived" ? "archived" : "restored"}.`, true);
    }
  }
  function openLabelEditor(label) {
    const form = byId("label-editor-form");
    form.elements.label_id.value = label.id;
    form.elements.version.value = label.version;
    form.elements.name.value = label.name;
    form.elements.color.value = label.color;
    byId("label-editor").showModal();
  }
  async function saveLabel(label, nextStatus = "active") {
    try {
      await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/labels/${encodeURIComponent(label.id)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
          body: JSON.stringify({
            name: label.name,
            color: label.color,
            status: nextStatus,
            version: label.version,
          }),
        },
      );
      byId("label-editor").close();
      await load(false);
      status(nextStatus === "archived" ? "Label archived." : label.status === "archived" ? "Label restored." : "Label saved.");
    } catch (error) {
      status(error.message || "The label could not be saved.", true);
    }
  }
  ["room", "track"].forEach((kind) => {
    byId(`${kind}-form`).addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      try {
        state.model = await api(
          `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/${kind}s`,
          {
            method: "POST",
            headers: {
              "content-type": "application/json",
              "x-csrf-token": state.csrf,
            },
            body: JSON.stringify({ name: form.elements.name.value }),
          },
        );
        form.reset();
        render();
        showConflicts([]);
        status(`${kind === "room" ? "Room" : "Track"} added.`);
      } catch (error) {
        status(error.message || `The ${kind} could not be added.`, true);
      } finally {
        button.disabled = false;
      }
    });
  });
  byId("label-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/labels`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
        body: JSON.stringify({ name: form.elements.name.value, color: form.elements.color.value }),
      });
      form.reset();
      form.elements.color.value = "#2563EB";
      await load(false);
      status("Label added.");
    } catch (error) {
      status(error.message || "The label could not be added.", true);
    } finally {
      button.disabled = false;
    }
  });
  byId("label-editor-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    await saveLabel({
      id: form.elements.label_id.value,
      version: Number(form.elements.version.value),
      name: form.elements.name.value,
      color: form.elements.color.value,
    });
  });
  byId("cancel-label-edit").addEventListener("click", () => byId("label-editor").close());
  byId("resource-archive-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const pending = state.pendingArchive;
    byId("resource-archive-dialog").close();
    state.pendingArchive = null;
    if (!pending) return;
    if (pending.kind === "label") await saveLabel(pending.resource, "archived");
    else await updateResource(pending.kind, pending.resource, "archived");
  });
  byId("cancel-resource-archive").addEventListener("click", () => {
    state.pendingArchive = null;
    byId("resource-archive-dialog").close();
  });
  byId("resource-archive-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    state.pendingArchive = null;
    byId("resource-archive-dialog").close();
  });
  byId("auto-schedule-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const button = byId("auto-schedule");
    button.disabled = true;
    try {
      const result = await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/auto-schedule`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": key(),
          },
          body: JSON.stringify({
            start_at_ms: inputMillis(form.elements.start_at.value),
            session_minutes: Number(form.elements.session_minutes.value),
            gap_minutes: Number(form.elements.gap_minutes.value),
            room_ids: [],
          }),
        },
      );
      state.model = result;
      render();
      showConflicts([]);
      const scheduled = result.auto_schedule?.scheduled_count || 0;
      const remaining = result.auto_schedule?.remaining_count || 0;
      status(
        remaining
          ? `${scheduled} sessions placed. ${remaining} did not fit and remain unscheduled.`
          : `${scheduled} sessions placed. Review the draft before publishing.`,
      );
    } catch (error) {
      status(error.message || "The draft schedule could not be built.", true);
    } finally {
      button.disabled = state.model?.unscheduled_sessions.length === 0;
    }
  });
  byId("manual-session-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const participantIds = [...form.elements.participant_ids.selectedOptions]
      .map((option) => option.value);
    if (!participantIds.length) {
      form.elements.participant_ids.setCustomValidity("Choose at least one participant.");
      form.elements.participant_ids.reportValidity();
      return;
    }
    form.elements.participant_ids.setCustomValidity("");
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      state.model = await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/sessions`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": key(),
          },
          body: JSON.stringify({
            title: form.elements.title.value.trim(),
            abstract: form.elements.abstract.value.trim(),
            participant_ids: participantIds,
          }),
        },
      );
      form.reset();
      render();
      status("Session created and added to the unscheduled list.");
    } catch (error) {
      status(error.message || "The session could not be created.", true);
    } finally {
      button.disabled = false;
    }
  });
  byId("agenda-setup-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const roomNames = names(form.elements.room_names.value);
    const trackNames = names(form.elements.track_names.value);
    if (!roomNames.length) {
      form.elements.room_names.setCustomValidity("Enter at least one room.");
      form.elements.room_names.reportValidity();
      return;
    }
    form.elements.room_names.setCustomValidity("");
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      state.model = await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/setup`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": key(),
          },
          body: JSON.stringify({
            room_names: roomNames,
            track_names: trackNames,
          }),
        },
      );
      render();
      status("Agenda created. Accepted sessions are ready to schedule.");
    } catch (error) {
      status(
        error.status === 409
          ? "This agenda was already created. Refresh to continue."
          : error.message,
        true,
      );
    } finally {
      button.disabled = false;
    }
  });
  document.querySelectorAll('input[name="view"]').forEach((input) =>
    input.addEventListener("change", render)
  );
  byId("refresh").addEventListener(
    "click",
    () => load().catch((error) => {
      // A missing agenda is the normal pre-setup state, not a missing event.
      if (error.status === 404) {
        showSetup();
        return;
      }
      if (window.SessionBuddyApi.redirectIfDocumentAccessChanged(error, window.SessionBuddyApi.recoveryScope.event(eventId))) return;
      status("Agenda could not be refreshed.", true);
    }),
  );
  byId("cancel-delete-session").addEventListener("click", () => {
    state.pendingDeleteSession = null;
    byId("delete-session-dialog").close();
  });
  byId("delete-session-dialog").addEventListener("cancel", () => {
    state.pendingDeleteSession = null;
  });
  byId("delete-session-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const item = state.pendingDeleteSession;
    if (!item) return;
    const button = event.currentTarget.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/sessions/${encodeURIComponent(item.session_id)}`, {
        method: "DELETE",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
      });
      state.pendingDeleteSession = null;
      byId("delete-session-dialog").close();
      await load(false);
      status("Session deleted.");
    } catch (error) {
      status(window.SessionBuddyApi.message(error, "The session could not be deleted."), true);
    } finally {
      button.disabled = false;
    }
  });
  function openPublishDialog() {
    const scheduled = state.model?.items.length || 0;
    const draftItems = state.model?.items.filter((item) => item.content_status !== "approved") || [];
    const hiddenDrafts = draftItems.length;
    const approved = scheduled - hiddenDrafts;
    const pendingSpeakerSessions = state.model?.items.filter((item) =>
      (item.participants || []).some((participant) => participant.recipient_state === "invited")
    ) || [];
    byId("publish-dialog-summary").textContent =
      hiddenDrafts
        ? `${approved} of ${scheduled} scheduled ${scheduled === 1 ? "session is" : "sessions are"} approved and will become publicly visible.`
        : `${scheduled} scheduled session${scheduled === 1 ? "" : "s"} will become publicly visible.`;
    byId("publish-dialog-draft-note").hidden = hiddenDrafts === 0;
    byId("publish-dialog-draft-note").textContent = hiddenDrafts
      ? `${hiddenDrafts} session${hiddenDrafts === 1 ? " has" : "s have"} draft content. Publish only keeps ${hiddenDrafts === 1 ? "it" : "them"} hidden.`
      : "";
    const draftList = byId("publish-dialog-draft-list");
    draftList.hidden = hiddenDrafts === 0;
    draftList.replaceChildren(...draftItems.map((item) => make("li", item.title)));
    byId("approve-and-publish").hidden = hiddenDrafts === 0;
    const publishOnly = byId("publish-only");
    publishOnly.textContent = hiddenDrafts ? "Publish only" : "Publish agenda";
    publishOnly.classList.toggle("secondary", hiddenDrafts > 0);
    // Publish only is first in form order, so Enter takes the privacy-safe
    // path when drafts exist rather than approving content implicitly.
    const inviteWarning = byId("publish-dialog-invite-warning");
    inviteWarning.hidden = pendingSpeakerSessions.length === 0;
    inviteWarning.querySelector("ul").replaceChildren(
      ...pendingSpeakerSessions.map((item) => make("li", item.title)),
    );
    byId("publish-dialog").showModal();
  }
  byId("publish").addEventListener("click", openPublishDialog);
  byId("cancel-publish").addEventListener("click", () => byId("publish-dialog").close());
  byId("publish-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("publish");
    const approveDrafts = event.submitter?.value === "approve-and-publish";
    byId("publish-dialog").close();
    button.disabled = true;
    status("Publishing agenda…");
    try {
      const payload = JSON.stringify({
        revision_id: state.model.revision.id,
        version: state.model.revision.version,
        approve_draft_sessions: approveDrafts,
      });
      if (!state.publishMutation || state.publishMutation.payload !== payload) {
        state.publishMutation = { payload, key: key() };
      }
      const result = await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/publish`,
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": state.publishMutation.key,
          },
          body: payload,
        },
      );
      state.publishMutation = null;
      await load(false);
      status(result.hidden_session_count
        ? `Agenda published. ${result.hidden_session_count} session${result.hidden_session_count === 1 ? " remains" : "s remain"} hidden until content is approved.`
        : result.newly_approved_session_ids?.length
          ? "Agenda published. All scheduled sessions are publicly visible."
          : "Agenda published. Calendar updates were queued for speakers.");
    } catch (error) {
      if (error.status === 409) {
        state.publishMutation = null;
        await load(false);
        openPublishDialog();
      }
      status(
        error.status === 409
          ? "This draft changed before publication. Refresh and review it again."
          : "The agenda could not be published.",
        true,
      );
    } finally {
      button.disabled = state.model.items.length === 0;
    }
  });
  byId("editor-form").addEventListener("input", (event) => {
    if (["start_at", "end_at", "room_id", "track_id"].includes(event.target.name)) schedulePreview();
  });
  byId("editor-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    const before = structuredClone(state.model);
    byId("save-item").disabled = true;
    try {
      const candidate = formCandidate();
      if (candidate.end_at_ms <= candidate.start_at_ms) {
        byId("preview-state").textContent =
          "The session must end after it starts.";
        byId("preview-state").className = "preview-state error";
        return;
      }
      const check = await preview(candidate);
      if (!check.valid) {
        showConflicts(check.conflicts, true);
        return;
      }
      await saveContent(state.selected);
      await saveLabels(state.selected);
      optimistic(candidate);
      byId("editor").close();
      render();
      await save(candidate);
      status("Session scheduled successfully.");
    } catch (error) {
      try { await load(false); } catch (_) { state.model = before; render(); }
      status(
        error.status === 409
          ? "The schedule or content changed elsewhere. Latest saved data was reloaded."
          : "The complete edit could not be saved. Latest saved data was reloaded.",
        true,
      );
    } finally {
      byId("save-item").disabled = false;
    }
  });
  byId("unschedule-item").addEventListener("click", async () => {
    const item = state.selected;
    if (!item?.id || !window.confirm(`Move “${item.title}” back to unscheduled sessions?`)) return;
    const button = byId("unschedule-item");
    button.disabled = true;
    try {
      await api(
        `/api/v1/admin/events/${encodeURIComponent(eventId)}/agenda/items/${encodeURIComponent(item.id)}?version=${encodeURIComponent(item.version)}`,
        {
          method: "DELETE",
          headers: {
            "content-type": "application/json",
            "x-csrf-token": state.csrf,
            "idempotency-key": key(),
          },
        },
      );
      byId("editor").close();
      await load(false);
      status("Session moved back to unscheduled sessions.");
    } catch (error) {
      status(
        error.status === 409
          ? "The schedule changed elsewhere. Refresh and try again."
          : "The session could not be unscheduled.",
        true,
      );
    } finally {
      button.disabled = false;
    }
  });
  ["close-editor", "cancel-editor"].forEach((id) =>
    byId(id).addEventListener("click", () => byId("editor").close())
  );
  async function initialize() {
    if (!eventId) {
      status(
        "Invalid agenda link. Return to Events and select an event.",
        true,
      );
      return;
    }
    try {
      const session = await api("/api/v1/auth/session");
      state.csrf = session.csrf_token;
      try {
        await load();
      } catch (error) {
        if (error.status === 404) showSetup();
        else throw error;
      }
    } catch (error) {
      status(
        error.status === 401
          ? "Sign in as an organization administrator to edit this agenda."
          : "Agenda unavailable. Try again.",
        true,
      );
      byId("agenda").setAttribute("aria-busy", "false");
    }
  }
  initialize();
})();

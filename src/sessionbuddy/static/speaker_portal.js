(() => {
  "use strict";
  const state = { csrf: "", portal: null, assets: [] };
  const uploadRules = {
    headshot: { max: 5 * 1024 * 1024, types: new Set(["image/jpeg", "image/png", "image/webp"]) },
    slides: { max: 50 * 1024 * 1024, types: new Set(["application/pdf", "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.oasis.opendocument.presentation"]) },
    supporting_document: { max: 20 * 1024 * 1024, types: new Set(["application/pdf"]) }
  };
  const byId = (id) => document.getElementById(id);
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function idempotencyKey() {
    if (typeof crypto.randomUUID === "function") return `${crypto.randomUUID()}-${crypto.randomUUID()}`;
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  }

  function setStatus(message, kind = "") {
    const status = byId("status");
    status.textContent = message;
    status.className = `status${kind ? ` ${kind}` : ""}`;
  }

  function recordTelemetry(started, response) {
    const navigation = performance.getEntriesByType("navigation")[0];
    const width = innerWidth;
    window.__sessionbuddyTelemetryDraft = {
      schema_version: 1, page_template: "/speaker",
      navigation_type: navigation?.type || "unknown",
      device_class: width < 640 ? "mobile" : width < 1024 ? "tablet" : "desktop",
      sampled: false, lcp_ms: null, inp_ms: null, cls: null,
      ttfb_ms: navigation?.responseStart ?? null, fcp_ms: null,
      route_transition_ms: null, critical_api_ms: Math.max(0, performance.now() - started),
      api_request_id: response.headers.get("x-request-id")
    };
  }

  async function api(path, options = {}, behavior = {}) {
    const started = performance.now();
    return window.SessionBuddyApi.request(path, options, {
      ...behavior,
      onResponse: (response) => {
        recordTelemetry(started, response);
        behavior.onResponse?.(response);
      }
    });
  }

  function formatDate(value, timezone) {
    if (!value) return "No due date";
    try {
      return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short", timeZone: timezone
      }).format(new Date(value));
    } catch (_) { return "Date unavailable"; }
  }

  function eventTimeLabel(value, timezone) {
    return `${formatDate(value, timezone)} · Event time (${timezone})`;
  }

  function safeMessageLink(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
        ? url
        : null;
    } catch (_) { return null; }
  }

  function taskDestination(task) {
    // Only same-page destinations are accepted; arbitrary API-provided URLs are never used.
    if (task.destination_path === "#profile") return "/account";
    if (task.destination_path === "#assets") return "#submissions";
    if (["#tasks", "#submissions"].includes(task.destination_path)) {
      return task.destination_path;
    }
    if (["profile", "biography"].includes(task.task_type)) return "/account";
    if (task.task_type === "headshot") return "/account";
    if (["slides", "supporting_document"].includes(task.task_type)) return "#submissions";
    return "#tasks";
  }

  function portalPath(eventId = state.portal?.event?.id) {
    return eventId
      ? `/api/v1/speaker/portal?event_id=${encodeURIComponent(eventId)}`
      : "/api/v1/speaker/portal";
  }

  function customTaskForm(task) {
    const form = make("form", undefined, "task-form");
    form.dataset.taskId = task.id;
    for (const field of task.form_fields || []) {
      const label = make("label", field.label);
      let input;
      if (field.type === "textarea") {
        input = document.createElement("textarea");
        input.maxLength = 4000;
      }
      else if (field.type === "select") {
        input = document.createElement("select");
        input.append(new Option("Choose…", ""));
        (field.choices || []).forEach((choice) => input.add(new Option(choice, choice)));
      } else {
        input = document.createElement("input");
        input.type = field.type === "checkbox" ? "checkbox" : field.type === "url" ? "url" : "text";
        if (field.type === "checkbox") label.classList.add("check-label");
      }
      input.name = field.key;
      input.required = Boolean(field.required);
      label.append(input);
      form.append(label);
    }
    const submit = make("button", task.form_fields?.length ? "Send response" : "Mark complete");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      submit.disabled = true;
      const values = {};
      for (const field of task.form_fields || []) {
        const input = form.elements.namedItem(field.key);
        values[field.key] = field.type === "checkbox" ? Boolean(input.checked) : input.value;
      }
      try {
        await api(`/api/v1/speaker/tasks/${encodeURIComponent(task.id)}/response`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
          body: JSON.stringify({ answers: values, version: task.version })
        });
        const portal = await api(portalPath());
        renderPortal(portal);
        announceOnboardingChange();
        setStatus("Task completed.", "success");
      } catch (error) {
        setStatus(error.status === 409 ? "This task changed. Reload the portal and try again." : error.message, "error");
        submit.disabled = false;
      }
    });
    return form;
  }

  function renderTasks(tasks, timezone) {
    const list = byId("task-list");
    list.replaceChildren();
    const outstanding = tasks.filter((task) => !["completed", "waived"].includes(task.state));
    byId("task-count").textContent = String(outstanding.length);
    if (!outstanding.length) list.append(make("li", "No actions due.", "empty"));
    outstanding.forEach((task) => {
      const item = make("li", undefined, "item-card");
      const heading = make("h3", task.title);
      const help = task.help_text ? make("p", task.help_text) : null;
      const meta = make("p", undefined, "item-meta");
      const overdue = task.due_at_ms !== null && task.due_at_ms < Date.now();
      const dueSoon = task.due_at_ms !== null && task.due_at_ms >= Date.now()
        && task.due_at_ms - Date.now() <= 7 * 24 * 60 * 60 * 1000;
      const stateText = overdue ? "Overdue" : dueSoon ? "Due soon" : "Outstanding";
      meta.append(
        make("span", stateText, `state-badge${overdue ? " overdue" : ""}`),
        make("span", `Due ${eventTimeLabel(task.due_at_ms, timezone)}`)
      );
      item.append(heading);
      if (help) item.append(help);
      item.append(meta);
      if (task.task_type === "custom") {
        item.append(customTaskForm(task));
      } else {
        const action = make("a", task.action_label || "Complete task", "task-link");
        action.href = taskDestination(task);
        item.append(action);
      }
      list.append(item);
    });
    const completed = tasks.filter((task) => ["completed", "waived"].includes(task.state));
    if (completed.length) {
      list.append(make("li", `Completed (${completed.length})`, "task-history-heading"));
      completed.forEach((task) => {
        const item = make("li", undefined, "item-card task-complete");
        item.append(
          make("h3", task.title),
          make("p", task.state === "waived" ? "Waived" : "Completed", "state-badge success"),
        );
        list.append(item);
      });
    }
  }

  function renderSubmissions(submissions) {
    const list = byId("submission-list");
    list.replaceChildren();
    if (!submissions.length) {
      list.append(make("li", "No proposals are connected to this account yet.", "empty"));
      return;
    }
    submissions.forEach((submission) => {
      const item = make("li", undefined, "item-card");
      const statusLabel = submission.status.replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
      item.append(
        make("h3", submission.proposal_title),
        make("p", statusLabel, `state-badge${submission.status === "accepted" ? " success" : ""}`)
      );
      if (submission.editable) {
        const edit = make("button", "Edit proposal", "secondary");
        edit.type = "button";
        edit.addEventListener("click", () => {
          document.querySelectorAll(".proposal-editor").forEach((editor) => editor.remove());
          const editor = make("form", undefined, "proposal-editor");
          const titleLabel = make("label", "Proposal title");
          const title = document.createElement("input");
          title.name = "proposal_title";
          title.maxLength = 300;
          title.required = true;
          title.value = submission.proposal_title;
          titleLabel.append(title);
          const abstractLabel = make("label", "Proposal abstract");
          const abstract = document.createElement("textarea");
          abstract.name = "proposal_abstract";
          abstract.rows = 7;
          abstract.maxLength = 5000;
          abstract.required = true;
          abstract.value = submission.proposal_abstract;
          abstractLabel.append(abstract);
          const actions = make("div", undefined, "actions");
          const cancel = make("button", "Cancel", "secondary");
          cancel.type = "button";
          cancel.addEventListener("click", () => editor.remove());
          const save = make("button", "Save changes");
          save.type = "submit";
          actions.append(cancel, save);
          editor.append(titleLabel, abstractLabel, actions);
          editor.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (!editor.reportValidity()) return;
            save.disabled = true;
            save.textContent = "Saving…";
            try {
              const updated = await api(`/api/v1/forms/${encodeURIComponent(submission.form_slug)}/submissions/${encodeURIComponent(submission.id)}`, {
                method: "PATCH",
                headers: {
                  "content-type": "application/json",
                  "x-csrf-token": state.csrf,
                  "idempotency-key": idempotencyKey()
                },
                body: JSON.stringify({
                  speaker_name: submission.speaker_name,
                  speaker_email: submission.speaker_email,
                  proposal_title: title.value.trim(),
                  proposal_abstract: abstract.value.trim(),
                  answers: {
                    ...submission.answers,
                    proposal_title: title.value.trim(),
                    proposal_abstract: abstract.value.trim()
                  },
                  version: submission.version
                })
              });
              Object.assign(submission, updated);
              renderSubmissions(submissions);
              setStatus("Proposal changes saved.", "success");
            } catch (error) {
              setStatus(error.status === 409
                ? "This proposal changed elsewhere. Reload the portal and try again."
                : window.SessionBuddyApi.message(error, "The proposal could not be saved."), "error");
              save.disabled = false;
              save.textContent = "Save changes";
            }
          });
          item.append(editor);
          title.focus();
        });
        item.append(edit);
        const withdraw = make("button", "Withdraw proposal", "secondary");
        withdraw.type = "button";
        withdraw.addEventListener("click", async () => {
          if (!confirm("Withdraw this proposal? It will become read-only and cannot enter review.")) return;
          withdraw.disabled = true;
          withdraw.textContent = "Withdrawing…";
          try {
            const updated = await api(`/api/v1/forms/${encodeURIComponent(submission.form_slug)}/submissions/${encodeURIComponent(submission.id)}/withdraw`, {
              method: "POST",
              headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
              body: "{}"
            });
            Object.assign(submission, updated);
            renderSubmissions(submissions);
            setStatus("Proposal withdrawn. It is now read-only.", "success");
          } catch (error) {
            setStatus(window.SessionBuddyApi.message(error, "The proposal could not be withdrawn."), "error");
            withdraw.disabled = false;
            withdraw.textContent = "Withdraw proposal";
          }
        });
        item.append(withdraw);
      }
      if (submission.status === "withdrawn") item.append(make("p", "This proposal is withdrawn and read-only.", "help"));
      const files = document.createElement("details");
      files.className = "session-files";
      files.append(make("summary", "Files"));
      const saved = make("ul", undefined, "session-file-list");
      const sessionAssets = state.assets.filter((asset) => asset.submission_id === submission.id);
      if (!sessionAssets.length) saved.append(make("li", "No files uploaded for this session.", "empty"));
      sessionAssets.forEach((asset) => saved.append(make("li", `${asset.kind === "slides" ? "Slides" : "Document"}: ${asset.filename}`)));
      files.append(saved);
      if (submission.status !== "withdrawn") {
        const uploads = make("div", undefined, "session-upload-grid");
        uploads.append(
          createUploadForm("slides", submission.id),
          createUploadForm("supporting_document", submission.id)
        );
        files.append(uploads);
      }
      item.append(files);
      list.append(item);
    });
  }

  function createUploadForm(kind, submissionId) {
    const slides = kind === "slides";
    const form = make("form", undefined, "session-upload-card");
    form.dataset.kind = kind;
    form.dataset.submissionId = submissionId;
    const title = slides ? "Slides" : "Supporting document";
    const fileLabel = make("label", `Choose ${slides ? "slides" : "document"}`);
    const file = document.createElement("input");
    file.name = "file"; file.type = "file"; file.required = true;
    file.accept = slides
      ? "application/pdf,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.oasis.opendocument.presentation"
      : "application/pdf";
    fileLabel.append(file);
    const commentLabel = make("label", "What changed?");
    const comment = document.createElement("textarea");
    comment.name = "version_comment"; comment.rows = 2; comment.minLength = 1; comment.maxLength = 1000; comment.required = true;
    commentLabel.append(comment);
    const button = make("button", `Upload ${slides ? "slides" : "document"}`);
    button.type = "submit";
    const progress = document.createElement("progress");
    progress.max = 100; progress.value = 0; progress.hidden = true; progress.setAttribute("aria-label", `${title} upload progress`);
    const status = make("p", "", "upload-status"); status.setAttribute("role", "status");
    form.append(make("h4", title), fileLabel, commentLabel, button, progress, status);
    bindUploadForm(form);
    return form;
  }

  function renderPortal(portal) {
    state.portal = portal;
    const event = portal.event;
    byId("welcome-name").textContent = portal.profile.display_name || "speaker";
    const publicProfile = byId("public-profile-link");
    publicProfile.hidden = !portal.public_profile_url;
    if (portal.public_profile_url) publicProfile.href = portal.public_profile_url;
    const availableEvents = portal.events || [event];
    const activeList = byId("active-event-list");
    const pastList = byId("past-event-list");
    activeList.replaceChildren();
    pastList.replaceChildren();
    byId("event-count").textContent = String(availableEvents.length);
    const pastEvents = availableEvents.filter((available) => available.ends_at_ms < Date.now());
    byId("past-event-count").textContent = String(pastEvents.length);
    availableEvents.forEach((available) => {
      const option = make("button", undefined, "event-index__item");
      option.type = "button";
      option.dataset.eventId = available.id;
      option.classList.toggle("is-current", available.id === event.id);
      if (available.id === event.id) option.setAttribute("aria-current", "page");
      const copy = make("span");
      copy.append(make("strong", available.name), make("small", eventTimeLabel(available.starts_at_ms, available.time_zone)));
      option.append(copy, make("span", available.id === event.id ? "Open" : "View", "event-index__state"));
      option.addEventListener("click", () => selectEvent(available.id));
      (available.ends_at_ms < Date.now() ? pastList : activeList).append(option);
    });
    if (!activeList.children.length) activeList.append(make("p", "No upcoming events.", "empty"));
    if (!pastList.children.length) pastList.append(make("p", "No past events yet.", "empty"));
    const eventDates = `${formatDate(event.starts_at_ms, event.time_zone)}–${formatDate(event.ends_at_ms, event.time_zone)}`;
    byId("event-summary").textContent = `${event.name} · ${eventDates} · Event time (${event.time_zone})`;
    byId("task-event-label").textContent = event.name;
    byId("session-event-label").textContent = event.name;
    byId("notification-event-label").textContent = `${event.name} · Event time (${event.time_zone})`;
    const tasks = portal.tasks || [];
    renderTasks(tasks, event.time_zone);
    renderSubmissions(portal.submissions || []);
    renderNotifications(portal.notifications || [], event.time_zone);
    byId("auth-state").hidden = true;
    byId("portal").hidden = false;
  }

  function renderNotifications(notifications, timezone) {
    const list = byId("notification-list");
    list.replaceChildren();
    byId("notification-count").textContent = String(notifications.length);
    if (!notifications.length) {
      list.append(make("li", "No updates from this event yet.", "empty"));
      return;
    }
    notifications.forEach((notification) => {
      const item = document.createElement("li");
      const detail = document.createElement("details");
      const summary = document.createElement("summary");
      summary.append(
        make("strong", notification.subject),
        make("time", `Delivered ${eventTimeLabel(notification.delivered_at_ms, timezone)}`)
      );
      const body = make("p", notification.body_text || "Message content is unavailable.", "help");
      detail.append(summary, body);
      const safeLinks = (notification.links || []).map(safeMessageLink).filter(Boolean);
      if (safeLinks.length) {
        const links = document.createElement("ul");
        links.className = "notification-links";
        safeLinks.forEach((url) => {
          const row = document.createElement("li");
          const link = make("a", url.href);
          link.href = url.href;
          link.target = "_blank";
          link.rel = "noopener";
          row.append(link);
          links.append(row);
        });
        detail.append(links);
      }
      item.append(detail);
      list.append(item);
    });
  }

  function announceOnboardingChange() {
    const eventId = state.portal?.event?.id;
    if (!eventId || !("BroadcastChannel" in window)) return;
    const channel = new BroadcastChannel(`sessionbuddy:onboarding:${eventId}`);
    channel.postMessage({ type: "snapshot-invalidated" });
    channel.close();
  }

  async function load() {
    setStatus("Checking your secure session…");
    try {
      const session = await api("/api/v1/session");
      state.csrf = session.csrf_token;
      await loadEvent();
      setStatus("Speaker details are ready.", "success");
    } catch (error) {
      if (error.status === 401 || error.status === 404) {
        byId("portal").hidden = true;
        byId("auth-state").hidden = false;
        setStatus("Speaker access is required to view this portal.", "error");
      } else {
        setStatus("We couldn’t load your portal. Try again in a moment.", "error");
      }
    }
  }

  async function loadEvent(eventId = "") {
    state.assets = [];
    const portal = await api(portalPath(eventId));
    renderPortal(portal);
    await Promise.all([loadAssets(), loadResources()]);
  }

  async function selectEvent(eventId) {
    if (eventId === state.portal?.event?.id) return;
    setStatus("Loading event…");
    try {
      await loadEvent(eventId);
      setStatus("Speaker details are ready.", "success");
    } catch (error) {
      setStatus(window.SessionBuddyApi.message(error, "This event could not be loaded."), "error");
    }
  }

  byId("speaker-sign-in").addEventListener("click", () => {
    location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`);
  });

  function safeUploadUrl(value) {
    try {
      const url = new URL(value, location.origin);
      const trustedR2 = url.protocol === "https:" && url.hostname.endsWith(".r2.cloudflarestorage.com");
      return url.origin === location.origin || trustedR2 ? url : null;
    } catch (_) { return null; }
  }

  function safeUploadHeaders(headers, contentType) {
    const safe = { "content-type": contentType };
    Object.entries(headers || {}).forEach(([name, value]) => {
      const lower = name.toLowerCase();
      if (["content-type", "x-amz-checksum-sha256"].includes(lower)) safe[lower] = String(value);
    });
    return safe;
  }

  function uploadFile(url, method, headers, file, progress) {
    return new Promise((resolve, reject) => {
      if (method !== "PUT") { reject(new Error("Upload authorization used an unsupported method.")); return; }
      const request = new XMLHttpRequest();
      request.open("PUT", url.toString());
      request.withCredentials = false;
      Object.entries(headers).forEach(([name, value]) => request.setRequestHeader(name, value));
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) progress.value = Math.round((event.loaded / event.total) * 100);
      });
      request.addEventListener("load", () => request.status >= 200 && request.status < 300
        ? resolve() : reject(new Error(`Storage upload failed (${request.status}).`)));
      request.addEventListener("error", () => reject(new Error("Storage upload could not be reached.")));
      request.send(file);
    });
  }

  async function checksum(file) {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  }

  function validateFile(kind, file) {
    const rule = uploadRules[kind];
    if (!file) return "Choose a file to upload.";
    if (!rule.types.has(file.type)) return "This file type is not allowed.";
    if (file.size <= 0 || file.size > rule.max) return `File must be larger than zero and no more than ${Math.round(rule.max / 1024 / 1024)} MB.`;
    return null;
  }

  function taskForKind(kind) {
    return state.portal?.tasks?.find((task) => task.task_type === kind && task.state === "open")?.id || null;
  }

  function safeEmbedUrl(value) {
    try {
      const url = new URL(value);
      const allowed = new Set(["www.youtube.com", "youtube.com", "www.youtube-nocookie.com", "player.vimeo.com", "docs.google.com", "drive.google.com", "calendar.google.com"]);
      return url.protocol === "https:" && allowed.has(url.hostname) ? url.toString() : null;
    } catch (_) { return null; }
  }

  async function loadResources() {
    const container = byId("resource-list");
    try {
      const result = await api("/api/v1/speaker/resources");
      const resources = result.data.filter((resource) => resource.event_id === state.portal?.event?.id);
      byId("resource-count").textContent = String(resources.length);
      if (!resources.length) {
        container.replaceChildren(make("p", "No resources have been published yet.", "empty"));
        return;
      }
      const cards = resources.map((resource) => {
        const details = make("details", undefined, "resource-card");
        details.append(make("summary", resource.title));
        if (resource.summary) details.append(make("p", resource.summary, "help"));
        if (resource.body_text) details.append(make("p", resource.body_text, "resource-card__body"));
        const embed = safeEmbedUrl(resource.embed_url);
        if (embed) {
          const frame = document.createElement("iframe");
          frame.className = "resource-embed";
          frame.src = embed;
          frame.title = resource.title;
          frame.loading = "lazy";
          frame.referrerPolicy = "no-referrer";
          frame.sandbox = "allow-scripts allow-same-origin allow-popups";
          details.append(frame);
        }
        return details;
      });
      container.replaceChildren(...cards);
    } catch (_) {
      container.replaceChildren(make("p", "Resources could not be loaded.", "empty"));
    }
  }

  async function loadAssets() {
    try {
      const eventId = state.portal?.event?.id;
      if (!eventId) throw new Error("Speaker event is unavailable.");
      const result = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/assets`);
      const assets = result.data || [];
      state.assets = assets;
      renderSubmissions(state.portal?.submissions || []);
    } catch (_) {
      state.assets = [];
      renderSubmissions(state.portal?.submissions || []);
      setStatus("Session files could not be loaded. Uploads are temporarily unavailable.", "error");
    }
  }

  function bindUploadForm(form) {
    let pendingCompletion = null;
    form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const kind = form.dataset.kind;
    const file = form.elements.file.files[0];
    const status = form.querySelector(".upload-status");
    const progress = form.querySelector("progress");
    const button = form.querySelector("button[type=submit]");
    const versionComment = form.elements.version_comment.value.trim();
    const validation = validateFile(kind, file);
    if (validation) { status.textContent = validation; status.classList.add("error"); return; }
    if (!versionComment) {
      form.elements.version_comment.setCustomValidity("Describe what changed in this version.");
      form.elements.version_comment.reportValidity();
      return;
    }
    form.elements.version_comment.setCustomValidity("");
    button.disabled = true; progress.hidden = false; progress.value = 0;
    status.classList.remove("error"); status.textContent = "Checking file integrity…";
    try {
      const eventId = state.portal?.event?.id;
      if (!eventId) throw new Error("Speaker event is unavailable.");
      const uploadRequest = {
        kind, submission_id: form.dataset.submissionId || null,
        task_id: taskForKind(kind), filename: file.name, content_type: file.type,
        byte_size: file.size, checksum_sha256: await checksum(file),
        version_comment: versionComment
      };
      const uploadFingerprint = JSON.stringify(uploadRequest);
      if (!pendingCompletion
          || pendingCompletion.fingerprint !== uploadFingerprint
          || pendingCompletion.expiresAtMs <= Date.now()) {
        pendingCompletion = null;
        const authorization = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/upload-authorizations`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() },
          body: JSON.stringify(uploadRequest)
        });
        const uploadUrl = safeUploadUrl(authorization.upload_url);
        if (!uploadUrl || authorization.expires_at_ms <= Date.now()) throw new Error("Upload authorization is invalid or expired.");
        status.textContent = "Uploading…";
        await uploadFile(uploadUrl, authorization.method, safeUploadHeaders(authorization.headers, file.type), file, progress);
        pendingCompletion = {
          fingerprint: uploadFingerprint,
          intentId: authorization.intent_id,
          expiresAtMs: authorization.expires_at_ms
        };
        status.textContent = "Upload received. Starting safety checks…";
      } else {
        status.textContent = "Upload received. Retrying safety checks…";
      }
      const completion = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/upload-intents/${encodeURIComponent(pendingCompletion.intentId)}/complete`, {
        method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() }, body: "{}"
      });
      pendingCompletion = null;
      progress.value = 100;
      if (completion.state === "rejected") {
        status.textContent = "This file was rejected by the safety scan. It was not made available. Choose a different file.";
        status.classList.add("error");
      } else if (completion.state === "clean") {
        status.textContent = "Upload checked and ready.";
      } else {
        status.textContent = "Upload complete. The file is being checked before it becomes current.";
      }
      form.elements.file.value = "";
      form.elements.version_comment.value = "";
      window.SessionBuddyApi.refreshCharacterCounters(form);
      await loadAssets();
      const portal = await api(portalPath()); renderPortal(portal);
      announceOnboardingChange();
    } catch (error) {
      status.textContent = pendingCompletion
        ? `File received. Safety checks are temporarily unavailable, so this file is not public or current yet. Press “${button.textContent.trim()}” again to retry. You do not need to choose or upload the file again.`
        : window.SessionBuddyApi.message(error, "Upload failed. Choose the file and try again.");
      status.classList.add("error");
    } finally { button.disabled = false; }
    });
  }

  load();
})();

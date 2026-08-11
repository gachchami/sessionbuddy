(() => {
  "use strict";
  const state = { csrf: "", portal: null, version: null };
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

  function taskDestination(task) {
    // Only same-page destinations are accepted; arbitrary API-provided URLs are never used.
    if (["#profile", "#tasks", "#submissions", "#assets"].includes(task.destination_path)) {
      return task.destination_path;
    }
    if (["profile", "biography"].includes(task.task_type)) return "#profile";
    if (["headshot", "slides", "supporting_document"].includes(task.task_type)) return "#assets";
    return "#tasks";
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
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` },
          body: JSON.stringify({ answers: values, version: task.version })
        });
        const portal = await api("/api/v1/speaker/portal");
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
    if (!outstanding.length) list.append(make("li", "You’re all caught up. There are no outstanding tasks.", "empty"));
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
        make("span", `Due ${formatDate(task.due_at_ms, timezone)} · ${timezone}`)
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
      item.append(
        make("h3", submission.proposal_title),
        make("p", submission.status.replaceAll("_", " "), `state-badge${submission.status === "accepted" ? " success" : ""}`)
      );
      if (submission.editable && submission.form_slug) {
        const edit = make("a", "Edit proposal", "button secondary");
        const eventKey = state.portal.event.id.replace(/[^a-z0-9]/gi, "").slice(0, 6).toLowerCase();
        edit.href = `/cfp/${eventKey}/${encodeURIComponent(submission.form_slug)}?submission_id=${encodeURIComponent(submission.id)}`;
        item.append(edit);
      }
      list.append(item);
    });
  }

  function fillSubmissionChoices(submissions) {
    ["slides-submission", "document-submission"].forEach((id) => {
      const select = byId(id);
      const selected = select.value;
      select.replaceChildren(new Option("Choose a session", ""));
      submissions.forEach((submission) => select.add(new Option(submission.proposal_title, submission.id)));
      if ([...select.options].some((option) => option.value === selected)) select.value = selected;
    });
  }

  function fillProfile(profile) {
    const form = byId("profile-form");
    ["display_name", "job_title", "company", "location", "biography"].forEach((name) => {
      form.elements[name].value = profile[name] || "";
    });
    const links = profile.links || [];
    form.elements.linkedin.value = links.find((value) => { try { return new URL(value).hostname.toLowerCase().endsWith("linkedin.com"); } catch (_) { return false; } }) || "";
    const remaining = links.filter((value) => value !== form.elements.linkedin.value);
    const socialHosts = ["twitter.com", "x.com", "bsky.app", "mastodon.social", "threads.net", "instagram.com", "facebook.com"];
    form.elements.social_link.value = remaining.find((value) => {
      try {
        const host = new URL(value).hostname.toLowerCase().replace(/^www\./, "");
        return socialHosts.some((candidate) => host === candidate || host.endsWith(`.${candidate}`));
      } catch (_) { return false; }
    }) || "";
    form.elements.website.value = remaining.find((value) => value !== form.elements.social_link.value) || "";
    state.version = profile.version;
    updateBiographyCount();
    clearErrors();
    byId("stale-warning").hidden = true;
  }

  function renderPortal(portal) {
    state.portal = portal;
    const event = portal.event;
    byId("portal-title").textContent = `Welcome, ${portal.profile.display_name || "speaker"}`;
    const eventDates = `${formatDate(event.starts_at_ms, event.time_zone)}–${formatDate(event.ends_at_ms, event.time_zone)}`;
    byId("event-summary").textContent = `${event.name} · ${eventDates}`;
    byId("event-timezone").textContent = `All deadlines shown in ${event.time_zone}.`;
    const tasks = portal.tasks || [];
    const completed = portal.completed_tasks;
    const total = portal.total_tasks;
    byId("progress-count").textContent = `${completed} of ${total} complete`;
    byId("progress").max = Math.max(total, 1);
    byId("progress").value = completed;
    const upcoming = tasks.filter((task) => task.state === "open" && task.due_at_ms !== null)
      .sort((left, right) => left.due_at_ms - right.due_at_ms)[0];
    byId("next-deadline").textContent = upcoming
      ? `Next: ${upcoming.title}, ${formatDate(upcoming.due_at_ms, event.time_zone)} · ${event.time_zone}`
      : "No upcoming deadline.";
    renderTasks(tasks, event.time_zone);
    renderSubmissions(portal.submissions || []);
    fillSubmissionChoices(portal.submissions || []);
    fillProfile(portal.profile);
    byId("auth-state").hidden = true;
    byId("portal").hidden = false;
  }

  function announceOnboardingChange() {
    const eventId = state.portal?.event?.id;
    if (!eventId || !("BroadcastChannel" in window)) return;
    const channel = new BroadcastChannel(`sessionbuddy:onboarding:${eventId}`);
    channel.postMessage({ type: "snapshot-invalidated" });
    channel.close();
  }

  function clearErrors() {
    byId("error-summary").hidden = true;
    byId("error-summary").querySelector("ul").replaceChildren();
    byId("profile-form").querySelectorAll("[aria-invalid]").forEach((field) => field.removeAttribute("aria-invalid"));
    byId("profile-form").querySelectorAll(".field-error").forEach((node) => { node.textContent = ""; });
  }

  function showErrors(errors) {
    clearErrors();
    const summary = byId("error-summary");
    const list = summary.querySelector("ul");
    errors.forEach(({ field, message }) => {
      const input = byId(field.replaceAll("_", "-"));
      if (!input) return;
      input.setAttribute("aria-invalid", "true");
      const error = byId(`${field.replaceAll("_", "-")}-error`);
      if (error) error.textContent = message;
      const link = make("a", message); link.href = `#${input.id}`;
      const item = document.createElement("li"); item.append(link); list.append(item);
    });
    summary.hidden = false;
    summary.focus();
  }

  function clientErrors(values) {
    const errors = [];
    if (!values.display_name.trim()) errors.push({ field: "display_name", message: "Enter your name." });
    if (!values.biography.trim()) errors.push({ field: "biography", message: "Enter your biography." });
    for (const field of ["website", "linkedin", "social_link"]) {
      if (!values[field]) continue;
      try {
        const url = new URL(values[field]);
        if (!["http:", "https:"].includes(url.protocol)) throw new Error();
      } catch (_) { errors.push({ field, message: "Enter a complete HTTP or HTTPS address." }); }
    }
    return errors;
  }

  async function load() {
    setStatus("Checking your secure session…");
    try {
      const session = await api("/api/v1/session");
      state.csrf = session.csrf_token;
      const portal = await api("/api/v1/speaker/portal");
      renderPortal(portal);
      await Promise.all([loadAssets(), loadResources()]);
      setStatus("Your speaker portal is ready.", "success");
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

  byId("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("save-profile");
    const values = Object.fromEntries(new FormData(event.currentTarget));
    const errors = clientErrors(values);
    values.links = [values.website, values.linkedin, values.social_link].filter(Boolean);
    delete values.website;
    delete values.linkedin;
    delete values.social_link;
    if (errors.length) { showErrors(errors); return; }
    clearErrors();
    button.disabled = true;
    button.textContent = "Saving…";
    try {
      const profile = await api("/api/v1/speaker/profile", {
        method: "PATCH",
        headers: {
          "content-type": "application/json", "x-csrf-token": state.csrf,
          "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}`
        },
        body: JSON.stringify({ ...values, version: state.version })
      });
      fillProfile(profile);
      const portal = await api("/api/v1/speaker/portal");
      renderPortal(portal);
      announceOnboardingChange();
      setStatus("Profile saved. Related onboarding tasks are up to date.", "success");
    } catch (error) {
      if (error.status === 409 || error.status === 412) {
        byId("stale-warning").hidden = false;
        byId("stale-warning").focus?.();
        setStatus("Your profile was not saved because a newer version exists.", "error");
      } else if (error.status === 401) {
        byId("portal").hidden = true; byId("auth-state").hidden = false;
        setStatus("Your session ended before the profile could be saved.", "error");
      } else if (error.status === 422 && error.details.length) {
        showErrors(error.details.map((detail) => ({ field: detail.field || detail.loc?.at(-1), message: detail.message || "Check this field." })));
        setStatus("Your profile has errors. Nothing was saved.", "error");
      } else {
        setStatus("We couldn’t save your profile. Your entries remain on this page.", "error");
      }
    } finally {
      button.disabled = false;
      button.textContent = "Save profile";
    }
  });

  byId("reload-profile").addEventListener("click", async () => {
    try {
      const portal = await api("/api/v1/speaker/portal");
      renderPortal(portal);
      setStatus("Latest profile loaded. Review it before saving.");
    } catch (_) { setStatus("We couldn’t reload the latest profile.", "error"); }
  });

  byId("speaker-sign-in").addEventListener("click", () => {
    location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`);
  });

  function updateBiographyCount() {
    const length = byId("biography").value.length;
    byId("biography-count").textContent = `${length.toLocaleString()} of 5,000 characters`;
  }
  byId("biography").addEventListener("input", updateBiographyCount);

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
      byId("resource-count").textContent = String(result.data.length);
      if (!result.data.length) {
        container.replaceChildren(make("p", "No resources have been published yet.", "empty"));
        return;
      }
      const cards = result.data.map((resource) => {
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
    const status = byId("asset-status");
    try {
      const eventId = state.portal?.event?.id;
      if (!eventId) throw new Error("Speaker event is unavailable.");
      const result = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/assets`);
      const assets = result.data || [];
      const list = byId("asset-list"); list.replaceChildren();
      if (!assets.length) list.append(make("li", "No clean assets uploaded yet.", "empty"));
      async function downloadVersion(asset, version, button) {
        button.disabled = true;
        try {
          const grant = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/assets/${encodeURIComponent(asset.id)}/versions/${encodeURIComponent(version.id)}/download-grants`, {
            method: "POST",
            headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
            body: "{}"
          });
          let response;
          await api("/api/v1/assets/download", {
            method: "POST",
            headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
            body: JSON.stringify({ token: grant.token })
          }, { expectJson: false, onResponse: (received) => { response = received; } });
          const url = URL.createObjectURL(await response.blob());
          const link = document.createElement("a");
          link.href = url; link.download = version.filename; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          status.textContent = `${version.filename} downloaded.`;
        } catch (error) {
          status.textContent = window.SessionBuddyApi.message(error, "The file could not be downloaded.");
          status.classList.add("error");
        } finally { button.disabled = false; }
      }
      assets.forEach((asset) => {
        const item = document.createElement("li");
        if (asset.kind === "headshot") {
          const preview = document.createElement("img");
          preview.src = `/api/v1/public/events/${encodeURIComponent(eventId)}/speakers/${encodeURIComponent(state.portal.event_speaker_id)}/headshot`;
          preview.alt = "Current headshot"; preview.loading = "lazy"; preview.className = "asset-preview";
          preview.addEventListener("error", () => preview.remove());
          item.append(preview);
        }
        item.append(
          make("strong", asset.filename),
          make("span", `${asset.state.replaceAll("_", " ")} · version ${asset.generation} of ${asset.version_count}`),
          make("p", asset.version_comment, "help"),
        );
        if (asset.versions?.length) {
          const details = document.createElement("details");
          details.append(make("summary", `${asset.versions.length} saved versions`));
          const history = document.createElement("ol");
          asset.versions.forEach((version) => {
            const versionItem = document.createElement("li");
            versionItem.append(
              make("strong", `Version ${version.generation} · ${version.filename}`),
              make("span", `${version.state} · ${new Date(version.uploaded_at_ms).toLocaleString()}`),
              make("p", version.version_comment, "help"),
            );
            const button = make("button", "Download", "secondary");
            button.type = "button";
            button.addEventListener("click", () => downloadVersion(asset, version, button));
            versionItem.append(button);
            history.append(versionItem);
          });
          details.append(history); item.append(details);
        }
        list.append(item);
      });
      status.textContent = `${assets.length} current clean asset${assets.length === 1 ? "" : "s"}.`;
      status.classList.remove("error");
    } catch (_) {
      status.textContent = "Current assets could not be loaded. Uploads are unavailable until this reconnects.";
      status.classList.add("error");
    }
  }

  document.querySelectorAll(".upload-card").forEach((form) => form.addEventListener("submit", async (event) => {
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
      const authorization = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/upload-authorizations`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` },
        body: JSON.stringify({
          kind, submission_id: form.elements.submission_id?.value || null,
          task_id: taskForKind(kind), filename: file.name, content_type: file.type,
          byte_size: file.size, checksum_sha256: await checksum(file),
          version_comment: versionComment
        })
      });
      const uploadUrl = safeUploadUrl(authorization.upload_url);
      if (!uploadUrl || authorization.expires_at_ms <= Date.now()) throw new Error("Upload authorization is invalid or expired.");
      status.textContent = "Uploading…";
      await uploadFile(uploadUrl, authorization.method, safeUploadHeaders(authorization.headers, file.type), file, progress);
      status.textContent = "Upload received. Starting safety checks…";
      const completion = await api(`/api/v1/speaker/events/${encodeURIComponent(eventId)}/upload-intents/${encodeURIComponent(authorization.intent_id)}/complete`, {
        method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": `${crypto.randomUUID()}-${crypto.randomUUID()}` }, body: "{}"
      });
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
      const portal = await api("/api/v1/speaker/portal"); renderPortal(portal);
      announceOnboardingChange();
    } catch (error) {
      status.textContent = window.SessionBuddyApi.message(error, "Upload failed. Choose the file and try again.");
      status.classList.add("error");
    } finally { button.disabled = false; }
  }));
  load();
})();

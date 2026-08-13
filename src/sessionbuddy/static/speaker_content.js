(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/speaker-content$/);
  let eventId = "";
  try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const state = { csrf: "", timeZone: "", taskMutation: null };
  const uploadRules = {
    headshot: { max: 5, types: ["image/jpeg", "image/png", "image/webp"] },
    slides: { max: 50, types: ["application/pdf", "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.oasis.opendocument.presentation"] },
    supporting_document: { max: 20, types: ["application/pdf"] }
  };

  function updateTaskType() {
    const type = byId("task-type").value;
    const rules = uploadRules[type];
    byId("response-options").hidden = Boolean(rules);
    byId("upload-options").hidden = !rules;
    const uploadEnabled = byId("task-form").elements.upload_enabled;
    uploadEnabled.disabled = !rules;
    uploadEnabled.checked = Boolean(rules);
    const purposeNote = byId("task-purpose-note");
    purposeNote.hidden = type !== "headshot";
    if (rules) {
      const max = byId("task-form").elements.max_file_mb;
      max.max = String(rules.max);
      max.value = String(rules.max);
      byId("allowed-file-types").textContent = `Allowed: ${rules.types.join(", ")}. Files remain quarantined until safety checks pass.`;
    }
  }

  function idempotencyKey() {
    const bytes = new Uint8Array(32);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }
  const api = (path, options = {}, behavior = {}) => window.SessionBuddyApi.request(path, options, behavior);
  async function loadEventTimeZone() {
    const event = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
    if (!event?.time_zone) throw new Error("The event time zone could not be loaded.");
    return event.time_zone;
  }
  function partsInTimeZone(value) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: state.timeZone,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23"
    }).formatToParts(new Date(value));
    return Object.fromEntries(parts
      .filter(({ type }) => type !== "literal")
      .map(({ type, value: part }) => [type, Number(part)]));
  }
  function inputMillis(value) {
    if (!value) return null;
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!match) return Number.NaN;
    const [, year, month, day, hour, minute] = match.map(Number);
    const intended = Date.UTC(year, month - 1, day, hour, minute);
    let timestamp = intended;
    for (let attempt = 0; attempt < 4; attempt += 1) {
      const actual = partsInTimeZone(timestamp);
      const actualAsUtc = Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute);
      const adjustment = intended - actualAsUtc;
      timestamp += adjustment;
      if (adjustment === 0) break;
    }
    const actual = partsInTimeZone(timestamp);
    return Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute) === intended
      ? timestamp
      : Number.NaN;
  }
  function eventTime(value) {
    try {
      const formatted = new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium", timeStyle: "short", timeZone: state.timeZone
      }).format(new Date(value));
      return `${formatted} · Event time (${state.timeZone})`;
    } catch (_) { return "Date unavailable"; }
  }
  function slug(value) { return value.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 80); }
  function mutationHeaders() { return { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": idempotencyKey() }; }
  function approvedEmbed(value) {
    if (!value) return true;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password && new Set([
        "www.youtube.com", "youtube.com", "www.youtube-nocookie.com", "player.vimeo.com",
        "docs.google.com", "drive.google.com", "calendar.google.com"
      ]).has(url.hostname.toLowerCase());
    } catch (_) { return false; }
  }

  async function loadResources() {
    const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/resources`);
    byId("resource-count").textContent = body.data.length;
    const nodes = body.data.map((item) => {
      const li = document.createElement("li");
      const title = document.createElement("strong"); title.textContent = item.title;
      const meta = document.createElement("span"); meta.className = "muted"; meta.textContent = `${item.status} · /${item.slug}`;
      li.append(title, meta); return li;
    });
    if (!nodes.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "No resources yet.";
      nodes.push(empty);
    }
    byId("resource-list").replaceChildren(...nodes);
  }
  function fileSize(value) {
    if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }
  async function downloadAsset(asset, version, button) {
    button.disabled = true;
    try {
      const grant = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/assets/${encodeURIComponent(asset.id)}/versions/${encodeURIComponent(version.id)}/download-grants`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
        body: "{}"
      });
      let response;
      await api("/api/v1/assets/download", {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": state.csrf },
        body: JSON.stringify({ token: grant.token })
      }, {
        expectJson: false,
        onResponse: (received) => { response = received; }
      });
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = version.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus(`${version.filename} downloaded.`);
    } catch (error) {
      setStatus(window.SessionBuddyApi.message(error, "The file could not be downloaded."), true);
    } finally { button.disabled = false; }
  }
  async function downloadProfileHeadshot(asset, button) {
    if (button.dataset.busy === "true") return;
    button.dataset.busy = "true";
    const label = button.textContent;
    button.textContent = "Downloading headshot…";
    setStatus(`Preparing ${asset.filename} for download…`);
    try {
      const response = await fetch(asset.direct_download_url, { credentials: "same-origin" });
      if (!response.ok) throw new Error("The headshot could not be downloaded.");
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url; link.download = asset.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus(`${asset.filename} downloaded.`);
    } catch (error) {
      setStatus(error.message || "The headshot could not be downloaded.", true);
    } finally {
      delete button.dataset.busy;
      button.textContent = label;
    }
  }
  async function loadAssets() {
    const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/assets`);
    byId("file-count").textContent = body.data.length;
    const updateExportState = () => {
      const selected = document.querySelectorAll('input[name="export_asset"]:checked').length;
      const button = byId("export-files");
      button.disabled = selected === 0;
      button.textContent = selected ? `Export ${selected} selected` : "Export selected ZIP";
    };
    const nodes = body.data.map((asset) => {
      const item = document.createElement("li");
      item.className = "speaker-file-card";
      const exportable = !asset.id.startsWith("profile-headshot:");
      const select = exportable ? document.createElement("input") : null;
      if (select) {
        select.type = "checkbox"; select.name = "export_asset"; select.value = asset.id;
        select.setAttribute("aria-label", `Select ${asset.filename} from ${asset.speaker_name} for export`);
        select.addEventListener("change", updateExportState);
      }
      const header = document.createElement("div"); header.className = "speaker-file-card__header";
      const selection = document.createElement("div"); selection.className = "speaker-file-card__selection";
      if (select) selection.append(select);
      else selection.setAttribute("aria-hidden", "true");
      header.append(selection);
      const visual = document.createElement("div"); visual.className = "speaker-file-card__visual";
      if (asset.preview_url) {
        const preview = document.createElement("img");
        preview.className = "speaker-file-preview";
        preview.src = asset.preview_url;
        preview.alt = `Headshot preview for ${asset.speaker_name}`;
        preview.loading = "lazy";
        visual.append(preview);
      } else {
        const extension = asset.filename.includes(".") ? asset.filename.split(".").pop() : asset.kind;
        visual.textContent = String(extension || "file").slice(0, 4).toUpperCase();
        visual.setAttribute("aria-hidden", "true");
      }
      header.append(visual);
      const identity = document.createElement("div"); identity.className = "speaker-file-card__identity";
      const titleRow = document.createElement("div"); titleRow.className = "speaker-file-card__title-row";
      const title = document.createElement("strong"); title.className = "speaker-file-card__title"; title.textContent = asset.filename;
      const scan = document.createElement("span"); scan.className = asset.scan_status === "clean" ? "status-badge success" : "status-badge";
      scan.textContent = asset.scan_status === "clean" ? "Safety checked" : `Scan ${asset.scan_status}`;
      titleRow.append(title, scan);
      const owner = document.createElement("p"); owner.className = "speaker-file-card__owner";
      owner.textContent = `${asset.speaker_name} · ${asset.kind.replaceAll("_", " ")}`;
      const meta = document.createElement("dl"); meta.className = "speaker-file-card__meta";
      [
        ["Size", fileSize(asset.byte_size)],
        ["Uploaded", eventTime(asset.uploaded_at_ms)],
        ["Uploaded by", asset.uploaded_by],
        ["Versions", String(asset.version_count)]
      ].forEach(([label, value]) => {
        const group = document.createElement("div");
        const term = document.createElement("dt"); term.textContent = label;
        const description = document.createElement("dd"); description.textContent = value;
        group.append(term, description); meta.append(group);
      });
      identity.append(titleRow, owner, meta); header.append(identity); item.append(header);
      if (asset.version_comment) {
        const currentComment = document.createElement("p");
        currentComment.className = "speaker-file-card__note";
        currentComment.textContent = asset.version_comment;
        item.append(currentComment);
      }
      const quickActions = document.createElement("div"); quickActions.className = "speaker-file-card__quick-actions";
      if (asset.direct_download_url) {
        const download = document.createElement("button");
        download.type = "button"; download.className = "secondary"; download.textContent = "Download headshot";
        download.addEventListener("click", () => downloadProfileHeadshot(asset, download));
        quickActions.append(download);
      }
      if (quickActions.children.length) item.append(quickActions);
      const history = document.createElement("details");
      history.className = "speaker-file-card__disclosure";
      const summary = document.createElement("summary"); summary.textContent = `Version history (${asset.versions.length})`;
      const versions = document.createElement("ol");
      asset.versions.forEach((version) => {
        const versionItem = document.createElement("li");
        const versionTitle = document.createElement("strong"); versionTitle.textContent = `Version ${version.generation} · ${version.filename}`;
        const versionMeta = document.createElement("span"); versionMeta.className = "muted"; versionMeta.textContent = `${version.state} · ${fileSize(version.byte_size)} · ${eventTime(version.uploaded_at_ms)}`;
        const comment = document.createElement("p"); comment.className = "help"; comment.textContent = version.version_comment;
        const button = document.createElement("button"); button.type = "button"; button.className = "secondary"; button.textContent = "Download";
        button.addEventListener("click", () => downloadAsset(asset, version, button));
        versionItem.append(versionTitle, versionMeta, comment, button); versions.append(versionItem);
      });
      history.append(summary, versions);
      if (asset.versions.length) item.append(history);
      if (exportable) {
        const discussion = document.createElement("details");
        discussion.className = "speaker-file-card__disclosure";
        const discussionSummary = document.createElement("summary"); discussionSummary.textContent = "Details and discussion";
        const thread = document.createElement("div"); thread.className = "asset-discussion";
        discussion.addEventListener("toggle", async () => {
          if (!discussion.open || discussion.dataset.loaded) return;
          discussion.dataset.loaded = "true"; thread.textContent = "Loading discussion…";
          try {
            const detail = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/assets/${encodeURIComponent(asset.id)}`);
            thread.replaceChildren();
            detail.comments.forEach((comment) => {
              const note = document.createElement("article"); note.className = comment.parent_comment_id ? "asset-comment asset-comment--reply" : "asset-comment";
              const heading = document.createElement("strong"); heading.textContent = comment.author_name;
              const time = document.createElement("span"); time.className = "muted"; time.textContent = eventTime(comment.created_at_ms);
              const audience = document.createElement("span");
              audience.className = comment.visibility === "shared" ? "status-badge success" : "status-badge";
              audience.textContent = comment.visibility === "shared" ? "Shared with speaker" : "Internal";
              const copy = document.createElement("p"); copy.textContent = comment.body_text;
              const reply = document.createElement("button"); reply.type = "button"; reply.className = "tertiary compact"; reply.textContent = "Reply";
              reply.addEventListener("click", () => {
                versionSelect.value = comment.version_id;
                visibility.value = comment.visibility;
                refreshParents();
                parent.value = comment.id;
                updateReplyContext();
                textarea.focus();
              });
              note.append(heading, time, audience, copy, reply); thread.append(note);
            });
            const form = document.createElement("form");
            const label = document.createElement("label"); label.textContent = "Add a comment or reply";
            const textarea = document.createElement("textarea"); textarea.name = "body_text"; textarea.maxLength = 5000; textarea.required = true; label.append(textarea);
            const versionLabel = document.createElement("label"); versionLabel.textContent = "File version";
            const versionSelect = document.createElement("select"); versionSelect.name = "version_id"; versionLabel.append(versionSelect);
            asset.versions.forEach((version) => { const option = document.createElement("option"); option.value = version.id; option.textContent = `Version ${version.generation}`; versionSelect.append(option); });
            const parentLabel = document.createElement("label"); parentLabel.textContent = "Reply to";
            const parent = document.createElement("select"); parent.name = "parent_comment_id"; parentLabel.append(parent);
            const replyContext = document.createElement("p"); replyContext.className = "help"; replyContext.setAttribute("role", "status");
            const updateReplyContext = () => {
              const selected = parent.selectedOptions[0];
              replyContext.textContent = parent.value ? `Replying to: ${selected.textContent}` : "Posting a new top-level comment.";
            };
            const refreshParents = () => {
              const selected = parent.value;
              parent.replaceChildren();
              const top = document.createElement("option"); top.value = ""; top.textContent = "New comment"; parent.append(top);
              detail.comments
                .filter((comment) => comment.version_id === versionSelect.value)
                .filter((comment) => visibility.value !== "shared" || comment.visibility === "shared")
                .forEach((comment) => {
                  const option = document.createElement("option"); option.value = comment.id;
                  option.textContent = `Reply to ${comment.author_name}: ${comment.body_text.slice(0, 50)}`;
                  parent.append(option);
                });
              if ([...parent.options].some((option) => option.value === selected)) parent.value = selected;
              updateReplyContext();
            };
            const visibilityLabel = document.createElement("label");
            visibilityLabel.textContent = "Audience";
            const visibility = document.createElement("select"); visibility.name = "visibility";
            [
              ["internal", "Internal note (organizers only)"],
              ["shared", "Shared with the speaker"]
            ].forEach(([value, text]) => {
              const option = document.createElement("option");
              option.value = value; option.textContent = text; visibility.append(option);
            });
            visibilityLabel.append(visibility);
            versionSelect.addEventListener("change", refreshParents);
            visibility.addEventListener("change", refreshParents);
            parent.addEventListener("change", updateReplyContext);
            refreshParents();
            const visibilityHelp = document.createElement("p");
            visibilityHelp.className = "help";
            visibilityHelp.textContent = "Internal notes stay with the organizing team. Shared comments appear in the speaker's portal and they can reply.";
            const submit = document.createElement("button"); submit.textContent = "Post comment";
            form.append(label, versionLabel, visibilityLabel, parentLabel, replyContext, visibilityHelp, submit);
            form.addEventListener("submit", async (event) => {
              event.preventDefault(); submit.disabled = true;
              try {
                await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/assets/${encodeURIComponent(asset.id)}/versions/${encodeURIComponent(versionSelect.value)}/comments`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ body_text: textarea.value, parent_comment_id: parent.value || null, visibility: visibility.value }) });
                discussion.dataset.loaded = ""; discussion.open = false; discussion.open = true; setStatus("Comment posted.");
              } catch (error) { setStatus(window.SessionBuddyApi.message(error, "The comment could not be posted."), true); }
              finally { submit.disabled = false; }
            });
            thread.append(form);
          } catch (error) { thread.textContent = window.SessionBuddyApi.message(error, "File details could not be loaded."); }
        });
        discussion.append(discussionSummary, thread); item.append(discussion);
      }
      return item;
    });
    if (!nodes.length) {
      const empty = document.createElement("li"); empty.className = "empty";
      empty.textContent = "No safety-checked speaker files yet."; nodes.push(empty);
    }
    byId("file-list").replaceChildren(...nodes);
    updateExportState();
  }
  byId("export-files").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const assetIds = [...document.querySelectorAll('input[name="export_asset"]:checked')].map((input) => input.value);
    if (!assetIds.length) return;
    button.disabled = true;
    try {
      let response;
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/deliverables/export`, {
        method: "POST", headers: { "content-type": "application/json", "x-csrf-token": state.csrf }, body: JSON.stringify({ asset_ids: assetIds })
      }, { expectJson: false, onResponse: (received) => { response = received; } });
      const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a");
      link.href = url; link.download = "speaker-deliverables.zip"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStatus(`${assetIds.length} deliverable${assetIds.length === 1 ? "" : "s"} exported.`);
    } catch (error) { setStatus(window.SessionBuddyApi.message(error, "The ZIP export could not be created."), true); }
    finally { button.disabled = !document.querySelector('input[name="export_asset"]:checked'); }
  });
  async function loadTargets() {
    const body = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-targets`);
    const available = body.data.filter((item) => ["invited", "submitted", "accepted"].includes(item.selection_status));
    const options = available.map((item) => {
      const label = document.createElement("label"); label.className = "speaker-picker__option";
      const input = document.createElement("input"); input.type = "checkbox"; input.name = "event_speaker_id"; input.value = item.event_speaker_id;
      const copy = document.createElement("span");
      const name = document.createElement("strong"); name.textContent = item.display_name;
      const stateLabel = { invited: "Invitation pending", submitted: "Registered", accepted: "Accepted" }[item.selection_status];
      const proposal = document.createElement("small"); proposal.textContent = `${stateLabel} · ${item.proposal_title}`;
      copy.append(name, proposal); label.append(input, copy); return label;
    });
    byId("speaker-target").replaceChildren(...options);
    if (!available.length) {
      const empty = document.createElement("p"); empty.className = "empty";
      empty.textContent = "No invited, registered, or accepted speakers yet.";
      byId("speaker-target").append(empty);
    }
  }
  byId("resource-form").elements.title.addEventListener("input", (event) => {
    const slugInput = event.currentTarget.form.elements.slug;
    if (!slugInput.dataset.edited) slugInput.value = slug(event.currentTarget.value);
  });
  byId("resource-form").elements.slug.addEventListener("input", (event) => { event.currentTarget.dataset.edited = "true"; });
  byId("resource-form").addEventListener("input", (event) => {
    event.target.setCustomValidity?.("");
    if (event.target.name === "embed_url") {
      event.target.setCustomValidity(
        approvedEmbed(event.target.value)
          ? ""
          : "Use an approved HTTPS Google, YouTube, or Vimeo URL."
      );
    }
  });
  byId("resource-form").addEventListener("submit", async (event) => {
    // event.currentTarget is null after any await; capture the form up front.
    event.preventDefault(); const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    const embed = form.elements.embed_url;
    embed.setCustomValidity(approvedEmbed(values.embed_url) ? "" : "Use an approved HTTPS Google, YouTube, or Vimeo URL.");
    if (!form.checkValidity()) {
      form.querySelector(":invalid")?.focus();
      return;
    }
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/resources`, { method: "POST", headers: mutationHeaders(), body: JSON.stringify({ title: values.title, slug: values.slug, summary: values.summary, body_text: values.body_text, embed_url: values.embed_url || null, status: values.status, sort_order: Number(values.sort_order) }) });
      form.reset(); delete form.elements.slug.dataset.edited; form.elements.sort_order.value = "0"; setStatus("Resource published to the speaker portal."); await loadResources();
    } catch (error) { setStatus(window.SessionBuddyApi.message(error), true); }
  });
  byId("task-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    // event.currentTarget is null after any await; capture the form up front.
    // Using it after the awaited task creation threw and turned a SUCCESSFUL
    // assignment into a failure banner (the eval run's SPK-15 sighting).
    const form = event.currentTarget;
    const data = new FormData(form);
    const values = Object.fromEntries(data);
    const speakerIds = data.getAll("event_speaker_id").filter(Boolean);
    if (!speakerIds.length) {
      setStatus("Choose at least one invited, registered, or accepted speaker.", true);
      byId("speaker-target").focus();
      return;
    }
    const label = form.elements.field_label;
    label.setCustomValidity(values.field_required && !String(values.field_label || "").trim() ? "Enter the required response question." : "");
    const dueInput = form.elements.due_at;
    const due = inputMillis(values.due_at);
    dueInput.setCustomValidity(Number.isNaN(due) ? `Choose a valid local time in ${state.timeZone}.` : "");
    if (!form.checkValidity()) {
      form.querySelector(":invalid")?.focus();
      return;
    }
    const taskType = String(values.task_type || "custom");
    const rules = uploadRules[taskType];
    const uploadEnabled = Boolean(values.upload_enabled);
    if (rules && !uploadEnabled) {
      setStatus("Enable speaker uploads to create a file request.", true);
      return;
    }
    const fields = !rules && values.field_label ? [{ key: "response", label: values.field_label, type: values.field_type, required: Boolean(values.field_required), choices: [] }] : [];
    const maxFileBytes = rules ? Number(values.max_file_mb) * 1024 * 1024 : null;
    const payloads = speakerIds.map((eventSpeakerId) => ({
      event_speaker_id: eventSpeakerId, submission_id: null, title: values.title,
      help_text: values.help_text, due_at_ms: due, task_type: taskType,
      upload_enabled: uploadEnabled, allowed_content_types: rules?.types || [],
      max_file_bytes: maxFileBytes, fields
    }));
    const fingerprint = JSON.stringify(payloads);
    if (!state.taskMutation || state.taskMutation.fingerprint !== fingerprint) {
      state.taskMutation = {
        fingerprint,
        keys: new Map(speakerIds.map((eventSpeakerId) => [eventSpeakerId, idempotencyKey()]))
      };
    }
    const button = form.querySelector('button[type="submit"], button:not([type])');
    button.disabled = true;
    try {
      await Promise.all(payloads.map((payload) =>
        api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/speaker-tasks`, {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": state.csrf, "idempotency-key": state.taskMutation.keys.get(payload.event_speaker_id) },
          body: JSON.stringify(payload)
        })
      ));
      form.reset();
      updateTaskType();
      // Keep this payload's idempotency keys. Clearing them meant an identical
      // second submit minted fresh keys, and the server dedupes by key alone -
      // so the same task was inserted again. Retaining them makes a repeat
      // submit replay the original request, and lets us say so plainly instead
      // of reporting a second assignment that did not happen.
      const alreadyAssigned = state.taskMutation.submitted === true;
      state.taskMutation.submitted = true;
      const speakerCount = `${speakerIds.length} speaker${speakerIds.length === 1 ? "" : "s"}`;
      setStatus(alreadyAssigned
        ? `That task is already assigned to ${speakerCount}; no duplicate was created.`
        : `Task assigned to ${speakerCount}.`);
    } catch (error) {
      setStatus(`${window.SessionBuddyApi.message(error)} Retry Assign task; the same request will not be duplicated.`, true);
    } finally { button.disabled = false; }
  });
  byId("task-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  byId("task-type").addEventListener("change", updateTaskType);
  async function initialize() {
    if (!eventId) throw new Error("Invalid event link.");
    const session = await api("/api/v1/auth/session"); state.csrf = session.csrf_token;
    state.timeZone = await loadEventTimeZone();
    byId("task-time-zone").textContent = state.timeZone;
    updateTaskType();
    const results = await Promise.allSettled([loadResources(), loadTargets(), loadAssets()]);
    if (results.some((result) => result.status === "rejected")) {
      setStatus("Some speaker information could not be loaded. Refresh to try again.", true);
    } else if (byId("status").textContent === "Loading…") {
      // Only claim readiness if nothing else spoke meanwhile: a fast user's
      // action feedback must not be stomped by slow initialization.
      setStatus("Speaker tasks and files ready.");
    }
  }
  initialize().catch((error) => { if (!window.SessionBuddyApi.redirectIfSignedOut(error)) setStatus(window.SessionBuddyApi.message(error), true); });
})();

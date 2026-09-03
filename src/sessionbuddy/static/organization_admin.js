(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  // The page edits exactly one organization at a time. `loadToken` is bumped on
  // every selection so a slow response for a previously selected organization
  // is dropped instead of rendering into the current one.
  const state = { session: null, organizations: [], owned: new Set(), organizationId: "", loadToken: 0, activities: [], activityFilter: "all" };
  let session;

  // One authoritative map from activity resource types to the filter chips.
  // Types not listed here belong to "All" only.
  // One chip per destination in the event workspace, in the order the program
  // runs. A compound chip ("Speakers & agenda") hid two unrelated things, and
  // invitations are people joining, not access control.
  const ACTIVITY_KINDS = Object.freeze({
    events: { label: "Events", types: ["event"] },
    cfp: { label: "Call for proposals", types: ["call_for_speaker_form"] },
    proposals: { label: "Proposals & reviews", types: ["proposal", "evaluation_round", "evaluation", "evaluation_assignment"] },
    speakers: { label: "Speakers", types: ["event_speaker", "speaker_task", "speaker_asset_version"] },
    agenda: { label: "Agenda", types: ["accepted_session", "agenda_item", "schedule_revision"] },
    invitations: { label: "Invitations", types: ["invitation"] },
    access: { label: "Access", types: ["organization", "resource_access_grant", "ownership_transfer"] },
  });
  const INVITATION_STATUS = Object.freeze({ pending: "Invitation pending", accepted: "Accepted", expired: "Expired", revoked: "Revoked", declined: "Declined" });
  const INVITATION_DELIVERY = Object.freeze({ queued: "queued", sent: "sent", delivered: "delivered", failed: "not delivered" });

  function showStatus(message, kind = "", focus = false) {
    const status = byId("status");
    status.className = `status status-compact${kind ? ` ${kind}` : ""}`;
    status.textContent = message;
    if (focus) status.focus();
  }

  function accessMessage(error, fallback) {
    return window.SessionBuddyApi.message(error) || fallback;
  }

  function accessSection(organizationId) {
    return [...byId("organization-settings-list").querySelectorAll("[data-organization-access-id]")]
      .find((node) => node.dataset.organizationAccessId === organizationId);
  }

  function selectedOrganization() {
    return state.organizations.find((organization) => organization.id === state.organizationId);
  }

  function selectedName() {
    return selectedOrganization()?.name || "";
  }

  function grantRow(organizationId, grant) {
    const row = document.createElement("li");
    row.className = "organizer-access-list__item organization-grant-row";
    const summary = document.createElement("span");
    summary.className = "organizer-access-list__summary";
    const email = document.createElement("strong");
    email.textContent = grant.email;
    const role = document.createElement("span");
    role.className = "organization-role";
    role.textContent = grant.permission === "owner" ? "Owner" : "Admin";
    summary.append(email, " ", role);
    row.append(summary);
    if (grant.permission === "owner") {
      const note = document.createElement("small");
      note.textContent = "Ownership cannot be revoked from this access list.";
      row.append(note);
      return row;
    }
    const revoke = document.createElement("button");
    revoke.type = "button";
    revoke.className = "secondary organization-grant-row__action";
    const reset = () => {
      revoke.dataset.confirming = "false";
      revoke.textContent = "Revoke access";
      revoke.setAttribute("aria-label", `Revoke organization access for ${grant.email}`);
    };
    reset();
    revoke.addEventListener("blur", reset);
    revoke.addEventListener("keydown", (event) => { if (event.key === "Escape") reset(); });
    revoke.addEventListener("click", async () => {
      if (revoke.dataset.confirming !== "true") {
        revoke.dataset.confirming = "true";
        revoke.textContent = "Confirm revoke";
        revoke.setAttribute("aria-label", `Confirm revoke organization access for ${grant.email}`);
        return;
      }
      revoke.disabled = true;
      try {
        await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/access-grants/${encodeURIComponent(grant.user_id)}`, {
          method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token }
        });
        await loadGrants(organizationId);
        showStatus(`Organization organizer access revoked for ${grant.email}.`, "success");
      } catch (error) {
        showStatus(accessMessage(error, "Organization access could not be revoked."), "error", true);
        revoke.disabled = false;
        reset();
      }
    });
    row.append(revoke);
    return row;
  }

  function syncSummary(organizerCount) {
    const organization = selectedOrganization();
    if (!organization) return;
    const events = Number(organization.event_count ?? 0);
    const parts = [
      state.owned.has(organization.id) ? "You own this organization" : "You administer this organization",
      `${events} ${events === 1 ? "event" : "events"}`,
    ];
    if (organizerCount !== undefined) parts.push(`${organizerCount} ${organizerCount === 1 ? "organizer" : "organizers"}`);
    byId("organization-summary").textContent = parts.join(" · ");
  }

  async function loadGrants(organizationId, token = state.loadToken) {
    const grants = await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/access-grants`);
    if (token !== state.loadToken) return;
    const section = accessSection(organizationId);
    if (!section) return;
    const list = section.querySelector(".organization-grant-list");
    const status = section.querySelector(".organization-access-status");
    const organizers = grants.data.filter((grant) => ["owner", "manage"].includes(grant.permission));
    list.replaceChildren(...organizers.map((grant) => grantRow(organizationId, grant)));
    status.textContent = `${organizers.length} active ${organizers.length === 1 ? "organizer" : "organizers"}.`;
    if (organizationId === state.organizationId) syncSummary(organizers.length);
    await loadInvitations(organizationId, token);
  }

  async function loadInvitations(organizationId, token = state.loadToken) {
    const invitations = await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/admin-invitations`);
    if (token !== state.loadToken) return;
    const section = accessSection(organizationId);
    if (!section) return;
    const list = section.querySelector(".organization-invitation-list");
    list.replaceChildren();
    if (!invitations.data.length) { const empty = document.createElement("li"); empty.className = "help"; empty.textContent="No invitations yet."; list.append(empty); }
    for (const invitation of invitations.data) {
      const row = document.createElement("li");
      row.className = "organization-invitation-row";
      const summary = document.createElement("p");
      const email = document.createElement("strong");
      email.textContent = invitation.email;
      const stateText = document.createElement("span");
      stateText.className = "organization-invitation-row__state";
      const delivery = invitation.delivery || {};
      stateText.textContent = `${INVITATION_STATUS[invitation.status] || invitation.status} · email ${INVITATION_DELIVERY[delivery.status] || delivery.status} · expires ${window.SessionBuddyActivityFormat.relativeTime(invitation.expires_at_ms)}${invitation.needs_reissue ? " · needs a new link" : ""}`;
      stateText.title = `Expires ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(invitation.expires_at_ms))}`;
      summary.append(email, stateText);
      row.append(summary);
      const actionsGroup = document.createElement("div");
      actionsGroup.className = "organization-invitation-row__actions";
      const actions = invitation.status === "pending" ? (invitation.needs_reissue ? ["Reissue", "Revoke"] : ["Resend", "Revoke"]) : (invitation.status === "accepted" ? [] : ["Invite again"]);
      for (const label of actions) {
        const button = document.createElement("button"); button.type="button"; button.className="secondary"; button.textContent=label;
        let confirming=false; const key=crypto.randomUUID();
        button.addEventListener("blur", () => { confirming=false; button.textContent=label; });
        button.addEventListener("click", async () => {
          if (!confirming) { confirming=true; button.textContent=`Confirm ${label.toLowerCase()}`; return; }
          button.disabled=true;
          const base=`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/admin-invitations`;
          const url=label === "Invite again" ? base : `${base}/${encodeURIComponent(invitation.id)}${label === "Revoke" ? "" : "/"+label.toLowerCase()}`;
          try {
            await api(url, {method:label === "Revoke" ? "DELETE" : "POST", headers:{"content-type":"application/json","x-csrf-token":session.csrf_token,"Idempotency-Key":key}, body:JSON.stringify(label === "Invite again" ? {email:invitation.email} : {})});
            await loadInvitations(organizationId); showStatus("Invitation updated.", "success");
          } catch(error) { showStatus(accessMessage(error,"Invitation could not be updated."),"error",true); button.disabled=false; }
        });
        actionsGroup.append(button);
      }
      if (actions.length) row.append(actionsGroup);
      list.append(row);
    }
  }

  function organizationPanels(organization, isOwner) {
    const panels = document.createDocumentFragment();
    const nameForm = document.createElement("form");
    nameForm.className = "organization-name-form organization-panel organization-panel--name";
    nameForm.dataset.organizationId = organization.id;
    nameForm.dataset.version = String(organization.version);
    nameForm.innerHTML = `<label>Organization name<input name="name" maxlength="200" required></label><button type="submit">Save organization</button>`;
    nameForm.elements.name.value = organization.name;

    const access = document.createElement("section");
    access.className = "organization-access-manager organization-panel";
    access.dataset.organizationAccessId = organization.id;
    access.setAttribute("aria-labelledby", "organization-access-title");
    access.innerHTML = `<div class="section-heading"><div><h3 id="organization-access-title">People with access</h3><p class="help">The owner and admins can manage every event in this organization.</p></div></div><form class="organization-grant-form" data-organization-grant-create="${organization.id}"><label>Email<input name="email" type="email" autocomplete="email" maxlength="320" required></label><button type="submit">Invite admin</button></form><p class="help">They will receive an invitation to administer this organization and all its events. They do not need an existing account. The invitation is valid for 3 days.</p><p class="help organization-access-status" role="status" aria-live="polite"></p><h4>Organizers</h4><ul class="organizer-access-list organization-grant-list"><li class="help" data-loading-placeholder>Loading organization access…</li></ul><h4>Invitations</h4><ul class="organization-invitation-list"><li class="help" data-loading-placeholder>Loading invitations…</li></ul>`;
    panels.append(nameForm, access);
    if (isOwner) {
      const transfer = document.createElement("form");
      transfer.className = "organization-owner-transfer-form organization-panel organization-panel--quiet";
      transfer.dataset.organizationOwnerTransfer = organization.id;
      transfer.innerHTML = `<h3>Transfer ownership</h3><p class="help">Only the current owner can transfer ownership, and only to an existing Admin. You will remain an Admin.</p><label>New owner’s admin email<input name="email" type="email" autocomplete="email" required></label><button class="danger" type="submit">Review ownership transfer</button>`;
      // Arming is reversible, like the revoke buttons: leaving the control or
      // pressing Escape returns the form to its unarmed state.
      const transferButton = transfer.querySelector("button");
      const disarm = () => disarmTransfer(transfer);
      transferButton.addEventListener("blur", disarm);
      transfer.addEventListener("keydown", (event) => { if (event.key === "Escape") disarm(); });
      panels.append(transfer);
    }
    return panels;
  }

  function disarmTransfer(form) {
    const button = form.querySelector("button");
    button.dataset.confirming = "false";
    button.textContent = "Review ownership transfer";
    form.elements.email.readOnly = false;
  }

  function resetActivity() {
    state.activities = [];
    state.activityFilter = "all";
    const placeholder = document.createElement("li");
    placeholder.className = "help";
    placeholder.dataset.loadingPlaceholder = "";
    placeholder.textContent = "Loading activity…";
    byId("organization-activity-list").replaceChildren(placeholder);
    byId("organization-activity-summary").textContent = "Loading activity…";
    byId("organization-activity-filters").hidden = true;
  }

  async function loadActivity(organizationId, token = state.loadToken) {
    const activities = (await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/activities`)).data;
    if (token !== state.loadToken) return;
    state.activities = activities;
    renderActivity();
  }

  function activityKind(activity) {
    const type = String(activity.resource_type || "");
    return Object.keys(ACTIVITY_KINDS).find((kind) => ACTIVITY_KINDS[kind].types.includes(type)) || "";
  }

  function dayLabel(date, now) {
    const startOfDay = (value) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
    const difference = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
    if (difference === 0) return "Today";
    if (difference === 1) return "Yesterday";
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
  }

  // One edit can write several audit rows, so the raw feed repeats the same
  // sentence back to back. Consecutive rows describing the same person doing
  // the same thing to the same object collapse into one entry with a count.
  function collapseRuns(activities) {
    const runs = [];
    for (const activity of activities) {
      const key = [activity.actor_name, activity.operation, activity.resource_type, activity.subject_name].join("\u0000");
      const previous = runs[runs.length - 1];
      if (previous && previous.key === key) {
        previous.count += 1;
        previous.earliestMs = activity.occurred_at_ms;
        continue;
      }
      runs.push({ key, activity, count: 1, latestMs: activity.occurred_at_ms, earliestMs: activity.occurred_at_ms });
    }
    return runs;
  }

  function activityItem(run) {
    const item = document.createElement("li");
    item.className = "organization-activity-item";
    const text = document.createElement("p");
    text.textContent = window.SessionBuddyActivityFormat.sentence(run.activity);
    if (run.count > 1) {
      const repeat = document.createElement("span");
      repeat.className = "organization-activity-item__repeat";
      repeat.textContent = `${run.count} times`;
      text.append(" ", repeat);
    }
    const latest = new Date(run.latestMs);
    const earliest = new Date(run.earliestMs);
    const clock = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
    const stamp = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
    const time = document.createElement("time");
    time.dateTime = latest.toISOString();
    time.title = run.count > 1 && clock.format(earliest) !== clock.format(latest)
      ? `${stamp.format(earliest)} to ${stamp.format(latest)}`
      : stamp.format(latest);
    time.textContent = run.count > 1 && clock.format(earliest) !== clock.format(latest)
      ? `${clock.format(earliest)} – ${clock.format(latest)}`
      : clock.format(latest);
    item.append(text, time);
    return item;
  }

  function renderActivity() {
    const name = selectedName();
    const total = state.activities.length;
    const counts = {};
    for (const activity of state.activities) {
      const kind = activityKind(activity);
      if (kind) counts[kind] = (counts[kind] || 0) + 1;
    }
    if (state.activityFilter !== "all" && !counts[state.activityFilter]) state.activityFilter = "all";
    const filters = byId("organization-activity-filters");
    for (const button of filters.querySelectorAll("[data-activity-filter]")) {
      const kind = button.dataset.activityFilter;
      if (kind !== "all") {
        const count = counts[kind] || 0;
        const label = document.createElement("span");
        label.textContent = ACTIVITY_KINDS[kind].label;
        const tally = document.createElement("span");
        tally.className = "organization-activity__count";
        tally.textContent = String(count);
        button.replaceChildren(label, tally);
        button.hidden = count === 0;
      }
      button.setAttribute("aria-pressed", String(kind === state.activityFilter));
    }
    const kindsPresent = Object.keys(counts).length;
    const allInOneKind = kindsPresent === 1 && counts[Object.keys(counts)[0]] === total;
    filters.hidden = kindsPresent === 0 || allInOneKind;

    const rows = state.activityFilter === "all"
      ? state.activities
      : state.activities.filter((activity) => activityKind(activity) === state.activityFilter);
    const now = new Date();
    const days = new Map();
    for (const run of collapseRuns(rows)) {
      const date = new Date(run.latestMs);
      const key = date.toDateString();
      if (!days.has(key)) {
        const day = document.createElement("li");
        day.className = "organization-activity-day";
        const title = document.createElement("h3");
        title.className = "organization-activity-day__title";
        title.textContent = dayLabel(date, now);
        const list = document.createElement("ol");
        list.className = "organization-activity-day__list";
        day.append(title, list);
        days.set(key, { day, list });
      }
      days.get(key).list.append(activityItem(run));
    }
    byId("organization-activity-list").replaceChildren(...[...days.values()].map((entry) => entry.day));

    const summary = byId("organization-activity-summary");
    if (total === 0) summary.textContent = `No changes recorded for ${name} yet.`;
    else if (state.activityFilter === "all") summary.textContent = total < 30 ? `Showing all ${total} ${total === 1 ? "change" : "changes"}.` : "Showing the 30 most recent changes.";
    else if (rows.length) summary.textContent = `Showing ${rows.length} of the ${total} most recent changes.`;
    else summary.textContent = `No ${ACTIVITY_KINDS[state.activityFilter].label.toLowerCase()} changes in the ${total} most recent.`;
  }

  function syncUrl() {
    history.replaceState(null, "", `${location.pathname}?organization_id=${encodeURIComponent(state.organizationId)}${location.hash}`);
  }

  function syncContext() {
    const context = byId("organization-context");
    if (state.organizations.length < 2) {
      const strong = document.createElement("strong");
      strong.textContent = selectedName();
      context.replaceChildren("Settings for ", strong);
    } else {
      context.textContent = "Choose which organization to edit.";
    }
    byId("organization-activity-scope").textContent = `Changes in ${selectedName()} across organizers, events, speakers, reviewers, and proposals. Only the 30 most recent are listed.`;
  }

  function renderSwitcher() {
    const switcher = byId("organization-switcher");
    switcher.replaceChildren(...state.organizations.map((organization) => new Option(organization.name, organization.id)));
    byId("organization-switcher-label").hidden = state.organizations.length < 2;
  }

  async function selectOrganization(id, { announce = false } = {}) {
    // Resolve the organization before committing state or the URL so a
    // tampered switcher value never leaves an unknown id in the address bar.
    const organization = state.organizations.find((item) => item.id === id);
    if (!organization) throw window.SessionBuddyApi.userError("That organization is not available to you.");
    state.organizationId = id;
    syncUrl();
    const isOwner = state.owned.has(id);
    byId("organization-settings-title").textContent = organization.name;
    byId("organization-switcher").value = id;
    syncSummary();
    syncContext();
    byId("organization-activity-list").setAttribute("aria-label", `Activity in ${organization.name}`);
    byId("organization-settings-list").replaceChildren(organizationPanels(organization, isOwner));
    resetActivity();
    if (announce) {
      // Announce and move focus with the change itself, not after the network
      // settles, so focus is never pulled away from wherever the user went next.
      showStatus(`Now showing ${organization.name}.`);
      byId("organization-settings-title").focus({ preventScroll: true });
    }
    const token = ++state.loadToken;
    const results = await Promise.allSettled([loadGrants(id, token), loadActivity(id, token)]);
    if (token !== state.loadToken) return;
    const failures = results.filter((result) => result.status === "rejected").map((result) => result.reason);
    if (!failures.length) return;
    // Keep whatever loaded; only the panels that failed say so.
    if (failures.some((error) => window.SessionBuddyApi.redirectIfSignedOut(error))) return;
    markPlaceholdersFailed();
    throw window.SessionBuddyApi.userError("Some organization details could not be loaded. Reload to try again.");
  }

  async function load() {
    // Show the page skeleton immediately so a slow or failed request is never
    // an empty page under the heading.
    const placeholder = document.createElement("p");
    placeholder.className = "help";
    placeholder.dataset.loadingPlaceholder = "";
    placeholder.textContent = "Loading organization settings…";
    byId("organization-settings-list").replaceChildren(placeholder);
    byId("organization-settings").hidden = false;
    byId("organization-activity").hidden = false;
    const [sessionView, organizationList] = await Promise.all([api("/api/v1/auth/session"), api("/api/v1/admin/organizations")]);
    session = state.session = sessionView;
    const manageable = new Set((session.organization_access || []).filter((item) => (
      (item.permissions || []).some((permission) => ["owner", "manage"].includes(permission))
    )).map((item) => item.organization_id));
    const owned = new Set((session.organization_access || []).filter((item) => (
      (item.permissions || []).includes("owner")
    )).map((item) => item.organization_id));
    const organizations = organizationList.data.filter((organization) => manageable.has(organization.id));
    if (!organizations.length) throw window.SessionBuddyApi.userError("No manageable organization is available to this account.");
    state.organizations = organizations;
    state.owned = owned;
    const requested = new URLSearchParams(location.search).get("organization_id");
    const fallback = organizations.some((organization) => organization.id === session.organization_id) ? session.organization_id : organizations[0].id;
    const selected = organizations.some((organization) => organization.id === requested) ? requested : fallback;
    renderSwitcher();
    const unavailable = Boolean(requested) && requested !== selected;
    if (unavailable) {
      const name = organizations.find((organization) => organization.id === selected).name;
      showStatus(`Showing ${name}. The requested organization is not available to you.`);
    }
    await selectOrganization(selected, { announce: false });
  }

  // Loading copy that is not a placeholder node, with its failed state. Only
  // lines still showing their loading copy are rewritten.
  const LOADING_STATES = Object.freeze([
    ["organization-activity-summary", "Loading activity…", "Activity could not be loaded."],
    ["organization-context", "Loading your organizations…", "Your organizations could not be loaded."],
    ["organization-summary", "Loading…", "Not loaded."],
  ]);

  function markPlaceholdersFailed() {
    for (const node of document.querySelectorAll("[data-loading-placeholder]")) node.textContent = "This information could not be loaded.";
    for (const [id, loading, failed] of LOADING_STATES) {
      const node = byId(id);
      if (node.textContent === loading) node.textContent = failed;
    }
  }

  function reportLoadFailure(error) {
    if (window.SessionBuddyApi.redirectIfSignedOut(error)) return;
    markPlaceholdersFailed();
    showStatus(window.SessionBuddyApi.message(error, "Organization settings could not be loaded."), "error", true);
  }

  byId("organization-switcher").addEventListener("change", (event) => {
    selectOrganization(event.currentTarget.value, { announce: true }).catch(reportLoadFailure);
  });

  byId("organization-activity-filters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-activity-filter]");
    if (!button) return;
    state.activityFilter = button.dataset.activityFilter;
    renderActivity();
  });

  byId("organization-settings-list").addEventListener("input", (event) => {
    const form = event.target.closest("[data-organization-grant-create]");
    if (form) delete form.dataset.requestKey;
  });
  byId("organization-settings-list").addEventListener("submit", async (event) => {
    const form = event.target.closest("form");
    if (!form) return;
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    if (form.matches(".organization-name-form")) {
      button.disabled = true;
      try {
        const organization = await api(`/api/v1/admin/organizations/${encodeURIComponent(form.dataset.organizationId)}`, { method: "PATCH", headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token }, body: JSON.stringify({ name: form.elements.name.value.trim(), version: Number(form.dataset.version) }) });
        form.dataset.version = String(organization.version);
        const entry = state.organizations.find((item) => item.id === form.dataset.organizationId);
        // Keep the version too: the panels are rebuilt from this entry on the
        // next switch back, and a stale version makes the next save a 409.
        if (entry) { entry.name = organization.name; entry.version = organization.version; }
        const option = [...byId("organization-switcher").options].find((item) => item.value === form.dataset.organizationId);
        if (option) option.textContent = organization.name;
        // The response may land after a switch; only the selected organization
        // may rename the heading and the activity list.
        if (form.dataset.organizationId === state.organizationId) {
          byId("organization-settings-title").textContent = organization.name;
          byId("organization-activity-list").setAttribute("aria-label", `Activity in ${organization.name}`);
        }
        syncContext();
        showStatus("Organization saved.", "success");
      } catch (error) { showStatus(window.SessionBuddyApi.message(error), "error", true); }
      finally { button.disabled = false; }
      return;
    }
    if (form.matches("[data-organization-grant-create]")) {
      button.disabled = true;
      const email = form.elements.email.value.trim();
      try {
        const result = await api(`/api/v1/admin/organizations/${encodeURIComponent(form.dataset.organizationGrantCreate)}/admin-invitations`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token, "Idempotency-Key": form.dataset.requestKey || (form.dataset.requestKey = crypto.randomUUID()) }, body: JSON.stringify({ email }) });
        delete form.dataset.requestKey;
        form.reset(); await loadGrants(form.dataset.organizationGrantCreate); showStatus(result.cause === "duplicate_pending" ? `An invitation for ${email} is already pending. No new email was sent.` : `Invitation created for ${email}. Email queued.`, "success");
      } catch (error) { showStatus(accessMessage(error, "Organization access could not be granted."), "error", true); }
      finally { button.disabled = false; }
      return;
    }
    if (form.matches("[data-organization-owner-transfer]")) {
      const email = form.elements.email.value.trim();
      if (button.dataset.confirming !== "true") { button.dataset.confirming = "true"; button.textContent = `Confirm transfer to ${email}`; form.elements.email.readOnly = true; return; }
      button.disabled = true;
      try {
        await api(`/api/v1/admin/organizations/${encodeURIComponent(form.dataset.organizationOwnerTransfer)}/ownership-transfers`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token }, body: JSON.stringify({ email }) });
        showStatus(`Organization ownership transferred to ${email}. You remain an Admin.`, "success", true); await load();
      } catch (error) { showStatus(window.SessionBuddyApi.message(error), "error", true); button.disabled = false; disarmTransfer(form); }
    }
  });

  load().catch(reportLoadFailure);
})();

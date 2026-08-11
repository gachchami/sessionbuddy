(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/access$/);
  const eventId = match ? decodeURIComponent(match[1]) : "";
  const byId = (id) => document.getElementById(id);
  let csrf = "";
  let transferContext = null;
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  const accessLabel = (value) => ({
    speaker: "Speaker assignment",
    evaluator: "Reviewer assignment",
    event_admin: "Can manage this event",
    organization_admin: "Can manage this organization"
  }[value] || value.replaceAll("_", " "));
  const statusTone = (value) => ({
    active: "success",
    accepted: "success",
    revoked: "overdue",
    expired: "overdue"
  }[value] || "");
  function item(text) {
    const node = document.createElement("li");
    node.className = "organizer-access-list__item";
    const summary = document.createElement("span");
    summary.className = "organizer-access-list__summary";
    summary.textContent = text;
    node.append(summary);
    return node;
  }
  // A row is an identity (the email) plus secondary facts. Keeping them in
  // separate elements lets a long address wrap on its own line instead of
  // dragging the role and status off the edge of the card with it.
  function accessRow(primary, meta = []) {
    const node = document.createElement("li");
    node.className = "organizer-access-list__item";
    const identity = document.createElement("span");
    identity.className = "organizer-access-list__primary";
    identity.textContent = primary;
    node.append(identity);
    const entries = meta.filter((entry) => entry && entry.text);
    if (entries.length) {
      const line = document.createElement("span");
      line.className = "item-meta organizer-access-list__meta";
      for (const entry of entries) {
        const part = document.createElement("span");
        if (entry.tone !== undefined) part.className = `state-badge ${entry.tone}`.trim();
        part.textContent = entry.text;
        line.append(part);
      }
      node.append(line);
    }
    return node;
  }
  // Controls belong in their own flex row so they wrap as a group and keep a
  // consistent gap, rather than flowing inline behind the summary text.
  function rowActions(node, ...controls) {
    const actions = document.createElement("div");
    actions.className = "actions organizer-access-list__actions";
    actions.append(...controls);
    node.append(actions);
    return actions;
  }
  function destructiveButton(label, confirmLabel, action) {
    // Removing access is destructive: require a second, explicit click and
    // surface failures instead of silently doing nothing.
    // Both labels stay in the layout (the hidden one is only made invisible),
    // so the button is sized to the wider of the two and the row does not
    // reflow when it switches into its confirm state. The visible confirm text
    // is kept short for that reason; the full sentence — "Select again to
    // revoke" — is carried on aria-label, which the visible text is a prefix
    // of, so the accessible name still matches what is on screen.
    const button = document.createElement("button");
    button.className = "secondary confirm-button";
    const idleLabel = document.createElement("span");
    idleLabel.className = "confirm-button__label";
    idleLabel.textContent = label;
    const confirmingLabel = document.createElement("span");
    confirmingLabel.className = "confirm-button__label";
    confirmingLabel.textContent = "Select again";
    button.append(idleLabel, confirmingLabel);
    const setConfirming = (confirming) => {
      button.dataset.confirming = confirming ? "true" : "false";
      idleLabel.setAttribute("aria-hidden", confirming ? "true" : "false");
      confirmingLabel.setAttribute("aria-hidden", confirming ? "false" : "true");
      if (confirming) button.setAttribute("aria-label", confirmLabel);
      else button.removeAttribute("aria-label");
    };
    setConfirming(false);
    button.addEventListener("click", async () => {
      if (button.dataset.confirming !== "true") {
        setConfirming(true);
        return;
      }
      button.disabled = true;
      try {
        await action();
        await load();
      } catch (error) {
        byId("status").textContent = window.SessionBuddyApi.message(error);
        byId("status").focus();
        button.disabled = false;
        setConfirming(false);
      }
    });
    button.addEventListener("blur", () => setConfirming(false));
    return button;
  }
  function resetOwnershipConfirmation() {
    transferContext = null;
    byId("ownership-confirmation").hidden = true;
    byId("confirm-ownership-transfer").disabled = false;
  }
  function renderOwnershipTransfer(session, event, grants) {
    const owner = grants.data.find((grant) => grant.permission === "owner");
    const ownsOrganization = Boolean(event) && (session.organization_access || []).some((entry) =>
      entry.organization_id === event.organization_id
      && (entry.permissions || []).includes("owner"));
    const ownsEvent = owner?.user_id === session.user_id;
    const panel = byId("ownership-panel");
    panel.hidden = !owner || (!ownsOrganization && !ownsEvent);
    if (panel.hidden) {
      resetOwnershipConfirmation();
      return;
    }
    byId("current-owner").textContent = owner.email;
    panel.dataset.currentOwnerUserId = owner.user_id;
    panel.dataset.currentOwnerEmail = owner.email;
    panel.dataset.ownsOrganization = String(ownsOrganization);
  }
  async function load() {
    const session = await api("/api/v1/auth/session");
    csrf = session.csrf_token;
    // Organization management is an exact resource permission, not an
    // account role and not authority inherited from another organization.
    const manageableOrganizationIds = new Set((session.organization_access || [])
      .filter((entry) => (entry.permissions || []).some((permission) =>
        ["owner", "manage"].includes(permission)))
      .map((entry) => entry.organization_id));
    let canInviteOrganizationAdmin = false;
    let selectedEvent = null;
    try {
      selectedEvent = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
      byId("access-event-name").textContent = selectedEvent.name;
      canInviteOrganizationAdmin = manageableOrganizationIds.has(selectedEvent.organization_id);
    } catch (_) { /* the heading simply stays generic */ }
    byId("invite-form").elements.role.querySelector('option[value="organization_admin"]')
      ?.toggleAttribute("hidden", !canInviteOrganizationAdmin);
    const [invitations, members, grants] = await Promise.all([
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`),
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/members`),
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/access-grants`)
    ]);
    const invitationList = byId("invitation-list"); invitationList.replaceChildren();
    for (const invitation of invitations.data) {
      const node = accessRow(invitation.email, [
        { text: accessLabel(invitation.role) },
        { text: invitation.status, tone: statusTone(invitation.status) }
      ]);
      if (invitation.status === "pending") {
        const resend = document.createElement("button"); resend.className = "secondary"; resend.textContent = "Send again";
        resend.addEventListener("click", async () => {
          resend.disabled = true;
          try {
            await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}/resend`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: "{}" });
            byId("status").textContent = `A new link was sent to ${invitation.email}.`;
          } catch (error) { byId("status").textContent = window.SessionBuddyApi.message(error); }
          finally { resend.disabled = false; }
        });
        const button = destructiveButton("Revoke", "Select again to revoke", () =>
          api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}`, { method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": csrf } }));
        rowActions(node, resend, button);
      }
      invitationList.append(node);
    }
    if (!invitations.data.length) invitationList.append(item("No invitations yet."));
    byId("invitation-count").textContent = String(invitations.data.filter((entry) => entry.status === "pending").length);
    const memberList = byId("member-list"); memberList.replaceChildren();
    for (const member of members.data) {
      const node = accessRow(member.email, [
        { text: accessLabel(member.role) },
        { text: member.status, tone: statusTone(member.status) }
      ]);
      if (member.status === "active" && member.user_id !== session.user_id) {
        const button = destructiveButton("Revoke assignment", "Select again to revoke", () =>
          api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/members/${encodeURIComponent(member.user_id)}/roles/${encodeURIComponent(member.role)}`, { method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": csrf } }));
        rowActions(node, button);
      }
      memberList.append(node);
    }
    // Without this the panel renders as an empty card with no explanation of
    // whether it is still loading, broken, or genuinely empty.
    if (!members.data.length) memberList.append(item("No members yet. Invitations appear here once they are accepted."));
    byId("member-count").textContent = String(members.data.length);
    const grantList = byId("grant-list"); grantList.replaceChildren();
    for (const grant of grants.data) {
      const node = accessRow(grant.email, [
        { text: grant.permission === "owner" ? "Owner" : `Can ${grant.permission}`, tone: grant.permission === "owner" ? "success" : undefined }
      ]);
      if (grant.permission !== "owner" && grant.user_id !== session.user_id) {
        const permission = document.createElement("select");
        permission.setAttribute("aria-label", `Permission for ${grant.email}`);
        for (const value of ["view", "edit", "manage"]) {
          const option = document.createElement("option"); option.value = value; option.textContent = `Can ${value}`;
          permission.append(option);
        }
        permission.value = grant.permission;
        permission.addEventListener("change", async () => {
          permission.disabled = true;
          try {
            await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/access-grants/${encodeURIComponent(grant.user_id)}`, {
              method: "PATCH", headers: { "content-type": "application/json", "x-csrf-token": csrf },
              body: JSON.stringify({ permission: permission.value })
            });
            await load();
          } catch (error) { byId("status").textContent = window.SessionBuddyApi.message(error); permission.disabled = false; }
        });
        const revoke = destructiveButton("Revoke access", "Select again to revoke", () =>
          api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/access-grants/${encodeURIComponent(grant.user_id)}`, {
            method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": csrf }
          }));
        rowActions(node, permission, revoke);
      }
      grantList.append(node);
    }
    if (!grants.data.length) grantList.append(item("No resource access entries."));
    byId("grant-count").textContent = String(grants.data.length);
    renderOwnershipTransfer(session, selectedEvent, grants);
    byId("status").textContent = "Event access is up to date.";
  }
  byId("grant-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/access-grants`, {
        method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify(values)
      });
      form.reset(); await load();
      byId("status").textContent = "Event access granted.";
    } catch (error) { byId("status").textContent = window.SessionBuddyApi.message(error); byId("status").focus(); }
  });
  byId("invite-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify({ ...values, expires_in_days: Number(values.expires_in_days) }) });
      // The work is done: close the dialog so the sender sees the pending
      // invitation appear in the list instead of a stale, still-open form.
      form.reset(); byId("invite-dialog").close();
      await load();
      // Set the outcome AFTER load(), which ends by writing its own generic
      // status line — otherwise the success feedback vanishes instantly.
      byId("status").textContent = "Invitation created and emailed to the invitee.";
    } catch (error) { byId("status").textContent = window.SessionBuddyApi.message(error); byId("status").focus(); }
  });
  byId("ownership-transfer-form").addEventListener("input", resetOwnershipConfirmation);
  byId("ownership-transfer-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const values = Object.fromEntries(new FormData(form));
    transferContext = {
      email: String(values.email),
      reason: String(values.reason || "") || null,
      grant_previous_owner_manage: values.grant_previous_owner_manage === "on"
    };
    const previous = byId("ownership-panel").dataset.currentOwnerEmail;
    byId("ownership-confirmation-summary").textContent = transferContext.grant_previous_owner_manage
      ? `${transferContext.email} will own this event. ${previous} will keep Can manage access.`
      : `${transferContext.email} will own this event. ${previous} will lose event access unless it is granted again.`;
    byId("ownership-confirmation").hidden = false;
    byId("ownership-confirmation").focus();
  });
  byId("cancel-ownership-transfer").addEventListener("click", () => {
    resetOwnershipConfirmation();
    byId("ownership-transfer-form").elements.email.focus();
  });
  byId("confirm-ownership-transfer").addEventListener("click", async () => {
    if (!transferContext) return;
    const button = byId("confirm-ownership-transfer");
    const transfer = transferContext;
    button.disabled = true;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/ownership-transfers`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify(transfer)
      });
      byId("ownership-transfer-form").reset();
      resetOwnershipConfirmation();
      await load().catch(() => { byId("ownership-panel").hidden = true; });
      byId("status").textContent = `Event ownership transferred to ${transfer.email}. Event content was not changed.`;
    } catch (error) {
      const message = error.status === 409
        ? "Event ownership changed while you were reviewing. Reload the page and try again."
        : error.status === 404
        ? "The target account was not found, or you no longer have permission to transfer this event."
        : window.SessionBuddyApi.message(error);
      byId("status").textContent = message;
      byId("status").focus();
      button.disabled = false;
    }
  });
  const role = byId("invite-form").elements.role;
  const emailLabel = byId("invite-form").elements.email.closest("label");
  const speakerDetails = document.createElement("fieldset");
  speakerDetails.hidden = role.value !== "speaker";
  const legend = document.createElement("legend"); legend.textContent = "Speaker details";
  const fields = [
    ["display_name", "Name", true], ["job_title", "Job title", false], ["company", "Company", false]
  ];
  speakerDetails.append(legend);
  fields.forEach(([name, labelText, required]) => {
    const label = document.createElement("label"); label.textContent = labelText;
    const input = document.createElement("input"); input.name = name; input.maxLength = 200;
    input.required = required && role.value === "speaker"; label.append(input); speakerDetails.append(label);
  });
  emailLabel.after(speakerDetails);
  // The fieldset must track the CURRENT role at every entry point — role
  // change, form reset, and dialog open — or the form renders with stale
  // fields (e.g. speaker selected but the speaker details hidden).
  function syncSpeakerDetails() {
    speakerDetails.hidden = role.value !== "speaker";
    speakerDetails.querySelector('[name="display_name"]').required = role.value === "speaker";
  }
  role.addEventListener("change", syncSpeakerDetails);
  byId("invite-form").addEventListener("reset", () => requestAnimationFrame(syncSpeakerDetails));
  const inviteDialog = byId("invite-dialog");
  byId("open-invite").addEventListener("click", () => { syncSpeakerDetails(); inviteDialog.showModal(); });
  byId("close-invite").addEventListener("click", () => inviteDialog.close());
  byId("cancel-invite").addEventListener("click", () => inviteDialog.close());
  if (!eventId) { byId("status").textContent = "This event link is invalid."; return; }
  load().catch((error) => { if (!window.SessionBuddyApi.redirectIfSignedOut(error)) byId("status").textContent = window.SessionBuddyApi.message(error); });
})();

(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/access$/);
  const eventId = match ? decodeURIComponent(match[1]) : "";
  const byId = (id) => document.getElementById(id);
  let csrf = "";
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  function item(text) {
    const node = document.createElement("li");
    node.className = "organizer-access-list__item";
    const summary = document.createElement("span");
    summary.className = "organizer-access-list__summary";
    summary.textContent = text;
    node.append(summary);
    return node;
  }
  function destructiveButton(label, confirmLabel, action) {
    // Removing access is destructive: require a second, explicit click and
    // surface failures instead of silently doing nothing.
    const button = document.createElement("button");
    button.className = "secondary";
    button.textContent = label;
    button.addEventListener("click", async () => {
      if (button.dataset.confirming !== "true") {
        button.dataset.confirming = "true";
        button.textContent = confirmLabel;
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
        button.dataset.confirming = "false";
        button.textContent = label;
      }
    });
    button.addEventListener("blur", () => {
      button.dataset.confirming = "false";
      button.textContent = label;
    });
    return button;
  }
  async function load() {
    const session = await api("/api/v1/auth/session");
    csrf = session.csrf_token;
    // Offering the organization-admin role requires administering THIS
    // event's organization, not just any organization the account belongs to.
    const adminOrganizationIds = new Set((session.organization_access || [])
      .filter((entry) => (entry.roles || []).includes("organization_admin"))
      .map((entry) => entry.organization_id));
    let canInviteOrganizationAdmin = false;
    try {
      const event = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`);
      byId("access-event-name").textContent = event.name;
      canInviteOrganizationAdmin = adminOrganizationIds.has(event.organization_id);
    } catch (_) { /* the heading simply stays generic */ }
    byId("invite-form").elements.role.querySelector('option[value="organization_admin"]')
      ?.toggleAttribute("hidden", !canInviteOrganizationAdmin);
    const [invitations, members] = await Promise.all([
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`),
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/members`)
    ]);
    const invitationList = byId("invitation-list"); invitationList.replaceChildren();
    for (const invitation of invitations.data) {
      const node = item(`${invitation.email} · ${invitation.role.replaceAll("_", " ")} · ${invitation.status}`);
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
        node.append(" ", resend, " ", button);
      }
      invitationList.append(node);
    }
    if (!invitations.data.length) invitationList.append(item("No invitations yet."));
    byId("invitation-count").textContent = String(invitations.data.filter((entry) => entry.status === "pending").length);
    const memberList = byId("member-list"); memberList.replaceChildren();
    for (const member of members.data) {
      const node = item(`${member.email} · ${member.role.replaceAll("_", " ")} · ${member.status}`);
      if (member.status === "active" && member.user_id !== session.user_id) {
        const button = destructiveButton("Revoke role", "Select again to revoke", () =>
          api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/members/${encodeURIComponent(member.user_id)}/roles/${encodeURIComponent(member.role)}`, { method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": csrf } }));
        node.append(" ", button);
      }
      memberList.append(node);
    }
    byId("member-count").textContent = String(members.data.length);
    byId("status").textContent = "Event access is up to date.";
  }
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

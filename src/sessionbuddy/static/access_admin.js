(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/access$/);
  const eventId = match ? decodeURIComponent(match[1]) : "";
  const byId = (id) => document.getElementById(id);
  let csrf = "";
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  function item(text) { const node = document.createElement("li"); node.textContent = text; return node; }
  function showInvitationLink(invitation) {
    const result = byId("invite-result");
    const input = byId("invite-url");
    if (!invitation.accept_url) { result.hidden = true; input.value = ""; return; }
    input.value = invitation.accept_url;
    result.hidden = false;
  }
  async function load() {
    const session = await api("/api/v1/auth/session");
    csrf = session.csrf_token;
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
            const refreshed = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}/resend`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: "{}" });
            showInvitationLink(refreshed);
            byId("status").textContent = `A new link was sent to ${invitation.email}.`;
          } catch (error) { byId("status").textContent = window.SessionBuddyApi.message(error); }
          finally { resend.disabled = false; }
        });
        const button = document.createElement("button"); button.className = "secondary"; button.textContent = "Revoke";
        button.addEventListener("click", async () => { await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}`, { method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": csrf } }); await load(); });
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
        const button = document.createElement("button"); button.className = "secondary"; button.textContent = "Revoke role";
        button.addEventListener("click", async () => { await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/members/${encodeURIComponent(member.user_id)}/roles/${encodeURIComponent(member.role)}`, { method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": csrf } }); await load(); });
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
      const invitation = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify({ ...values, expires_in_days: Number(values.expires_in_days) }) });
      form.reset(); showInvitationLink(invitation); byId("status").textContent = "Invitation created and queued for delivery."; await load();
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
  role.addEventListener("change", () => {
    speakerDetails.hidden = role.value !== "speaker";
    speakerDetails.querySelector('[name="display_name"]').required = role.value === "speaker";
  });
  byId("copy-invite").addEventListener("click", async () => {
    const input = byId("invite-url");
    try { await navigator.clipboard.writeText(input.value); byId("status").textContent = "Invitation link copied."; }
    catch (_) { input.focus(); input.select(); byId("status").textContent = "Copy the selected invitation link."; }
  });
  const inviteDialog = byId("invite-dialog");
  byId("open-invite").addEventListener("click", () => inviteDialog.showModal());
  byId("close-invite").addEventListener("click", () => inviteDialog.close());
  byId("cancel-invite").addEventListener("click", () => inviteDialog.close());
  if (!eventId) { byId("status").textContent = "This event link is invalid."; return; }
  load().catch((error) => { if (!window.SessionBuddyApi.redirectIfSignedOut(error)) byId("status").textContent = window.SessionBuddyApi.message(error); });
})();

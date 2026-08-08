(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/access$/);
  const eventId = match ? decodeURIComponent(match[1]) : "";
  const byId = (id) => document.getElementById(id);
  let csrf = "";
  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const body = response.status === 204 ? null : await response.json();
    if (!response.ok) { const error = new Error(body?.error?.message || `Request failed (${response.status})`); error.status = response.status; throw error; }
    return body;
  }
  function item(text) { const node = document.createElement("li"); node.textContent = text; return node; }
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
        const button = document.createElement("button"); button.className = "secondary"; button.textContent = "Revoke";
        button.addEventListener("click", async () => { await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}`, { method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": csrf } }); await load(); });
        node.append(" ", button);
      }
      invitationList.append(node);
    }
    if (!invitations.data.length) invitationList.append(item("No invitations yet."));
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
    byId("status").textContent = "Event access is up to date.";
  }
  byId("invite-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": csrf }, body: JSON.stringify({ ...values, expires_in_days: Number(values.expires_in_days) }) });
      form.reset(); byId("status").textContent = "Invitation created and queued for delivery."; await load();
    } catch (error) { byId("status").textContent = error.message; byId("status").focus(); }
  });
  if (!eventId) { byId("status").textContent = "This event link is invalid."; return; }
  load().catch((error) => { if (error.status === 401) location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`); else byId("status").textContent = error.message; });
})();

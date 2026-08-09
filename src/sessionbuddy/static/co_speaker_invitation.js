(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/co-speaker-invitations\/([^/]+)$/);
  let token = "";
  try { token = match ? decodeURIComponent(match[1]) : ""; } catch (_) { token = ""; }
  const endpoint = `/api/v1/co-speaker-invitations/${encodeURIComponent(token)}`;
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }

  async function respond(response) {
    const actions = byId("invitation-actions");
    for (const button of actions.querySelectorAll("button")) button.disabled = true;
    try {
      const invitation = await api(`${endpoint}/${response}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: "{}"
      });
      actions.hidden = true;
      setStatus(response === "accept"
        ? `Invitation accepted. You are now a co-speaker for “${invitation.proposal_title}”.`
        : `Invitation declined for “${invitation.proposal_title}”.`);
      byId("status").focus();
    } catch (error) {
      for (const button of actions.querySelectorAll("button")) button.disabled = false;
      setStatus(window.SessionBuddyApi.message(error, "The invitation could not be updated."), true);
      byId("status").focus();
    }
  }

  async function load() {
    if (!token) throw new Error("This invitation link is invalid.");
    const invitation = await api(endpoint);
    byId("invited-name").textContent = invitation.display_name;
    byId("proposal-title").textContent = invitation.proposal_title;
    byId("event-name").textContent = invitation.event_name;
    byId("invitation-expiry").textContent = invitation.expires_at_ms
      ? `This invitation expires ${new Date(invitation.expires_at_ms).toLocaleString()}.`
      : "";
    byId("invitation-details").hidden = false;
    setStatus("Choose whether to join this proposal.");
  }

  byId("accept-invitation").addEventListener("click", () => respond("accept"));
  byId("decline-invitation").addEventListener("click", () => respond("decline"));
  load().catch((error) => {
    setStatus(error.status === 404
      ? "This invitation is invalid, expired, or has already been used."
      : window.SessionBuddyApi.message(error, "The invitation could not be loaded."), true);
    byId("status").focus();
  });
})();

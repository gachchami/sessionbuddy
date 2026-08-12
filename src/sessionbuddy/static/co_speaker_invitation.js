(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/co-speaker-invitations\/([^/]+)$/);
  let token = "";
  try { token = match ? decodeURIComponent(match[1]) : ""; } catch (_) { token = ""; }
  const endpoint = `/api/v1/co-speaker-invitations/${encodeURIComponent(token)}`;
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function viewerLocalTime(value) {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "your device time zone";
    return `${new Date(value).toLocaleString()} (your local time: ${zone})`;
  }

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
        ? `Invitation accepted. You joined “${invitation.proposal_title}” as ${invitation.role_label}.`
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
    byId("invited-role").textContent = invitation.role_label;
    byId("proposal-title").textContent = invitation.proposal_title;
    byId("event-name").textContent = invitation.event_name;
    byId("invitation-expiry").textContent = invitation.expires_at_ms
      ? `This invitation expires ${viewerLocalTime(invitation.expires_at_ms)}.`
      : "";
    byId("invitation-details").hidden = false;
    setStatus("Choose whether to join this proposal.");
  }

  byId("accept-invitation").addEventListener("click", () => respond("accept"));
  const declineDialog = byId("confirm-decline");
  const declineButton = byId("decline-invitation");
  declineButton.addEventListener("click", () => declineDialog.showModal());
  byId("cancel-decline").addEventListener("click", () => declineDialog.close("cancel"));
  declineDialog.addEventListener("close", () => declineButton.focus());
  byId("confirm-decline-button").addEventListener("click", () => {
    declineDialog.close("confirmed");
    respond("decline");
  });
  load().catch((error) => {
    setStatus(error.status === 404
      ? "This invitation is invalid, expired, or has already been used."
      : window.SessionBuddyApi.message(error, "The invitation could not be loaded."), true);
    byId("status").focus();
  });
})();

(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/(?:reviewers|access)$/);
  const eventId = match ? decodeURIComponent(match[1]) : "";
  const byId = (id) => document.getElementById(id);
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  let csrf = "";

  const statusTone = (status) => status === "accepted" ? "success"
    : ["revoked", "expired"].includes(status) ? "overdue" : "";

  function emptyRow(message) {
    const row = document.createElement("p");
    row.className = "empty";
    row.textContent = message;
    return row;
  }

  function invitationRow(invitation) {
    const row = document.createElement("div");
    row.className = "people-table-row";
    row.setAttribute("role", "row");
    const person = document.createElement("div");
    person.className = "people-person-cell";
    person.setAttribute("role", "cell");
    const displayName = invitation.display_name || invitation.email.split("@", 1)[0];
    const initials = document.createElement("span");
    initials.className = "people-monogram";
    initials.setAttribute("aria-hidden", "true");
    initials.textContent = displayName.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "?";
    const identity = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = displayName;
    const detail = document.createElement("small");
    detail.textContent = [invitation.job_title, invitation.company].filter(Boolean).join(" · ") || "Reviewer";
    identity.append(name, detail);
    person.append(initials, identity);
    const email = document.createElement("span");
    email.className = "people-email";
    email.setAttribute("role", "cell");
    email.textContent = invitation.email;
    const status = document.createElement("span");
    status.setAttribute("role", "cell");
    status.className = `state-badge ${statusTone(invitation.status)}`.trim();
    status.textContent = invitation.status === "accepted" ? "Eligible" : "Invitation pending";
    const actions = document.createElement("div");
    actions.setAttribute("role", "cell");
    actions.className = "actions reviewer-row-actions";
    if (invitation.status === "pending") {
      const resend = document.createElement("button");
      resend.type = "button";
      resend.className = "secondary";
      resend.textContent = "Send again";
      resend.addEventListener("click", async () => {
        resend.disabled = true;
        try {
          await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}/resend`, {
            method: "POST",
            headers: { "content-type": "application/json", "x-csrf-token": csrf },
            body: "{}"
          });
          byId("status").textContent = `A new invitation was sent to ${invitation.email}.`;
        } catch (error) {
          byId("status").textContent = window.SessionBuddyApi.message(error);
          byId("status").focus();
        } finally { resend.disabled = false; }
      });
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "secondary";
      revoke.textContent = "Revoke";
      revoke.dataset.confirming = "false";
      const reset = () => {
        revoke.dataset.confirming = "false";
        revoke.textContent = "Revoke";
        revoke.removeAttribute("aria-label");
      };
      revoke.addEventListener("blur", reset);
      revoke.addEventListener("keydown", (event) => { if (event.key === "Escape") reset(); });
      revoke.addEventListener("click", async () => {
        if (revoke.dataset.confirming !== "true") {
          revoke.dataset.confirming = "true";
          revoke.textContent = "Confirm revoke";
          revoke.setAttribute("aria-label", `Confirm revoke reviewer eligibility for ${invitation.email}`);
          return;
        }
        revoke.disabled = true;
        try {
          await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}`, {
            method: "DELETE",
            headers: { "content-type": "application/json", "x-csrf-token": csrf }
          });
          await load();
          byId("status").textContent = invitation.status === "accepted"
            ? `Reviewer eligibility revoked for ${invitation.email}.`
            : `Invitation revoked for ${invitation.email}.`;
        } catch (error) {
          byId("status").textContent = window.SessionBuddyApi.message(error);
          byId("status").focus();
          revoke.disabled = false;
          reset();
        }
      });
      actions.append(resend, revoke);
    } else {
      const revoke = document.createElement("button");
      revoke.type = "button";
      revoke.className = "secondary";
      revoke.textContent = "Revoke";
      revoke.dataset.confirming = "false";
      const reset = () => { revoke.dataset.confirming = "false"; revoke.textContent = "Revoke"; revoke.removeAttribute("aria-label"); };
      revoke.addEventListener("blur", reset);
      revoke.addEventListener("keydown", (event) => { if (event.key === "Escape") reset(); });
      revoke.addEventListener("click", async () => {
        if (revoke.dataset.confirming !== "true") {
          revoke.dataset.confirming = "true";
          revoke.textContent = "Confirm revoke";
          revoke.setAttribute("aria-label", `Confirm revoke reviewer eligibility for ${invitation.email}`);
          return;
        }
        revoke.disabled = true;
        try {
          await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}`, { method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": csrf } });
          await load();
          byId("status").textContent = `Reviewer eligibility revoked for ${invitation.email}.`;
        } catch (error) {
          byId("status").textContent = window.SessionBuddyApi.message(error);
          byId("status").focus();
          revoke.disabled = false;
          reset();
        }
      });
      actions.append(revoke);
    }
    row.append(person, email, status, actions);
    return row;
  }

  async function load() {
    const [session, selectedEvent, invitations] = await Promise.all([
      api("/api/v1/auth/session"),
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`),
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`)
    ]);
    csrf = session.csrf_token;
    byId("access-event-name").textContent = selectedEvent.name;
    const reviewerInvitations = invitations.data.filter((invitation) => invitation.role === "evaluator");
    const visible = reviewerInvitations.filter((invitation) => ["accepted", "pending"].includes(invitation.status));
    visible.sort((left, right) => Number(right.status === "accepted") - Number(left.status === "accepted") || left.email.localeCompare(right.email));
    byId("reviewer-list").replaceChildren(...(visible.length
      ? visible.map(invitationRow)
      : [emptyRow("No reviewers yet. Invite someone to review proposals for this event.")]));
    byId("reviewer-count").textContent = String(visible.length);
    byId("status").textContent = "Reviewer eligibility is up to date.";
  }

  byId("reviewer-search-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const email = form.elements.email.value.trim();
    const result = byId("reviewer-search-result");
    result.textContent = "Searching…";
    try {
      const reviewers = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/evaluators?email=${encodeURIComponent(email)}`);
      if (reviewers.data.length) {
        result.textContent = `${reviewers.data[0].display_name} is eligible for this event and can be assigned to a round.`;
        return;
      }
      result.replaceChildren(document.createTextNode("No eligible reviewer found. "));
      const invite = document.createElement("button");
      invite.type = "button";
      invite.className = "secondary";
      invite.textContent = `Invite ${email}`;
      invite.addEventListener("click", () => {
        byId("invite-form").elements.email.value = email;
        byId("invite-dialog").showModal();
        byId("invite-form").elements.email.focus();
      });
      result.append(invite);
    } catch (error) {
      result.textContent = window.SessionBuddyApi.message(error);
    }
  });
  byId("invite-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    const values = Object.fromEntries(new FormData(form));
    button.disabled = true;
    try {
      await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ ...values, expires_in_days: Number(values.expires_in_days) })
      });
      form.reset();
      byId("invite-dialog").close();
      await load();
      byId("status").textContent = "Reviewer invitation created and emailed.";
    } catch (error) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
      byId("status").focus();
    } finally { button.disabled = false; }
  });

  const dialog = byId("invite-dialog");
  byId("open-invite").addEventListener("click", () => dialog.showModal());
  byId("close-invite").addEventListener("click", () => dialog.close());
  byId("cancel-invite").addEventListener("click", () => dialog.close());
  if (!eventId) {
    byId("status").textContent = "This event link is invalid.";
    return;
  }
  load().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      byId("status").textContent = window.SessionBuddyApi.message(error);
    }
  });
})();

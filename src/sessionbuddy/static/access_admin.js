(() => {
  "use strict";
  const match = location.pathname.match(/^\/admin\/events\/([^/]+)\/(?:reviewers|access)$/);
  const eventId = match ? decodeURIComponent(match[1]) : "";
  const byId = (id) => document.getElementById(id);
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  let csrf = "";
  let eventArchived = false;

  function showAccessLink(accessUrl, email) {
    byId("invitation-access-url").value = accessUrl;
    byId("invitation-access-title").textContent = `Access link for ${email}`;
    byId("invitation-access").hidden = false;
  }

  const statusTone = (status) => status === "accepted" ? "success"
    : ["revoked", "expired"].includes(status) ? "overdue" : "";

  function emptyRow(message) {
    const row = document.createElement("div");
    row.className = "empty";
    row.setAttribute("role", "row");
    const cell = document.createElement("span");
    cell.setAttribute("role", "cell");
    cell.textContent = message;
    row.append(cell);
    return row;
  }

  function invitationRow(invitation) {
    const row = document.createElement("div");
    row.className = "people-table-row";
    row.setAttribute("role", "row");
    const person = document.createElement("div");
    person.className = "people-person-cell";
    person.setAttribute("role", "cell");
    const displayName = invitation.display_name || invitation.email;
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
    email.dataset.label = "Email";
    email.textContent = invitation.email;
    const status = document.createElement("span");
    status.setAttribute("role", "cell");
    status.dataset.label = "Eligibility";
    status.className = `state-badge ${statusTone(invitation.status)}`.trim();
    status.textContent = invitation.status === "accepted" ? "Eligible" : "Invitation pending";
    const actions = document.createElement("div");
    actions.setAttribute("role", "cell");
    actions.dataset.label = "Actions";
    actions.className = "actions reviewer-row-actions";
    if (invitation.status === "pending") {
      const resend = document.createElement("button");
      resend.type = "button";
      resend.className = "secondary";
      resend.textContent = "Send again";
      resend.addEventListener("click", async () => {
        resend.disabled = true;
        try {
          const issued = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}/resend`, {
            method: "POST",
            headers: { "content-type": "application/json", "x-csrf-token": csrf },
            body: "{}"
          });
          showAccessLink(issued.access_url, invitation.email);
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
      actions.append(...(eventArchived ? [revoke] : [resend, revoke]));
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
    const [sessionResult, selectedEventResult, invitationsResult] = await Promise.allSettled([
      api("/api/v1/auth/session"),
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}`),
      api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`),
    ]);
    if (sessionResult.status === "rejected") throw sessionResult.reason;
    if (selectedEventResult.status === "rejected") {
      const error = selectedEventResult.reason;
      const recoveryScope = window.SessionBuddyApi.recoveryScope.event(eventId);
      if (window.SessionBuddyApi.redirectIfWorkspaceUnavailable(error, recoveryScope)
          || window.SessionBuddyApi.redirectIfDocumentAccessChanged(error, recoveryScope)) return;
      throw error;
    }
    if (invitationsResult.status === "rejected") throw invitationsResult.reason;
    const session = sessionResult.value;
    const selectedEvent = selectedEventResult.value;
    const invitations = invitationsResult.value;
    csrf = session.csrf_token;
    eventArchived = selectedEvent.status === "archived";
    byId("access-event-name").textContent = selectedEvent.name;
    byId("open-invite").hidden = eventArchived;
    byId("reviewer-search-form").hidden = eventArchived;
    const reviewerInvitations = invitations.data.filter((invitation) => invitation.role === "evaluator");
    const visible = reviewerInvitations.filter((invitation) => ["accepted", "pending"].includes(invitation.status));
    visible.sort((left, right) => Number(right.status === "accepted") - Number(left.status === "accepted") || left.email.localeCompare(right.email));
    byId("reviewer-list").replaceChildren(...(visible.length
      ? visible.map(invitationRow)
      : [emptyRow("No reviewers yet.")]));
    byId("reviewer-count").textContent = `${visible.length} ${visible.length === 1 ? "reviewer" : "reviewers"}`;
    byId("status").textContent = eventArchived
      ? "This event is archived. Existing reviewer access can be revoked, but new invitations cannot be sent."
      : "";
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
      // An outstanding invitation is not "no reviewer found". Offering Invite here
      // contradicted the roster below, which already showed this person as
      // "Invitation pending", and re-inviting only reissues the same pending row.
      const pending = (reviewers.pending || [])[0];
      // The lookup reports state only -- it is reachable with an event `edit` grant, so it
      // must not hand out roster facts. This page holds `manage`, so it reads the name and
      // the invitation id from the invitation list itself, freshly, rather than from the
      // lookup response.
      let invitation = null;
      if (pending) {
        try {
          const invitations = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`);
          invitation = invitations.data.find((item) => item.role === "evaluator"
            && item.email.toLowerCase() === pending.email.toLowerCase()) || null;
        } catch (error) { invitation = null; }
      }
      const who = (invitation && invitation.display_name) || pending?.email || email;
      if (pending && pending.expired) {
        // Resend only accepts an unexpired pending invitation -- see resend_invitation,
        // which requires `expires_at_ms>?4` and 404s otherwise. An expired invitation
        // needs a fresh one, which is what the roster below does too: invitationRow
        // offers "Send again" only while status is still `pending`.
        result.replaceChildren(document.createTextNode(`${who} was invited, but the invitation expired before it was accepted, so they cannot be assigned to a round. Send a new invitation. `));
        const reinvite = document.createElement("button");
        reinvite.type = "button";
        reinvite.className = "secondary";
        reinvite.textContent = `Invite ${pending.email} again`;
        reinvite.addEventListener("click", () => {
          byId("invite-form").elements.email.value = pending.email;
          openInviteDialog();
          byId("invite-form").elements.email.focus();
        });
        result.append(reinvite);
        return;
      }
      if (pending && invitation) {
        result.replaceChildren(document.createTextNode(`${who} was invited but has not accepted yet, so they cannot be assigned to a round. Acceptance happens when they open their invitation link and sign in. `));
        const resend = document.createElement("button");
        resend.type = "button";
        resend.className = "secondary";
        resend.textContent = "Send again";
        resend.addEventListener("click", async () => {
          resend.disabled = true;
          try {
            const issued = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations/${encodeURIComponent(invitation.id)}/resend`, {
              method: "POST",
              headers: { "content-type": "application/json", "x-csrf-token": csrf },
              body: "{}"
            });
            showAccessLink(issued.access_url, pending.email);
            byId("status").textContent = `A new invitation was sent to ${pending.email}.`;
          } catch (error) {
            byId("status").textContent = window.SessionBuddyApi.message(error);
          } finally { resend.disabled = false; }
        });
        result.append(resend);
        return;
      }
      if (pending) {
        // Outstanding, but the roster did not yield the invitation -- revoked or replaced
        // between the two reads. Say the true thing and offer the flow that still works.
        result.replaceChildren(document.createTextNode(`${who} has an invitation that has not been accepted, so they cannot be assigned to a round yet. `));
      } else {
        result.replaceChildren(document.createTextNode("No eligible reviewer found. "));
      }
      const invite = document.createElement("button");
      invite.type = "button";
      invite.className = "secondary";
      invite.textContent = `Invite ${email}`;
      invite.addEventListener("click", () => {
        byId("invite-form").elements.email.value = email;
        openInviteDialog();
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
    const dialogStatus = byId("invite-status");
    const values = Object.fromEntries(new FormData(form));
    button.disabled = true;
    button.textContent = "Sending…";
    dialogStatus.hidden = false;
    dialogStatus.textContent = "Sending reviewer invitation…";
    try {
      const issued = await api(`/api/v1/admin/events/${encodeURIComponent(eventId)}/invitations`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": csrf },
        body: JSON.stringify({ ...values, expires_in_days: Number(values.expires_in_days) })
      });
      form.reset();
      byId("invite-dialog").close();
      showAccessLink(issued.access_url, issued.email);
      await load();
      byId("status").textContent = "Reviewer invitation created and emailed. The access link is ready to copy.";
    } catch (error) {
      dialogStatus.textContent = window.SessionBuddyApi.message(error);
      dialogStatus.focus();
    } finally {
      button.disabled = false;
      button.textContent = "Send invitation";
    }
  });

  function openInviteDialog() {
    byId("invite-status").hidden = true;
    byId("invite-status").textContent = "";
    byId("invite-dialog").showModal();
  }
  const dialog = byId("invite-dialog");
  byId("open-invite").addEventListener("click", openInviteDialog);
  byId("close-invite").addEventListener("click", () => dialog.close());
  byId("cancel-invite").addEventListener("click", () => dialog.close());
  byId("copy-invitation-access").addEventListener("click", async () => {
    const input = byId("invitation-access-url");
    try {
      await navigator.clipboard.writeText(input.value);
      byId("status").textContent = "Reviewer access link copied.";
    } catch (_) {
      input.focus();
      input.select();
      byId("status").textContent = "Copy was unavailable. The access link is selected for manual copying.";
    }
  });
  if (!eventId) {
    byId("status").textContent = "This event link is invalid.";
    return;
  }
  load().catch((error) => {
    if (window.SessionBuddyApi.redirectIfSignedOut(error)) return;
    byId("status").textContent = window.SessionBuddyApi.message(error);
  });
})();

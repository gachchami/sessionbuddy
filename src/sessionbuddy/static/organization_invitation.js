(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const api = window.SessionBuddyApi;
  const token = new URLSearchParams(location.hash.slice(1)).get("token");
  history.replaceState(null, "", location.pathname + location.search);
  let session = null;
  function status(text, kind = "") { byId("invitation-status").textContent = text; byId("invitation-status").dataset.kind = kind; }
  function path(title, description) {
    const section = document.createElement("section"); section.className = "invitation-path";
    const heading = document.createElement("h2"); heading.textContent = title;
    const copy = document.createElement("p"); copy.textContent = description;
    section.append(heading, copy); byId("invitation-actions").append(section); return section;
  }
  function button(label, action, confirm = false, parent = byId("invitation-actions")) {
    const element = document.createElement("button");
    element.className = confirm ? "button secondary" : "button";
    element.type = "button"; element.textContent = label;
    let confirmed = false;
    element.addEventListener("click", async () => {
      if (confirm && !confirmed) { confirmed = true; element.textContent = "Confirm decline"; return; }
      element.disabled = true; element.textContent = "Please wait…";
      try { await action(); } catch (error) { status(api.message(error), "error"); }
      finally { element.disabled = false; confirmed = false; element.textContent = label; }
    });
    element.addEventListener("blur", () => { confirmed = false; element.textContent = label; });
    element.addEventListener("keydown", (event) => { if (event.key === "Escape") { confirmed = false; element.textContent = label; } });
    parent.append(element);
  }
  async function start() {
    session = null;
    byId("invitation-actions").replaceChildren();
    byId("invitation-title").textContent = "Organization invitation";
    byId("invitation-description").textContent = "";
    byId("invitation-expiry").hidden = true;
    try { session = await api.request("/api/v1/auth/session"); }
    catch (error) { if (error.status !== 401) throw error; }
    byId("invitation-footer").hidden = true;
    let invitation;
    if (token) {
      try {
        invitation = await api.request("/api/v1/organization-admin-invitations/resolve", {
          method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({token})
        });
      } catch (error) {
        if (error.status !== 404) throw error;
        status("This invitation link is no longer valid. Ask the admin who invited you for a new link.", "error"); return;
      }
    } else {
      const id = new URLSearchParams(location.search).get("invitation");
      if (!session || !id) { status("Open the invitation link from your email to continue."); return; }
      const invitations = await api.request("/api/v1/account/invitations");
      invitation = invitations.pending_organization_invitations.find((item) => item.id === id);
      if (!invitation) { status("This invitation is no longer pending. Check your Account page."); return; }
      invitation.identity_matches = true;
    }
    if (invitation.identity_matches === false) {
      byId("invitation-title").textContent = "Switch accounts to continue";
      status("Use the account this invitation was sent to.");
      button("Switch account", async () => {
        try {
          await api.request("/api/v1/session/logout", {
            method: "POST", headers: {"content-type": "application/json", "x-csrf-token": session.csrf_token}, body: "{}"
          });
        } catch (error) { if (error.status !== 401) throw error; }
        api.prepareForSessionReplacement();
        // Keep the lookup token in this page's memory, never storage or a query string.
        await start();
        byId("invitation-title").tabIndex = -1;
        byId("invitation-title").focus();
      });
      return;
    }
    byId("invitation-title").textContent = `Join ${invitation.organization_name}`;
    byId("invitation-description").textContent = "You've been invited as an Admin to manage this organization and all its events.";
    byId("invitation-expiry").textContent = `Invitation expires ${new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(invitation.expires_at_ms))}.`;
    byId("invitation-expiry").hidden = false;
    if (invitation.status !== "pending") { status(`This invitation is ${invitation.status}.`); return; }
    if (invitation.needs_reissue) { status("The admin who invited you no longer manages this organization. Ask a current admin to reissue this invitation."); return; }
    if (invitation.identity_matches === true) {
      status("Your identity is verified. Accepting gives you administration access to this organization.");
      for (const action of ["accept", "decline"]) {
        const key = crypto.randomUUID();
        button(action === "accept" ? "Accept invitation" : "Decline", async () => {
          const result = await api.request(`/api/v1/account/organization-invitations/${encodeURIComponent(invitation.id)}/${action}`, {
            method:"POST", headers:{"content-type":"application/json", "x-csrf-token":session.csrf_token, "Idempotency-Key":key}, body:"{}"
          });
          byId("invitation-actions").replaceChildren(); status(`Invitation ${result.status}.`);
          if (action === "accept") { api.prepareForSessionReplacement(); location.href=result.workspace_path; }
        }, action === "decline");
      }
    } else {
      status("");
      const firstTime = path("First time here?", "Use a verification link sent to your email to create your account. After that, you can review and accept this invitation.");
      button("Email me a verification link", async () => {
        await api.request("/api/v1/organization-admin-invitations/verification", {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify({token})});
        firstTime.replaceChildren();
        const heading = document.createElement("h2"); heading.textContent = "Check your email";
        const copy = document.createElement("p"); copy.textContent = "We requested your verification link. Open it within 15 minutes to continue. No admin access is granted until you accept the invitation.";
        firstTime.append(heading, copy);
        status("Verification link requested.");
      }, false, firstTime);
      const returning = path("Already have an account?", "Sign in with your password using the email address that received this invitation. You can use email verification instead if you prefer.");
      const signIn = document.createElement("a");
      signIn.className = "button secondary";
      signIn.textContent = "Sign in with password";
      signIn.href = `/sign-in?redirect=${encodeURIComponent("/organization-admin-invitations?invitation="+invitation.id)}`;
      returning.append(signIn);
    }
  }
  start().catch((error) => status(api.message(error), "error"))
    .finally(() => document.querySelector(".invitation-card").setAttribute("aria-busy", "false"));
})();

(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);
  let session;

  function showStatus(message, kind = "", focus = false) {
    const status = byId("status");
    status.className = `status status-compact${kind ? ` ${kind}` : ""}`;
    status.textContent = message;
    if (focus) status.focus();
  }

  function accessMessage(error, fallback) {
    if (error.status === 404) return "No active SessionBuddy account uses that email. Ask them to sign in once, then try again.";
    if (error.status === 409) return "Owner access cannot be changed or revoked here.";
    return window.SessionBuddyApi.message(error) || fallback;
  }

  function accessSection(organizationId) {
    return [...byId("organization-settings-list").querySelectorAll("[data-organization-access-id]")]
      .find((node) => node.dataset.organizationAccessId === organizationId);
  }

  function grantRow(organizationId, grant) {
    const row = document.createElement("li");
    row.className = "organizer-access-list__item";
    const summary = document.createElement("span");
    summary.className = "organizer-access-list__summary";
    const email = document.createElement("strong");
    email.textContent = grant.email;
    summary.append(email, ` · ${grant.permission === "owner" ? "Owner" : "Admin"}`);
    row.append(summary);
    if (grant.permission === "owner") {
      const note = document.createElement("small");
      note.textContent = "Ownership cannot be revoked from this access list.";
      row.append(note);
      return row;
    }
    const revoke = document.createElement("button");
    revoke.type = "button";
    revoke.className = "secondary";
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

  async function loadGrants(organizationId) {
    const section = accessSection(organizationId);
    const list = section.querySelector(".organization-grant-list");
    const status = section.querySelector(".organization-access-status");
    const grants = await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/access-grants`);
    const organizers = grants.data.filter((grant) => ["owner", "manage"].includes(grant.permission));
    list.replaceChildren(...organizers.map((grant) => grantRow(organizationId, grant)));
    status.textContent = `${organizers.length} active ${organizers.length === 1 ? "organizer" : "organizers"}.`;
  }

  function organizationCard(organization, isOwner) {
    const card = document.createElement("article");
    card.className = "card organizer-panel organizer-organization-card";
    const nameForm = document.createElement("form");
    nameForm.className = "organization-name-form";
    nameForm.dataset.organizationId = organization.id;
    nameForm.dataset.version = String(organization.version);
    nameForm.innerHTML = `<h3></h3><label>Organization name<input name="name" maxlength="200" required></label><button type="submit">Save organization</button>`;
    nameForm.querySelector("h3").textContent = organization.name;
    nameForm.elements.name.value = organization.name;

    const access = document.createElement("section");
    access.className = "organization-access-manager";
    access.dataset.organizationAccessId = organization.id;
    access.innerHTML = `<div class="section-heading"><div><h4>Organizers</h4><p class="help">The owner and admins can manage every event in this organization.</p></div></div><form class="organization-grant-form" data-organization-grant-create="${organization.id}"><label>Account email<input name="email" type="email" autocomplete="email" maxlength="320" required></label><button type="submit">Add admin</button></form><p class="help organization-access-status" role="status" aria-live="polite"></p><ul class="item-list organizer-access-list organization-grant-list organization-admin-grid"><li>Loading organization access…</li></ul>`;
    card.append(nameForm, access);
    if (isOwner) {
      const transfer = document.createElement("form");
      transfer.className = "organization-owner-transfer-form";
      transfer.dataset.organizationOwnerTransfer = organization.id;
      transfer.innerHTML = `<h4>Transfer ownership</h4><p class="help">Only the current owner can transfer ownership, and only to an existing Admin. You will remain an Admin.</p><label>New owner’s admin email<input name="email" type="email" autocomplete="email" required></label><button class="danger" type="submit">Review ownership transfer</button>`;
      card.append(transfer);
    }
    return card;
  }

  async function loadActivity(organizationId) {
    const activities = (await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/activities`)).data;
    const rows = activities.map((activity) => {
      const row = document.createElement("li");
      row.className = "organization-activity-item";
      const title = document.createElement("strong");
      title.textContent = window.SessionBuddyActivityFormat.sentence(activity);
      const detail = document.createElement("span");
      detail.textContent = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(activity.occurred_at_ms));
      row.append(title, detail);
      return row;
    });
    if (!rows.length) {
      const empty = document.createElement("li");
      empty.className = "help";
      empty.textContent = "No organization activity has been projected yet.";
      rows.push(empty);
    }
    byId("organization-activity-list").replaceChildren(...rows);
    byId("organization-activity").hidden = false;
  }

  async function load() {
    session = await api("/api/v1/auth/session");
    const manageable = new Set((session.organization_access || []).filter((item) => (
      (item.permissions || []).some((permission) => ["owner", "manage"].includes(permission))
    )).map((item) => item.organization_id));
    const owned = new Set((session.organization_access || []).filter((item) => (
      (item.permissions || []).includes("owner")
    )).map((item) => item.organization_id));
    const organizations = (await api("/api/v1/admin/organizations")).data.filter((organization) => manageable.has(organization.id));
    if (!organizations.length) throw new Error("No manageable organization is available to this account.");
    byId("organization-settings-list").replaceChildren(...organizations.map((organization) => organizationCard(organization, owned.has(organization.id))));
    byId("organization-settings").hidden = false;
    await Promise.all(organizations.flatMap((organization) => [loadGrants(organization.id), loadActivity(organization.id)]));
  }

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
        form.dataset.version = String(organization.version); form.querySelector("h3").textContent = organization.name; showStatus("Organization saved.", "success");
      } catch (error) { showStatus(window.SessionBuddyApi.message(error), "error", true); }
      finally { button.disabled = false; }
      return;
    }
    if (form.matches("[data-organization-grant-create]")) {
      button.disabled = true;
      const email = form.elements.email.value.trim();
      try {
        await api(`/api/v1/admin/organizations/${encodeURIComponent(form.dataset.organizationGrantCreate)}/access-grants`, { method: "POST", headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token }, body: JSON.stringify({ email }) });
        form.reset(); await loadGrants(form.dataset.organizationGrantCreate); showStatus(`Organization organizer access granted to ${email}.`, "success");
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
      } catch (error) { showStatus(window.SessionBuddyApi.message(error), "error", true); button.disabled = false; }
    }
  });

  load().catch((error) => showStatus(window.SessionBuddyApi.message(error, "Organization settings could not be loaded."), "error", true));
})();

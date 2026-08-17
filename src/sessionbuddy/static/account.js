(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const labels = {
    organizer: "Organizer",
    reviewer: "Reviewer",
    speaker: "Speaker",
    owner: "Owner",
    view: "Can view",
    edit: "Can edit",
    manage: "Can manage"
  };

  let session;
  let version;
  let selectedHeadshot;
  let selectedHeadshotContentType;
  let inferredNameDraft = false;
  const query = new URLSearchParams(location.search);
  const organizationMode = location.pathname === "/admin/organization";
  if (!organizationMode) {
    document.querySelector(".account-profile-panel").hidden = false;
    document.querySelector(".organizer-section--account-access").hidden = false;
  }
  const onboarding = query.get("onboarding") === "1";
  const nextPath = query.get("next") || "";

  function validTimeZone(value) {
    if (!value) return true;
    try { new Intl.DateTimeFormat(undefined, { timeZone: value }).format(); return true; }
    catch (_) { return false; }
  }

  function validProfileUrl(value) {
    if (!value) return true;
    try {
      const url = new URL(value);
      return url.protocol === "https:" && Boolean(url.hostname) && !url.username && !url.password;
    } catch (_) {
      return false;
    }
  }

  function profilePhotoContentType(file) {
    const declaredType = String(file?.type || "").toLowerCase();
    if (declaredType === "image/jpeg" || declaredType === "image/jpg") return "image/jpeg";
    if (declaredType === "image/png" || declaredType === "image/webp") return declaredType;
    const extension = String(file?.name || "").toLowerCase().match(/\.([a-z0-9]+)$/)?.[1];
    if (extension === "jpg" || extension === "jpeg") return "image/jpeg";
    if (extension === "png") return "image/png";
    if (extension === "webp") return "image/webp";
    return "";
  }

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function showStatus(message, kind = "", focus = false) {
    const status = byId("status");
    status.hidden = false;
    status.className = `status${kind ? ` ${kind}` : ""}`;
    status.textContent = message;
    if (focus) status.focus();
  }

  function setProfileSaveState(state) {
    const button = byId("save-profile");
    button.dataset.state = state;
    button.textContent = state === "saving" ? "Saving…" : state === "saved" ? "✓ Saved" : "Save profile";
  }

  function showProfilePhoto(src, alt, {
    removable = true,
    errorMessage = "",
    errorKind = "error",
    focusOnError = true,
  } = {}) {
    const image = byId("headshot-preview");
    const fallback = byId("headshot-fallback");
    image.hidden = true;
    fallback.hidden = false;
    image.alt = alt;
    image.onload = () => { image.hidden = false; fallback.hidden = true; };
    image.onerror = () => {
      image.hidden = true;
      fallback.hidden = false;
      if (errorMessage) showStatus(errorMessage, errorKind, focusOnError);
    };
    image.src = src;
    byId("remove-headshot").hidden = !removable;
  }

  function setProfile(profile) {
    const form = byId("profile-form");
    let firstName = profile.first_name || "";
    let lastName = profile.last_name || "";
    inferredNameDraft = false;
    if (!firstName && !lastName && profile.display_name?.trim()) {
      const nameParts = profile.display_name.trim().split(/\s+/);
      firstName = nameParts.length > 1 ? nameParts.slice(0, -1).join(" ") : nameParts[0];
      lastName = nameParts.length > 1 ? nameParts.at(-1) : "";
      inferredNameDraft = true;
    }
    byId("account-email").value = profile.email;
    form.elements.first_name.value = firstName;
    form.elements.last_name.value = lastName;
    byId("name-draft-help").hidden = !inferredNameDraft;
    form.elements.job_title.value = profile.job_title || "";
    form.elements.company.value = profile.company || "";
    form.elements.time_zone.value = profile.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    form.elements.description.value = profile.description || "";
    form.elements.website_url.value = profile.website_url || "";
    form.elements.linkedin_url.value = profile.linkedin_url || "";
    form.elements.x_url.value = profile.x_url || "";
    form.elements.public_profile_enabled.checked = profile.public_profile_enabled === true;
    const roleNodes = (profile.roles || []).map((role) => {
      const item = document.createElement("span");
      item.className = "account-role-chip";
      item.textContent = labels[role] || role;
      return item;
    });
    byId("profile-roles").replaceChildren(...roleNodes);
    const initials = `${firstName[0] || ""}${lastName[0] || ""}`.toUpperCase() || "SB";
    byId("headshot-fallback").textContent = initials;
    if (profile.headshot_url) {
      const headshotOwner = `${firstName} ${lastName}`.trim() || profile.display_name?.trim();
      showProfilePhoto(
        `${profile.headshot_url}?v=${profile.version}`,
        headshotOwner ? `Current profile photo for ${headshotOwner}` : "Current profile photo",
      );
    } else {
      const image = byId("headshot-preview");
      image.onload = null;
      image.onerror = null;
      image.removeAttribute("src");
      image.hidden = true;
      byId("headshot-fallback").hidden = false;
      byId("remove-headshot").hidden = true;
    }
    byId("password-legend").childNodes[0].textContent = profile.has_password ? "Change password " : "Create a password ";
    byId("password-help").textContent = profile.has_password
      ? "Leave both fields blank to keep your current password. Use at least 15 characters to change it. Changing it signs out your other sessions."
      : "Add a password to sign in without waiting for an email link. Use at least 15 characters. Creating it signs out your other sessions.";
    version = profile.version;
  }

  function accessCard(title, subtitle, roles, href) {
    const card = document.createElement("article");
    card.className = "entity-card organizer-card organizer-access-card";
    const eyebrow = document.createElement("p");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = subtitle;
    const heading = document.createElement("h3");
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = title;
      heading.append(link);
    } else heading.textContent = title;
    const roleText = document.createElement("p");
    roleText.className = "result";
    roleText.textContent = roles.map((role) => labels[role] || role).join(" · ");
    card.append(eyebrow, heading, roleText);
    return card;
  }

  function accountRoleChoice(role) {
    const choice = document.createElement("label");
    choice.className = "default-role-choice";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "default_role";
    input.value = role;
    input.checked = role === session.default_role;
    const copy = document.createElement("span");
    copy.append(document.createElement("strong"), document.createElement("small"));
    copy.querySelector("strong").textContent = labels[role] || role;
    copy.querySelector("small").textContent = role === session.active_role
      ? "Active now" : "Available role";
    const marker = document.createElement("b");
    marker.textContent = input.checked ? "Default" : "";
    choice.append(input, copy, marker);
    return choice;
  }

  function renderDefaultRoles() {
    byId("default-role-list").replaceChildren(
      ...(session.account_roles || []).map(accountRoleChoice)
    );
    byId("save-default-role").disabled = true;
  }

  function organizationForm(organization, canRecoverEventOwnership) {
    const card = document.createElement("article");
    card.className = "card organizer-panel organizer-organization-card";
    const form = document.createElement("form");
    form.className = "organization-name-form";
    form.dataset.organizationId = organization.id;
    form.dataset.version = String(organization.version);
    const heading = document.createElement("h3");
    heading.textContent = organization.name;
    const label = document.createElement("label");
    label.textContent = "Organization name";
    const input = document.createElement("input");
    input.name = "name";
    input.maxLength = 200;
    input.required = true;
    input.value = organization.name;
    const save = document.createElement("button");
    save.type = "submit";
    save.textContent = "Save organization";
    label.append(input);
    form.append(heading, label, save);
    const access = document.createElement("section");
    access.className = "organization-access-manager";
    access.dataset.organizationAccessId = organization.id;
    const accessHeading = document.createElement("div");
    accessHeading.className = "section-heading";
    const accessCopy = document.createElement("div");
    const accessTitle = document.createElement("h4");
    accessTitle.textContent = "Organizers";
    const accessSummary = document.createElement("p");
    accessSummary.className = "help";
    accessSummary.textContent = "The owner and admins can manage every event in this organization.";
    accessCopy.append(accessTitle, accessSummary);
    accessHeading.append(accessCopy);
    const create = document.createElement("form");
    create.className = "organization-grant-form";
    create.dataset.organizationGrantCreate = organization.id;
    const emailLabel = document.createElement("label");
    emailLabel.textContent = "Account email";
    const email = document.createElement("input");
    email.name = "email";
    email.type = "email";
    email.autocomplete = "email";
    email.maxLength = 320;
    email.required = true;
    emailLabel.append(email);
    const grant = document.createElement("button");
    grant.type = "submit";
    grant.textContent = "Add admin";
    create.append(emailLabel, grant);
    const accessStatus = document.createElement("p");
    accessStatus.className = "help organization-access-status";
    accessStatus.setAttribute("role", "status");
    accessStatus.setAttribute("aria-live", "polite");
    const list = document.createElement("ul");
    list.className = "item-list organizer-access-list organization-grant-list organization-admin-grid";
    list.append(document.createElement("li"));
    list.firstElementChild.textContent = "Loading organization access…";
    access.append(accessHeading, create, accessStatus, list);
    card.append(form, access);
    if (organizationMode && canRecoverEventOwnership) {
      const transfer = document.createElement("form");
      transfer.className = "organization-owner-transfer-form";
      transfer.dataset.organizationOwnerTransfer = organization.id;
      const transferTitle = document.createElement("h4");
      transferTitle.textContent = "Transfer ownership";
      const transferHelp = document.createElement("p");
      transferHelp.className = "help";
      transferHelp.textContent = "Only the current owner can transfer ownership, and only to an existing Admin. You will remain an Admin.";
      const transferLabel = document.createElement("label");
      transferLabel.textContent = "New owner’s admin email";
      const transferEmail = document.createElement("input");
      transferEmail.name = "email";
      transferEmail.type = "email";
      transferEmail.autocomplete = "email";
      transferEmail.required = true;
      transferLabel.append(transferEmail);
      const transferButton = document.createElement("button");
      transferButton.type = "submit";
      transferButton.className = "danger";
      transferButton.textContent = "Review ownership transfer";
      transfer.append(transferTitle, transferHelp, transferLabel, transferButton);
      card.append(transfer);
    }
    return card;
  }

  function organizationAccessSection(organizationId) {
    return [...byId("organization-settings-list").querySelectorAll("[data-organization-access-id]")]
      .find((node) => node.dataset.organizationAccessId === organizationId);
  }

  function organizationAccessMessage(error, fallback) {
    if (error.status === 404) {
      return "No active SessionBuddy account uses that email. Ask them to sign in once, then try again.";
    }
    if (error.status === 409) return "Owner access cannot be changed or revoked here.";
    return window.SessionBuddyApi.message(error) || fallback;
  }

  function organizationGrantRow(organizationId, grant) {
    const row = document.createElement("li");
    row.className = "organizer-access-list__item";
    const summary = document.createElement("span");
    summary.className = "organizer-access-list__summary";
    const email = document.createElement("strong");
    email.textContent = grant.email;
    const current = document.createElement("span");
    current.textContent = grant.permission === "owner" ? "Owner" : "Admin";
    summary.append(email, " · ", current);
    row.append(summary);
    if (grant.permission === "owner") {
      const note = document.createElement("small");
      note.textContent = "Ownership cannot be revoked from this access list.";
      row.append(note);
      return row;
    }
    const actions = document.createElement("span");
    actions.className = "actions";
    const revoke = document.createElement("button");
    revoke.type = "button";
    revoke.className = "secondary";
    revoke.textContent = "Revoke access";
    const resetRevoke = () => {
      revoke.dataset.confirming = "false";
      revoke.textContent = "Revoke access";
      revoke.setAttribute("aria-label", `Revoke organization access for ${grant.email}`);
    };
    resetRevoke();
    revoke.addEventListener("blur", resetRevoke);
    revoke.addEventListener("keydown", (event) => { if (event.key === "Escape") resetRevoke(); });
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
          method: "DELETE",
          headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token }
        });
        await loadOrganizationGrantList(organizationId);
        showStatus(`Organization organizer access revoked for ${grant.email}.`, "success");
      } catch (error) {
        showStatus(organizationAccessMessage(error, "Organization access could not be revoked."), "error", true);
        revoke.disabled = false;
        resetRevoke();
      }
    });
    actions.append(revoke);
    row.append(actions);
    return row;
  }

  async function loadOrganizationGrantList(organizationId) {
    const section = organizationAccessSection(organizationId);
    if (!section) return;
    const list = section.querySelector(".organization-grant-list");
    const status = section.querySelector(".organization-access-status");
    try {
      const grants = await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/access-grants`);
      const organizers = grants.data.filter((grant) => ["owner", "manage"].includes(grant.permission));
      list.replaceChildren(...organizers.map((grant) => organizationGrantRow(organizationId, grant)));
      if (!organizers.length) {
        const empty = document.createElement("li");
        empty.textContent = "No organization access entries.";
        list.append(empty);
      }
      status.textContent = `${organizers.length} active ${organizers.length === 1 ? "organizer" : "organizers"}.`;
    } catch (error) {
      section.hidden = true;
      if (error.status !== 404) showStatus(window.SessionBuddyApi.message(error), "error", true);
    }
  }

  async function loadOrganizationSettings() {
    const organizationAccess = session.organization_access || [];
    const manageable = new Set(
      organizationAccess
        .filter((item) => (item.permissions || []).some(
          (permission) => ["owner", "manage"].includes(permission)
        ))
        .map((item) => item.organization_id)
    );
    const owned = new Set(
      organizationAccess
        .filter((item) => (item.permissions || []).includes("owner"))
        .map((item) => item.organization_id)
    );
    if (!manageable.size) return;
    const organizations = (await api("/api/v1/admin/organizations")).data
      .filter((organization) => manageable.has(organization.id));
    if (!organizations.length) return;
    byId("organization-settings-list").replaceChildren(
      ...organizations.map((organization) => organizationForm(
        organization,
        owned.has(organization.id)
      ))
    );
    byId("organization-settings").hidden = false;
    await Promise.all(organizations.flatMap((organization) => [
      loadOrganizationGrantList(organization.id),
      ...(organizationMode ? [loadOrganizationActivity(organization.id)] : [])
    ]));
  }

  function activityLabel(resourceType, operation) {
    const action = `${resourceType}.${operation}`;
    const labels = {
      "resource_access_grant.upsert": "Admin access added or updated",
      "resource_access_grant.create": "Admin access added",
      "resource_access_grant.revoke": "Admin access revoked",
      "resource_ownership.transfer": "Ownership transferred",
      "event.create": "Event created",
      "event.update": "Event updated",
      "form.publish": "Call for proposals published",
      "submission.create": "Proposal submitted",
      "evaluation.submit": "Review finalized",
      "decision.finalize": "Proposal decision finalized",
      "agenda.publish": "Schedule published"
    };
    return labels[action] || action.split(/[._]/).filter(Boolean).map((part) => `${part[0].toUpperCase()}${part.slice(1)}`).join(" ");
  }

  async function loadOrganizationActivity(organizationId) {
    const section = byId("organization-activity");
    const list = byId("organization-activity-list");
    try {
      const activities = (await api(`/api/v1/admin/organizations/${encodeURIComponent(organizationId)}/activities`)).data;
      const rows = activities.map((activity) => {
        const row = document.createElement("li");
        row.className = "organization-activity-item";
        const title = document.createElement("strong");
        title.textContent = activityLabel(activity.resource_type, activity.operation);
        const detail = document.createElement("span");
        const time = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(activity.occurred_at_ms));
        detail.textContent = `${activity.actor_name} · ${time}`;
        row.append(title, detail);
        return row;
      });
      if (!rows.length) {
        const empty = document.createElement("li");
        empty.className = "help";
        empty.textContent = "No organization activity has been projected yet.";
        rows.push(empty);
      }
      list.replaceChildren(...rows);
      section.hidden = false;
    } catch (error) {
      section.hidden = true;
      if (error.status !== 404) showStatus(window.SessionBuddyApi.message(error), "error", true);
    }
  }

  async function initialize() {
    [session] = await Promise.all([api("/api/v1/auth/session"), api("/api/v1/account/profile").then(setProfile)]);
    if (organizationMode) {
      byId("account-context-heading").hidden = false;
      document.title = "Organization settings · SessionBuddy";
      document.querySelector(".skip-link").textContent = "Skip to organization settings";
      document.querySelector(".account-context-heading .eyebrow").textContent = "Organization administration";
      byId("account-title").textContent = "Organization settings";
      byId("account-summary").textContent = "Manage organization details and organizers whose authority applies across every event.";
    }
    if (organizationMode) await loadOrganizationSettings();
    const access = [];
    renderDefaultRoles();
    for (const item of session.organization_access || []) {
      access.push(accessCard(
        item.organization_name,
        "Owned or delegated resource",
        item.permissions,
        "/admin"
      ));
    }
    for (const item of session.event_access || []) {
      const assignments = item.assignments || [];
      const href = assignments.includes("speaker") ? "/speaker" : "/reviews";
      access.push(accessCard(
        item.event_name,
        "Event assignment",
        assignments,
        href
      ));
    }
    if (!access.length) access.push(accessCard("No resource access yet", "Organizations and events", [], null));
    byId("access-list").replaceChildren(...access);
    byId("access-count").textContent = String((session.account_roles || []).length);
    byId("save-profile").disabled = false;
    if (!session.profile_complete) {
      byId("account-context-heading").hidden = false;
      byId("account-title").textContent = "Complete your profile";
      byId("account-summary").textContent = "Add your details before continuing.";
      showStatus(inferredNameDraft
        ? "Review the suggested name fields, complete the required fields, then save your profile."
        : "Complete the required fields, then save your profile.");
      byId("profile-form").elements.first_name.focus();
    }
  }

  byId("profile-form").addEventListener("input", (event) => {
    event.target.setCustomValidity?.("");
    setProfileSaveState("idle");
    byId("save-profile").disabled = false;
  });
  byId("default-role-form").addEventListener("change", () => {
    byId("save-default-role").disabled = false;
    for (const choice of byId("default-role-list").querySelectorAll(".default-role-choice")) {
      choice.querySelector("b").textContent = choice.querySelector("input").checked ? "Selected" : "";
    }
  });
  byId("default-role-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const selected = new FormData(event.currentTarget).get("default_role");
    if (!selected) return;
    const button = byId("save-default-role");
    button.disabled = true;
    try {
      session = await api("/api/v1/account/default-role", {
        method: "PUT",
        headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
        body: JSON.stringify({ role: selected })
      });
      renderDefaultRoles();
      showStatus(`${labels[selected] || selected} is now your default role.`, "success");
    } catch (error) {
      showStatus(window.SessionBuddyApi.message(error), "error", true);
      button.disabled = false;
    }
  });
  byId("headshot-input").addEventListener("change", (event) => {
    selectedHeadshot = event.target.files?.[0];
    if (!selectedHeadshot) return;
    selectedHeadshotContentType = profilePhotoContentType(selectedHeadshot);
    if (!selectedHeadshotContentType) {
      selectedHeadshot = undefined;
      selectedHeadshotContentType = undefined;
      event.target.value = "";
      showStatus("That file type is not supported. Choose a JPG, JPEG, PNG, or WebP image.", "error", true);
      return;
    }
    if (selectedHeadshot.size <= 0 || selectedHeadshot.size > 5 * 1024 * 1024) {
      selectedHeadshot = undefined;
      selectedHeadshotContentType = undefined;
      event.target.value = "";
      showStatus("The profile photo must be larger than 0 bytes and no larger than 5 MB.", "error", true);
      return;
    }
    const previewSelection = selectedHeadshot;
    const previewReader = new FileReader();
    previewReader.onload = () => {
      if (selectedHeadshot !== previewSelection || typeof previewReader.result !== "string") return;
      showProfilePhoto(previewReader.result, "Selected profile photo preview; uploads when you save", {
        removable: false,
        errorMessage: "This image file could not be decoded. Export it as a standard JPEG, PNG, or WebP and try again.",
      });
    };
    previewReader.onerror = () => {
      if (selectedHeadshot === previewSelection) {
        showStatus("This image could not be read. Choose the file again or export a new copy.", "error", true);
      }
    };
    previewReader.readAsDataURL(previewSelection);
    showStatus(`Profile photo selected: ${selectedHeadshot.name}. Choose Save profile to upload it.`);
    setProfileSaveState("idle");
    byId("save-profile").disabled = false;
  });
  byId("remove-headshot").addEventListener("click", async () => {
    const button = byId("remove-headshot");
    button.disabled = true;
    try {
      await api("/api/v1/account/headshot", { method: "DELETE", headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token } });
      byId("headshot-preview").hidden = true;
      byId("headshot-fallback").hidden = false;
      button.hidden = true;
      showStatus("Headshot removed.", "success");
    } catch (error) {
      showStatus(window.SessionBuddyApi.message(error), "error", true);
      button.disabled = false;
    }
  });
  byId("organization-settings-list").addEventListener("submit", async (event) => {
    const form = event.target.closest("form.organization-name-form[data-organization-id]");
    if (!form) return;
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const organization = await api(
        `/api/v1/admin/organizations/${encodeURIComponent(form.dataset.organizationId)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
          body: JSON.stringify({ name: form.elements.name.value.trim(), version: Number(form.dataset.version) })
        }
      );
      form.dataset.version = String(organization.version);
      form.querySelector("h3").textContent = organization.name;
      form.elements.name.value = organization.name;
      showStatus("Organization saved.", "success");
    } catch (error) {
      showStatus(error.status === 409
        ? "The organization changed elsewhere. Reload and try again."
        : window.SessionBuddyApi.message(error), "error", true);
    } finally { button.disabled = false; }
  });
  byId("organization-settings-list").addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-organization-owner-transfer]");
    if (!form) return;
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    const email = form.elements.email.value.trim();
    if (button.dataset.confirming !== "true") {
      button.dataset.confirming = "true";
      button.textContent = `Confirm transfer to ${email}`;
      form.elements.email.readOnly = true;
      return;
    }
    button.disabled = true;
    try {
      await api(`/api/v1/admin/organizations/${encodeURIComponent(form.dataset.organizationOwnerTransfer)}/ownership-transfers`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
        body: JSON.stringify({ email })
      });
      showStatus(`Organization ownership transferred to ${email}. You remain an Admin.`, "success", true);
      await loadOrganizationSettings();
    } catch (error) {
      showStatus(error.status === 404
        ? "That account is not an Admin of this organization. Add them as an Admin first."
        : window.SessionBuddyApi.message(error), "error", true);
      button.disabled = false;
      button.dataset.confirming = "false";
      button.textContent = "Review ownership transfer";
      form.elements.email.readOnly = false;
    }
  });
  byId("organization-settings-list").addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-organization-grant-create]");
    if (!form) return;
    event.preventDefault();
    if (!form.reportValidity()) return;
    const button = form.querySelector('button[type="submit"]');
    const values = Object.fromEntries(new FormData(form));
    button.disabled = true;
    try {
      await api(`/api/v1/admin/organizations/${encodeURIComponent(form.dataset.organizationGrantCreate)}/access-grants`, {
        method: "POST",
        headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
        body: JSON.stringify({ email: values.email })
      });
      const email = values.email;
      form.reset();
      await loadOrganizationGrantList(form.dataset.organizationGrantCreate);
      showStatus(`Organization organizer access granted to ${email}.`, "success");
    } catch (error) {
      showStatus(organizationAccessMessage(error, "Organization access could not be granted."), "error", true);
    } finally {
      button.disabled = false;
    }
  });
  byId("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const timeZone = form.elements.time_zone;
    timeZone.setCustomValidity(validTimeZone(timeZone.value.trim()) ? "" : "Enter a valid IANA time zone, such as Asia/Kolkata.");
    for (const name of ["website_url", "linkedin_url", "x_url"]) {
      const field = form.elements[name];
      field.setCustomValidity(validProfileUrl(field.value.trim())
        ? ""
        : "Enter a complete HTTPS URL, such as https://example.com, without a username or password.");
    }
    const password = form.elements.password;
    const confirmation = form.elements.password_confirmation;
    confirmation.setCustomValidity(password.value === confirmation.value ? "" : "Passwords must match.");
    if (!form.reportValidity()) return;
    const button = byId("save-profile");
    button.disabled = true;
    setProfileSaveState("saving");
    showStatus("Saving your profile…");
    const values = Object.fromEntries(new FormData(form).entries());
    let saved = false;
    try {
      let profile = await api("/api/v1/account/profile", {
        method: "PATCH",
        headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
        body: JSON.stringify({
          first_name: values.first_name,
          last_name: values.last_name,
          job_title: values.job_title || null,
          company: values.company || null,
          time_zone: values.time_zone || null,
          description: values.description || null,
          website_url: values.website_url || null,
          linkedin_url: values.linkedin_url || null,
          x_url: values.x_url || null,
          public_profile_enabled: form.elements.public_profile_enabled.checked,
          password: values.password || null,
          password_confirmation: values.password_confirmation || null,
          version
        })
      });
      if (profile.csrf_token) session.csrf_token = profile.csrf_token;
      if (selectedHeadshot) {
        showStatus("Saving your headshot…");
        await api("/api/v1/account/headshot", {
          method: "PUT",
          headers: { "content-type": selectedHeadshotContentType, "x-csrf-token": session.csrf_token },
          body: selectedHeadshot
        });
        selectedHeadshot = undefined;
        selectedHeadshotContentType = undefined;
        byId("headshot-input").value = "";
        profile = await api("/api/v1/account/profile");
      }
      setProfile(profile);
      showStatus(values.password
        ? "Profile and password saved. Your other sessions were signed out."
        : "Profile saved.", "success");
      saved = true;
      setProfileSaveState("saved");
      form.elements.password.value = "";
      form.elements.password_confirmation.value = "";
      window.dispatchEvent(new CustomEvent("sessionbuddy:profile-updated", { detail: profile }));
      if (onboarding) {
        const safeNext = nextPath.startsWith("/") && !nextPath.startsWith("//") && !nextPath.includes("\\")
          ? nextPath
          : "/";
        location.replace(safeNext);
      }
    } catch (error) {
      const message = error.status === 409
        ? "Your profile changed elsewhere. Reload and try again."
        : error.status === 503 && values.password
        ? "Your profile was not saved because password sign-in is temporarily unavailable. Leave both password fields blank to save the rest of your profile now, or ask the administrator to check password configuration."
        : window.SessionBuddyApi.message(error);
      setProfileSaveState("idle");
      showStatus(message, "error", true);
    } finally {
      button.disabled = saved;
    }
  });

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      showStatus(window.SessionBuddyApi.message(error), "error", true);
    }
  });
})();

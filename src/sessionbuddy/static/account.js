(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const labels = {
    organizer: "Organizer",
    reviewer: "Reviewer",
    organization_admin: "Organization administrator",
    event_admin: "Event administrator",
    evaluator: "Reviewer",
    speaker: "Speaker"
  };

  let session;
  let version;
  let selectedHeadshot;
  let previewObjectUrl;
  const query = new URLSearchParams(location.search);
  const onboarding = query.get("onboarding") === "1";
  const nextPath = query.get("next") || "";

  function validTimeZone(value) {
    if (!value) return true;
    try { new Intl.DateTimeFormat(undefined, { timeZone: value }).format(); return true; }
    catch (_) { return false; }
  }

  const api = (path, options = {}) => window.SessionBuddyApi.request(path, options);

  function showStatus(message, kind = "", focus = false) {
    const status = byId("status");
    status.hidden = false;
    status.className = `status${kind ? ` ${kind}` : ""}`;
    status.textContent = message;
    if (focus) status.focus();
  }

  function setProfile(profile) {
    const form = byId("profile-form");
    byId("account-email").value = profile.email;
    form.elements.first_name.value = profile.first_name || "";
    form.elements.last_name.value = profile.last_name || "";
    form.elements.job_title.value = profile.job_title || "";
    form.elements.company.value = profile.company || "";
    form.elements.time_zone.value = profile.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    form.elements.description.value = profile.description || "";
    form.elements.website_url.value = profile.website_url || "";
    form.elements.linkedin_url.value = profile.linkedin_url || "";
    form.elements.x_url.value = profile.x_url || "";
    const roleNodes = (profile.roles || []).map((role) => {
      const item = document.createElement("span");
      item.className = "account-role-chip";
      item.textContent = labels[role] || role;
      return item;
    });
    byId("profile-roles").replaceChildren(...roleNodes);
    const initials = `${profile.first_name?.[0] || ""}${profile.last_name?.[0] || ""}`.toUpperCase() || "SB";
    byId("headshot-fallback").textContent = initials;
    if (profile.headshot_url) {
      byId("headshot-preview").src = `${profile.headshot_url}?v=${profile.version}`;
      byId("headshot-preview").hidden = false;
      byId("headshot-fallback").hidden = true;
      byId("remove-headshot").hidden = false;
    } else {
      byId("headshot-preview").hidden = true;
      byId("headshot-fallback").hidden = false;
      byId("remove-headshot").hidden = true;
    }
    byId("password-legend").childNodes[0].textContent = profile.has_password ? "Change password " : "Create a password ";
    byId("password-help").textContent = profile.has_password
      ? "Leave both fields blank to keep your current password. Use at least 15 characters to change it. Changing it signs you out on every device."
      : "Add a password to sign in without waiting for an email link. Use at least 15 characters. Changing it signs you out on every device.";
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

  function organizationForm(organization) {
    const card = document.createElement("article");
    card.className = "card organizer-panel organizer-organization-card";
    const form = document.createElement("form");
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
    card.append(form);
    return card;
  }

  async function loadOrganizationSettings() {
    const manageable = new Set(
      (session.organization_access || [])
        .filter((item) => (item.roles || []).includes("organization_admin"))
        .map((item) => item.organization_id)
    );
    if (!manageable.size) return;
    const organizations = (await api("/api/v1/admin/organizations")).data
      .filter((organization) => manageable.has(organization.id));
    if (!organizations.length) return;
    byId("organization-settings-list").replaceChildren(...organizations.map(organizationForm));
    byId("organization-settings").hidden = false;
  }

  async function initialize() {
    [session] = await Promise.all([api("/api/v1/auth/session"), api("/api/v1/account/profile").then(setProfile)]);
    await loadOrganizationSettings();
    const access = [];
    renderDefaultRoles();
    for (const item of session.organization_access || []) {
      access.push(accessCard("Organization", "Organization access", item.roles, "/admin"));
    }
    for (const item of session.event_access || []) {
      const roles = item.roles || [];
      const href = roles.includes("event_admin") ? `/admin/events/${encodeURIComponent(item.event_id)}`
        : roles.includes("speaker") ? "/speaker" : "/reviews";
      access.push(accessCard("Event", `Event ${item.event_id}`, roles, href));
    }
    if (!access.length) access.push(accessCard("No resource access yet", "Organizations and events", [], null));
    byId("access-list").replaceChildren(...access);
    byId("access-count").textContent = String((session.account_roles || []).length);
    byId("save-profile").disabled = false;
    if (onboarding && !session.profile_complete) {
      byId("account-title").textContent = "Complete your profile";
      byId("account-summary").textContent = "Add your details before continuing.";
      showStatus("Complete the required fields, then save your profile.");
      byId("profile-form").elements.first_name.focus();
    }
  }

  byId("profile-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
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
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = URL.createObjectURL(selectedHeadshot);
    byId("headshot-preview").src = previewObjectUrl;
    byId("headshot-preview").hidden = false;
    byId("headshot-fallback").hidden = true;
    byId("upload-headshot").disabled = false;
  });
  byId("upload-headshot").addEventListener("click", async () => {
    if (!selectedHeadshot) return;
    const button = byId("upload-headshot");
    button.disabled = true;
    try {
      await api("/api/v1/account/headshot", {
        method: "PUT",
        headers: { "content-type": selectedHeadshot.type, "x-csrf-token": session.csrf_token },
        body: selectedHeadshot
      });
      selectedHeadshot = undefined;
      byId("headshot-input").value = "";
      byId("remove-headshot").hidden = false;
      showStatus("Headshot saved.", "success");
    } catch (error) {
      showStatus(window.SessionBuddyApi.message(error), "error", true);
      button.disabled = false;
    }
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
    event.preventDefault();
    const form = event.target.closest("form[data-organization-id]");
    if (!form || !form.reportValidity()) return;
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
  byId("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const timeZone = form.elements.time_zone;
    timeZone.setCustomValidity(validTimeZone(timeZone.value.trim()) ? "" : "Enter a valid IANA time zone, such as Asia/Kolkata.");
    const password = form.elements.password;
    const confirmation = form.elements.password_confirmation;
    confirmation.setCustomValidity(password.value === confirmation.value ? "" : "Passwords must match.");
    if (!form.reportValidity()) return;
    const button = byId("save-profile");
    button.disabled = true;
    showStatus("Saving your profile…");
    const values = Object.fromEntries(new FormData(form).entries());
    try {
      const profile = await api("/api/v1/account/profile", {
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
          password: values.password || null,
          password_confirmation: values.password_confirmation || null,
          version
        })
      });
      setProfile(profile);
      showStatus(values.password
        ? "Profile and password saved. Sign in again to continue."
        : "Profile saved.", "success");
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
      showStatus(error.status === 409 ? "Your profile changed elsewhere. Reload and try again." : window.SessionBuddyApi.message(error), "error", true);
    } finally {
      button.disabled = false;
    }
  });

  initialize().catch((error) => {
    if (!window.SessionBuddyApi.redirectIfSignedOut(error)) {
      showStatus(window.SessionBuddyApi.message(error), "error", true);
    }
  });
})();

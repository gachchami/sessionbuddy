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
  document.querySelector(".account-profile-panel").hidden = false;
  document.querySelector(".organizer-section--account-access").hidden = false;
  const onboarding = query.get("onboarding") === "1";
  const workspaceRecoveryRequested = query.get("workspace") === "recovery";
  const nextPath = query.get("next") || "";

  function renderWorkspaceRecovery(session) {
    const recoveryState = session.workspace_state === "active_role_invalid"
      || session.workspace_state === "organizer_authority_missing";
    if (!recoveryState) return;
    const notice = byId("workspace-recovery");
    const copy = byId("workspace-recovery-copy");
    copy.textContent = session.workspace_state === "organizer_authority_missing"
      ? "Your organizer role no longer manages an organization. You can still update your account here, choose another available role from the account menu, or sign out."
      : "Your current role is no longer available. You can still update your account here, choose another available role from the account menu, or sign out.";
    if (workspaceRecoveryRequested) notice.dataset.destination = "server-recovery";
    notice.hidden = false;
  }

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


  async function initialize() {
    [session] = await Promise.all([api("/api/v1/auth/session"), api("/api/v1/account/profile").then(setProfile)]);
    renderWorkspaceRecovery(session);
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
      // The profile write is committed here. Adopt its version now so a failed
      // headshot upload below cannot leave every retry stuck on a stale 409.
      version = profile.version;
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

(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const labels = {
    organization_admin: "Organization administrator",
    event_admin: "Event administrator",
    evaluator: "Reviewer",
    speaker: "Speaker"
  };

  let session;
  let version;

  function validTimeZone(value) {
    if (!value) return true;
    try { new Intl.DateTimeFormat(undefined, { timeZone: value }).format(); return true; }
    catch (_) { return false; }
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(body?.error?.message || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function setProfile(profile) {
    const form = byId("profile-form");
    byId("account-email").value = profile.email;
    form.elements.display_name.value = profile.display_name || "";
    form.elements.job_title.value = profile.job_title || "";
    form.elements.company.value = profile.company || "";
    form.elements.time_zone.value = profile.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    version = profile.version;
  }

  function accessCard(title, subtitle, roles, href) {
    const card = document.createElement("article");
    card.className = "entity-card";
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

  async function initialize() {
    [session] = await Promise.all([api("/api/v1/auth/session"), api("/api/v1/account/profile").then(setProfile)]);
    const access = [];
    for (const item of session.organization_access || []) {
      access.push(accessCard("Organization", "Organization access", item.roles, "/admin"));
    }
    for (const item of session.event_access || []) {
      const roles = item.roles || [];
      const href = roles.includes("event_admin") ? `/admin/events/${encodeURIComponent(item.event_id)}`
        : roles.includes("speaker") ? "/speaker" : "/reviews";
      access.push(accessCard("Event", `Event ${item.event_id}`, roles, href));
    }
    if (!access.length) access.push(accessCard("No assigned roles", "Account", [], null));
    byId("access-list").replaceChildren(...access);
    byId("access-count").textContent = String(access.length);
    byId("save-profile").disabled = false;
    byId("status").textContent = "Your account is up to date.";
  }

  byId("profile-form").addEventListener("input", (event) => event.target.setCustomValidity?.(""));
  byId("profile-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const timeZone = event.currentTarget.elements.time_zone;
    timeZone.setCustomValidity(validTimeZone(timeZone.value.trim()) ? "" : "Enter a valid IANA time zone, such as Asia/Kolkata.");
    if (!event.currentTarget.reportValidity()) return;
    const button = byId("save-profile");
    button.disabled = true;
    byId("status").className = "status";
    byId("status").textContent = "Saving your profile…";
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      const profile = await api("/api/v1/account/profile", {
        method: "PATCH",
        headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
        body: JSON.stringify({
          display_name: values.display_name,
          job_title: values.job_title || null,
          company: values.company || null,
          time_zone: values.time_zone || null,
          version
        })
      });
      setProfile(profile);
      byId("status").className = "status success";
      byId("status").textContent = "Profile saved.";
      window.dispatchEvent(new CustomEvent("sessionbuddy:profile-updated", { detail: profile }));
    } catch (error) {
      byId("status").className = "status error";
      byId("status").textContent = error.status === 409 ? "Your profile changed elsewhere. Reload and try again." : error.message;
      byId("status").focus();
    } finally {
      button.disabled = false;
    }
  });

  initialize().catch((error) => {
    if (error.status === 401) location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`);
    else { byId("status").textContent = error.message; byId("status").classList.add("error"); }
  });
})();

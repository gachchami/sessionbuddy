(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const labels = {
    organization_admin: "Organization administrator",
    event_admin: "Event administrator",
    evaluator: "Reviewer",
    speaker: "Speaker"
  };

  async function api(path) {
    const response = await fetch(path, { credentials: "same-origin" });
    const body = await response.json();
    if (!response.ok) {
      const error = new Error(body?.error?.message || `Request failed (${response.status})`);
      error.status = response.status;
      throw error;
    }
    return body;
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
    const session = await api("/api/v1/auth/session");
    byId("account-email").textContent = session.email;
    const access = [];
    for (const item of session.organization_access || []) {
      access.push(accessCard("Organizer workspace", "Organization access", item.roles, "/admin"));
    }
    for (const item of session.event_access || []) {
      const roles = item.roles || [];
      const href = roles.includes("event_admin") ? `/admin/events/${encodeURIComponent(item.event_id)}`
        : roles.includes("speaker") ? "/speaker" : "/reviews";
      access.push(accessCard("Event workspace", `Event ${item.event_id}`, roles, href));
    }
    if (!access.length) access.push(accessCard("No workspace roles", "Account", [], null));
    byId("access-list").replaceChildren(...access);
    byId("access-count").textContent = String(access.length);
    byId("status").textContent = "Your account is up to date.";
  }

  initialize().catch((error) => {
    if (error.status === 401) location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname)}`);
    else { byId("status").textContent = error.message; byId("status").classList.add("error"); }
  });
})();

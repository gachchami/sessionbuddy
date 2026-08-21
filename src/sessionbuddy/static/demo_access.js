(() => {
  "use strict";

  // The demo section is constructed here rather than hidden in the page source,
  // so when demo mode is unavailable the markup is absent entirely and the
  // capability is not discoverable from the shipped HTML.
  const UNAVAILABLE = "Demo access is currently unavailable.";

  function safeRedirect(value) {
    return typeof value === "string"
      && value.startsWith("/")
      && !value.startsWith("//")
      && !value.includes("\\")
      ? value
      : "/";
  }

  function requestedRedirect(fallback) {
    const requested = new URLSearchParams(location.search).get("redirect");
    return requested ? safeRedirect(requested) : fallback;
  }

  function buildSection(personas, mount) {
    const section = document.createElement("section");
    section.className = "demo-access";
    section.setAttribute("aria-labelledby", "demo-access-title");

    const heading = document.createElement("h2");
    heading.id = "demo-access-title";
    heading.className = "demo-access__title";
    heading.textContent = "Explore the demo";
    section.append(heading);

    const intro = document.createElement("p");
    intro.className = "demo-access__intro";
    intro.textContent = "Sign in instantly as a sample account. No password needed.";
    section.append(intro);

    const status = document.createElement("p");
    status.className = "demo-access__status auth-message";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.tabIndex = -1;
    status.hidden = true;

    const list = document.createElement("ul");
    list.className = "demo-access__list";

    const buttons = [];
    for (const persona of personas) {
      const item = document.createElement("li");
      item.className = "demo-access__item";

      const button = document.createElement("button");
      button.type = "button";
      button.className = "demo-access__button secondary";
      button.dataset.demoRole = persona.role;

      const label = document.createElement("span");
      label.className = "demo-access__label";
      label.textContent = persona.label;

      // The destination is named in the accessible description, so the role and
      // where it lands are both explicit before the control is activated.
      const description = document.createElement("span");
      description.className = "demo-access__description";
      description.id = `demo-access-description-${persona.role}`;
      description.textContent = persona.description;

      button.append(label);
      button.setAttribute("aria-describedby", description.id);
      item.append(button, description);
      list.append(item);
      buttons.push(button);

      button.addEventListener("click", async () => {
        status.hidden = true;
        status.classList.remove("error");
        for (const other of buttons) other.disabled = true;
        button.dataset.loading = "true";
        label.textContent = "Signing in…";
        try {
          const session = await window.SessionBuddyApi.request("/api/v1/auth/demo-sign-in", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              role: persona.role,
              redirect_path: requestedRedirect(persona.destination)
            })
          });
          location.assign(safeRedirect(session.redirect_path) || persona.destination);
        } catch (error) {
          // Deliberately generic: the visitor cannot act on a backend reason,
          // and the reason may describe the gate itself.
          status.textContent = error?.status === 429
            ? "Demo sign-in is busy right now. Try again in a moment."
            : UNAVAILABLE;
          status.classList.add("error");
          status.hidden = false;
          status.focus();
          for (const other of buttons) other.disabled = false;
          delete button.dataset.loading;
          label.textContent = persona.label;
        }
      });
    }

    section.append(list, status);
    mount.replaceChildren(section);
  }

  async function mountDemoAccess() {
    const mount = document.querySelector("[data-demo-access]");
    if (!mount || !window.SessionBuddyApi) return;
    let personas;
    try {
      personas = await window.SessionBuddyApi.request("/api/v1/auth/demo-personas");
    } catch {
      // 404 is the ordinary "demo mode is off" answer. Leave the page as-is.
      return;
    }
    const available = Array.isArray(personas?.data) ? personas.data : [];
    if (!available.length) return;
    buildSection(available, mount);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountDemoAccess, { once: true });
  } else {
    mountDemoAccess();
  }
})();

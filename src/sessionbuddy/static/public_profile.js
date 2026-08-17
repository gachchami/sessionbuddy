(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const match = location.pathname.match(/^\/people\/([^/]+)$/);
  let userId = "";
  try { userId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { userId = ""; }

  function initials(name) {
    return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2)
      .map((part) => part[0]).join("").toUpperCase() || "?";
  }

  function addLink(container, url) {
    if (!url) return;
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    anchor.textContent = url;
    container.append(anchor);
  }

  function initializeBiography() {
    const biography = byId("profile-biography");
    const toggle = byId("profile-biography-toggle");
    toggle.addEventListener("click", () => {
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      toggle.setAttribute("aria-expanded", String(!expanded));
      toggle.textContent = expanded ? "Show more" : "Show less";
      biography.classList.toggle("is-collapsed", expanded);
    });
    requestAnimationFrame(() => { toggle.hidden = biography.scrollHeight <= biography.clientHeight; });
  }

  async function load() {
    if (!userId) throw Object.assign(new Error("missing profile"), { status: 404 });
    const profile = await window.SessionBuddyApi.request(`/api/v1/public/people/${encodeURIComponent(userId)}`);
    byId("profile-name").textContent = profile.display_name;
    byId("profile-monogram").textContent = initials(profile.display_name);
    const role = [profile.job_title, profile.company].filter(Boolean).join(" · ");
    byId("profile-role").textContent = role || "SessionBuddy community member";
    if (profile.biography) {
      byId("profile-biography").textContent = profile.biography;
      byId("profile-about").hidden = false;
      initializeBiography();
    }
    if (profile.headshot_url) {
      const headshot = byId("profile-headshot");
      headshot.src = profile.headshot_url;
      headshot.alt = `${profile.display_name} headshot`;
      headshot.hidden = false;
      byId("profile-monogram").hidden = true;
      headshot.addEventListener("error", () => {
        headshot.hidden = true;
        byId("profile-monogram").hidden = false;
      }, { once: true });
    }
    const links = byId("profile-links");
    addLink(links, profile.website_url);
    addLink(links, profile.linkedin_url);
    addLink(links, profile.x_url);
    links.hidden = links.childElementCount === 0;
    byId("public-profile").hidden = false;
    byId("profile-status").hidden = true;
    document.title = `${profile.display_name} · SessionBuddy`;
  }

  load().catch((error) => {
    byId("profile-status").hidden = true;
    byId("profile-missing").hidden = false;
    if (error?.status !== 404) byId("profile-missing").querySelector("p").textContent = "The profile could not be loaded. Try again later.";
  });
})();

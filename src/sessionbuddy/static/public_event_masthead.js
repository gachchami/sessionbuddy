(() => {
  "use strict";

  const initials = (value) => String(value || "Event").trim().split(/\s+/).filter(Boolean)
    .slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "EV";

  function render(mount, options) {
    if (!mount || options?.embedded) { if (mount) mount.hidden = true; return; }
    const event = options.event || {};
    const eventId = encodeURIComponent(event.id || "");
    const band = document.createElement("header"); band.className = "public-event-masthead__band";
    band.setAttribute("aria-label", "Event header");
    if (event.coverUrl) {
      const cover = document.createElement("img"); cover.className = "public-event-masthead__cover";
      cover.src = event.coverUrl; cover.alt = "";
      cover.addEventListener("error", () => cover.remove(), { once: true });
      band.append(cover);
    }
    const identity = document.createElement("div"); identity.className = "public-event-masthead__identity";
    const mark = document.createElement("span"); mark.className = "public-event-masthead__mark";
    const monogram = document.createElement("span"); monogram.textContent = initials(event.name); monogram.setAttribute("aria-hidden", "true");
    mark.append(monogram);
    if (event.logoUrl) {
      const logo = document.createElement("img"); logo.src = event.logoUrl; logo.alt = `${event.name} logo`;
      logo.addEventListener("load", () => { monogram.hidden = true; }, { once: true });
      logo.addEventListener("error", () => logo.remove(), { once: true });
      mark.append(logo);
    }
    const name = document.createElement("strong"); name.className = "public-event-masthead__name"; name.textContent = event.name || "Event";
    identity.append(mark, name); band.append(identity);
    const children = [band];
    if (options.navigation !== false) {
      const nav = document.createElement("nav"); nav.className = "public-event-masthead__nav"; nav.setAttribute("aria-label", "Event pages");
      [["Schedule", `/events/${eventId}/schedule`, "schedule"], ["Speakers", `/events/${eventId}/speakers`, "speakers"]]
        .forEach(([label, href, key]) => { const link = document.createElement("a"); link.textContent = label; link.href = href; if (options.active === key) link.setAttribute("aria-current", "page"); nav.append(link); });
      children.push(nav);
    }
    mount.hidden = false; mount.replaceChildren(...children);
  }

  window.SessionBuddyPublicEventMasthead = { render };
})();

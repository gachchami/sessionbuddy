(() => {
  "use strict";

  const initials = (value) => String(value || "Event").trim().split(/\s+/).filter(Boolean)
    .slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "EV";

  const factIcons = {
    date: '<rect x="3.5" y="5.5" width="17" height="15" rx="3"/><path d="M7.5 3v5M16.5 3v5M3.5 10h17"/><path d="M8 14h.01M12 14h.01M16 14h.01M8 17h.01M12 17h.01"/>',
    location: '<path d="M20 10c0 5.2-8 11-8 11S4 15.2 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="2.5"/>',
    format: '<path d="M4 20V8.5L12 4l8 4.5V20"/><path d="M8 20v-5h8v5M8 10h.01M12 10h.01M16 10h.01"/>',
  };

  function renderFacts(mount, facts) {
    if (!mount) return;
    const nodes = facts.filter((fact) => fact.value).map((fact) => {
      const item = document.createElement("div"); item.className = "public-event-fact";
      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      icon.setAttribute("viewBox", "0 0 24 24"); icon.setAttribute("aria-hidden", "true");
      icon.innerHTML = factIcons[fact.icon] || factIcons.format;
      const copy = document.createElement("span"); copy.className = "public-event-fact__copy";
      const label = document.createElement("span"); label.className = "public-event-fact__label"; label.textContent = fact.label;
      const value = document.createElement("strong"); value.className = "public-event-fact__value"; value.textContent = fact.value;
      if (fact.id) value.id = fact.id;
      copy.append(label, value); item.append(icon, copy); return item;
    });
    mount.classList.add("public-event-facts"); mount.replaceChildren(...nodes); mount.hidden = nodes.length === 0;
  }

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
      const links = [["Schedule", `/events/${eventId}/schedule`, "schedule"], ["Speakers", `/events/${eventId}/speakers`, "speakers"]];
      if (event.cfpUrl) links.push(["Call for proposals", event.cfpUrl, "cfp"]);
      links
        .forEach(([label, href, key]) => { const link = document.createElement("a"); link.textContent = label; link.href = href; if (options.active === key) link.setAttribute("aria-current", "page"); nav.append(link); });
      children.push(nav);
    }
    mount.hidden = false; mount.replaceChildren(...children);
  }

  window.SessionBuddyPublicEventMasthead = { render, renderFacts };
})();

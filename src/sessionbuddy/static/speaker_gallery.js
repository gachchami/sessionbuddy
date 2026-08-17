(() => {
  "use strict";
  const match = location.pathname.match(/^(?:\/embeds)?\/events\/([^/]+)\/(?:speakers|gallery)$/);
  document.body.classList.toggle("embedded", location.pathname.startsWith("/embeds/"));
  const galleryLayout = location.pathname.endsWith("/gallery");
  document.body.dataset.layout = galleryLayout ? "gallery" : "directory";
  let eventId = ""; try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const state = { speakers: [], event: null };
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
  const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
  const suffixes = new Set(["jr", "sr", "ii", "iii", "iv"]);
  function normalizedName(value) { return String(value || "").trim().replace(/\s+/g, " "); }
  function surnameFrom(value) {
    const name = normalizedName(value);
    const comma = name.indexOf(",");
    if (comma > 0) return name.slice(0, comma).trim();
    const parts = name.split(" ").filter(Boolean);
    if (parts.length > 1 && suffixes.has(parts.at(-1).replace(/\./g, "").toLowerCase())) parts.pop();
    return parts.at(-1) || "";
  }
  function compareSpeakers(left, right) {
    return collator.compare(surnameFrom(left.display_name), surnameFrom(right.display_name))
      || collator.compare(normalizedName(left.display_name), normalizedName(right.display_name))
      || collator.compare(String(left.id), String(right.id));
  }
  function openProfile(speaker, role) {
    const dialog = byId("speaker-profile");
    const content = byId("speaker-profile-content");
    const portrait = make("div", undefined, "speaker-profile__portrait");
    if (speaker.headshot_url) { const image = document.createElement("img"); image.src = speaker.headshot_url; image.alt = ""; portrait.append(image); }
    else portrait.append(make("span", speaker.display_name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase(), "speaker-initials"));
    const story = make("div", undefined, "speaker-profile__story");
    const title = make("h2", speaker.display_name); title.id = "speaker-profile-title";
    story.append(make("p", "Speaker profile", "eyebrow"), title);
    if (role) story.append(make("p", role, "speaker-profile__role"));
    if (speaker.biography) {
      const biography = make("p", speaker.biography, "speaker-profile__bio biography-copy is-collapsed");
      biography.id = "speaker-profile-biography";
      const toggle = make("button", "Show more", "speaker-profile__bio-toggle biography-toggle text-link");
      toggle.type = "button";
      toggle.setAttribute("aria-controls", biography.id);
      toggle.setAttribute("aria-expanded", "false");
      window.SessionBuddyBiographyDisclosure.attach(biography, toggle);
      story.append(biography, toggle);
    }
    if (speaker.links?.length) {
      const links = make("p", undefined, "speaker-links");
      speaker.links.forEach((value, index) => {
        const anchor = make("a", (() => { try { return new URL(value).hostname.replace(/^www\./, ""); } catch (_) { return "Profile"; } })());
        anchor.href = value; anchor.target = "_blank"; anchor.rel = "noopener noreferrer";
        if (index) links.append(" · ");
        links.append(anchor);
      });
      story.append(links);
    }
    if (speaker.sessions.length) {
      story.append(make("h3", "On the program", "speaker-profile__sessions-title"));
      const list = document.createElement("ul"); list.className = "speaker-profile__sessions";
      speaker.sessions.forEach((session) => {
        const item = document.createElement("li");
        const sessionLink = make("a", session.title); sessionLink.href = `/events/${encodeURIComponent(eventId)}/schedule?search=${encodeURIComponent(session.title)}`;
        item.append(sessionLink);
        if (session.starts_at_ms) {
          const when = new Intl.DateTimeFormat(undefined, {
            dateStyle: "medium", timeStyle: "short", timeZone: state.event?.time_zone,
          }).format(new Date(session.starts_at_ms));
          item.append(make("span", `${when} · ${session.room_name}${session.track_name ? ` · ${session.track_name}` : ""}`, "muted"));
        }
        list.append(item);
      });
      story.append(list);
    }
    content.replaceChildren(portrait, story);
    dialog.showModal();
  }
  function speakerCard(speaker) {
    const card = document.createElement("article"); card.className = "speaker-card";
    const media = document.createElement("div"); media.className = "speaker-card__media";
    if (speaker.headshot_url) { const image = document.createElement("img"); image.src = speaker.headshot_url; image.alt = ""; image.loading = "lazy"; media.append(image); }
    else media.append(make("span", speaker.display_name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase(), "speaker-initials"));
    const body = document.createElement("div"); body.className = "speaker-card__body"; body.append(make("h2", speaker.display_name));
    const role = [speaker.job_title, speaker.company].filter(Boolean).join(" · "); if (role) body.append(make("p", role, "muted"));
    if (!galleryLayout && speaker.biography) body.append(make("p", speaker.biography, "speaker-bio"));
    if (speaker.sessions.length) {
      const list = document.createElement("ul"); list.className = "speaker-program-list";
      const sessions = [...speaker.sessions].sort((left, right) => {
        const leftStart = Number.isFinite(left.starts_at_ms) ? left.starts_at_ms : Number.MAX_SAFE_INTEGER;
        const rightStart = Number.isFinite(right.starts_at_ms) ? right.starts_at_ms : Number.MAX_SAFE_INTEGER;
        return leftStart - rightStart || collator.compare(normalizedName(left.title), normalizedName(right.title)) || collator.compare(String(left.id), String(right.id));
      });
      sessions.slice(0, galleryLayout ? 1 : 2).forEach((session) => {
        const item = document.createElement("li");
        const link = make("a", session.title); link.href = `/events/${encodeURIComponent(eventId)}/schedule?search=${encodeURIComponent(session.title)}`;
        item.append(make("span", session.track_name || "Program", "speaker-program-list__track"), link); list.append(item);
      });
      body.append(list);
    }
    const details = make("button", "View profile", "speaker-card__action secondary");
    details.type = "button";
    details.addEventListener("click", () => openProfile(speaker, role));
    body.append(details);
    card.append(media, body); return card;
  }
  function render() {
    const query = byId("speaker-search").value.trim().toLowerCase();
    const visibleQuery = byId("speaker-search").value.trim();
    const querySuffix = visibleQuery ? `?q=${encodeURIComponent(visibleQuery)}` : "";
    byId("speaker-list-link").href = `/events/${encodeURIComponent(eventId)}/speakers${querySuffix}`;
    byId("speaker-gallery-link").href = `/events/${encodeURIComponent(eventId)}/gallery${querySuffix}`;
    const speakers = query ? state.speakers.filter((speaker) => [speaker.display_name, speaker.job_title, speaker.company, speaker.biography, ...speaker.sessions.map((session) => session.title)].join(" ").toLowerCase().includes(query)) : state.speakers;
    byId("speaker-grid").replaceChildren(...speakers.map(speakerCard));
    byId("empty").hidden = speakers.length !== 0;
    byId("speaker-search-label").hidden = state.speakers.length === 0;
    byId("status").textContent = query
      ? `${speakers.length} of ${state.speakers.length} speaker${state.speakers.length === 1 ? "" : "s"} shown`
      : state.speakers.length
        ? `${speakers.length} speaker${speakers.length === 1 ? "" : "s"} announced`
        : "No speakers announced yet";
  }
  async function load() {
    if (!eventId) throw new Error("Invalid speaker gallery link.");
    const body = await window.SessionBuddyApi.request(`/api/v1/public/events/${encodeURIComponent(eventId)}/speakers`);
    state.event = body.event;
    document.documentElement.style.setProperty("--event-accent", body.event.accent_color || "#3159d9");
    const query = new URLSearchParams(location.search).get("q") || "";
    const querySuffix = query ? `?q=${encodeURIComponent(query)}` : "";
    byId("speaker-list-link").href = `/events/${encodeURIComponent(eventId)}/speakers${querySuffix}`;
    byId("speaker-gallery-link").href = `/events/${encodeURIComponent(eventId)}/gallery${querySuffix}`;
    byId(galleryLayout ? "speaker-gallery-link" : "speaker-list-link").setAttribute("aria-current", "page");
    window.SessionBuddyPublicEventMasthead.render(document.querySelector("[data-public-event-masthead]"), {
      event: { id: body.event.id, name: body.event.name, accentColor: body.event.accent_color, logoUrl: body.event.logo_url, coverUrl: body.event.cover_image_url, cfpUrl: body.event.cfp_url },
      active: "speakers",
      embedded: document.body.classList.contains("embedded"),
    });
    state.speakers = [...body.data].sort(compareSpeakers); render();
    if (query) { byId("speaker-search").value = query; render(); }
    const requested = new URLSearchParams(location.search).get("speaker");
    const selected = state.speakers.find((speaker) => speaker.id === requested);
    if (selected) {
      const role = [selected.job_title, selected.company].filter(Boolean).join(" · ");
      openProfile(selected, role);
    }
  }
  byId("speaker-search").addEventListener("input", render);
  if (galleryLayout) {
    byId("speaker-grid").setAttribute("aria-label", "Speaker photo gallery");
  }
  load().catch((error) => { byId("status").textContent = window.SessionBuddyApi.message(error, "The speaker gallery is unavailable. Try again."); byId("status").classList.add("error-text"); });
})();

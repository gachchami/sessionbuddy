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
      toggle.addEventListener("click", () => {
        const expanded = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", String(!expanded));
        toggle.textContent = expanded ? "Show more" : "Show less";
        biography.classList.toggle("is-collapsed", expanded);
      });
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
    const biography = byId("speaker-profile-biography");
    const toggle = story.querySelector(".speaker-profile__bio-toggle");
    if (biography && toggle) requestAnimationFrame(() => { toggle.hidden = biography.scrollHeight <= biography.clientHeight; });
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
      speaker.sessions.slice(0, galleryLayout ? 1 : 2).forEach((session) => {
        const item = document.createElement("li"); item.append(make("span", session.track_name || "Program", "speaker-program-list__track"), make("strong", session.title)); list.append(item);
      });
      body.append(list);
    }
    const details = make("button", "Meet the speaker", "speaker-card__action secondary");
    details.type = "button";
    details.addEventListener("click", () => openProfile(speaker, role));
    body.append(details);
    card.append(media, body); return card;
  }
  function render() {
    const query = byId("speaker-search").value.trim().toLowerCase();
    const speakers = query ? state.speakers.filter((speaker) => [speaker.display_name, speaker.job_title, speaker.company, speaker.biography, ...speaker.sessions.map((session) => session.title)].join(" ").toLowerCase().includes(query)) : state.speakers;
    byId("speaker-grid").replaceChildren(...speakers.map(speakerCard));
    byId("empty").hidden = speakers.length !== 0;
    byId("status").textContent = query
      ? `${speakers.length} of ${state.speakers.length} speaker${state.speakers.length === 1 ? "" : "s"} shown`
      : `${speakers.length} speaker${speakers.length === 1 ? "" : "s"} on the published program`;
  }
  async function load() {
    if (!eventId) throw new Error("Invalid speaker gallery link.");
    const body = await window.SessionBuddyApi.request(`/api/v1/public/events/${encodeURIComponent(eventId)}/speakers`);
    state.event = body.event;
    document.documentElement.style.setProperty("--event-accent", body.event.accent_color || "#3159d9"); byId("event-name").textContent = body.event.name;
    byId("schedule-link").href = `/events/${encodeURIComponent(eventId)}/schedule`;
    byId("speaker-list-link").href = `/events/${encodeURIComponent(eventId)}/speakers`;
    byId("speaker-gallery-link").href = `/events/${encodeURIComponent(eventId)}/gallery`;
    byId(galleryLayout ? "speaker-gallery-link" : "speaker-list-link").setAttribute("aria-current", "page");
    const monogram = normalizedName(body.event.name).slice(0, 2).toUpperCase() || "EV";
    byId("event-cover-name").textContent = body.event.name;
    byId("event-cover-monogram").textContent = monogram;
    byId("event-logo-fallback").textContent = monogram;
    if (body.event.logo_url) { byId("event-logo").src = body.event.logo_url; byId("event-logo").alt = `${body.event.name} logo`; byId("event-logo").hidden = false; byId("event-logo-fallback").hidden = true; }
    if (body.event.cover_image_url) { byId("event-cover").src = body.event.cover_image_url; byId("event-cover").alt = `${body.event.name} cover`; byId("event-cover").hidden = false; byId("event-cover-fallback").hidden = true; }
    state.speakers = [...body.data].sort(compareSpeakers); render();
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

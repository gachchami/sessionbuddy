(() => {
  "use strict";
  const match = location.pathname.match(/^(?:\/embeds)?\/events\/([^/]+)\/(?:speakers|gallery)$/);
  document.body.classList.toggle("embedded", location.pathname.startsWith("/embeds/"));
  const galleryLayout = location.pathname.endsWith("/gallery");
  document.body.dataset.layout = galleryLayout ? "gallery" : "directory";
  let eventId = ""; try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const state = { speakers: [] };
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
  function speakerCard(speaker) {
    const card = document.createElement("article"); card.className = "speaker-card";
    const media = document.createElement("div"); media.className = "speaker-card__media";
    if (speaker.headshot_url) { const image = document.createElement("img"); image.src = speaker.headshot_url; image.alt = ""; image.loading = "lazy"; media.append(image); }
    else media.append(make("span", speaker.display_name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase(), "speaker-initials"));
    const body = document.createElement("div"); body.className = "speaker-card__body"; body.append(make("h2", speaker.display_name));
    const role = [speaker.job_title, speaker.company].filter(Boolean).join(" · "); if (role) body.append(make("p", role, "muted"));
    if (!galleryLayout && speaker.biography) body.append(make("p", speaker.biography, "speaker-bio"));
    if (speaker.sessions.length) { const list = document.createElement("ul"); list.className = "session-chips"; speaker.sessions.forEach((session) => list.append(make("li", session.title))); body.append(list); }
    if (galleryLayout) {
      const details = make("button", "View profile", "secondary");
      details.type = "button";
      details.addEventListener("click", () => {
        const dialog = byId("speaker-profile");
        const content = byId("speaker-profile-content");
        content.replaceChildren(make("h2", speaker.display_name));
        if (role) content.append(make("p", role, "muted"));
        if (speaker.biography) content.append(make("p", speaker.biography));
        if (speaker.sessions.length) {
          const list = document.createElement("ul");
          speaker.sessions.forEach((session) => list.append(make("li", session.title)));
          content.append(list);
        }
        dialog.showModal();
      });
      body.append(details);
    }
    card.append(media, body); return card;
  }
  function render() {
    const query = byId("speaker-search").value.trim().toLowerCase();
    const speakers = query ? state.speakers.filter((speaker) => [speaker.display_name, speaker.job_title, speaker.company, speaker.biography, ...speaker.sessions.map((session) => session.title)].join(" ").toLowerCase().includes(query)) : state.speakers;
    byId("speaker-grid").replaceChildren(...speakers.map(speakerCard));
    byId("empty").hidden = speakers.length !== 0;
    byId("status").textContent = `${speakers.length} accepted speaker${speakers.length === 1 ? "" : "s"}${query ? " match your search" : ""}.`;
  }
  async function load() {
    if (!eventId) throw new Error("Invalid speaker gallery link.");
    const body = await window.SessionBuddyApi.request(`/api/v1/public/events/${encodeURIComponent(eventId)}/speakers`);
    document.documentElement.style.setProperty("--blue", body.event.accent_color || "#3159d9"); byId("event-name").textContent = body.event.name;
    byId("schedule-link").href = `/events/${encodeURIComponent(eventId)}/schedule`;
    if (body.event.logo_url) { byId("event-logo").src = body.event.logo_url; byId("event-logo").alt = `${body.event.name} logo`; byId("event-logo").hidden = false; }
    state.speakers = body.data; render();
  }
  byId("speaker-search").addEventListener("input", render);
  if (galleryLayout) {
    byId("speaker-search-label").hidden = true;
    byId("speaker-grid").setAttribute("aria-label", "Speaker photo gallery");
  }
  load().catch((error) => { byId("status").textContent = window.SessionBuddyApi.message(error, "The speaker gallery is unavailable. Try again."); byId("status").classList.add("error-text"); });
})();

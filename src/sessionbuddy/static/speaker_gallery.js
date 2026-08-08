(() => {
  "use strict";
  const match = location.pathname.match(/^(?:\/embeds)?\/events\/([^/]+)\/speakers$/);
  document.body.classList.toggle("embedded", location.pathname.startsWith("/embeds/"));
  let eventId = ""; try { eventId = match ? decodeURIComponent(match[1]) : ""; } catch (_) { eventId = ""; }
  const byId = (id) => document.getElementById(id);
  const make = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
  function speakerCard(speaker) {
    const card = document.createElement("article"); card.className = "speaker-card";
    const media = document.createElement("div"); media.className = "speaker-card__media";
    if (speaker.headshot_url) { const image = document.createElement("img"); image.src = speaker.headshot_url; image.alt = ""; image.loading = "lazy"; media.append(image); }
    else media.append(make("span", speaker.display_name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase(), "speaker-initials"));
    const body = document.createElement("div"); body.className = "speaker-card__body"; body.append(make("h2", speaker.display_name));
    const role = [speaker.job_title, speaker.company].filter(Boolean).join(" · "); if (role) body.append(make("p", role, "muted"));
    if (speaker.biography) body.append(make("p", speaker.biography, "speaker-bio"));
    if (speaker.sessions.length) { const list = document.createElement("ul"); list.className = "session-chips"; speaker.sessions.forEach((session) => list.append(make("li", session.title))); body.append(list); }
    card.append(media, body); return card;
  }
  async function load() {
    if (!eventId) throw new Error("Invalid speaker gallery link.");
    const response = await fetch(`/api/v1/public/events/${encodeURIComponent(eventId)}/speakers`, { headers: { accept: "application/json" } });
    const body = await response.json(); if (!response.ok) throw new Error(body?.error?.message || "Speaker gallery unavailable.");
    document.documentElement.style.setProperty("--blue", body.event.accent_color || "#3159d9"); byId("event-name").textContent = body.event.name;
    byId("schedule-link").href = `/events/${encodeURIComponent(eventId)}/schedule`;
    if (body.event.logo_url) { byId("event-logo").src = body.event.logo_url; byId("event-logo").alt = `${body.event.name} logo`; byId("event-logo").hidden = false; }
    byId("speaker-grid").replaceChildren(...body.data.map(speakerCard)); byId("empty").hidden = body.data.length !== 0; byId("status").textContent = `${body.data.length} accepted speaker${body.data.length === 1 ? "" : "s"}.`;
  }
  load().catch((error) => { byId("status").textContent = error.message; byId("status").classList.add("error-text"); });
})();

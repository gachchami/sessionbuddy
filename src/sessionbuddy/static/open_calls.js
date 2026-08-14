(() => {
  "use strict";
  const root = document.getElementById("open-calls");
  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };

  async function load() {
    try {
      const response = await window.SessionBuddyApi.request("/api/v1/public/calls");
      const calls = response.data.filter((call) => call.cfp_state !== "closed");
      root.replaceChildren();
      if (!calls.length) {
        root.append(make("p", "No calls are open or scheduled right now.", "public-events-empty"));
        return;
      }
      for (const call of calls) {
        const card = make("article", undefined, "public-event-card");
        card.append(make("p", window.SessionBuddyCfpState.copy(call), "role-label"));
        card.append(make("h3", call.name));
        card.append(make("p", [call.location, call.delivery_mode.replaceAll("_", " ")].filter(Boolean).join(" · ")));
        const actions = make("div", undefined, "public-event-actions");
        const eventKey = call.id.replace(/[^a-z0-9]/gi, "").slice(0, 6).toLowerCase();
        const link = make("a", call.cfp_state === "open" ? "Submit a proposal →" : "View call →");
        link.href = `/cfp/${eventKey}/${encodeURIComponent(call.cfp_slug)}`;
        actions.append(link); card.append(actions); root.append(card);
      }
    } catch (_) {
      const error = make("p", "Calls could not be loaded. Try again later.", "public-events-empty");
      error.setAttribute("role", "alert"); root.replaceChildren(error);
    } finally { root.setAttribute("aria-busy", "false"); }
  }
  load();
})();

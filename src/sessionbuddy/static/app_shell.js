(() => {
  "use strict";

  const shell = document.querySelector("[data-auth-shell]");
  const landingAccount = document.querySelector("[data-landing-account]");
  if (!shell && !landingAccount) return;

  const make = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };

  function link(label, href, current = false) {
    const node = make("a", label);
    node.href = href;
    if (current) node.setAttribute("aria-current", "page");
    return node;
  }

  function icon(name) {
    const paths = {
      home: ["M3 11.5 12 4l9 7.5", "M5.5 10v10h13V10", "M9.5 20v-6h5v6"],
      building: ["M5 21V4h10v17", "M15 9h4v12", "M8 8h4", "M8 12h4", "M8 16h4"],
      calendar: ["M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2Z", "M8 2v4", "M16 2v4", "M3 9h18"],
      people: ["M16 20v-1.5a4.5 4.5 0 0 0-4.5-4.5h-3A4.5 4.5 0 0 0 4 18.5V20", "M10 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z", "M18 11a3 3 0 0 0 0-6", "M18 14a4 4 0 0 1 4 4v2"],
      review: ["M5 3h14v18H5z", "M8 8h8", "M8 12h5", "m8 16 1.5 1.5L13 14"],
      mic: ["M12 15a4 4 0 0 0 4-4V6a4 4 0 0 0-8 0v5a4 4 0 0 0 4 4Z", "M5 11a7 7 0 0 0 14 0", "M12 18v4", "M9 22h6"],
      overview: ["M4 4h6v6H4z", "M14 4h6v6h-6z", "M4 14h6v6H4z", "M14 14h6v6h-6z"],
      form: ["M6 3h12v18H6z", "M9 8h6", "M9 12h6", "M9 16h4"],
      access: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M19 8v6", "M16 11h6"],
      tasks: ["M5 4h14v16H5z", "m8 9 2 2 4-4", "M12 16h5"],
      resource: ["M4 5h16v14H4z", "M8 9h8", "M8 13h5"],
      agenda: ["M6 3h12v18H6z", "M9 7h6", "M9 11h6", "M9 15h4"],
      external: ["M14 4h6v6", "M20 4 11 13", "M18 13v7H4V6h7"],
      account: ["M20 21a8 8 0 0 0-16 0", "M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z"]
    };
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.classList.add("sb-nav-icon");
    for (const definition of paths[name] || paths.overview) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", definition);
      svg.append(path);
    }
    return svg;
  }

  function navLink(label, href, iconName, current = false) {
    const node = link("", href, current);
    node.append(icon(iconName), make("span", label));
    return node;
  }

  function eventIdFromLocation() {
    const match = location.pathname.match(/^\/admin\/events\/([^/]+)/);
    if (match) {
      try { return decodeURIComponent(match[1]); } catch (_) { return ""; }
    }
    return new URLSearchParams(location.search).get("event_id") || "";
  }

  function roleSet(session) {
    const roles = new Set();
    for (const item of session.organization_access || []) {
      for (const role of item.roles || []) roles.add(role);
    }
    for (const item of session.event_access || []) {
      for (const role of item.roles || []) roles.add(role);
    }
    return roles;
  }

  function displayName(session) {
    return session.display_name || String(session.email || "Account").split("@", 1)[0];
  }

  function initials(session) {
    const value = displayName(session);
    const parts = value.trim().split(/[\s._+-]+/).filter(Boolean);
    return (parts.length > 1 ? `${parts[0][0]}${parts[parts.length - 1][0]}` : value.slice(0, 2)).toUpperCase();
  }

  function accountMenu(session, roles) {
    const details = make("details", undefined, "sb-account");
    const summary = make("summary");
    summary.setAttribute("aria-label", `Profile and account for ${session.email}`);
    const avatar = make("span", initials(session), "sb-account__avatar");
    avatar.setAttribute("aria-hidden", "true");
    const identity = make("span", undefined, "sb-account__identity");
    identity.append(make("strong", displayName(session)), make("span", "Account"));
    summary.append(avatar, identity);

    const menu = make("div", undefined, "sb-account__menu");
    const menuHeader = make("div", undefined, "sb-account__menu-header");
    menuHeader.append(make("strong", displayName(session)), make("span", session.email));
    menu.append(menuHeader, navLink("Account & access", "/account", "account"));
    if (roles.has("organization_admin") || roles.has("event_admin")) {
      menu.append(navLink("Home", "/admin", "overview"));
    }
    if (roles.has("evaluator")) menu.append(navLink("Reviews", "/reviews", "review"));
    if (roles.has("speaker")) menu.append(navLink("Speaker profile", "/speaker#profile", "mic"));
    const signOut = make("button", "Sign out", "sb-account__sign-out");
    signOut.type = "button";
    signOut.addEventListener("click", async () => {
      signOut.disabled = true;
      try {
        await window.SessionBuddyApi.request("/api/v1/session/logout", {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
          body: "{}"
        });
        location.assign("/");
      } catch (_) {
        signOut.disabled = false;
        signOut.textContent = "Try sign out again";
      }
    });
    menu.append(signOut);
    details.append(summary, menu);
    return details;
  }

  function currentSection() {
    if (location.pathname === "/admin") return "home";
    if (location.pathname.startsWith("/admin/speakers") || /\/speakers(?:\/|$)/.test(location.pathname)) return "speakers";
    if (location.pathname.startsWith("/reviews") || location.pathname.includes("evaluation-rounds")) return "reviews";
    if (location.pathname.startsWith("/speaker")) return "speaker";
    if (location.pathname.startsWith("/admin")) return "events";
    if (location.pathname.startsWith("/account")) return "account";
    return "";
  }

  function pageLabel(section, eventId) {
    if (eventId) {
      if (location.pathname.includes("/speakers")) return "Event speakers";
      if (location.pathname.includes("/access")) return "People & access";
      if (location.pathname.includes("/onboarding")) return "Speaker onboarding";
      if (location.pathname.includes("/workspace")) return "Resources";
      if (location.pathname.includes("/agenda")) return "Agenda";
      if (location.pathname.endsWith("/cfp")) return "Call for speakers";
      if (location.pathname === "/admin/programs") return "Call for speakers";
      return "Event overview";
    }
    return { home: "Home", events: "Events", speakers: "Speakers", reviews: "Reviews", speaker: "Speaker portal", account: "Account" }[section] || "Home";
  }

  function eventNav(eventId) {
    const group = make("div", undefined, "sb-sidebar__group sb-sidebar__event");
    group.append(make("p", "Current event", "sb-sidebar__label"));
    const nav = make("nav", undefined, "sb-sidebar__nav");
    nav.setAttribute("aria-label", "Current event");
    const encoded = encodeURIComponent(eventId);
    const prefix = `/admin/events/${encoded}`;
    const items = [
      ["Overview", prefix, "overview"],
      ["Call for speakers", `${prefix}/cfp`, "form"],
      ["People & access", `${prefix}/access`, "access"],
      ["Speakers", `${prefix}/speakers`, "mic"],
      ["Onboarding", `${prefix}/onboarding`, "tasks"],
      ["Resources", `${prefix}/workspace`, "resource"],
      ["Agenda", `${prefix}/agenda`, "agenda"],
      ["Public schedule", `/events/${encoded}/schedule`, "external"]
    ];
    for (const [label, href, iconName] of items) {
      const target = new URL(href, location.origin);
      const current = target.pathname === location.pathname && target.search === location.search;
      nav.append(navLink(label, href, iconName, current));
    }
    group.append(nav);
    return group;
  }

  function renderShell(session) {
    const roles = roleSet(session);
    const organizer = roles.has("organization_admin") || roles.has("event_admin");
    const section = currentSection();
    const currentEventId = eventIdFromLocation();
    document.body.classList.add("sb-shell-authenticated");

    const sidebar = make("aside", undefined, "sb-sidebar");
    sidebar.id = "workspace-navigation";
    const brand = link("", organizer ? "/admin" : roles.has("speaker") ? "/speaker" : "/reviews");
    brand.className = "sb-app-brand";
    const mark = make("span", "S", "sb-app-brand__mark");
    mark.setAttribute("aria-hidden", "true");
    const brandText = make("span", undefined, "sb-app-brand__text");
    brandText.append(make("strong", "SessionBuddy"));
    brand.append(mark, brandText);
    sidebar.append(brand);

    const primaryGroup = make("div", undefined, "sb-sidebar__group");
    primaryGroup.append(make("p", "Main", "sb-sidebar__label"));
    const nav = make("nav", undefined, "sb-sidebar__nav");
    nav.setAttribute("aria-label", "Main navigation");
    if (organizer) {
      nav.append(
        navLink("Home", "/admin", "home", section === "home"),
        navLink("Organizations", "/admin#organizations", "building"),
        navLink("Events", "/admin/events", "calendar", section === "events" && !currentEventId),
        navLink("Speakers", "/admin/speakers", "people", section === "speakers" && !currentEventId)
      );
    }
    if (roles.has("evaluator") || organizer) nav.append(navLink("Reviews", "/reviews", "review", section === "reviews"));
    if (roles.has("speaker")) nav.append(navLink("Speaker portal", "/speaker", "mic", section === "speaker"));
    primaryGroup.append(nav);
    sidebar.append(primaryGroup);
    if (organizer && currentEventId) sidebar.append(eventNav(currentEventId));

    const topbar = make("div", undefined, "sb-topbar");
    const menuButton = make("button", undefined, "sb-menu-button");
    menuButton.type = "button";
    menuButton.setAttribute("aria-label", "Open navigation");
    menuButton.setAttribute("aria-controls", sidebar.id);
    menuButton.setAttribute("aria-expanded", "false");
    menuButton.append(make("span"), make("span"), make("span"));
    const crumb = make("div", undefined, "sb-topbar__title");
    crumb.append(make("strong", pageLabel(section, currentEventId)));
    topbar.append(menuButton, crumb, accountMenu(session, roles));

    const backdrop = make("button", undefined, "sb-nav-backdrop");
    backdrop.type = "button";
    backdrop.setAttribute("aria-label", "Close navigation");
    const closeNavigation = () => {
      document.body.classList.remove("sb-navigation-open");
      menuButton.setAttribute("aria-expanded", "false");
      menuButton.setAttribute("aria-label", "Open navigation");
    };
    const toggleNavigation = () => {
      const open = document.body.classList.toggle("sb-navigation-open");
      menuButton.setAttribute("aria-expanded", String(open));
      menuButton.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
    };
    menuButton.addEventListener("click", toggleNavigation);
    backdrop.addEventListener("click", closeNavigation);
    sidebar.addEventListener("click", (event) => {
      if (event.target.closest("a")) closeNavigation();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeNavigation();
    });

    shell.className = "sb-app-shell";
    shell.replaceChildren(sidebar, topbar, backdrop);
  }

  function renderLandingAccount(session) {
    const roles = roleSet(session);
    const wrapper = make("span", undefined, "sb-landing-account");
    let label = "View account";
    let href = "/account";
    if (roles.has("organization_admin") || roles.has("event_admin")) [label, href] = ["Open app", "/admin"];
    else if (roles.has("speaker")) [label, href] = ["Open portal", "/speaker"];
    else if (roles.has("evaluator")) [label, href] = ["Open reviews", "/reviews"];
    wrapper.append(link(label, href));
    wrapper.append(accountMenu(session, roles));
    landingAccount.replaceChildren(wrapper);
    for (const entry of document.querySelectorAll("[data-auth-entry]")) {
      entry.textContent = label;
      entry.href = href;
    }
  }

  function renderGuestShell() {
    document.body.classList.add("sb-shell-guest");
    const inner = make("div", undefined, "sb-guest-header__inner");
    const brand = link("", "/");
    brand.className = "sb-app-brand";
    const mark = make("span", "S", "sb-app-brand__mark");
    mark.setAttribute("aria-hidden", "true");
    brand.append(mark, make("span", "SessionBuddy"));
    const signIn = link("Sign in", `/sign-in?redirect=${encodeURIComponent(location.pathname + location.search)}`);
    signIn.className = "sb-guest-sign-in";
    inner.append(brand, signIn);
    shell.className = "sb-guest-header";
    shell.replaceChildren(inner);
  }

  function renderUnavailableShell() {
    document.body.classList.add("sb-shell-guest");
    const inner = make("div", undefined, "sb-guest-header__inner");
    const brand = link("", "/");
    brand.className = "sb-app-brand";
    const mark = make("span", "S", "sb-app-brand__mark");
    mark.setAttribute("aria-hidden", "true");
    brand.append(mark, make("span", "SessionBuddy"));
    const retry = make("button", "Try again", "sb-guest-sign-in");
    retry.type = "button";
    retry.addEventListener("click", () => location.reload());
    inner.append(brand, make("span", "Temporarily unavailable"), retry);
    shell.className = "sb-guest-header";
    shell.replaceChildren(inner);
  }

  async function initialize() {
    let session;
    try {
      session = await window.SessionBuddyApi.request("/api/v1/auth/session");
    } catch (error) {
      if (error.status !== 401) {
        if (shell) renderUnavailableShell();
        return;
      }
      if (landingAccount) {
        try {
          const setupState = await window.SessionBuddyApi.request("/api/v1/setup/status");
          if (!setupState.configured) {
            location.assign("/setup");
            return;
          }
        } catch (_) { /* The public landing page remains available if setup status is unavailable. */ }
      }
      if (shell?.hasAttribute("data-allow-guest")) renderGuestShell();
      else if (shell) location.assign(`/sign-in?redirect=${encodeURIComponent(location.pathname + location.search)}`);
      return;
    }
    if (shell) renderShell(session);
    if (landingAccount) renderLandingAccount(session);
    window.dispatchEvent(new CustomEvent("sessionbuddy:session", { detail: session }));
  }

  initialize().catch(() => { if (shell) renderUnavailableShell(); });
})();

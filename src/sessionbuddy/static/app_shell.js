(() => {
  "use strict";

  const shell = document.querySelector("[data-auth-shell]");
  const landingAccount = document.querySelector("[data-landing-account]");
  const publicEvents = document.querySelector("[data-public-events]");
  if (!shell && !landingAccount && !publicEvents) return;

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
      account: ["M20 21a8 8 0 0 0-16 0", "M12 13a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z"],
      chevron: ["m9 18 6-6-6-6"],
      collapse: ["m14 18-6-6 6-6", "M20 4v16"],
      check: ["m5 12 4 4L19 6"],
      logout: ["M10 17l5-5-5-5", "M15 12H3", "M15 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4"]
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
    node.setAttribute("aria-label", label);
    node.title = label;
    node.append(icon(iconName), make("span", label));
    return node;
  }

  function brandMark() {
    const mark = make("img", undefined, "sb-app-brand__mark");
    mark.src = "/landing/assets/sessionbuddy-favicon.svg";
    mark.alt = "";
    return mark;
  }

  function eventIdFromLocation() {
    const match = location.pathname.match(/^\/admin\/events\/([^/]+)/);
    if (match) {
      try { return decodeURIComponent(match[1]); } catch (_) { return ""; }
    }
    return new URLSearchParams(location.search).get("event_id") || document.body.dataset.eventId || "";
  }

  const supportedRoles = new Set(["organizer", "reviewer", "speaker"]);

  function roleChoices(session) {
    return (session.account_roles || [])
      .filter((role) => supportedRoles.has(role))
      .map((role) => ({ role, organizationId: "", eventId: "" }));
  }

  const roleLabel = (role) => ({
    organizer: "Organizer",
    reviewer: "Reviewer",
    speaker: "Speaker"
  }[role] || role);

  // Mirrors the server's authority model in platform/authorization/policy.py:
  // owner/manage/edit may work inside a resource, but only owner/manage may
  // administer its access. Keeping these two lists next to each other is what
  // stops the console from offering a control the server refuses, or hiding
  // one it allows.
  const CONTENT_PERMISSIONS = ["owner", "manage", "edit"];
  const ADMIN_PERMISSIONS = ["owner", "manage"];

  const grants = (item) => item.permissions || [];
  const holds = (item, allowed) => grants(item).some((permission) => allowed.includes(permission));

  function eventsWithContentAccess(session) {
    return (session.event_access || []).filter((item) => holds(item, CONTENT_PERMISSIONS));
  }

  function organizerDestination(session) {
    if (canManageOrganization(session)) return "/admin";
    const event = eventsWithContentAccess(session)[0];
    return event ? `/admin/events/${encodeURIComponent(event.event_id)}` : null;
  }

  const roleDestination = (choice, session) => {
    if (!choice || !supportedRoles.has(choice.role)) return null;
    if (choice.role === "organizer") return organizerDestination(session);
    if (choice.role === "reviewer") return "/reviews";
    if (choice.role === "speaker") return "/speaker";
    return null;
  };

  function sameRole(a, b) {
    return Boolean(a && b && a.role === b.role && a.organizationId === b.organizationId && a.eventId === b.eventId);
  }

  function activeRole(session) {
    const requested = String(session.active_role || "").trim();
    if (!supportedRoles.has(requested)) return null;
    return roleChoices(session).find((choice) => choice.role === requested) || null;
  }

  function roleSet(session) {
    const active = activeRole(session);
    return new Set(active ? [active.role] : []);
  }

  function canManageOrganization(session) {
    return (session.organization_access || []).some((item) => holds(item, ADMIN_PERMISSIONS));
  }

  function worksInEventDirectly(session, eventId) {
    // Event authority is exact and never inherited from an organization. An
    // `edit` grant is real authority over the event's content, so it earns the
    // event navigation even though it may not administer access.
    if (!eventId) return false;
    return eventsWithContentAccess(session).some((item) => item.event_id === eventId);
  }

  function administersEventDirectly(session, eventId) {
    if (!eventId) return false;
    return (session.event_access || []).some((item) =>
      item.event_id === eventId && holds(item, ADMIN_PERMISSIONS));
  }

  function displayName(session) {
    const configured = String(session.display_name || "").trim();
    if (configured) return configured;
    const active = activeRole(session);
    if (active) return roleLabel(active.role);
    return "Account";
  }

  function initials(session) {
    const value = displayName(session);
    const parts = value.trim().split(/[\s._+-]+/).filter(Boolean);
    return (parts.length > 1 ? `${parts[0][0]}${parts[parts.length - 1][0]}` : value.slice(0, 2)).toUpperCase();
  }

  function accountMenu(session, roles) {
    const active = activeRole(session);
    const choices = roleChoices(session);
    const details = make("details", undefined, "sb-account");
    const summary = make("summary");
    summary.setAttribute("aria-label", `Profile and account for ${session.email}`);
    const avatar = make("span", initials(session), "sb-account__avatar");
    avatar.setAttribute("aria-hidden", "true");
    const identity = make("span", undefined, "sb-account__identity");
    const triggerLabel = make("strong", displayName(session));
    if (active) triggerLabel.append(make("span", ` · ${roleLabel(active.role)}`));
    identity.append(triggerLabel);
    summary.append(avatar, identity, icon("chevron"));

    const menu = make("div", undefined, "sb-account__menu");
    menu.setAttribute("aria-label", "Account menu");
    menu.append(make("p", "Account", "sb-account__menu-title"));
    const menuHeader = make("div", undefined, "sb-account__menu-header");
    const menuAvatar = make("span", initials(session), "sb-account__avatar sb-account__menu-avatar");
    menuAvatar.setAttribute("aria-hidden", "true");
    const menuIdentity = make("span", undefined, "sb-account__menu-identity");
    menuIdentity.append(make("strong", displayName(session)), make("span", session.email));
    menuHeader.append(menuAvatar, menuIdentity);
    menu.append(menuHeader);
    if (choices.length > 1) {
      const switcher = make("div", undefined, "sb-role-switcher");
      switcher.setAttribute("role", "group");
      switcher.setAttribute("aria-labelledby", "sb-role-switcher-label");
      const switcherLabel = make("p", "Switch role", "sb-role-switcher__label");
      switcherLabel.id = "sb-role-switcher-label";
      switcher.append(switcherLabel);
      for (const choice of choices) {
        const button = make("button", undefined, "sb-role-option");
        button.type = "button";
        const isActive = sameRole(choice, active);
        button.setAttribute("aria-pressed", String(isActive));
        if (isActive) button.setAttribute("aria-disabled", "true");
        const scope = choice.role === "speaker"
          ? "Speaker home"
          : choice.role === "reviewer"
            ? "Assigned reviews"
            : choice.role === "organizer"
              ? (session.organization_name || "Organization workspace")
              : "Account role";
        const roleIconName = choice.role === "speaker" ? "mic" : choice.role === "reviewer" ? "review" : "calendar";
        const roleCopy = make("span", undefined, "sb-role-option__copy");
        roleCopy.append(make("strong", roleLabel(choice.role)), make("small", scope));
        button.append(icon(roleIconName), roleCopy);
        if (isActive) {
          const status = make("span", undefined, "sb-role-option__status");
          const check = make("span", undefined, "sb-role-option__check");
          check.append(icon("check"));
          status.append(check, make("span", "Active"));
          button.append(status);
        }
        button.addEventListener("click", async () => {
          if (isActive) return;
          button.disabled = true;
          button.querySelector("small").textContent = "Switching…";
          try {
            await window.SessionBuddyApi.request("/api/v1/session/active-role", {
              method: "PUT",
              headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
              body: JSON.stringify({ role: choice.role })
            });
            const destination = roleDestination(choice, session);
            if (!destination) {
              renderSessionContractError();
              return;
            }
            location.assign(destination);
          } catch (error) {
            button.disabled = false;
            button.querySelector("small").textContent = "Could not switch role. Try again.";
          }
        });
        switcher.append(button);
      }
      menu.append(switcher);
    }
    const accountSettings = navLink("Account settings", "/account", "account");
    menu.append(accountSettings);
    const signOut = make("button", undefined, "sb-account__sign-out");
    signOut.type = "button";
    signOut.append(icon("logout"), make("span", "Sign out"));
    signOut.addEventListener("click", async () => {
      signOut.disabled = true;
      try {
        await window.SessionBuddyApi.request("/api/v1/session/logout", {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": session.csrf_token },
          body: "{}"
        });
        location.assign("/");
      } catch (error) {
        // A 401 (session already gone) means the user is, for all practical
        // purposes, signed out already; retrying can never succeed, so send
        // them home rather than trapping them in a retry loop.
        if (error && error.status === 401) {
          location.assign("/");
          return;
        }
        signOut.disabled = false;
        signOut.replaceChildren(icon("logout"), make("span", "Try sign out again"));
      }
    });
    menu.append(signOut);
    details.append(summary, menu);
    return details;
  }

  function closeOpenAccount({ restoreFocus = false } = {}) {
    const openAccount = document.querySelector("details.sb-account[open]");
    if (!openAccount) return false;
    openAccount.removeAttribute("open");
    if (restoreFocus) openAccount.querySelector("summary")?.focus();
    return true;
  }

  // Account menus appear in both sidebar and topbar-only shells. Register
  // dismissal once at the shared document boundary so early layout returns and
  // later shell rerenders cannot omit or duplicate these handlers.
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeOpenAccount({ restoreFocus: true });
  });
  document.addEventListener("click", (event) => {
    const openAccount = document.querySelector("details.sb-account[open]");
    if (openAccount && !openAccount.contains(event.target)) closeOpenAccount();
  });

  function currentSection() {
    if (location.pathname === "/admin") return "home";
    if (location.pathname.startsWith("/admin/people") || /\/speakers(?:\/|$)/.test(location.pathname)) return "speakers";
    if (location.pathname.startsWith("/reviews") || location.pathname.includes("evaluation-rounds")) return "reviews";
    if (location.pathname.startsWith("/speaker")) return "speaker";
    if (location.pathname.startsWith("/admin")) return "events";
    if (location.pathname.startsWith("/account")) return "account";
    return "";
  }

  function pageLabel(section, eventId) {
    if (eventId) {
      if (location.pathname.includes("/speakers")) return "Speakers";
      if (location.pathname.includes("/speaker-content")) return "Speakers";
      if (location.pathname.includes("/messages")) return "Speakers";
      if (location.pathname.includes("/onboarding")) return "Speakers";
      if (location.pathname.includes("/reviewers") || location.pathname.includes("/access")) return "Reviewers";
      if (location.pathname.includes("/workspace")) return "Share & integrations";
      if (location.pathname.includes("/agenda")) return "Agenda";
      if (location.pathname.endsWith("/cfp")) return "Call for Proposals";
      if (location.pathname.includes("/submissions")) return "Proposals";
      return "Overview";
    }
    return { home: "Home", events: "Events", speakers: "People", reviews: "Reviews", speaker: "Speaker home", account: "Account" }[section] || "Home";
  }

  function eventNav(eventId, canAdministerAccess = true) {
    const nav = make("nav", undefined, "sb-event-nav");
    nav.setAttribute("aria-label", "Event navigation");
    const encoded = encodeURIComponent(eventId);
    const prefix = `/admin/events/${encoded}`;
    // One flat list in the program's real order; every label is the page's
    // canonical name, used identically in the page heading and the topbar.
    const items = [
      ["Overview", prefix, "overview", [prefix]],
      ["CFP", `${prefix}/cfp`, "form", [`${prefix}/cfp`]],
      ["Proposals", `${prefix}/submissions`, "review", [`${prefix}/submissions`]],
      ["Speakers", `${prefix}/speakers`, "mic", [
        `${prefix}/speakers`,
        `${prefix}/onboarding`,
        `${prefix}/speaker-content`,
        `${prefix}/messages`
      ]],
      ["Agenda", `${prefix}/agenda`, "agenda", [`${prefix}/agenda`]],
      ["Share", `${prefix}/workspace`, "external", [`${prefix}/workspace`]]
    ];
    // Access administration needs owner/manage on this exact event. An `edit`
    // grantee reaches every page above and is refused this one, so offering it
    // would be a link straight to a 404.
    if (canAdministerAccess) {
      items.splice(3, 0, ["Reviewers", `${prefix}/reviewers`, "people", [`${prefix}/reviewers`, `${prefix}/access`]]);
    }
    for (const [label, href, iconName, matches] of items) {
      const current = matches.some((path) => (
        location.pathname === path || (path !== prefix && location.pathname.startsWith(`${path}/`))
      ));
      nav.append(navLink(label, href, iconName, current));
    }
    return nav;
  }

  function speakerHubTabs(eventId) {
    const mount = document.querySelector("[data-speaker-hub-tabs]");
    if (!mount) return;
    if (!eventId) { mount.replaceChildren(); return; }
    const prefix = `/admin/events/${encodeURIComponent(eventId)}`;
    const nav = make("nav", undefined, "sb-hub-tabs");
    nav.setAttribute("aria-label", "Speaker areas");
    const tabs = [
      ["Directory", `${prefix}/speakers`],
      ["Onboarding", `${prefix}/onboarding`],
      ["Tasks & files", `${prefix}/speaker-content`],
      ["Messages", `${prefix}/messages`]
    ];
    for (const [label, href] of tabs) {
      const current = location.pathname === href || location.pathname.startsWith(`${href}/`);
      nav.append(link(label, href, current));
    }
    mount.replaceChildren(nav);
  }

  function renderShell(session) {
    const active = activeRole(session);
    if (!active) {
      renderSessionContractError();
      return;
    }
    const activeDestination = roleDestination(active, session);
    if (!activeDestination) {
      renderSessionContractError("workspace");
      return;
    }
    const roles = new Set([active.role]);
    const organizer = roles.has("organizer");
    const section = currentSection();
    const singleSpeakerWorkspace = roles.size === 1 && roles.has("speaker");
    if (location.pathname.startsWith("/admin") && !organizer) {
      if (roles.has("reviewer")) location.replace("/reviews");
      else if (roles.has("speaker")) location.replace("/speaker");
      else renderSessionContractError();
      return;
    }
    // Account settings are persona-neutral. Treating /account as an organizer
    // workspace made an event-scoped organizer bounce account -> event ->
    // account forever while their required profile was still incomplete.
    const organizerWorkspace = organizer && !["account", "speaker", "reviews"].includes(section);
    const currentEventId = eventIdFromLocation();
    const organizationWorkspace = organizerWorkspace && canManageOrganization(session);
    if (organizerWorkspace && !organizationWorkspace && !currentEventId) {
      const destination = activeDestination;
      if (destination !== location.pathname) {
        location.replace(destination);
        return;
      }
    }
    const globalOrganizerWorkspace = organizerWorkspace || section === "account";
    document.body.classList.add("sb-shell-authenticated");
    document.body.classList.toggle("sb-shell-global", globalOrganizerWorkspace);
    document.body.classList.toggle("sb-shell-event", Boolean(organizerWorkspace && currentEventId));

    const sidebar = make("aside", undefined, "sb-sidebar");
    sidebar.id = "workspace-navigation";
    const brand = link("", activeDestination);
    brand.className = "sb-app-brand";
    const mark = brandMark();
    const brandText = make("span", undefined, "sb-app-brand__text");
    brandText.append(make("strong", "SessionBuddy"));
    brand.append(mark, brandText);
    if (!organizerWorkspace || currentEventId) sidebar.append(brand);

    const primaryGroup = make(
      "div",
      undefined,
      `sb-sidebar__group sb-sidebar__primary${organizerWorkspace ? " sb-sidebar__mobile-global" : ""}`
    );
    primaryGroup.append(make("p", "Main", "sb-sidebar__label"));
    const nav = make("nav", undefined, "sb-sidebar__nav");
    nav.setAttribute("aria-label", "Main navigation");
    // These three destinations are organization wide, so they need organization
    // authority — event grants never cascade upward. They are NOT gated on
    // organizerWorkspace: /account is persona-neutral for the redirect guard,
    // but an organizer still needs a way out of it.
    const organizationNavigation = organizer && canManageOrganization(session);
    if (organizationNavigation) {
      nav.append(navLink("Home", "/admin", "home", organizerWorkspace && !currentEventId && section === "home"));
      nav.append(navLink("People", "/admin/people", "people", organizerWorkspace && !currentEventId && section === "speakers"));
    }
    // Portals are offered for every role on the account, not just the active
    // one. Reading only the active role left an organizer who is also a speaker
    // with no route back to their portal.
    const accountRoles = new Set((session.account_roles || []).filter((role) => supportedRoles.has(role)));
    const showPortals = !organizerWorkspace && (accountRoles.has("reviewer") || accountRoles.has("speaker"));
    if (!organizerWorkspace && !showPortals) {
      if (section === "reviews") nav.append(navLink("My reviews", "/reviews", "review", true));
      if (section === "speaker") nav.append(navLink("Speaker portal", "/speaker", "mic", true));
    }
    primaryGroup.append(nav);
    if (nav.children.length) sidebar.append(primaryGroup);
    // An organizer whose authority is a set of exact event grants has no
    // organization-wide page to link. Without this group their sidebar was
    // empty on every page they could reach, including /account.
    if (organizer && !organizationNavigation) {
      const grantedEvents = eventsWithContentAccess(session);
      if (grantedEvents.length) {
        const eventsGroup = make("div", undefined, "sb-sidebar__group sb-sidebar__primary");
        eventsGroup.append(make("p", "Your events", "sb-sidebar__label"));
        const eventsNav = make("nav", undefined, "sb-sidebar__nav");
        eventsNav.setAttribute("aria-label", "Your events");
        grantedEvents.forEach((item) => {
          eventsNav.append(navLink(
            item.event_name || "Event",
            `/admin/events/${encodeURIComponent(item.event_id)}`,
            "calendar",
            organizerWorkspace && item.event_id === currentEventId
          ));
        });
        eventsGroup.append(eventsNav);
        sidebar.append(eventsGroup);
      }
    }
    if (showPortals) {
      const utilityGroup = make("div", undefined, "sb-sidebar__group sb-sidebar__utility");
      utilityGroup.append(make("p", "Your portals", "sb-sidebar__label"));
      const utilityNav = make("nav", undefined, "sb-sidebar__nav");
      utilityNav.setAttribute("aria-label", "Your portals");
      if (accountRoles.has("reviewer")) utilityNav.append(navLink("My reviews", "/reviews", "review", section === "reviews"));
      if (accountRoles.has("speaker")) utilityNav.append(navLink("Speaker portal", "/speaker", "mic", section === "speaker"));
      utilityGroup.append(utilityNav);
      sidebar.append(utilityGroup);
    }
    speakerHubTabs(organizerWorkspace ? currentEventId : "");
    const hasSidebarNavigation = Boolean(sidebar.querySelector(".sb-sidebar__nav a"));
    const topbarOnlyWorkspace = singleSpeakerWorkspace || organizerWorkspace || !hasSidebarNavigation;
    document.body.classList.toggle("sb-shell-single", topbarOnlyWorkspace);

    const topbar = make("div", undefined, "sb-topbar");
    const menuButton = make("button", undefined, "sb-menu-button");
    menuButton.type = "button";
    menuButton.setAttribute("aria-label", "Open navigation");
    menuButton.setAttribute("aria-controls", sidebar.id);
    menuButton.setAttribute("aria-expanded", "false");
    menuButton.append(make("span"), make("span"), make("span"));
    const crumb = make("div", undefined, "sb-topbar__title");
    crumb.append(make("strong", pageLabel(section, currentEventId)));
    if (globalOrganizerWorkspace) {
      const globalNav = make("nav", undefined, "sb-global-nav");
      globalNav.setAttribute("aria-label", "Workspace navigation");
      if (organizationNavigation) {
        globalNav.append(navLink("Home", "/admin", "home", !currentEventId && section === "home"));
        globalNav.append(navLink("People", "/admin/people", "people", !currentEventId && section === "speakers"));
      }
      if (currentEventId) {
        topbar.classList.add("sb-topbar--event");
        const topbarBrand = link("", organizationNavigation ? "/admin" : activeDestination);
        topbarBrand.className = "sb-global-brand";
        topbarBrand.append(brandMark(), make("strong", "SessionBuddy"));
        topbar.append(topbarBrand, globalNav, accountMenu(session, roles));
      } else {
        const topbarBrand = link("", "/admin");
        topbarBrand.className = "sb-global-brand";
        const topbarMark = brandMark();
        topbarBrand.append(topbarMark, make("strong", "SessionBuddy"));
        topbar.append(topbarBrand, globalNav, accountMenu(session, roles));
      }
    } else if (singleSpeakerWorkspace) {
      const speakerBrand = link("", "/speaker");
      speakerBrand.className = "sb-global-brand";
      speakerBrand.append(brandMark(), make("strong", "SessionBuddy"));
      topbar.append(speakerBrand, accountMenu(session, roles));
    } else if (!hasSidebarNavigation) {
      const accountBrand = link("", activeDestination);
      accountBrand.className = "sb-global-brand";
      accountBrand.append(brandMark(), make("strong", "SessionBuddy"));
      topbar.append(accountBrand, crumb, accountMenu(session, roles));
    } else {
      topbar.append(menuButton, crumb, accountMenu(session, roles));
    }

    const horizontalEventNav = organizerWorkspace && currentEventId && (worksInEventDirectly(session, currentEventId) || organizationNavigation)
      ? eventNav(currentEventId, administersEventDirectly(session, currentEventId))
      : null;
    if (topbarOnlyWorkspace) {
      shell.className = "sb-app-shell sb-app-shell--single";
      shell.replaceChildren(...[topbar, horizontalEventNav].filter(Boolean));
      return;
    }

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
      if (event.key !== "Escape") return;
      closeNavigation();
    });

    shell.className = "sb-app-shell";
    shell.replaceChildren(sidebar, topbar, backdrop);
  }

  window.addEventListener("sessionbuddy:event-context", () => {
    if (shell && window.SessionBuddyShellSession) renderShell(window.SessionBuddyShellSession);
  });

  function dashboardDestination(session) {
    const active = activeRole(session);
    return active ? roleDestination(active, session) : null;
  }

  function renderLandingAccount(session) {
    const href = dashboardDestination(session);
    if (!href) {
      renderLandingSessionContractError();
      return;
    }
    const roles = roleSet(session);
    const wrapper = make("span", undefined, "sb-landing-account");
    const label = "Open dashboard";
    wrapper.append(link(label, href));
    wrapper.append(accountMenu(session, roles));
    landingAccount.replaceChildren(wrapper);
    for (const entry of document.querySelectorAll("[data-auth-entry]")) {
      entry.textContent = label;
      entry.href = href;
    }
  }

  async function renderPublicEvents() {
    if (!publicEvents) return;
    publicEvents.setAttribute("aria-busy", "true");
    try {
      const response = await window.SessionBuddyApi.request("/api/v1/public/events");
      publicEvents.replaceChildren();
      if (!response.data.length) {
        const empty = make("p", "No public events are available yet.", "public-events-empty");
        empty.setAttribute("role", "status");
        publicEvents.append(empty);
        return;
      }
      for (const event of response.data) {
        const card = make("article", undefined, "public-event-card");
        const date = make("time", new Date(event.starts_at_ms).toLocaleDateString());
        date.dateTime = new Date(event.starts_at_ms).toISOString();
        card.append(date, make("h3", event.name));
        const details = [event.location, event.delivery_mode.replaceAll("_", " ")].filter(Boolean).join(" · ");
        card.append(make("p", details || "Event details coming soon."));
        const actions = make("div", undefined, "public-event-actions");
        if (event.cfp_slug) {
          const eventKey = event.id.replace(/[^a-z0-9]/gi, "").slice(0, 6).toLowerCase();
          actions.append(link("Call for Proposals →", `/cfp/${eventKey}/${encodeURIComponent(event.cfp_slug)}`));
        }
        if (event.schedule_published) actions.append(link("Schedule →", `/events/${encodeURIComponent(event.id)}/schedule`));
        if (event.speaker_count) actions.append(link("Speakers →", `/events/${encodeURIComponent(event.id)}/speakers`));
        if (!actions.children.length) actions.append(make("span", "Program details coming soon.", "role-label"));
        card.append(actions); publicEvents.append(card);
      }
    } catch (_) {
      const error = make("p", "Public events could not be loaded. Try again later.", "public-events-empty");
      error.setAttribute("role", "alert");
      publicEvents.replaceChildren(error);
    } finally {
      publicEvents.setAttribute("aria-busy", "false");
    }
  }

  function renderGuestShell() {
    document.body.classList.add("sb-shell-guest");
    const inner = make("div", undefined, "sb-guest-header__inner");
    const brand = link("", "/");
    brand.className = "sb-app-brand";
    const mark = brandMark();
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
    const mark = brandMark();
    brand.append(mark, make("span", "SessionBuddy"));
    const retry = make("button", "Try again", "sb-guest-sign-in");
    retry.type = "button";
    retry.addEventListener("click", () => location.reload());
    inner.append(brand, make("span", "Temporarily unavailable"), retry);
    shell.className = "sb-guest-header";
    shell.replaceChildren(inner);
  }

  function renderSessionContractError(reason = "active-role") {
    if (!shell) return;
    document.body.classList.remove("sb-shell-authenticated", "sb-shell-single", "sb-shell-global");
    document.body.classList.add("sb-shell-guest");
    const inner = make("div", undefined, "sb-guest-header__inner");
    const identity = make("span", undefined, "sb-app-brand");
    identity.append(brandMark(), make("span", "SessionBuddy"));
    const error = make(
      "span",
      reason === "workspace"
        ? "Organizer access is unavailable because this session has no manageable organization or event. Sign in again or ask an administrator to restore your access."
        : "Account access is unavailable because this session has no valid active role. Sign in again or ask an administrator to restore your access.",
      "sb-session-contract-error"
    );
    error.setAttribute("role", "alert");
    inner.append(identity, error);
    shell.className = "sb-guest-header";
    shell.replaceChildren(inner);
  }

  function renderLandingSessionContractError() {
    if (landingAccount) {
      const error = make("span", "Account access unavailable", "sb-landing-account-error");
      error.setAttribute("role", "alert");
      landingAccount.replaceChildren(error);
    }
    for (const entry of document.querySelectorAll("[data-auth-entry]")) {
      entry.textContent = "Account access unavailable";
      entry.removeAttribute("href");
      entry.setAttribute("aria-disabled", "true");
    }
  }

  async function initialize() {
    renderPublicEvents();
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
    if (!activeRole(session)) {
      renderSessionContractError();
      renderLandingSessionContractError();
      return;
    }
    if (!dashboardDestination(session)) {
      renderSessionContractError("workspace");
      renderLandingSessionContractError();
      return;
    }
    window.SessionBuddyShellSession = session;
    if (!session.profile_complete && location.pathname !== "/account") {
      const next = `${location.pathname}${location.search}${location.hash}`;
      location.replace(`/account?onboarding=1&next=${encodeURIComponent(next)}`);
      return;
    }
    if (landingAccount && location.pathname === "/") {
      const destination = dashboardDestination(session);
      if (!destination) {
        renderLandingSessionContractError();
        return;
      }
      location.replace(destination);
      return;
    }
    if (shell) renderShell(session);
    if (landingAccount) renderLandingAccount(session);
    window.dispatchEvent(new CustomEvent("sessionbuddy:session", { detail: session }));
  }

  initialize().catch(() => { if (shell) renderUnavailableShell(); });
})();

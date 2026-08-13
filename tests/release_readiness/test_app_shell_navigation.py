from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_global_navigation_is_separate_from_the_scrollable_event_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "`sb-sidebar__group sb-sidebar__primary${organizerWorkspace" in javascript
    assert '"sb-event-nav"' in javascript
    assert "if (nav.children.length) sidebar.append(primaryGroup);" in javascript
    assert "sidebar.append(eventNav(currentEventId" not in javascript
    assert "horizontalEventNav" in javascript
    assert (
        'document.body.classList.toggle("sb-shell-global", globalOrganizerWorkspace)' in javascript
    )
    assert 'const globalNav = make("nav", undefined, "sb-global-nav")' in javascript
    assert "if (organizationNavigation) {" in javascript
    assert 'navLink("People", "/admin/people"' in javascript
    assert 'navLink("Events", "/admin/events"' not in javascript
    assert "if (!organizerWorkspace || currentEventId) sidebar.append(brand);" in javascript
    assert 'topbar.classList.add("sb-topbar--event")' in javascript
    assert '["Overview", prefix, "overview"' in javascript


def test_event_navigation_is_centered_on_desktop_and_left_aligned_when_wrapped() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    event_nav_rule = stylesheet.split(".sb-event-nav {", 1)[1].split("}", 1)[0]
    assert "justify-content: center;" in event_nav_rule
    compact_rule = stylesheet.split("@media (max-width: 60rem)", 1)[1]
    compact_event_nav = compact_rule.split(".sb-event-nav {", 1)[1].split("}", 1)[0]
    assert "justify-content: flex-start;" in compact_event_nav


def test_event_navigation_warms_documents_and_transitions_without_hijacking_links() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert "function warmNavigation(node)" in javascript
    link_function = javascript.split("function link", 1)[1].split("function icon", 1)[0]
    assert "warmNavigation(node);" in link_function
    # A prerender executes the destination's session check and API queries, so
    # it is reserved for hover - the one signal that reliably precedes a click
    # - and only one candidate is ever alive; hovering elsewhere cancels it.
    # Keyboard traversal and touch get the cheap prefetch cache warmer, so
    # tabbing across the event navigation never executes eight full pages.
    assert 'HTMLScriptElement.supports("speculationrules")' in javascript
    assert 'hint.type = "speculationrules";' in javascript
    assert 'hint.rel = "prefetch";' in javascript
    # A pointer crossing the bar is not intent: the prerender arms only after
    # a dwell delay and pointerleave disarms it before any request starts.
    assert "const PRERENDER_INTENT_DELAY_MS = 120;" in javascript
    assert (
        "intentTimer = setTimeout(() => prerenderDocument(node.href), PRERENDER_INTENT_DELAY_MS);"
        in javascript
    )
    assert 'node.addEventListener("pointerleave", () => clearTimeout(intentTimer)' in javascript
    assert 'node.addEventListener("focus", () => prefetchDocument(node.href)' in javascript
    assert 'node.addEventListener("touchstart", () => prefetchDocument(node.href)' in javascript
    prerender_function = javascript.split("function prerenderDocument(href)", 1)[1].split(
        "function warmNavigation", 1
    )[0]
    assert "if (prerenderCandidate?.href === href) return;" in prerender_function
    assert "cancelSpeculativeLoads();" in prerender_function
    assert "prerenderCandidate = { href, hint };" in prerender_function
    # Navigation stays the browser's: durable URLs, Back/Forward, bfcache.
    # Nothing intercepts clicks or replays documents by hand.
    warm_function = javascript.split("function warmNavigation", 1)[1].split(
        "const SESSION_CACHE_KEY", 1
    )[0]
    assert "preventDefault" not in warm_function
    assert "document.write" not in javascript
    intercepted_click = (
        'document.addEventListener("click", (event) => {\n'
        "    if (event.defaultPrevented"
    )
    assert intercepted_click not in javascript
    assert "@view-transition { navigation: auto; }" in stylesheet
    reduced_motion = stylesheet.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "::view-transition-old(root)" in reduced_motion
    assert "::view-transition-group(*)" in reduced_motion


def test_shell_chrome_is_pinned_across_document_navigations() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    sidebar_rule = stylesheet.split(".sb-sidebar {", 1)[1].split("}", 1)[0]
    assert "view-transition-name: sessionbuddy-sidebar;" in sidebar_rule
    topbar_rule = stylesheet.split(".sb-topbar {", 1)[1].split("}", 1)[0]
    assert "view-transition-name: sessionbuddy-topbar;" in topbar_rule
    event_nav_rule = stylesheet.split(".sb-event-nav {", 1)[1].split("}", 1)[0]
    assert "view-transition-name: sessionbuddy-event-navigation;" in event_nav_rule


def test_shell_paints_from_the_cached_session_and_revalidates_in_the_background() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    # The per-tab cache lets the shell join the page's first frame instead of
    # rebuilding over a network round-trip on every document.
    assert 'const SESSION_CACHE_KEY = "sessionbuddy:shell-session";' in javascript
    assert "sessionStorage.getItem(SESSION_CACHE_KEY)" in javascript
    assert "sessionStorage.setItem(" in javascript
    assert "stored_at: Date.now(), session: cacheableSession(session)" in javascript
    assert "sessionStorage.removeItem(SESSION_CACHE_KEY)" in javascript

    # Cached data must satisfy the same session contract as a fresh response,
    # and both paths route through one function so they can never disagree.
    assert "function usableCachedSession()" in javascript
    usable = javascript.split("function usableCachedSession()", 1)[1].split(
        "function brandMark", 1
    )[0]
    assert "activeRole(cached)" in usable
    assert "dashboardDestination(cached)" in usable
    assert "clearCachedSession();" in usable
    assert "function applySession(session)" in javascript
    assert "const cached = shell ? usableCachedSession() : null;" in javascript

    # The fresh response reconciles: identical means done, different means
    # re-render, 401 means the cache dies with the session.
    assert "JSON.stringify(cacheableSession(session)) === JSON.stringify(cached)" in javascript
    sign_out = javascript.split('signOut.addEventListener("click"', 1)[1].split(
        "menu.append(signOut);", 1
    )[0]
    assert "clearCachedSession();" in sign_out
    assert "broadcastSessionChange();" in sign_out
    role_switch = javascript.split('"/api/v1/session/active-role"', 1)[1].split(
        "switcher.append(button);", 1
    )[0]
    assert "clearCachedSession();" in role_switch
    assert "cancelSpeculativeLoads();" in role_switch
    assert "broadcastSessionChange();" in role_switch

    # The landing page stays network-first so signed-out visitors are never
    # bounced toward a dashboard by stale data.
    assert "shell ? usableCachedSession() : null" in javascript


def test_cached_session_never_stores_credentials_and_cannot_outlive_its_welcome() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    # Only an explicit allowlist of presentation fields is persisted. Tokens,
    # ids, and configuration the shell never renders stay out of storage, and
    # menu actions read the CSRF token from the live session at click time.
    cacheable = javascript.split("function cacheableSession(session)", 1)[1].split(
        "function readCachedSession", 1
    )[0]
    for field in (
        "email:",
        "display_name:",
        "organization_name:",
        "active_role:",
        "account_roles:",
        "profile_complete:",
        "organization_access:",
        "event_access:",
    ):
        assert field in cacheable
    for excluded in ("csrf_token", "user_id", "...session", "Object.assign"):
        assert excluded not in cacheable
    assert "function sessionCsrfToken(renderedSession)" in javascript
    assert "window.SessionBuddyShellSession?.csrf_token" in javascript
    assert '"x-csrf-token": sessionCsrfToken(session)' in javascript

    # Entries expire, so a stale identity is bounded by minutes even when
    # revalidation keeps failing transiently.
    assert "const SESSION_CACHE_TTL_MS = 15 * 60 * 1000;" in javascript
    reader = javascript.split("function readCachedSession()", 1)[1].split(
        "function writeCachedSession", 1
    )[0]
    assert "Date.now() - record.stored_at < SESSION_CACHE_TTL_MS" in reader
    assert "clearCachedSession();" in reader

    # Sign-out or a role switch in one tab invalidates the cached presentation
    # in the account's other tabs immediately.
    assert 'new BroadcastChannel("sessionbuddy-auth")' in javascript
    assert "function broadcastSessionChange()" in javascript
    listener = javascript.split('authChannel?.addEventListener("message"', 1)[1].split(
        "function broadcastSessionChange", 1
    )[0]
    assert "clearCachedSession();" in listener
    assert "cancelSpeculativeLoads();" in listener


def test_csp_permits_only_the_inline_speculation_rules_the_shell_emits() -> None:
    security = (STATIC.parent / "security.py").read_text(encoding="utf-8")

    # Without this source, script-src falls back to default-src 'self' and
    # Chromium silently drops the inline rule: the shell would detect support,
    # skip the prefetch fallback, and end up with neither.
    assert "script-src 'self' 'inline-speculation-rules'; " in security
    assert "'unsafe-inline'" not in security.split("style-src-attr", 1)[0]


def test_global_pages_use_the_approved_horizontal_navigation() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert ".sb-shell-global .sb-sidebar { display: none; }" in stylesheet
    assert ".sb-global-nav" in stylesheet
    assert ".sb-sidebar__mobile-global { display: none; }" in stylesheet
    assert ".sb-sidebar__mobile-global { display: block; }" in stylesheet
    global_nav_rule = stylesheet.split(".sb-global-nav {", 1)[1].split("}", 1)[0]
    active_rule = stylesheet.split('.sb-global-nav a[aria-current="page"] {', 1)[1].split("}", 1)[0]
    assert "border:" not in global_nav_rule
    assert "border-radius:" not in global_nav_rule
    assert "background:" not in global_nav_rule
    assert "linear-gradient" not in active_rule
    assert '.sb-global-nav a[aria-current="page"]::after' in stylesheet
    account_rule = stylesheet.split(".sb-account summary {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid" in account_rule
    primary_rule = stylesheet.split(".sb-sidebar__primary {", 1)[1].split("}", 1)[0]
    assert "border:" in primary_rule
    assert "border-radius:" in primary_rule
    assert "background:" in primary_rule


def test_account_menu_layer_stays_above_workflow_content() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert "z-index: 100;" in stylesheet.split(".sb-sidebar {", 1)[1].split("}", 1)[0]
    topbar = stylesheet.split(".sb-topbar {", 1)[1].split("}", 1)[0]
    assert "z-index: 90;" in topbar
    assert "isolation: isolate;" in topbar
    account = stylesheet.split(".sb-account {", 1)[1].split("}", 1)[0]
    assert "z-index: 1;" in account
    menu = stylesheet.split(".sb-account__menu {", 1)[1].split("}", 1)[0]
    assert "z-index: 1;" in menu
    mobile_backdrop = stylesheet.split(".sb-nav-backdrop { position: fixed;", 1)[1]
    assert "z-index: 95;" in mobile_backdrop.split("}", 1)[0]


def test_role_portals_do_not_render_an_empty_primary_navigation_group() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "if (nav.children.length) sidebar.append(primaryGroup);" in javascript
    assert 'if (accountRoles.has("reviewer")) utilityNav.append' not in javascript
    assert 'navLink("My reviews", "/reviews"' not in javascript
    assert "if (utilityNav.children.length) sidebar.append(utilityGroup);" in javascript


def test_zero_link_account_shell_collapses_the_empty_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert (
        'const hasSidebarNavigation = Boolean(sidebar.querySelector(".sb-sidebar__nav a"));'
        in javascript
    )
    assert '|| section === "reviews" || !hasSidebarNavigation;' in javascript
    assert '} else if (!hasSidebarNavigation || section === "reviews") {' in javascript
    assert "topbar.append(accountBrand, crumb, accountMenu(session, roles));" in javascript
    assert "if (topbarOnlyWorkspace) {" in javascript


def test_account_brand_uses_the_active_role_destination() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "const activeDestination = roleDestination(active, session)" in javascript
    assert 'const brand = link("", activeDestination)' in javascript
    assert 'const accountBrand = link("", activeDestination)' in javascript
    assert (
        'organizerWorkspace ? organizerDestination(session) : roles.has("speaker")'
        not in javascript
    )


def test_single_speaker_workspace_has_no_one_item_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert 'const singleSpeakerWorkspace = roles.size === 1 && roles.has("speaker")' in javascript
    assert "shell.replaceChildren(...[topbar, horizontalEventNav].filter(Boolean));" in javascript
    assert 'speakerBrand = link("", "/speaker")' in javascript
    assert 'document.body.classList.toggle("sb-shell-single", topbarOnlyWorkspace)' in javascript
    assert ".sb-shell-single .sb-topbar {" in stylesheet
    assert ".app-body.sb-shell-authenticated.sb-shell-single > main.shell" in stylesheet


def test_account_navigation_exposes_one_active_role_and_role_switching() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert '"/api/v1/session/active-role"' in javascript
    assert "localStorage" not in javascript
    assert "` · ${roleLabel(active.role)}`" in javascript


def test_account_settings_uses_the_global_shell_without_polluting_primary_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert (
        'const globalOrganizerWorkspace = organizerWorkspace || section === "account"' in javascript
    )
    assert "if (globalOrganizerWorkspace) {" in javascript
    assert (
        'globalNav.append(navLink("Account settings", "/account", "account", true))'
        not in javascript
    )
    assert 'globalNav.append(navLink("Speaker portal", "/speaker", "mic"' not in javascript
    assert 'globalNav.append(navLink("My reviews", "/reviews", "review"' not in javascript
    assert "topbar.append(topbarBrand, globalNav, accountMenu(session, roles));" in javascript
    assert 'make("p", "Switch role", "sb-role-switcher__label")' in javascript
    assert 'make("p", "Account", "sb-account__menu-title")' in javascript
    assert 'switcher.setAttribute("role", "group")' in javascript
    assert 'button.setAttribute("aria-pressed", String(isActive))' in javascript
    assert 'button.setAttribute("aria-disabled", "true")' in javascript
    assert 'make("span", undefined, "sb-role-option__check")' in javascript
    assert 'make("span", "Active")' in javascript
    assert 'navLink("Account settings", "/account", "account")' in javascript
    assert 'make("span", "Sign out")' in javascript
    assert "return new Set(active ? [active.role] : [])" in javascript
    assert "return organizerDestination(session)" in javascript
    assert ".sb-role-option" in stylesheet
    assert ".sb-role-option__check" in stylesheet
    assert ".sb-account__menu-identity" in stylesheet
    assert ":focus-visible" in stylesheet


def test_shell_uses_brand_asset_and_organizer_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert 'mark.src = "/landing/assets/sessionbuddy-favicon.svg"' in javascript
    assert 'nav.append(navLink("People", "/admin/people"' in javascript
    assert 'globalNav.append(navLink("People", "/admin/people"' in javascript
    assert 'navLink("Events", "/admin/events", "calendar"' not in javascript


def test_single_speaker_shell_has_no_redundant_page_heading() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    branch = javascript.split("} else if (singleSpeakerWorkspace) {", 1)[1].split(
        '} else if (!hasSidebarNavigation || section === "reviews") {', 1
    )[0]
    assert "crumb" not in branch
    assert "topbar.append(speakerBrand, accountMenu(session, roles));" in branch


def test_landing_uses_one_role_aware_dashboard_entry() -> None:
    landing = (STATIC / "landing.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    primary_navigation = landing.split(
        '<nav class="primary-nav" aria-label="Primary navigation">', 1
    )[1].split("</nav>", 1)[0]
    hero_actions = landing.split('<div class="hero-actions">', 1)[1].split("</div>", 1)[0]
    assert "Speaker portal" not in primary_navigation
    assert "Platform status" not in primary_navigation
    assert "Speaker portal" not in hero_actions
    assert 'const label = "Open dashboard"' in javascript
    assert 'if (canManageOrganization(session)) return "/admin"' in javascript
    assert 'return "/speaker"' in javascript
    assert 'return "/reviews"' in javascript
    assert 'choice.role === "speaker"' in javascript
    assert "session.account_roles || []" in javascript
    assert 'choice.role === "organization_admin"' not in javascript
    assert 'choice.role === "event_admin"' not in javascript
    assert 'if (landingAccount && location.pathname === "/")' in javascript
    assert "location.replace(destination)" in javascript
    assert "return active ? roleDestination(active, session) : null" in javascript


def test_missing_or_unknown_active_role_fails_closed_without_a_destination_guess() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    active_role = javascript.split("function activeRole(session)", 1)[1].split(
        "function roleSet", 1
    )[0]
    assert "session.active_role" in active_role
    assert "session.default_role" not in active_role
    assert "choices[0]" not in active_role
    assert "supportedRoles.has(requested)" in active_role
    assert "return roleChoices(session).find" in active_role

    destination = javascript.split("const roleDestination", 1)[1].split("function sameRole", 1)[0]
    assert 'return "/account"' not in destination
    assert "return null" in destination

    assert "if (!activeRole(session)) {" in javascript
    assert "renderSessionContractError();" in javascript
    assert "renderLandingSessionContractError();" in javascript
    assert "this session has no valid active role" in javascript
    assert 'error.setAttribute("role", "alert")' in javascript
    assert 'entry.removeAttribute("href")' in javascript
    assert 'entry.setAttribute("aria-disabled", "true")' in javascript


def test_organizer_without_manageable_resources_has_no_account_fallback() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    organizer_destination = javascript.split("function organizerDestination(session)", 1)[1].split(
        "const roleDestination", 1
    )[0]
    assert 'return "/admin"' in organizer_destination
    assert "event.event_id" in organizer_destination
    assert 'return "/account"' not in organizer_destination
    assert " : null" in organizer_destination

    assert "const activeDestination = roleDestination(active, session)" in javascript
    assert "if (!activeDestination) {" in javascript
    assert 'renderSessionContractError("workspace")' in javascript
    assert "this session has no manageable organization or event" in javascript
    assert "if (!dashboardDestination(session)) {" in javascript


def test_exact_event_only_organizers_land_in_their_event_workspace() -> None:
    shell = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    overview = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert "function organizerDestination(session)" in shell
    assert "session.event_access || []" in shell
    assert "if (organizerWorkspace && !organizationWorkspace && !currentEventId)" in shell
    assert 'api("/api/v1/admin/organizations")' not in overview
    assert 'if (!organization) throw new Error("This event is not available' not in overview


def test_account_is_persona_neutral_and_same_destination_still_renders_shell() -> None:
    shell = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert '!["account", "speaker", "reviews"].includes(section)' in shell
    destination_guard = shell.split(
        "if (organizerWorkspace && !organizationWorkspace && !currentEventId)", 1
    )[1].split("const globalOrganizerWorkspace", 1)[0]
    assert "if (destination !== location.pathname) {" in destination_guard
    assert "location.replace(destination);\n        return;" in destination_guard
    assert not destination_guard.rstrip().endswith("return;\n    }")


def test_organization_context_does_not_use_an_ambiguous_letter_tile() -> None:
    page = (STATIC / "events_admin.html").read_text(encoding="utf-8")
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")

    assert "context-bar__icon" not in page
    assert ".context-bar__icon" not in stylesheet


def test_only_event_navigation_scrolls_inside_the_sidebar_on_all_viewports() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    sidebar_rule = stylesheet.split(".sb-sidebar {", 1)[1].split("}", 1)[0]
    primary_rule = stylesheet.split(".sb-sidebar__primary {", 1)[1].split("}", 1)[0]
    event_rule = stylesheet.split(".sb-sidebar__event {", 1)[1].split("}", 1)[0]

    assert "overflow: hidden" in sidebar_rule
    assert "overflow-y: auto" not in sidebar_rule
    assert "flex: 0 0 auto" in primary_rule
    assert "min-height: 0" in event_rule
    assert "flex: 1 1 auto" in event_rule
    assert "overflow-y: auto" in event_rule
    assert "overscroll-behavior: contain" in event_rule

    mobile_rules = stylesheet.split("@media (max-width: 52rem)", 1)[1]
    assert ".sb-sidebar { transform:" in mobile_rules
    assert ".sb-sidebar { overflow" not in mobile_rules


def test_every_organizer_persona_reaches_a_navigable_workspace() -> None:
    """An organizer must never be shown an empty navigation rail.

    Three separate gates each used to empty the sidebar: the organization links
    were gated on ``organizerWorkspace`` (which excludes /account), an organizer
    holding only exact event grants had no organization-wide page to link, and
    the portals group read the active role instead of the account's roles.
    """

    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    # Organization links are gated on organization authority alone, so they
    # still render on the persona-neutral /account page.
    organization_gate = (
        "const organizationNavigation = organizer && canManageOrganization(session);"
    )
    assert organization_gate in javascript
    assert "if (organizationNavigation) {" in javascript

    # An organizer whose authority is a set of exact event grants gets their
    # granted events instead of an empty rail.
    assert "if (organizer && !organizationNavigation) {" in javascript
    assert "const grantedEvents = eventsWithContentAccess(session);" in javascript
    assert '"Your events"' in javascript

    # Portals come from every role on the account, not just the active one.
    assert "const accountRoles = new Set((session.account_roles || [])" in javascript
    assert 'accountRoles.has("reviewer") || accountRoles.has("speaker")' in javascript

    # The group is appended only when it actually holds links.
    assert "if (nav.children.length) sidebar.append(primaryGroup);" in javascript


def test_event_navigation_matches_the_server_authority_split() -> None:
    """`edit` earns the event workspace; only owner/manage earns Team & access."""

    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert 'const CONTENT_PERMISSIONS = ["owner", "manage", "edit"];' in javascript
    assert 'const ADMIN_PERMISSIONS = ["owner", "manage"];' in javascript

    # Content authority opens the event navigation.
    assert "function worksInEventDirectly(session, eventId)" in javascript
    assert "worksInEventDirectly(session, currentEventId) || organizationNavigation" in javascript

    # Access administration is a separate, narrower test.
    assert "function administersEventDirectly(session, eventId)" in javascript
    assert "item.event_id === eventId && holds(item, ADMIN_PERMISSIONS)" in javascript
    event_nav_call = "eventNav(currentEventId, administersEventDirectly(session, currentEventId))"
    assert event_nav_call in javascript


def test_event_navigation_exposes_evaluation_rounds() -> None:
    javascript = Path("src/sessionbuddy/static/app_shell.js").read_text()

    assert '["Rounds", `${prefix}/submissions#rounds-title`, "review", []]' in javascript
    assert "if (canAdministerAccess) {" in javascript


def test_account_menu_dismissal_is_shared_across_shell_layouts() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    shared_handler = javascript.index("function closeOpenAccount")
    topbar_return = javascript.index('shell.className = "sb-app-shell sb-app-shell--single"')
    assert shared_handler < topbar_return
    escape_handler = 'if (event.key === "Escape") closeOpenAccount({ restoreFocus: true });'
    assert escape_handler in javascript
    assert "!openAccount.contains(event.target)" in javascript

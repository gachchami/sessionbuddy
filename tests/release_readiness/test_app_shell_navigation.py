from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_source_wiring_global_navigation_is_separate_from_the_scrollable_event_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert '"sb-sidebar__group sb-sidebar__primary"' in javascript
    assert '"sb-event-nav"' in javascript
    assert "if (nav.children.length) sidebar.append(primaryGroup);" in javascript
    assert "sidebar.append(eventNav(currentEventId" not in javascript
    assert '"sb-sidebar__group sb-sidebar__event"' in javascript
    assert "eventNav(currentEventId, true)" in javascript
    assert (
        'document.body.classList.toggle("sb-shell-global", globalOrganizerWorkspace)' in javascript
    )
    assert 'const globalNav = make("nav", undefined, "sb-global-nav")' in javascript
    assert "if (organizationNavigation) {" in javascript
    assert 'navLink("People", "/admin/people"' in javascript
    assert 'navLink("Events", "/admin/events"' not in javascript
    assert "sidebar.append(brand);" in javascript
    assert 'topbar.classList.add("sb-topbar--event")' in javascript
    assert '["Overview", prefix, "overview"' in javascript


def test_source_wiring_event_navigation_uses_the_shared_vertical_sidebar() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    event_nav_rule = stylesheet.split(".sb-event-nav {", 1)[1].split("}", 1)[0]
    assert "display: grid;" in event_nav_rule
    event_group = stylesheet.split(".sb-sidebar__event {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto;" in event_group


def test_source_wiring_event_navigation_warms_documents_without_hijacking_links() -> None:
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
    # Warming never hijacks a link by itself; the only click interception is
    # the event-workspace swap below, and it is gated on the destination
    # sharing the current event so every other link stays the browser's.
    warm_function = javascript.split("function warmNavigation", 1)[1].split(
        "const SESSION_CACHE_KEY", 1
    )[0]
    assert "preventDefault" not in warm_function
    assert "document.write" not in javascript
    click_handler = javascript.split(
        'document.addEventListener("click", (event) => {\n    if (event.defaultPrevented', 1
    )[1].split("\n  });", 1)[0]
    assert "if (!softNavigable(url)) return;" in click_handler
    # Durable URLs and Back/Forward survive the in-place swap.
    push = 'history.pushState({ sessionbuddy: "workspace", sbIndex: historyIndex }, "", url.href);'
    assert push in javascript
    assert 'window.addEventListener("popstate", (event) => {' in javascript
    # Unsaved work, foreign responses, and missing page structure fall back to
    # a document navigation rather than a half-swapped page.
    assert "if (!consented && pageRefusesToLeave()) {" in javascript
    assert "const abandon = () => {" in javascript
    assert "location.replace(url.href);" in javascript
    # Only pages that declare themselves lifecycle-safe take part, on both ends.
    assert 'root.querySelector("main[data-sb-swappable]")' in javascript
    assert "!swappableDocument(next)" in javascript
    # A refused Back/Forward is undone before the organizer is asked.
    assert "history.go(delta);" in javascript
    prompt = 'window.confirm("You have unsaved changes. Leave this page and lose them?")'
    assert prompt in javascript
    # Prerender is reserved for links the swap will not handle.
    assert "if (softNavigable(new URL(node.href, location.href))) return;" in warm_function
    assert "@view-transition { navigation: auto; }" in stylesheet
    reduced_motion = stylesheet.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "::view-transition-old(root)" in reduced_motion
    assert "::view-transition-group(*)" in reduced_motion


def test_source_wiring_shell_chrome_is_pinned_across_document_navigations() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    sidebar_rule = stylesheet.split(".sb-sidebar {", 1)[1].split("}", 1)[0]
    assert "view-transition-name: sessionbuddy-sidebar;" in sidebar_rule
    topbar_rule = stylesheet.split(".sb-topbar {", 1)[1].split("}", 1)[0]
    assert "view-transition-name: sessionbuddy-topbar;" in topbar_rule
    # Event navigation is now inside the transitioning sidebar, not a second
    # independently pinned horizontal strip.
    assert "sessionbuddy-event-navigation" not in stylesheet


def test_source_wiring_shell_paints_from_the_cached_session_and_revalidates_in_the_background() -> (
    None
):
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    # The per-tab cache lets the shell join the page's first frame instead of
    # rebuilding over a network round-trip on every document.
    assert 'const SESSION_CACHE_KEY = "sessionbuddy:shell-session";' in javascript
    assert "sessionStorage.getItem(SESSION_CACHE_KEY)" in javascript
    assert "sessionStorage.setItem(" in javascript
    assert "stored_at: Date.now(), session: cacheableSession(session)" in javascript
    assert "sessionStorage.removeItem(SESSION_CACHE_KEY)" in javascript

    # Cached data paints identity only. It carries no authority-sensitive
    # destination, and the authoritative response always replaces it.
    assert "function usableCachedSession()" in javascript
    usable = javascript.split("function usableCachedSession()", 1)[1].split(
        "function brandMark", 1
    )[0]
    assert "activeRole(cached)" not in usable
    assert "dashboardDestination(cached)" not in usable
    assert "return cached" in usable
    assert "function applySession(session, { authoritative = true } = {})" in javascript
    assert "renderCachedShell(session);" in javascript
    assert "const cached = shell ? usableCachedSession() : null;" in javascript

    # The fresh response always re-renders; sign-out and role switching clear
    # every tab's identity hint.
    assert "JSON.stringify(cacheableSession(session)) === JSON.stringify(cached)" not in javascript
    sign_out = javascript.split("async function signOutCurrentSession", 1)[1].split(
        "async function switchSessionRole", 1
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


def test_source_wiring_cached_session_never_stores_credentials_and_cannot_outlive_its_welcome() -> (
    None
):
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
    ):
        assert field in cacheable
    for excluded in (
        "csrf_token",
        "user_id",
        "workspace_path",
        "organization_access",
        "event_access",
        "...session",
        "Object.assign",
    ):
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


def test_source_wiring_csp_permits_only_the_inline_speculation_rules_the_shell_emits() -> None:
    from sessionbuddy.security import content_security_policy

    policy = content_security_policy(None)

    assert "script-src 'self' 'inline-speculation-rules'; " in policy
    assert "connect-src 'self'; " in policy
    assert "localhost" not in policy
    # style-src is assembled now, so read the emitted directive rather than the
    # source text: inline stylesheets stay blocked everywhere the dev flag is unset.
    assert "style-src 'self'; " in policy
    assert "'unsafe-inline'" not in policy.split("style-src-attr", 1)[0]


def test_source_wiring_global_pages_use_the_reference_sidebar() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert ".sb-shell-global .sb-sidebar { display: none; }" not in stylesheet
    sidebar = stylesheet.split(".sb-sidebar {", 1)[1].split("}", 1)[0]
    assert "border-radius:" in sidebar
    assert "background:" in sidebar
    active_rule = stylesheet.split('.sb-sidebar__nav a[aria-current="page"] {', 1)[1].split("}", 1)[
        0
    ]
    assert "linear-gradient" not in active_rule
    assert "background:" in active_rule


def test_source_wiring_account_menu_layer_stays_above_workflow_content() -> None:
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


def test_source_wiring_role_portals_do_not_render_an_empty_primary_navigation_group() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "if (nav.children.length) sidebar.append(primaryGroup);" in javascript
    assert 'if (accountRoles.has("reviewer")) utilityNav.append' not in javascript
    assert 'navLink("My reviews", "/reviews"' not in javascript
    assert "if (utilityNav.children.length) sidebar.append(utilityGroup);" in javascript


def test_source_wiring_zero_link_account_shell_collapses_the_empty_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert (
        'const hasSidebarNavigation = Boolean(sidebar.querySelector(".sb-sidebar__nav a"));'
        in javascript
    )
    assert "const topbarOnlyWorkspace = !hasSidebarNavigation;" in javascript
    assert '} else if (!hasSidebarNavigation || section === "reviews") {' in javascript
    assert "topbar.append(accountBrand, crumb, account);" in javascript
    assert "if (topbarOnlyWorkspace) {" in javascript


def test_account_brand_wiring_uses_the_active_role_destination() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "const activeDestination = dashboardDestination(session)" in javascript
    assert 'link("", activeDestination)' in javascript
    assert "organizerDestination" not in javascript
    assert "roleDestination" not in javascript


def test_source_wiring_single_speaker_workspace_retains_role_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert 'const singleSpeakerWorkspace = roles.size === 1 && roles.has("speaker")' in javascript
    assert "shell.replaceChildren(topbar);" in javascript
    assert 'navLink("Speaker portal", "/speaker"' in javascript
    assert 'navLink("Calls for proposals", "/speaker#calls"' in javascript
    assert 'speakerBrand = link("", activeDestination)' in javascript
    assert 'document.body.classList.toggle("sb-shell-single", topbarOnlyWorkspace)' in javascript
    assert ".sb-shell-single .sb-topbar {" in stylesheet
    assert ".app-body.sb-shell-authenticated.sb-shell-single > main.shell" in stylesheet


def test_source_wiring_account_navigation_exposes_one_active_role_and_role_switching() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert '"/api/v1/session/active-role"' in javascript
    assert "localStorage" not in javascript
    assert "` · ${roleLabel(active.role)}`" in javascript


def test_source_wiring_account_settings_uses_shell_without_polluting_navigation() -> None:
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
    assert "topbar.append(topbarBrand, globalNav, account);" in javascript
    assert javascript.count("const account = accountMenu(session, roles);") == 1
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
    assert 'return typeof session.workspace_path === "string"' in javascript
    assert ".sb-role-option" in stylesheet
    assert ".sb-role-option__check" in stylesheet
    assert ".sb-account__menu-identity" in stylesheet
    assert ".sb-account:not([open]) > .sb-account__menu { display: none; }" in stylesheet
    assert ":focus-visible" in stylesheet


def test_source_wiring_shell_uses_brand_asset_and_organizer_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert 'mark.src = "/landing/assets/sessionbuddy-favicon.svg"' in javascript
    assert 'nav.append(navLink("People", "/admin/people"' in javascript
    assert 'globalNav.append(navLink("People", "/admin/people"' in javascript
    assert 'navLink("Events", "/admin/events", "calendar"' not in javascript


def test_source_wiring_single_speaker_shell_has_no_redundant_page_heading() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    branch = javascript.split("} else if (singleSpeakerWorkspace) {", 1)[1].split(
        '} else if (!hasSidebarNavigation || section === "reviews") {', 1
    )[0]
    assert "crumb" not in branch
    assert "topbar.append(speakerBrand, account);" in branch


def test_source_wiring_landing_uses_one_role_aware_dashboard_entry() -> None:
    landing = (STATIC / "landing.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    primary_navigation = landing.split(
        '<nav class="primary-nav" aria-label="Primary navigation">', 1
    )[1].split("</nav>", 1)[0]
    hero_actions = landing.split('<div class="session__actions">', 1)[1].split("</div>", 1)[0]
    assert "Speaker portal" not in primary_navigation
    assert "Platform status" not in primary_navigation
    assert "Speaker portal" not in hero_actions
    assert 'const label = "Open dashboard"' in javascript
    assert 'return typeof session.workspace_path === "string"' in javascript
    assert "session.usable_personas || []" in javascript
    assert 'choice.role === "organization_admin"' not in javascript
    assert 'choice.role === "event_admin"' not in javascript
    assert 'if (landingAccount && location.pathname === "/")' in javascript
    assert "location.replace(destination)" in javascript
    assert "location.replace(destination)" in javascript


def test_source_wiring_unknown_active_role_fails_closed_without_destination_guess() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    active_role = javascript.split("function activeRole(session)", 1)[1].split(
        "function roleSet", 1
    )[0]
    assert "session.active_role" in active_role
    assert "session.default_role" not in active_role
    assert "choices[0]" not in active_role
    assert "supportedRoles.has(requested)" in active_role
    assert "return roleChoices(session).find" in active_role

    assert 'const roleless = session.workspace_state === "roleless"' in javascript
    assert 'return location.pathname === "/calls" || location.pathname === "/account"' in javascript
    assert "const rolelessNeutral = roleless && isPersonaNeutralPath()" in javascript
    assert 'session.workspace_state === "active_role_invalid"' in javascript
    assert "renderSessionContractError(session.workspace_state, session)" in javascript
    assert "renderLandingSessionContractError();" in javascript
    assert "This session does not have a valid active role." in javascript
    assert 'error.setAttribute("role", "alert")' in javascript
    assert 'entry.removeAttribute("href")' in javascript
    assert 'entry.setAttribute("aria-disabled", "true")' in javascript


def test_source_wiring_organizer_without_manageable_resources_recovers_only_on_account() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "function organizerDestination" not in javascript
    assert "const roleDestination" not in javascript
    assert "const activeDestination = dashboardDestination(session)" in javascript
    assert "if (!activeDestination) {" in javascript
    assert 'session.workspace_state === "organizer_authority_missing"' in javascript
    assert 'workspaceRecovery && currentSection() === "account"' in javascript
    assert "you no longer manage an organization" in javascript


def test_source_wiring_organizer_destination_does_not_use_event_access() -> None:
    shell = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    overview = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert "function organizerDestination(session)" not in shell
    assert "const roleDestination" not in shell
    assert "if (organizerWorkspace && !organizationWorkspace && !currentEventId)" in shell
    assert 'api("/api/v1/admin/organizations")' not in overview
    assert 'if (!organization) throw new Error("This event is not available' not in overview


def test_account_is_persona_neutral_and_same_destination_still_renders_shell() -> None:
    shell = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert '!["account", "speaker", "reviews", "calls"].includes(section)' in shell
    destination_guard = shell.split(
        "if (organizerWorkspace && !organizationWorkspace && !currentEventId)", 1
    )[1].split("const globalOrganizerWorkspace", 1)[0]
    assert "if (destination !== location.pathname) {" in destination_guard
    assert "location.replace(destination);\n        return;" in destination_guard
    assert not destination_guard.rstrip().endswith("return;\n    }")


def test_source_wiring_recovery_states_render_the_account_shell_without_a_self_loop() -> None:
    shell = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert 'workspaceRecovery && currentSection() === "account"' in shell
    assert "renderRecoveryAccountShell(session);" in shell
    assert 'if (currentSection() !== "account")' in shell
    assert 'brandIdentity("sb-global-brand")' in shell


def test_source_wiring_cached_identity_never_routes_an_authorization_sensitive_document() -> None:
    shell = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    api_client = (STATIC / "api_client.js").read_text(encoding="utf-8")

    assert "const requiredPersona = personaForCurrentPath();" in shell
    persona_matcher = shell.split("function personaForCurrentPath()", 1)[1].split(
        "function isPersonaNeutralPath()", 1
    )[0]
    assert 'if (inWorkspace("/speaker")) return "speaker";' in persona_matcher
    assert 'location.pathname.startsWith("/speaker")' not in persona_matcher
    assert "requiredPersona && active?.role !== requiredPersona" in shell
    cached_paint = shell.split("function renderCachedShell(session)", 1)[1].split(
        "function applySession", 1
    )[0]
    assert "link(" not in cached_paint
    assert 'brandIdentity("sb-global-brand")' in cached_paint
    assert "function prepareForSessionReplacement()" in api_client
    assert 'sessionStorage.removeItem("sessionbuddy:shell-session")' in api_client
    assert 'key?.startsWith("sessionbuddy:document-recovery:")' in api_client
    for source_name in ("demo_access.js", "sign_in.js", "public_cfp.js", "speaker_portal.js"):
        source = (STATIC / source_name).read_text(encoding="utf-8")
        assert "SessionBuddyApi.prepareForSessionReplacement();" in source


def test_source_wiring_organization_context_does_not_use_an_ambiguous_letter_tile() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")

    assert "context-bar__icon" not in page
    assert ".context-bar__icon" not in stylesheet


def test_source_wiring_only_event_navigation_scrolls_inside_the_sidebar_on_all_viewports() -> None:
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
    mobile_sidebar = mobile_rules.split(".sb-sidebar {", 1)[1].split("}", 1)[0]
    assert "transform:" in mobile_sidebar
    assert "visibility: hidden" in mobile_sidebar
    assert ".sb-sidebar { overflow" not in mobile_rules


def test_source_wiring_every_organizer_persona_reaches_a_navigable_workspace() -> None:
    """An organizer must never be shown an empty navigation rail.

    Three separate gates each used to empty the sidebar: the organization links
    were gated on ``organizerWorkspace`` (which excludes /account), an organizer
    holding only exact event grants had no organization-wide page to link, and
    the portals group read the active role instead of the account's roles.
    """

    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    # Organization links consume the server-resolved usable-persona contract,
    # so they still render on the persona-neutral /account page without
    # reconstructing organization authority in the browser.
    assert 'choice.role === "organizer"' in javascript
    assert (
        "const organizationNavigation = !onboardingLocked && hasOrganizerWorkspace;" in javascript
    )
    assert "managesAnyOrganization" not in javascript
    assert "if (organizationNavigation) {" in javascript

    # Retired event grants never manufacture organizer navigation.
    assert "eventsWithContentAccess" not in javascript
    assert '"Your events"' not in javascript

    # Portals come from every role on the account, not just the active one.
    assert "const accountRoles = new Set((session.account_roles || [])" in javascript
    assert 'accountRoles.has("reviewer") || accountRoles.has("speaker")' in javascript

    # The group is appended only when it actually holds links.
    assert "if (nav.children.length) sidebar.append(primaryGroup);" in javascript


def test_source_wiring_event_navigation_comes_only_from_organization_authority() -> None:
    """Event assignments never manufacture organizer navigation."""

    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert 'const ADMIN_PERMISSIONS = ["owner", "manage"];' in javascript

    assert "function worksInEventDirectly" not in javascript
    assert "function administersEventDirectly" not in javascript
    assert "organizerWorkspace && currentEventId && organizationNavigation" in javascript
    assert "eventNav(currentEventId, true)" in javascript


def test_source_wiring_event_navigation_does_not_duplicate_proposal_sections() -> None:
    javascript = Path("src/sessionbuddy/static/app_shell.js").read_text()

    assert '["Rounds", `${prefix}/submissions#rounds-title`' not in javascript
    assert '["Proposals", `${prefix}/submissions`' in javascript
    assert "if (canAdministerAccess) {" in javascript


def test_source_wiring_event_settings_and_new_event_have_stable_shell_destinations() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert 'return value === "new" ? "" : value;' in javascript
    assert 'if (location.pathname === "/admin/events/new") return "New event";' in javascript
    assert 'if (location.pathname.endsWith("/settings")) return "Settings";' in javascript
    assert '["Settings", `${prefix}/settings`, "settings"' in javascript


def test_sessionbuddy_access_helpers_are_exact_resource_scoped() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "function canManageOrganization(session, organizationId)" in javascript
    assert "item.organization_id === organizationId" in javascript
    assert "function canEditEvent(session, event)" in javascript
    assert "function canManageLifecycle(session, event)" in javascript
    assert "function canDuplicateEvent(session, event)" in javascript
    assert "exactEventAccess" not in javascript
    assert javascript.count("return canManageOrganization(session, event?.organization_id);") == 3
    assert "window.SessionBuddyAccess = Object.freeze({" in javascript


def test_account_menu_dismissal_is_shared_across_shell_layouts() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    shared_handler = javascript.index("function closeOpenAccount")
    topbar_return = javascript.index('shell.className = "sb-app-shell sb-app-shell--single"')
    assert shared_handler < topbar_return
    escape_handler = 'if (event.key === "Escape") closeOpenAccount({ restoreFocus: true });'
    assert escape_handler in javascript
    assert "!openAccount.contains(event.target)" in javascript

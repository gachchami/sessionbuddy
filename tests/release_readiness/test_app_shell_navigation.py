from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_global_navigation_is_separate_from_the_scrollable_event_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "`sb-sidebar__group sb-sidebar__primary${organizerWorkspace" in javascript
    assert '"sb-sidebar__group sb-sidebar__event"' in javascript
    assert "sidebar.append(primaryGroup);" in javascript
    assert "sidebar.append(eventNav(currentEventId))" in javascript
    assert javascript.index("sidebar.append(primaryGroup);") < javascript.index(
        "sidebar.append(eventNav(currentEventId))"
    )
    assert (
        'document.body.classList.toggle("sb-shell-global", globalOrganizerWorkspace)'
        in javascript
    )
    assert "const globalNav" in javascript
    assert 'if (organizerWorkspace) {' in javascript
    assert 'Boolean(currentEventId) || section === "events"' in javascript
    assert "if (!organizerWorkspace || currentEventId) sidebar.append(brand);" in javascript
    assert 'topbar.classList.add("sb-topbar--event")' in javascript
    assert 'make("span", undefined, "sb-topbar__brand-space")' in javascript


def test_global_pages_use_the_approved_horizontal_navigation() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert ".sb-shell-global .sb-sidebar { display: none; }" in stylesheet
    assert ".sb-global-nav" in stylesheet
    assert ".sb-sidebar__mobile-global { display: none; }" in stylesheet
    assert ".sb-sidebar__mobile-global { display: block; }" in stylesheet
    primary_rule = stylesheet.split(".sb-sidebar__primary {", 1)[1].split("}", 1)[0]
    assert "border:" in primary_rule
    assert "border-radius:" in primary_rule
    assert "background:" in primary_rule


def test_role_portals_do_not_render_an_empty_primary_navigation_group() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert "if (organizerWorkspace) sidebar.append(primaryGroup);" in javascript
    assert 'if (roles.has("reviewer")) utilityNav.append' in javascript
    assert 'navLink("My reviews", "/reviews"' in javascript


def test_account_navigation_exposes_one_active_role_and_role_switching() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert 'const ACTIVE_ROLE_KEY = "sessionbuddy.active-role"' in javascript
    assert 'active ? roleLabel(active.role) : "Account"' in javascript
    assert 'make("p", "Switch role", "sb-role-switcher__label")' in javascript
    assert "return new Set(active ? [active.role] : [])" in javascript
    assert ".sb-active-role" in stylesheet
    assert ".sb-role-option" in stylesheet


def test_shell_uses_brand_asset_and_organizer_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert 'mark.src = "/landing/assets/sessionbuddy-favicon.svg"' in javascript
    assert 'nav.append(navLink("People", "/admin/speakers"' in javascript
    assert 'globalNav.append(navLink("People", "/admin/speakers"' in javascript


def test_landing_uses_one_role_aware_dashboard_entry() -> None:
    landing = (STATIC / "landing.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    primary_navigation = landing.split(
        '<nav class="primary-nav" aria-label="Primary navigation">', 1
    )[1].split("</nav>", 1)[0]
    hero_actions = landing.split('<div class="hero-actions">', 1)[1].split(
        "</div>", 1
    )[0]
    assert "Speaker portal" not in primary_navigation
    assert "Platform status" not in primary_navigation
    assert "Speaker portal" not in hero_actions
    assert 'const label = "Open dashboard"' in javascript
    assert 'return "/admin"' in javascript
    assert 'return "/speaker"' in javascript
    assert 'return "/reviews"' in javascript
    assert 'choice.role === "organization_admin"' in javascript
    assert 'choice.role === "evaluator"' in javascript
    assert 'choice.role === "speaker"' in javascript


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

    mobile_rules = stylesheet.split("@media (max-width: 60rem)", 1)[1]
    assert ".sb-sidebar { transform:" in mobile_rules
    assert ".sb-sidebar { overflow" not in mobile_rules

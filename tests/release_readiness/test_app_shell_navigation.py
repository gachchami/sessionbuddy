from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_global_navigation_is_separate_from_the_scrollable_event_navigation() -> None:
    javascript = (STATIC / "app_shell.js").read_text(encoding="utf-8")

    assert '"sb-sidebar__group sb-sidebar__primary"' in javascript
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


def test_global_pages_use_the_approved_horizontal_navigation() -> None:
    stylesheet = (STATIC / "app_shell.css").read_text(encoding="utf-8")

    assert ".sb-shell-global .sb-sidebar { display: none; }" in stylesheet
    assert ".sb-global-nav" in stylesheet
    primary_rule = stylesheet.split(".sb-sidebar__primary {", 1)[1].split("}", 1)[0]
    assert "border:" in primary_rule
    assert "border-radius:" in primary_rule
    assert "background:" in primary_rule


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

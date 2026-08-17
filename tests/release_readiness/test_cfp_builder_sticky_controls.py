"""The CFP builder must keep controls reachable without nested scrollports.

An automated browser scenario could not reach the CFP builder's publish and
availability controls because scrolling moved them behind inert sticky chrome.
The page already declared `position: sticky` on those controls, but an
`overflow: hidden` on the `.cfp-builder` ancestor made it their nearest
scrollport, and that box never scrolls -- so every sticky rule inside the
builder was inert and the controls scrolled away with the form.

The current design removes the competing builder scrollport and keeps its
controls in normal flow. Shell offsets remain derived from the chrome height.

A sibling defect existed one level down: the "Session format" system-field row
and its reorder buttons sat behind the sticky chrome, and scrolling them into
view did not recover them. Two causes, both pinned below. `.question-card`
carried the same `overflow: hidden` that had disabled `.cfp-builder`, which made
each card the scrollport `scrollIntoView` resolves against and discarded the
target's scroll-margin entirely. And the shell's scroll offsets were literals
(`9rem`) *smaller* than the chrome they had to clear (`--sb-chrome-top: 9.5rem`),
before counting the two sticky bars the builder stacks below it.
"""

import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"

# Values that make an element a scroll container, so `position: sticky`
# descendants stick to *it* instead of the viewport. `clip` and `visible` do not.
SCROLL_CONTAINER_VALUES = ("hidden", "auto", "scroll", "overlay")


def _declarations(styles: str, selector: str) -> str:
    """Return the merged declaration text of every top-level rule for a selector."""
    pattern = re.compile(
        r"(?:^|[},])\s*" + re.escape(selector) + r"\s*\{([^}]*)\}",
        re.MULTILINE,
    )
    blocks = pattern.findall(styles)
    assert blocks, f"no rule found for {selector!r}"
    return " ".join(blocks)


def _value(declarations: str, prop: str) -> str | None:
    match = re.search(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", declarations)
    return match.group(1).strip() if match else None


def _rules_defining(
    styles: str, prop: str, *, selector_contains: str
) -> list[tuple[str, str]]:
    """Return rules that own a declaration, independent of selector reshaping."""
    styles = re.sub(r"/\*.*?\*/", "", styles, flags=re.DOTALL)
    rules = []
    for selector, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", styles):
        normalized_selector = " ".join(selector.split())
        if selector_contains not in normalized_selector:
            continue
        if _value(declarations, prop) is not None:
            rules.append((normalized_selector, declarations))
    assert rules, (
        f"no rule containing {selector_contains!r} defines {prop!r}"
    )
    return rules


def _is_root_scoped(selector: str) -> bool:
    return selector.startswith(("html", ":root"))


def test_cfp_builder_uses_the_document_scroll_context() -> None:
    styles = (STATIC / "product.css").read_text()
    builder = _declarations(styles, ".cfp-builder")

    assert _value(builder, "position") == "static"
    assert _value(builder, "height") == "auto"
    assert _value(builder, "overflow") == "visible"


def test_publish_actions_stay_in_the_builder_header() -> None:
    page = (STATIC / "admin_programs.html").read_text()
    styles = (STATIC / "product.css").read_text()
    actions = _declarations(styles, ".cfp-editor-actions")

    assert page.index('id="cfp-editor-actions"') < page.index('id="publish-form"')
    assert 'id="publish-cfp-action" type="submit" form="publish-form"' in page
    assert _value(actions, "display") == "flex"


def test_form_outline_and_add_question_bar_remain_in_normal_flow() -> None:
    """Superseded design, deliberately.

    These two bars used to be pinned against `--sb-chrome-top` over a
    page-scrolled document. That kept them on screen but put them *in front of*
    the question rows, which is what made the Session-format row unclickable.
    They now sit in, or above, the builder pane's own scrolling region, so the
    offsets are relative to the pane and there is nothing left to overlap.
    """
    styles = (STATIC / "product.css").read_text()

    nav = _declarations(styles, ".cfp-section-nav")
    assert _value(nav, "position") != "sticky"
    assert _value(nav, "flex") is not None, (
        "the outline is a fixed-size row of the builder pane's flex column now"
    )

    actions = _declarations(styles, ".cfp-question-actions")
    assert _value(actions, "position") == "static"


def test_app_shell_publishes_its_chrome_height() -> None:
    styles = (STATIC / "app_shell.css").read_text()

    assert "--sb-chrome-top" in styles
    event_rules = _rules_defining(
        styles, "--sb-chrome-top", selector_contains="sb-shell-event"
    )
    event_shell = " ".join(declarations for _, declarations in event_rules)
    assert any(_is_root_scoped(selector) for selector, _ in event_rules)
    assert _value(event_shell, "--sb-chrome-top") is not None, (
        "the event shell must publish its own chrome height; page styles pin "
        "sticky elements against it"
    )


# Every box that wraps the builder's scrolling content. A scroll-container
# `overflow` on any of them silently captures both `position: sticky` resolution
# and `scrollIntoView`, and the symptom shows up on the controls inside, never on
# the container itself -- which is why this is checked by shape, not by location.
# Every box that wraps the builder's scrolling content *except* the scrollport
# itself. A scroll-container `overflow` on one of these captures both
# `position: sticky` resolution and `scrollIntoView` while never scrolling, and
# the symptom appears on the controls inside, never on the container -- which is
# why this is checked by shape, not by location.
BUILDER_CONTAINERS = (
    ".cfp-builder",
    ".cfp-editor-layout",
    ".cfp-editor-section",
    ".question-card",
)

SHELL_CONTROLS = (
    ".app-body.sb-shell-authenticated "
    ":where(button, a, input, select, textarea, summary)"
)


def test_no_builder_container_creates_a_scroll_container() -> None:
    styles = (STATIC / "product.css").read_text()

    for selector in BUILDER_CONTAINERS:
        overflow = _value(_declarations(styles, selector), "overflow")
        if overflow is None:
            continue
        assert overflow.split()[0] not in SCROLL_CONTAINER_VALUES, (
            f"`{selector}` must not create a scroll container: it wraps the "
            "builder's sticky controls and scroll targets, so a scrollport here "
            "makes `position: sticky` inert and makes `scrollIntoView` ignore "
            "the target's scroll-margin. Use `overflow: clip`."
        )


def test_shell_scroll_offsets_are_derived_from_the_chrome_height() -> None:
    styles = (STATIC / "app_shell.css").read_text()

    shell_rules = _rules_defining(
        styles, "--sb-scroll-offset", selector_contains="sb-shell-authenticated"
    )
    shell = " ".join(declarations for _, declarations in shell_rules)
    viewport_rules = _rules_defining(
        styles, "scroll-padding-top", selector_contains="sb-shell-authenticated"
    )
    viewport = " ".join(declarations for _, declarations in viewport_rules)
    assert any(_is_root_scoped(selector) for selector, _ in viewport_rules)
    assert _value(shell, "--sb-scroll-offset") is not None, (
        "the shell must publish the offset a scrolled-to element has to clear"
    )
    for block in (shell, viewport, _declarations(styles, SHELL_CONTROLS)):
        for prop in ("scroll-padding-top", "scroll-margin-top"):
            value = _value(block, prop)
            if value is None:
                continue
            assert "--sb-scroll-offset" in value, (
                f"`{prop}: {value}` is a literal. An offset smaller than the "
                "chrome it must clear parks the target behind that chrome, and "
                "scrolling into view again cannot recover it -- derive it from "
                "--sb-scroll-offset instead."
            )

    assert "scroll-margin-top: 9rem" not in styles, (
        "9rem is less than the 9.5rem event chrome it is supposed to clear"
    )


def test_cfp_page_clears_its_own_sticky_bars_too() -> None:
    styles = (STATIC / "product.css").read_text()

    page_rules = _rules_defining(
        styles, "--sb-scroll-offset", selector_contains="cfp-page"
    )
    page = " ".join(declarations for _, declarations in page_rules)
    assert any(_is_root_scoped(selector) for selector, _ in page_rules)
    offset = _value(page, "--sb-scroll-offset")

    assert offset is not None and "--sb-chrome-top" in offset, (
        "the builder stacks the form outline and the add-question bar below the "
        "shell chrome; scroll targets must clear all three, so the page has to "
        "raise --sb-scroll-offset above the bare chrome height"
    )
    assert "calc(" in offset, (
        "the page offset is the chrome height *plus* the builder's own sticky bars"
    )


def test_add_question_bar_does_not_swallow_clicks_beneath_it() -> None:
    styles = (STATIC / "product.css").read_text()

    bar = _declarations(styles, ".cfp-question-actions")
    assert _value(bar, "position") == "static"
    assert _value(bar, "pointer-events") != "none"


def test_the_form_does_not_create_a_second_vertical_scroll_region() -> None:
    styles = (STATIC / "product.css").read_text()

    builder = _declarations(styles, ".cfp-builder")
    assert _value(builder, "height") == "auto"

    editor = _declarations(styles, ".cfp-editor")
    assert _value(editor, "overflow-y") == "visible"

    nav = _declarations(styles, ".cfp-section-nav")
    assert _value(nav, "position") != "sticky", (
        "the form outline sits above the scrolling region now; leaving it sticky "
        "puts it back on top of the question rows, which is the defect"
    )

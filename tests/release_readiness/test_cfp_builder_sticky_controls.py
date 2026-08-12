"""The CFP builder's publish and navigation controls must stay on screen.

An SBek eval scenario burned its whole turn budget scrolling the CFP builder to
relocate the publish and availability controls (`claude/ux-evidence-log.md`).
The page already declared `position: sticky` on those controls, but an
`overflow: hidden` on the `.cfp-builder` ancestor made it their nearest
scrollport, and that box never scrolls -- so every sticky rule inside the
builder was inert and the controls scrolled away with the form.

These tests pin the contract that made them stick: the builder clips without
becoming a scroll container, and the pinned offsets are derived from the shell
chrome height rather than a hardcoded guess that drifts from the shell.

A later eval run found the sibling of that defect one level down
(`claude/ux-evidence-log.md`, 2026-08-16): the "Session format" system-field row
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


def test_cfp_builder_clips_without_becoming_a_scroll_container() -> None:
    styles = (STATIC / "product.css").read_text()
    overflow = _value(_declarations(styles, ".cfp-builder"), "overflow")

    assert overflow is not None
    assert overflow.split()[0] not in SCROLL_CONTAINER_VALUES, (
        "`.cfp-builder` must not create a scroll container: it is the ancestor of "
        "every sticky control in the form builder, and a scrollport ancestor that "
        "never scrolls makes `position: sticky` inert. Use `overflow: clip`."
    )


def test_publish_action_bar_stays_pinned_to_the_viewport() -> None:
    styles = (STATIC / "product.css").read_text()
    actions = _declarations(styles, ".cfp-editor-actions")

    assert _value(actions, "position") == "sticky"
    assert _value(actions, "bottom") == "0"


def test_form_outline_and_add_question_bar_pin_to_the_pane_not_the_page() -> None:
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

    top = _value(_declarations(styles, ".cfp-question-actions"), "top")
    assert top == "0", (
        "inside the pane's scrollport, the add-question bar pins at its top; a "
        "`--sb-chrome-top` offset here would push it a whole chrome height down "
        "into the form"
    )


def test_app_shell_publishes_its_chrome_height() -> None:
    styles = (STATIC / "app_shell.css").read_text()

    assert "--sb-chrome-top" in styles
    event_shell = _declarations(
        styles, ".app-body.sb-shell-authenticated.sb-shell-event"
    )
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

    shell = _declarations(styles, ".app-body.sb-shell-authenticated")
    assert _value(shell, "--sb-scroll-offset") is not None, (
        "the shell must publish the offset a scrolled-to element has to clear"
    )
    for block in (shell, _declarations(styles, SHELL_CONTROLS)):
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

    page = _declarations(styles, ".app-body.sb-shell-authenticated.cfp-page")
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
    assert _value(bar, "pointer-events") == "none", (
        "`.cfp-question-actions` fades to transparent over its lower third, so "
        "question rows scrolling beneath it stay visible while it takes their "
        "clicks; the bar itself must not be a hit-test target"
    )
    inner = _declarations(styles, ".cfp-question-actions > *")
    assert _value(inner, "pointer-events") == "auto", (
        "the + Add custom question button inside the bar must stay clickable"
    )


def test_the_form_is_the_one_scrolling_region_and_the_bars_sit_outside_it() -> None:
    """The builder is a pane, not a document.

    Sticky bars over a page-scrolled document always overlap the content beneath
    them. That is fine for a person, who scrolls a little further; it is not fine
    for an automated operator, because Chromium's `scrollIntoViewIfNeeded` -- what
    click harnesses call -- ignores `scroll-padding`/`scroll-margin` and stops as
    soon as the element is anywhere in the viewport, which may be directly under a
    bar. An eval agent lost 19 consecutive clicks to exactly that.

    So the form scrolls inside a bounded pane, with the outline nav above it and
    the publish bar pinned to the pane's own bottom. Nothing overlaps the rows.
    """
    styles = (STATIC / "product.css").read_text()

    builder = _declarations(styles, ".cfp-builder")
    assert _value(builder, "height") is not None, (
        "the builder must be bounded to the viewport, otherwise its form has no "
        "region of its own to scroll in"
    )
    assert "--sb-chrome-top" in (_value(builder, "top") or ""), (
        "pin the pane against the shell chrome variable, not a literal"
    )

    editor = _declarations(styles, ".cfp-editor")
    assert _value(editor, "overflow-y") in SCROLL_CONTAINER_VALUES, (
        "`.cfp-editor` is the intended scrollport -- unlike the containers in "
        "BUILDER_CONTAINERS, this box really does scroll, which is what makes "
        "the sticky bars inside it pin to the pane instead of over the rows"
    )

    nav = _declarations(styles, ".cfp-section-nav")
    assert _value(nav, "position") != "sticky", (
        "the form outline sits above the scrolling region now; leaving it sticky "
        "puts it back on top of the question rows, which is the defect"
    )

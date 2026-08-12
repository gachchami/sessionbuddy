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


def test_form_outline_stays_pinned_below_the_shell_chrome() -> None:
    styles = (STATIC / "product.css").read_text()
    nav = _declarations(styles, ".cfp-section-nav")

    assert _value(nav, "position") == "sticky"
    top = _value(nav, "top")
    assert top is not None and "--sb-chrome-top" in top, (
        "pin the form outline with the shell chrome variable so it tracks the "
        "topbar and event nav across breakpoints instead of a hardcoded offset"
    )


def test_question_actions_clear_the_shell_chrome() -> None:
    styles = (STATIC / "product.css").read_text()
    top = _value(_declarations(styles, ".cfp-question-actions"), "top")

    assert top is not None and "--sb-chrome-top" in top, (
        "the add-question bar sticks below the chrome and the form outline; a "
        "literal offset hides it behind the fixed shell header"
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

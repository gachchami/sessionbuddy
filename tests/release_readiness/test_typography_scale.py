"""The type scale is enforced here so it cannot silently erode again.

Before this test existed the stylesheets carried 52 distinct fixed font sizes
and 13 font weights. Ten of those sizes sat between 12.48px and 14.08px, gaps
too small to see, and 33 rules rendered below 11px. None of that was decided;
it accumulated one component at a time, which is exactly what a test prevents.

If you need a size or weight that is not below, change the scale here first and
in `assets/logo/README.md`, then update the stylesheets to match. Adding a
one-off value to a component is the failure mode this guards against.
"""

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"
STYLESHEETS = sorted(STATIC.glob("*.css")) + sorted((STATIC / "app" / "assets").glob("*.css"))

SCALE_PX = frozenset({12, 13, 14, 16, 18, 22, 28, 36, 48})
WEIGHTS = frozenset({"400", "500", "600", "700", "800"})
MINIMUM_PX = 12
# iOS Safari zooms the viewport when a focusable text field is smaller than
# this. File inputs are exempt because they open a picker, not the keyboard.
INPUT_MINIMUM_PX = 16

SIZE = re.compile(r"font-size:\s*([\d.]+)(rem|px)")
SHORTHAND = re.compile(r"font:\s*(\d{3})\s+([\d.]+)(rem|px)")
WEIGHT = re.compile(r"font-weight:\s*(\d{3})")
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
TEXT_INPUT = re.compile(r"\b(input|textarea|select)\b")


def to_px(value: str, unit: str) -> float:
    return float(value) * 16 if unit == "rem" else float(value)


def sizes_in(css: str) -> list[float]:
    found = [to_px(v, u) for v, u in SIZE.findall(css)]
    found += [to_px(v, u) for _, v, u in SHORTHAND.findall(css)]
    # font-size: 0 is a legitimate way to hide text from a visual layout
    return [px for px in found if px > 0]


@pytest.mark.parametrize("path", STYLESHEETS, ids=lambda p: p.name)
def test_every_font_size_is_on_the_scale(path: Path) -> None:
    off_scale = sorted(
        {px for px in sizes_in(path.read_text(encoding="utf-8")) if px not in SCALE_PX}
    )
    assert not off_scale, (
        f"{path.name} declares font sizes outside the scale: {off_scale}. "
        f"Allowed: {sorted(SCALE_PX)}"
    )


@pytest.mark.parametrize("path", STYLESHEETS, ids=lambda p: p.name)
def test_nothing_renders_below_twelve_pixels(path: Path) -> None:
    tiny = sorted({px for px in sizes_in(path.read_text(encoding="utf-8")) if px < MINIMUM_PX})
    assert not tiny, f"{path.name} sets text below {MINIMUM_PX}px: {tiny}"


@pytest.mark.parametrize("path", STYLESHEETS, ids=lambda p: p.name)
def test_only_standard_font_weights_are_used(path: Path) -> None:
    css = path.read_text(encoding="utf-8")
    used = set(WEIGHT.findall(css)) | {w for w, _, _ in SHORTHAND.findall(css)}
    assert used <= WEIGHTS, (
        f"{path.name} uses non-standard weights {sorted(used - WEIGHTS)}. "
        "font-synthesis is disabled, so these snap to a neighbouring weight and "
        "the intent is lost."
    )


@pytest.mark.parametrize("path", STYLESHEETS, ids=lambda p: p.name)
def test_text_inputs_are_at_least_sixteen_pixels(path: Path) -> None:
    offenders = []
    for selector, body in RULE.findall(path.read_text(encoding="utf-8")):
        selector = " ".join(selector.split())
        if not TEXT_INPUT.search(selector) or "file" in selector:
            continue
        match = SIZE.search(body)
        if match and to_px(match.group(1), match.group(2)) < INPUT_MINIMUM_PX:
            offenders.append(selector)
    assert not offenders, (
        f"{path.name} sets text inputs below {INPUT_MINIMUM_PX}px, which makes iOS "
        f"Safari zoom the page on focus: {offenders}"
    )


def test_source_wiring_dense_admin_controls_keep_mobile_zoom_safe() -> None:
    stylesheet = (STATIC / "admin_home.css").read_text(encoding="utf-8")

    assert "--home-control-font-size:1rem" in stylesheet
    assert "@media (min-width:64.01rem)" in stylesheet
    assert ".organizer-page--home { --home-control-font-size:.875rem; }" in stylesheet
    assert "font-size:var(--home-control-font-size)" in stylesheet


def test_no_stylesheet_names_a_font_the_project_does_not_ship() -> None:
    """A font named in CSS but never loaded silently falls back per platform.

    A named webfont must have both a bundled font file and an @font-face
    declaration. Inter was named in two stylesheets for months and never once
    rendered.
    """
    styles = {path: path.read_text(encoding="utf-8") for path in STYLESHEETS}
    webfonts = list(STATIC.rglob("*.woff2")) + list(STATIC.rglob("*.woff"))
    if any("DM Sans" in css for css in styles.values()):
        assert webfonts, "DM Sans is named but no webfont file is bundled"
        assert any(
            "@font-face" in css and 'font-family:"DM Sans"' in css
            for css in styles.values()
        ), "DM Sans is named but has no @font-face declaration"

    named = {"Inter", "Manrope", "Roboto Flex", "Satoshi", "Geist"}
    offenders = {
        path.name: sorted(
            font
            for font in named
            if re.search(rf"\b{font}\b", styles[path])
        )
        for path in STYLESHEETS
    }
    offenders = {name: fonts for name, fonts in offenders.items() if fonts}
    assert not offenders, (
        f"these stylesheets name fonts that are never loaded: {offenders}. "
        "Either ship the font or remove it from the stack."
    )


def test_source_wiring_home_font_is_content_addressed() -> None:
    stylesheet = (STATIC / "admin_home.css").read_text(encoding="utf-8")
    font = STATIC / "fonts" / "dm-sans-latin-wght-normal.woff2"
    expected_digest = hashlib.sha256(font.read_bytes()).hexdigest()[:12]
    match = re.search(r"dm-sans\.woff2\?v=([a-f0-9]{12})", stylesheet)

    assert match is not None
    assert match.group(1) == expected_digest

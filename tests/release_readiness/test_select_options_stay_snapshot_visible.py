"""Native <select> options must stay visible to an accessibility snapshot.

A `<select>` whose options are nested inside `<optgroup>` serialises to an
accessibility snapshot with *only* its direct-child options -- the grouped ones,
including whichever is selected, are dropped. Reduced to a control: two selects
on one page, both with a `selected` option, one grouped and one flat.

    GROUPED (optgroup):        FLAT:
    - combobox "Grouped":      - combobox "Flat":
      - option "Choose..."       - option "Choose..."
                                 - option "X" [selected]
                                 - option "Y"

The consequence is not cosmetic. Any operator reading the page through that
snapshot -- the SBek eval agent, any tool driving the console -- sees the control
as unset no matter how many times it sets it. In run `2026-08-17T00-14-15` the
agent set the CFP display-rule trigger correctly on its first attempt, then
re-selected it fifteen more times because every snapshot reported it empty, and
the scenario died at the 90-turn cap having done the work it could not observe.

Grouping is a presentational nicety; a control whose value cannot be read back is
a broken control. If grouping is wanted again, carry it in the option label.
"""

import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"

# Scripts that build the organizer console's form controls.
CONSOLE_SCRIPTS = sorted(STATIC.glob("*.js"))

# `<optgroup>` in markup, and the DOM call that creates one.
OPTGROUP = re.compile(r"<optgroup|createElement\(\s*[\"']optgroup[\"']\s*\)", re.IGNORECASE)


def _without_comments(source: str) -> str:
    """Drop // and /* */ comments so prose about the ban does not trip it."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


def test_no_console_script_groups_select_options_in_an_optgroup() -> None:
    offenders = [
        path.name
        for path in CONSOLE_SCRIPTS
        if OPTGROUP.search(_without_comments(path.read_text()))
    ]

    assert not offenders, (
        f"{', '.join(offenders)} build a <select> with <optgroup>. Every option "
        "inside an optgroup disappears from the accessibility snapshot, so the "
        "control reports as unset however many times it is set -- see this "
        "module's docstring for the run that lost a scenario to it. Append the "
        "options directly and put any grouping in the label text."
    )


def test_no_console_page_ships_a_static_optgroup() -> None:
    offenders = [
        path.name for path in sorted(STATIC.glob("*.html")) if OPTGROUP.search(path.read_text())
    ]

    assert not offenders, f"{', '.join(offenders)} contain a literal <optgroup>"


def test_source_wiring_the_display_rule_trigger_lists_its_options_directly() -> None:
    """Pin the specific control the eval lost turns to."""
    source = _without_comments((STATIC / "admin_programs.js").read_text())
    trigger = source[source.index('conditionQuestion.name = "condition_source"'):][:2000]

    assert "conditionQuestion.add(option)" in trigger, (
        "options for the display-rule trigger must be added straight to the "
        "select, so the selected one is readable from a snapshot"
    )
    assert "option.dataset.sourceKey" in trigger, (
        "the stable schema key still travels on the option; only the grouping "
        "went away"
    )

"""Source and wiring checks for the event editor's section navigation, live
summary, and filterable flat time-zone select.

These are literal-membership tests over the static sources; they pin the
markup contract the Playwright specs and the accessibility snapshot rely on,
not rendered behaviour.
"""

import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"
SECTION_IDS = ("general", "date-time", "branding", "email", "lifecycle")


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_source_wiring_event_editor_nav_links_every_section_with_a_state() -> None:
    page = source("event_editor.html")

    assert 'id="event-editor-nav"' in page
    nav = page.split('id="event-editor-nav"', 1)[1].split("</nav>", 1)[0]
    links = re.findall(r"<a [^>]*>.*?</a>", nav)
    assert len(links) == len(SECTION_IDS)
    for section_id, link in zip(SECTION_IDS, links, strict=True):
        assert f'data-section-link="{section_id}"' in link
        assert f'href="#{section_id}"' in link
        # A non-empty state span keeps every link's text distinct from the
        # section's own heading, which the exact-text browser checks rely on.
        assert f'<span id="nav-state-{section_id}"' in link
        assert re.search(r'class="event-editor__nav-state">[^<]+</span>', link)
    lifecycle_link = nav.split('id="nav-lifecycle"', 1)[1].split(">", 1)[0]
    assert "hidden" in lifecycle_link


def test_source_wiring_event_editor_summary_panel_follows_the_form() -> None:
    page = source("event_editor.html")
    script = source("event_editor.js")

    assert 'id="event-summary-panel"' in page
    panel = page.split('id="event-summary-panel"', 1)[1].split("</section>", 1)[0]
    for summary_id in (
        "summary-name",
        "summary-status",
        "summary-dates",
        "summary-location",
        "summary-organization",
    ):
        assert f'id="{summary_id}"' in panel
    # The event name lives in a <dd>; the page's only heading named after the
    # event stays the <h1>.
    assert "<h2" not in panel.split("</h2>", 1)[1]
    assert "function updateSummary(" in script
    assert "function syncSectionNav(" in script
    assert "function describeDates(values)" in script
    assert 'byId("summary-name").textContent = values.name.trim() || "Untitled event"' in script


def test_source_wiring_time_zone_select_stays_flat_and_filterable() -> None:
    page = source("event_editor.html")
    script = source("event_editor.js")

    assert "<optgroup" not in page
    assert "optgroup" not in script
    filter_tag = page.split('id="time-zone-filter"', 1)[1].split(">", 1)[0]
    assert 'aria-describedby="event-time-zone-context"' not in filter_tag
    assert "name=" not in filter_tag
    assert 'aria-controls="event-time-zone"' in filter_tag
    assert (
        '<select name="time_zone" id="event-time-zone" required '
        'aria-describedby="event-time-zone-context">'
    ) in page
    assert 'id="time-zone-filter-status"' in page

    assert "function renderZoneOptions" in script
    assert "function updateTimeZoneContext" in script
    assert 'const REGION_ORDER = ["UTC", "Africa", "America"' in script
    assert "zones match" in script
    keydown = script.split('byId("time-zone-filter").addEventListener("keydown"', 1)[1]
    keydown = keydown.split("});", 1)[0]
    assert "stopPropagation()" in keydown
    assert "form.elements.time_zone.focus()" in keydown
    assert "onLeave(() => clearInterval(tick))" in script
    # Typing in the filter must not dirty the form or trigger a preview.
    for listener in ("input", "change"):
        assert (
            f'form.addEventListener("{listener}", (event) => '
            '{ if (event.target.id === "time-zone-filter") return;'
        ) in script
    # Enter on the select the filter hands focus to must not reach the form's
    # Enter-to-submit handler either.
    select_keydown = script.split('form.elements.time_zone.addEventListener("keydown"', 1)[1]
    select_keydown = select_keydown.split("});", 1)[0]
    assert 'if (event.key === "Enter") event.stopPropagation()' in select_keydown
    # A reconcile, "Use latest", or discard assigns a zone directly, so every
    # such value stays an option even while the list is narrowed, and the
    # filter is inert while a save is in flight.
    assert (
        "const keep = new Set([current, state.baseline.time_zone, state.latest.time_zone, "
        'state.conflicts.get("time_zone")?.latest].filter(Boolean))'
    ) in script
    assert 'form.querySelectorAll("button, input[type=file], #time-zone-filter")' in script


def test_source_wiring_section_navigation_marks_current_and_deep_links() -> None:
    script = source("event_editor.js")

    assert (
        'const SECTION_IDS = ["general", "date-time", "branding", "email", "lifecycle"]' in script
    )
    assert 'setAttribute("aria-current", "location")' in script
    assert "function goToSection(id" in script
    assert "section instanceof HTMLDetailsElement" in script
    assert "observer.disconnect()" in script
    assert "const target = location.hash.slice(1);" in script
    # The hash update keeps the app shell's history stamp (sbIndex) on the
    # entry; replaceState(null, ...) would disable its unsaved-changes guard.
    assert 'history.replaceState(history.state, "", `#${id}`)' in script
    assert "history.replaceState(null" not in script
    # The spy records every callback, including during the post-click pause,
    # and re-syncs once the pause ends.
    assert (
        "for (const entry of entries) { if (entry.isIntersecting) inView.add(entry.target.id); "
        "else inView.delete(entry.target.id); }"
    ) in script
    assert "spyTimer = setTimeout(syncCurrentSection, SPY_PAUSE_MS)" in script


def test_source_wiring_summary_never_guesses_a_status_or_organization() -> None:
    page = source("event_editor.html")
    script = source("event_editor.js")

    assert 'id="summary-status-row"' in page
    assert 'function statusUnknown() { return state.mode === "edit" && !state.event; }' in script
    assert 'byId("summary-status-row").hidden = statusUnknown()' in script
    assert 'byId("nav-lifecycle").hidden = state.mode !== "edit" || statusUnknown()' in script
    # session.organization_name names the first administered organization; it
    # is only used when that is the event's organization.
    assert (
        "state.session?.organization_id === state.organizationId "
        '? state.session.organization_name : ""'
    ) in script
    assert "|| state.session?.organization_name ||" not in script

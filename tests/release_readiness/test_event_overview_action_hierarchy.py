from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_overview_keeps_cfp_prominent_and_links_each_status_row() -> None:
    page = (STATIC / "event_overview.html").read_text(encoding="utf-8")
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert 'id="primary-action"' not in page
    assert 'byId("primary-action")' not in script
    assert 'id="next-step-action" class="button"' in page
    assert 'id="cfp-action" class="button event-command-header__primary"' in page
    assert 'id="cfp-link"' in page
    assert 'id="public-schedule" class="button secondary"' in page
    assert 'byId("next-step").hidden = cfpPublished' in script
    assert 'byId("cfp-link").href' in script
    assert 'byId("cfp-action").href' in script


def test_overview_defers_navigation_to_the_horizontal_event_bar() -> None:
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")
    page = (STATIC / "event_overview.html").read_text(encoding="utf-8")

    assert 'class="event-stage-list"' not in page
    assert "Program status" not in page
    assert 'id="event-tools"' not in page
    assert 'aria-label="Event status"' in page
    assert 'id="proposal-count"' in page
    assert 'id="agenda-count"' in page
    assert 'phase === "current" ? `Continue ${title} →` : `View ${title} →`' not in script
    assert 'padStart(2, "0")' not in script
    assert 'function tool(' not in script


def test_live_cfp_does_not_present_reviewing_as_an_organizer_next_step() -> None:
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert '"Review incoming proposals"' not in script
    assert '"Open submissions"' not in script
    assert 'byId("next-step").hidden = cfpPublished' in script
    assert '`${selected.name} is ready.`' not in script


def test_event_cover_is_top_anchored_in_a_stable_header_ratio() -> None:
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")
    page = (STATIC / "event_overview.html").read_text(encoding="utf-8")

    branding = stylesheet.split(
        ".event-command-header .public-brand-preview__cover {", 1
    )[1].split("}", 1)[0]
    banner = stylesheet.split(".public-brand-preview__cover img {", 1)[1].split(
        "}", 1
    )[0]
    assert "aspect-ratio: 4.5 / 1;" in branding
    assert "object-position: center top;" in banner
    assert 'class="event-command-header public-brand-preview__card"' in page
    assert 'class="public-brand-preview__header"' in page
    assert 'class="public-brand-preview__logo"' in page
    assert 'class="public-brand-preview__cover"' in page
    assert 'class="public-brand-preview__body"' in page

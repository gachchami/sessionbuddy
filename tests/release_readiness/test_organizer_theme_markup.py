from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_organizer_pages_expose_consistent_theme_hooks() -> None:
    pages = {
        "admin_home.html": "organizer-page--home",
        "events_admin.html": "organizer-page--events",
        "event_overview.html": "organizer-page--event-overview",
        "access_admin.html": "organizer-page--access",
        "account.html": "organizer-page--account",
        "admin_onboarding.html": "organizer-page--onboarding",
    }

    for name, modifier in pages.items():
        page = source(name)
        assert "organizer-page" in page
        assert modifier in page
        assert "organizer-main" in page
        assert "organizer-hero" in page


def test_organizer_pages_mark_major_surfaces_and_generated_cards() -> None:
    assert "organizer-metrics" in source("admin_home.html")
    assert "organizer-section--events" in source("events_admin.html")
    assert "organizer-callout" in source("event_overview.html")
    assert "organizer-split-layout" in source("access_admin.html")
    assert "organizer-panel--profile" in source("account.html")
    assert "organizer-panel--results" in source("admin_onboarding.html")

    scripts_and_hooks = {
        "admin_home.js": "organizer-event-card",
        "events_admin.js": "organizer-event-list-card",
        "event_overview.js": "organizer-workflow-card",
        "access_admin.js": "organizer-access-list__item",
        "account.js": "organizer-access-card",
        "admin_onboarding.js": "organizer-task-card",
    }
    for name, hook in scripts_and_hooks.items():
        assert hook in source(name)


def test_theme_markup_keeps_page_headings_and_landmarks_accessible() -> None:
    for name in (
        "admin_home.html",
        "events_admin.html",
        "event_overview.html",
        "access_admin.html",
        "account.html",
        "admin_onboarding.html",
    ):
        page = source(name)
        assert '<main id="main"' in page
        assert 'tabindex="-1"' in page
        assert "<h1" in page

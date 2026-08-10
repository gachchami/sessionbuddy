from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_home_renders_events_as_responsive_visual_cards() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")
    styles = (STATIC / "product.css").read_text(encoding="utf-8")

    assert 'id="event-list" class="home-event-grid"' in page
    assert "function eventCard(event)" in script
    assert (
        'make("article", undefined, "event-visual-card organizer-card organizer-event-card")'
        in script
    )
    assert ".home-event-grid { display: grid;" in styles
    assert "repeat(auto-fit, minmax(min(100%, 25rem), 1fr))" in styles
    assert "grid-template-columns: 9rem minmax(0,1fr)" in styles
    assert ".event-visual-card__media" in styles


def test_home_event_cards_show_branding_and_operational_details() -> None:
    page = (STATIC / "admin_home.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'href="/admin/events#event-form"' in page
    assert "event.cover_image_url" in script
    assert "event.logo_url" in script
    assert 'eventInitials(event.name)' in script
    assert "event.accent_color" in script
    assert "formatEventDateTime(event)" in script
    assert "event.location" in script
    assert "deliveryModeLabel(event.delivery_mode)" in script
    assert 'make("span", event.status, "badge")' in script
    assert 'make("a", "Open event →", "event-visual-card__action")' in script
    assert 'open.setAttribute("aria-label", `Open ${event.name}`)' in script


def test_home_event_branding_falls_back_when_images_fail() -> None:
    script = (STATIC / "admin_home.js").read_text(encoding="utf-8")

    assert 'cover.addEventListener("error", () => cover.remove(), { once: true })' in script
    assert 'logo.addEventListener("error", () => logo.remove(), { once: true })' in script
    assert 'placeholder.setAttribute("aria-hidden", "true")' in script

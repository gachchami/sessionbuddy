"""Contracts for shared UI behavior that must survive visual distillation."""

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_public_event_accent_drives_shared_action_tokens() -> None:
    styles = (STATIC / "product.css").read_text()

    assert ":where(.cfp-public-page, .schedule-page, .speaker-gallery-page)" in styles
    assert "--action-primary: var(--event-accent" in styles
    assert "--blue: var(--action-primary)" in styles


def test_distillation_does_not_hide_every_eyebrow_or_section_summary() -> None:
    styles = (STATIC / "product.css").read_text()

    assert ".eyebrow,\n.portal-hero__eyebrow" not in styles
    assert ".eyebrow { display: none !important; }" not in styles
    assert ".workflow-page .section-heading .section-summary" not in styles


def test_landing_demo_promise_is_gated_with_demo_personas() -> None:
    markup = (STATIC / "landing.html").read_text()
    scripts = (STATIC / "demo_access.js").read_text()

    assert 'class="demo-section" data-demo-panel' in markup
    assert 'data-demo-panel aria-labelledby="demo-section-title" hidden' in markup
    assert 'closest("[data-demo-panel]")?.removeAttribute("hidden")' in scripts


def test_mobile_multi_value_people_cells_keep_flex_layout() -> None:
    styles = (STATIC / "product.css").read_text()
    generic = styles.index(".people-table-row [data-label] { display: grid;")
    multi_value = styles.index(
        ".people-table-row :is(.people-role-list, .reviewer-row-actions)[data-label] "
        "{ display: flex;"
    )

    assert multi_value > generic
    assert (
        ".people-table-row :is(.people-role-list, .reviewer-row-actions)"
        "[data-label]::before { flex: 0 0 100%; }"
    ) in styles

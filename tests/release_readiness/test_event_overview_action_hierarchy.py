from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_overview_has_one_primary_cfp_next_step() -> None:
    page = (STATIC / "event_overview.html").read_text(encoding="utf-8")
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert 'id="primary-action"' not in page
    assert 'byId("primary-action")' not in script
    assert 'id="next-step-action" class="button"' in page
    assert 'id="public-schedule" class="button secondary"' in page
    assert 'cfpLive ? "Open submissions" : "Set up the form"' in script


def test_cfp_workflow_card_is_status_and_navigation_not_a_setup_cta() -> None:
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert 'open.textContent = "Open →"' in script
    assert 'tool(1, "Call for Proposals", "Manage the form and its public link."' in script
    assert 'cfpLive ? "Live" : "Not published"' in script
    assert 'cfpLive ? "Live" : "Set up"' not in script

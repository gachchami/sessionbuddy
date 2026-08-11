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
    assert 'submissionCount === 0 ? "Open Call for Proposals" : "Open submissions"' in script


def test_program_rail_is_status_and_navigation_not_a_second_action_list() -> None:
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")
    page = (STATIC / "event_overview.html").read_text(encoding="utf-8")

    assert 'class="event-stage-list"' in page
    assert 'id="workflow-title">Program status<' in page
    assert 'aria-label="Program snapshot"' in page
    assert 'id="proposal-count"' in page
    assert 'id="agenda-count"' in page
    assert 'phase === "current" ? `Continue ${title} →` : `View ${title} →`' not in script
    assert 'padStart(2, "0")' not in script
    assert 'cfpLive ? "Live" : "Not published"' in script
    assert 'cfpLive ? "Live" : "Set up"' not in script


def test_zero_proposal_state_does_not_recommend_reviewing_empty_inbox() -> None:
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert 'submissionCount === 0\n        ? "Bring in the first proposal"' in script
    assert 'No proposals have arrived yet.' in script
    assert '`${selected.name} is ready.`' not in script

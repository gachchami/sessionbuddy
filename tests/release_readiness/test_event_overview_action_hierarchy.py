from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_source_wiring_overview_keeps_cfp_prominent_and_links_each_status_row() -> None:
    page = (STATIC / "event_overview.html").read_text(encoding="utf-8")
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert 'id="primary-action"' not in page
    assert 'byId("primary-action")' not in script
    assert 'id="next-step-action" class="button"' in page
    assert (
        'id="cfp-action" class="button event-command-header__primary" '
        'href="#" hidden>Edit CFP</a>' in page
    )
    assert 'id="launch-readiness-steps"' in page
    assert 'id="edit-event" class="button secondary"' in page
    assert 'id="public-schedule" class="button secondary"' in page
    assert 'byId("next-step").hidden = cfpPublished' in script
    assert "href: `${prefix}/cfp`" in script
    assert "link.href = step.href" in script
    assert 'byId("cfp-action").href' in script
    assert 'byId("cfp-action").hidden = !cfpPublished' in script
    assert 'byId("edit-event").href' in script


def test_source_wiring_overview_defers_navigation_to_the_horizontal_event_bar() -> None:
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")
    page = (STATIC / "event_overview.html").read_text(encoding="utf-8")

    assert 'class="event-stage-list"' not in page
    assert "Program status" not in page
    assert 'id="event-tools"' not in page
    assert 'aria-label="Event status"' not in page
    assert 'id="proposal-count"' not in page
    assert 'id="agenda-count"' not in page
    assert 'id="launch-readiness-steps"' in page
    assert "received`" in script
    assert "confirmed of ${speakers.total} invited" in script
    assert 'phase === "current" ? `Continue ${title} →` : `View ${title} →`' not in script
    assert 'padStart(2, "0")' not in script
    assert 'function tool(' not in script


def test_source_wiring_live_cfp_does_not_present_reviewing_as_an_organizer_next_step() -> None:
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    assert '"Review incoming proposals"' not in script
    assert '"Open submissions"' not in script
    assert 'byId("next-step").hidden = cfpPublished' in script
    assert '`${selected.name} is ready.`' not in script


def test_source_wiring_launch_readiness_reads_publication_and_confirmation_fields() -> None:
    script = (STATIC / "event_overview.js").read_text(encoding="utf-8")

    # AdminAgendaView.revision is always the working draft; only
    # published_revision says an agenda is published (scheduling/router.py).
    assert "Boolean(agenda && agenda.published_revision)" in script
    assert "revision.status" not in script
    # /speaker-targets includes pending invitations; only confirmed rows count.
    assert 'speaker.confirmation_status === "confirmed"' in script
    assert "${speakers.length} confirmed" not in script
    # CfpWorkspaceView carries the draft as draft_form only.
    assert "hasDraft: Boolean(cfp.draft_form) }" in script
    # A draft event is never a complete part; activation is the remaining step.
    assert '"Details complete · activate to publish"' in script
    assert '"Dates have passed · update before activating"' in script
    assert "draft, activate to publish" not in script


def test_source_wiring_event_overview_keeps_public_brand_preview_in_event_settings() -> None:
    page = (STATIC / "event_overview.html").read_text(encoding="utf-8")
    editor = (STATIC / "event_editor.html").read_text(encoding="utf-8")
    assert "data-public-event-masthead" not in page
    assert "/public/assets/event-masthead.js" not in page
    assert "data-public-event-masthead" in editor
    assert "/public/assets/event-masthead.js" in editor
    assert 'class="event-command-header"' in page

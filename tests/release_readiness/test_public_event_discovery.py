from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_landing_mounts_the_existing_public_event_directory() -> None:
    landing = source("landing.html")
    shell = source("app_shell.js")

    assert 'aria-labelledby="public-programs-title"' in landing
    assert 'data-public-events class="public-events-grid" aria-busy="true"' in landing
    assert 'class="public-events-empty" role="status"' in landing
    assert 'request("/api/v1/public/events")' in shell
    assert '`/cfp/${eventKey}/${encodeURIComponent(event.cfp_slug)}`' in shell
    assert '`/events/${encodeURIComponent(event.id)}/schedule`' in shell
    assert '`/events/${encodeURIComponent(event.id)}/speakers`' in shell


def test_public_event_directory_announces_empty_and_failure_states() -> None:
    shell = source("app_shell.js")

    assert 'empty.setAttribute("role", "status")' in shell
    assert 'error.setAttribute("role", "alert")' in shell
    assert 'publicEvents.setAttribute("aria-busy", "true")' in shell
    assert 'publicEvents.setAttribute("aria-busy", "false")' in shell


def test_share_page_points_publication_to_the_agenda_surface() -> None:
    page = source("event_workspace.html")
    script = source("event_workspace.js")
    shell = source("app_shell.js")

    assert "<h1>Share &amp; integrations</h1>" in page
    assert "Agenda publication is controlled from the Agenda page." in page
    assert 'id="publish-agenda"' in page
    assert 'byId("publish-agenda").href = `/admin/events/${encoded}/agenda`' in script
    assert '["Agenda & publish", `${prefix}/agenda`' in shell
    assert '["Share & integrations", `${prefix}/workspace`' in shell
    assert '["Publish", `${prefix}/workspace`' not in shell

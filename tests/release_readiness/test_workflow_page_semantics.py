from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_event_workflow_pages_expose_consistent_page_and_stage_hooks() -> None:
    pages = {
        "admin_programs.html": "workflow-page--cfp",
        "admin_submissions.html": "workflow-page--submissions",
        "agenda_admin.html": "workflow-page--agenda",
        "event_workspace.html": "workflow-page--resources",
        "speaker_content.html": "workflow-page--speaker-content",
        "speaker_messages.html": "workflow-page--messages",
    }

    for filename, page_class in pages.items():
        page = source(filename)
        assert f"workflow-page {page_class}" in page
        assert "workflow-shell" in page
        assert "workflow-hero" in page
        assert "workflow-stage" in page


def test_workflow_loading_and_empty_states_are_announced_without_alert_noise() -> None:
    submissions = source("admin_submissions.html")
    agenda = source("agenda_admin.html")
    resources = source("speaker_content.html")
    messages = source("speaker_messages.html")

    assert 'id="submissions" aria-live="polite"' in submissions
    assert 'id="round-history" class="entity-grid" aria-live="polite"' in submissions
    assert 'id="round-result" class="public-link" role="status" aria-live="polite"' in submissions
    assert 'id="unscheduled" class="session-list" aria-live="polite"' in agenda
    assert 'class="empty workflow-empty-state" role="status"' in agenda
    assert (
        'id="file-list" class="item-list" aria-label="Speaker files" aria-live="polite"'
        in resources
    )
    assert 'id="recipient-list" class="recipient-list" aria-live="polite"' in messages
    assert 'id="message-history" aria-live="polite"' in messages


def test_each_complex_workflow_region_has_an_accessible_name() -> None:
    cfp = source("admin_programs.html")
    submissions = source("admin_submissions.html")
    sharing = source("event_workspace.html")
    resources = source("speaker_content.html")
    messages = source("speaker_messages.html")

    assert 'aria-labelledby="cfp-builder-title"' in cfp
    assert 'id="cfp-builder-title"' in cfp
    assert 'aria-labelledby="submission-list-title"' in submissions
    assert 'aria-labelledby="rounds-title"' in submissions
    assert 'aria-label="Sharing and integration tools"' in sharing
    assert 'id="publish-agenda"' in sharing
    assert 'aria-label="Speaker tasks, files, and resources"' in resources
    assert 'aria-labelledby="recipients-title"' in messages
    assert 'aria-labelledby="compose-title"' in messages
    assert 'aria-labelledby="history-title"' in messages

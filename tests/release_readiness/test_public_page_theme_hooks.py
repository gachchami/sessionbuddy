from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def page(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_public_and_speaker_pages_have_distinct_layout_hooks() -> None:
    expected = {
        "landing.html": ("marketing-page", "marketing-main"),
        "public_cfp.html": ("cfp-public-page", "public-flow-shell", "flow-form-card"),
        "speaker_portal.html": ("speaker-portal-page", "speaker-portal-shell", "portal-hero"),
        "speaker_directory.html": (
            "speaker-directory-page",
            "speaker-directory-shell",
            "directory-hero",
        ),
        "speaker_gallery.html": (
            "speaker-gallery-page",
            "speaker-gallery-shell",
            "gallery-hero",
        ),
        "schedule.html": ("schedule-page", "schedule-shell", "schedule-controls"),
    }
    for filename, hooks in expected.items():
        markup = page(filename)
        for hook in hooks:
            assert hook in markup, f"{filename} is missing {hook}"


def test_auth_setup_invitation_and_error_pages_have_specific_surfaces() -> None:
    expected = {
        "sign_in.html": ("sign-in-page", "sign-in-shell", "sign-in-card"),
        "setup.html": ("setup-page", "setup-form-surface"),
        "co_speaker_invitation.html": (
            "invitation-page",
            "invitation-shell",
            "invitation-card",
        ),
        "auth_link_error.html": ("auth-error-page", "auth-error-shell", "auth-error-card"),
        "error_page.html": ("error-page", "error-surface"),
    }
    for filename, hooks in expected.items():
        markup = page(filename)
        for hook in hooks:
            assert hook in markup, f"{filename} is missing {hook}"


def test_page_specific_stylesheets_own_visual_treatments() -> None:
    styles = {
        "landing.css": (".marketing-page", ".hero:after"),
        "speaker.css": (".speaker-portal-page", ".portal-hero"),
        "schedule.css": (".schedule-page", ".schedule-hero", ".schedule-controls"),
        "setup.css": ("linear-gradient", ".setup-card"),
        "error_page.css": ("radial-gradient", ".status-panel", ".route-line"),
    }
    for filename, markers in styles.items():
        stylesheet = page(filename)
        for marker in markers:
            assert marker in stylesheet, f"{filename} is missing {marker}"


def test_error_document_is_reusable_and_offers_explicit_recovery() -> None:
    markup = page("error_page.html")

    assert '<section class="error-surface" aria-labelledby="error-heading">' in markup
    assert '<h1 id="error-heading">{{HEADING}}</h1>' in markup
    assert '<p class="status-code"><span>Status</span>{{STATUS}}</p>' in markup
    assert '<nav class="actions" aria-label="Recovery options">' in markup
    assert 'href="{{PRIMARY_HREF}}">{{PRIMARY_LABEL}}</a>' in markup
    assert '<footer class="error-reference">{{REFERENCE}}</footer>' in markup
    assert 'class="skip-link" href="#main"' in markup

    # The shared 403/404 document presents server-selected actions. It never
    # guesses a role portal or navigates before the person chooses an action.
    assert 'href="/admin"' not in markup
    assert 'href="/speaker"' not in markup
    assert 'href="/reviews"' not in markup
    assert "http-equiv=\"refresh\"" not in markup
    assert "<script" not in markup


def test_error_document_remains_accessible_on_small_screens() -> None:
    stylesheet = page("error_page.css")

    assert "a:focus-visible" in stylesheet
    assert "@media (max-width: 42rem)" in stylesheet
    assert ".actions { display: grid; }" in stylesheet
    assert ".button { width: 100%; }" in stylesheet
    assert "@media (forced-colors: active)" in stylesheet


def test_schedule_controls_remain_semantically_labelled() -> None:
    markup = page("schedule.html")

    assert '<section class="schedule-controls" aria-label="Find and filter sessions">' in markup
    assert '<nav class="schedule-filters" aria-label="Schedule views">' in markup
    assert 'role="status" aria-live="polite"' in markup

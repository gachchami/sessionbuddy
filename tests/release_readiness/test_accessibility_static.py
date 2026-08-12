from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"
CORE_HTML = [
    "public_cfp.html",
    "speaker_portal.html",
    "admin_onboarding.html",
    "agenda_admin.html",
    "schedule.html",
    "admin_home.html",
    "event_overview.html",
    "speaker_directory.html",
    "speaker_messages.html",
    "account.html",
    "setup.html",
]


class AuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def parse(name: str) -> tuple[str, AuditParser]:
    text = (STATIC / name).read_text(encoding="utf-8")
    parser = AuditParser()
    parser.feed(text)
    return text, parser


@pytest.mark.parametrize("name", CORE_HTML)
def test_core_pages_have_language_viewport_skip_target_and_live_status(name: str) -> None:
    text, parser = parse(name)
    assert any(tag == "html" and attrs.get("lang") == "en" for tag, attrs in parser.tags)
    assert any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in parser.tags)
    skip_targets = {
        attrs["href"].removeprefix("#")
        for tag, attrs in parser.tags
        if tag == "a"
        and attrs.get("class") == "skip-link"
        and attrs.get("href", "").startswith("#")
    }
    ids = {attrs.get("id") for _, attrs in parser.tags}
    assert skip_targets and skip_targets <= ids
    assert 'aria-live="polite"' in text
    assert 'tabindex="1"' not in text and 'tabindex="2"' not in text


def test_public_form_and_portal_expose_errors_and_progress_accessibly() -> None:
    cfp = (STATIC / "public_cfp.html").read_text()
    cfp_js = (STATIC / "public_cfp.js").read_text()
    portal = (STATIC / "speaker_portal.html").read_text()
    assert 'id="status"' in cfp and 'tabindex="-1"' in cfp
    assert 'byId("status").focus()' in cfp_js
    assert 'role="status" aria-live="polite"' in cfp
    assert 'aria-labelledby="submissions-title"' in portal
    assert 'aria-label="Submission summary"' in portal
    # Event groups are generated, so their heading link is asserted in the
    # renderer rather than in the served shell.
    portal_js = (STATIC / "speaker_portal.js").read_text()
    assert 'section.setAttribute("aria-labelledby", title.id)' in portal_js
    assert 'id="profile"' not in portal
    assert 'id="public-profile-link"' in portal


def test_landing_page_has_semantic_navigation_and_role_entry_points() -> None:
    text, parser = parse("landing.html")
    assert any(tag == "html" and attrs.get("lang") == "en" for tag, attrs in parser.tags)
    assert any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in parser.tags)
    assert any(tag == "main" and attrs.get("id") == "main" for tag, attrs in parser.tags)
    assert any(
        tag == "nav" and attrs.get("aria-label") == "Primary navigation"
        for tag, attrs in parser.tags
    )
    assert 'class="skip-link" href="#main"' in text
    assert text.count("<h1") == 1
    assert 'href="/sign-in?redirect=%2Fadmin"' in text
    assert 'aria-label="Role portals"' not in text
    assert 'href="/engine-room"' not in text
    assert 'tabindex="1"' not in text and 'tabindex="2"' not in text


def test_expired_link_recovery_page_has_accessible_actions() -> None:
    text, parser = parse("auth_link_error.html")
    assert any(tag == "html" and attrs.get("lang") == "en" for tag, attrs in parser.tags)
    assert any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in parser.tags)
    assert any(tag == "main" and attrs.get("id") == "main" for tag, attrs in parser.tags)
    assert 'class="skip-link" href="#main"' in text
    assert text.count("<h1") == 1
    assert 'href="/sign-in"' in text
    assert 'href="/"' in text


def test_event_creation_defaults_are_explained_and_not_demo_data() -> None:
    text, _ = parse("events_admin.html")
    javascript = (STATIC / "events_admin.js").read_text()
    stylesheet = (STATIC / "product.css").read_text()
    assert 'id="new-event"' in text
    assert 'id="event-dialog"' in text
    event_form = text.split('<form id="event-form">', 1)[1].split("</form>", 1)[0]
    event_form_parser = AuditParser()
    event_form_parser.feed(event_form)
    event_name = next(
        attrs
        for tag, attrs in event_form_parser.tags
        if tag == "input" and attrs.get("name") == "name"
    )
    assert "placeholder" not in event_name
    for field_name in (
        "name",
        "time_zone",
        "delivery_mode",
        "start_date",
        "start_time",
        "end_date",
        "end_time",
        "location",
        "description",
    ):
        field_line = next(
            line for line in event_form.splitlines() if f'name="{field_name}"' in line
        )
        assert '<span class="required-marker" aria-hidden="true">*</span>' in field_line
        assert " required" in field_line
    assert ".required-marker" in stylesheet
    assert "color: var(--danger)" in stylesheet
    assert "form.validation-attempted" in stylesheet
    assert 'classList.add("validation-attempted")' in javascript
    assert 'setAttribute("aria-invalid", "true")' in javascript
    assert "--status-danger: #b42318" in stylesheet
    assert "--danger: var(--status-danger)" in stylesheet
    assert 'value="Asia/Kolkata"' not in text
    assert '<option value="" selected disabled>Select a format</option>' in text
    assert 'aria-describedby="time-zone-help"' in text
    assert '<strong>Schedule</strong>' in text
    assert 'name="start_date" type="date"' in text
    assert 'name="start_time" type="time" value="09:00"' in text
    assert 'name="end_date" type="date"' in text
    assert 'name="end_time" type="time" value="17:00"' in text
    assert 'id="date-time-preview"' in text
    assert "showModal()" in javascript
    assert 'form.elements.delivery_mode.value = ""' in javascript
    assert 'intendedStatus === "active" && endsAt <= Date.now()' in javascript
    assert "Update the event dates before activating." in javascript
    assert '["Asia/Calcutta", "Asia/Kolkata"]' in javascript
    assert "zonedDateTimeToMillis" in javascript


def test_authenticated_pages_share_navigation_and_account_menu() -> None:
    pages = (
        "admin_home.html",
        "events_admin.html",
        "admin_programs.html",
        "access_admin.html",
        "admin_submissions.html",
        "admin_onboarding.html",
        "event_workspace.html",
        "speaker_content.html",
        "agenda_admin.html",
        "speaker_directory.html",
        "event_overview.html",
        "account.html",
    )
    for name in pages:
        text = (STATIC / name).read_text()
        assert "data-auth-shell" in text
        assert "/app-shell/assets/app-shell.css" in text
        assert "/app-shell/assets/app-shell.js" in text
    programs = (STATIC / "admin_programs.html").read_text()
    assert 'id="sign-in"' not in programs
    assert 'id="logout"' not in programs
    shell = (STATIC / "app_shell.js").read_text()
    assert '"Home"' in shell
    assert '"Events"' in shell
    assert '"People"' in shell
    for label in (
        '"Overview"',
        '"Call for Proposals"',
        '"Proposals"',
        '"Speakers"',
        '"Agenda"',
        '"Share"',
        '"Reviewers"',
    ):
        assert label in shell
    assert '"Account settings"' in shell
    assert '"Sign out"' in shell


def test_evaluation_shell_and_runtime_admin_table_have_keyboard_repairs() -> None:
    reviews = (STATIC / "app/index.html").read_text()
    reviews_css = (STATIC / "app/assets/reviews.css").read_text()
    submissions_js = (STATIC / "admin_submissions.js").read_text()
    assert 'class="review-skip"' in reviews and 'href="#root"' in reviews
    # The keyboard repairs live in the island stylesheet, which must be a real
    # head <link> (not injected by the bundle) so they apply before hydration.
    assert '<link rel="stylesheet" crossorigin href="/app/assets/reviews.css">' in reviews
    assert ".review-skip" in reviews_css
    assert ":focus-visible" in reviews_css
    assert "prefers-reduced-motion" in reviews_css
    assert 'heading.setAttribute("scope", "col")' in submissions_js
    assert 'skip.href = "#main"' in submissions_js


def test_core_styles_include_focus_touch_motion_and_mobile_rules() -> None:
    css = "\n".join(
        (STATIC / name).read_text()
        for name in ("product.css", "speaker.css", "admin_onboarding.css", "agenda.css")
    )
    assert "focus-visible" in css
    assert "min-height: 2.75rem" in css or "min-height:2.75rem" in css
    assert "prefers-reduced-motion" in css
    assert "max-width: 48rem" in css or "max-width:48rem" in css


@pytest.mark.parametrize(
    "name",
    ["public_cfp.js", "speaker_portal.js", "admin_onboarding.js", "agenda.js", "schedule.js"],
)
def test_core_browser_code_avoids_unsafe_html_injection(name: str) -> None:
    javascript = (STATIC / name).read_text()
    assert "innerHTML" not in javascript
    assert "document.write" not in javascript

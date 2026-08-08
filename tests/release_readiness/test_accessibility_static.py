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
    for error_id in (
        "display-name-error",
        "job-title-error",
        "company-error",
        "location-error",
        "website-error",
        "biography-error",
    ):
        assert error_id in portal
        assert (
            f'aria-describedby="{error_id}"' in portal
            or error_id in portal.split('aria-describedby="', 1)[1]
        )
    assert 'role="alert" tabindex="-1"' in portal
    assert 'aria-label="Headshot upload progress"' in portal


def test_evaluation_shell_and_runtime_admin_table_have_keyboard_repairs() -> None:
    reviews = (STATIC / "app/index.html").read_text()
    submissions_js = (STATIC / "admin_submissions.js").read_text()
    assert 'class="review-skip"' in reviews and 'href="#root"' in reviews
    assert ":focus-visible" in reviews
    assert "prefers-reduced-motion" in reviews
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

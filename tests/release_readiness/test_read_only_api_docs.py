from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


class DocsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_api_docs_are_self_contained_and_csp_compatible() -> None:
    markup = source("api_docs.html")
    parser = DocsParser()
    parser.feed(markup)

    urls = [
        value
        for _, attrs in parser.tags
        for key, value in attrs.items()
        if key in {"href", "src"} and value is not None
    ]
    assert all(value.startswith("/") or value.startswith("#") for value in urls)
    assert "/product/assets/product.css?v=72" in urls
    assert "/docs/assets/api-docs.css?v=2" in urls
    assert "/app-shell/assets/api-client.js?v=4" in urls
    assert "/docs/assets/api-docs.js?v=2" in urls
    assert "swagger" not in markup.lower()
    assert "redoc" not in markup.lower()
    assert "https://" not in markup and "http://" not in markup
    assert "<style" not in markup and "<script>" not in markup


def test_api_docs_have_accessible_read_only_structure() -> None:
    markup = source("api_docs.html")
    parser = DocsParser()
    parser.feed(markup)
    ids = {attrs.get("id") for _, attrs in parser.tags}

    assert any(tag == "html" and attrs.get("lang") == "en" for tag, attrs in parser.tags)
    assert any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in parser.tags)
    assert 'class="skip-link" href="#main"' in markup and "main" in ids
    assert markup.count("<h1") == 1
    assert 'for="operation-search"' in markup
    assert 'id="operation-search" type="search"' in markup
    assert 'role="status" aria-live="polite"' in markup
    assert 'aria-busy="true"' in markup
    assert "This surface documents requests; it never sends them." in markup
    assert "Try it out" not in markup and "Execute" not in markup
    assert 'tabindex="1"' not in markup and 'tabindex="2"' not in markup


def test_api_docs_client_can_only_read_the_checked_contract() -> None:
    javascript = source("api_docs.js")

    assert "window.SessionBuddyApi.request(\"/api/v1/openapi.json\"" in javascript
    assert '{ cache: "no-store" }' in javascript
    assert "fetch(" not in javascript
    assert "method:" not in javascript
    assert "body:" not in javascript
    assert "authorization" not in javascript.lower()
    assert "localStorage" not in javascript and "sessionStorage" not in javascript
    assert "innerHTML" not in javascript and "outerHTML" not in javascript
    assert "eval(" not in javascript and "Function(" not in javascript
    assert "XMLHttpRequest" not in javascript and "WebSocket" not in javascript


def test_api_docs_styles_cover_mobile_keyboard_and_reduced_motion() -> None:
    stylesheet = source("api_docs.css")

    assert ".docs-page :focus-visible" in stylesheet
    assert "min-height: 2.5rem" in stylesheet
    assert "@media (max-width: 54rem)" in stylesheet
    assert "@media (max-width: 36rem)" in stylesheet
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert ".operation-list::before" in stylesheet
    assert '[data-method="delete"]' in stylesheet

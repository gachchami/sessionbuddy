import re
from pathlib import Path

STATIC = Path("src/sessionbuddy/static")
REVIEW_SOURCE = Path("frontend/src/styles.css")

SHARED_TOKENS = {
    "action-primary": "#225c9e",
    "focus-ring": "#2563eb",
}


def _css(path: str) -> str:
    return (STATIC / path).read_text()


def _custom_property(css: str, name: str) -> str | None:
    match = re.search(rf"--{re.escape(name)}\s*:\s*([^;}}]+)", css)
    return match.group(1).strip().lower() if match else None


def test_source_wiring_product_surfaces_share_action_and_focus_tokens() -> None:
    stylesheets = {
        "product": _css("product.css"),
        "app shell": _css("app_shell.css"),
        "landing": _css("landing.css"),
        "review source": REVIEW_SOURCE.read_text(),
        "reviews": _css("app/assets/reviews.css"),
    }

    for surface, css in stylesheets.items():
        for token, value in SHARED_TOKENS.items():
            # Landing imports the product sheet; React's HTML loads it before
            # its bundle. Inheritance is intentional, not a missing declaration.
            declared = _custom_property(css, token)
            if declared is None:
                if surface == "landing":
                    assert '@import url("/product/assets/product.css?v=' in css
                else:
                    assert surface in {"review source", "reviews"}
                    assert (
                        "/product/assets/product.css?v=" in (STATIC / "app/index.html").read_text()
                    )
                declared = _custom_property(stylesheets["product"], token)
            assert declared == value, f"{surface} must give --{token} the shared {value} meaning"

    assert "var(--action-primary)" in stylesheets["reviews"]
    assert "var(--action-primary)" in stylesheets["app shell"]
    assert "var(--focus-ring)" in stylesheets["product"]
    assert "var(--focus-ring)" in stylesheets["reviews"]
    assert "--blue:" not in stylesheets["landing"]
    assert "--blue:" not in stylesheets["review source"]
    assert "--blue:" not in stylesheets["reviews"]
    assert "--sb-violet:" not in stylesheets["app shell"]


def test_status_surfaces_use_semantic_product_tokens() -> None:
    product = _css("product.css")
    onboarding = _css("admin_onboarding.css")
    reviews = _css("app/assets/reviews.css")

    for token in (
        "status-info-border",
        "status-info-surface",
        "status-danger",
        "status-success",
    ):
        assert _custom_property(product, token) is not None

    assert "var(--status-info-border)" in onboarding
    assert "var(--status-info-surface)" in onboarding
    assert "var(--status-danger)" in onboarding
    assert "var(--status-success)" in onboarding
    assert "var(--status-info-border)" in reviews
    assert "var(--status-info-surface)" in reviews
    assert "var(--status-danger)" in reviews


def test_dead_page_header_overrides_do_not_return() -> None:
    onboarding = _css("admin_onboarding.css")
    agenda = _css("agenda.css")

    assert ".header-layout" not in onboarding
    assert "button.compact" not in onboarding
    assert ".agenda-header" not in agenda
    assert ".skip-link" not in agenda

from pathlib import Path

STATIC = Path("src/sessionbuddy/static")


def test_organizer_speaker_pages_have_distinct_titles_and_headings() -> None:
    expected = {
        "speaker_directory.html": ("Speaker directory", "Speaker directory"),
        "admin_onboarding.html": ("Speaker onboarding", "Speaker onboarding"),
        "speaker_content.html": ("Speaker tasks and files", "Speaker tasks and files"),
        "speaker_messages.html": ("Speaker messages", "Speaker messages"),
    }

    for filename, (title, heading) in expected.items():
        markup = (STATIC / filename).read_text()
        assert f"<title>{title} · SessionBuddy</title>" in markup
        assert "<h1" in markup and f">{heading}</h1>" in markup


def test_shared_muted_utility_uses_the_product_token() -> None:
    stylesheet = (STATIC / "product.css").read_text()
    assert ".muted { color: var(--muted); }" in stylesheet


def test_onboarding_uses_product_tokens_and_complete_focus_selector() -> None:
    markup = (STATIC / "admin_onboarding.html").read_text()
    stylesheet = (STATIC / "admin_onboarding.css").read_text()
    product = (STATIC / "product.css").read_text()

    assert "/product/assets/product.css" in markup
    assert ":root" not in stylesheet
    assert "button {" not in stylesheet
    assert "focus-visible" not in stylesheet
    assert ":where(a, button, input, textarea, select, summary):focus-visible" in product

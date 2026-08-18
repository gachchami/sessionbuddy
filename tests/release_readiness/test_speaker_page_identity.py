from pathlib import Path

STATIC = Path("src/sessionbuddy/static")


def test_speaker_pages_keep_specific_document_titles_and_stable_workspace_headings() -> None:
    expected = {
        "speaker_directory.html": ("People", "People"),
        "admin_onboarding.html": ("Speaker onboarding", "Speakers"),
        "speaker_content.html": ("Speaker tasks and files", "Speakers"),
        # Messages is an event-wide organizer page reached from the main nav,
        # not one of the speaker-hub areas above.
        "speaker_messages.html": ("Event messages", "Messages"),
    }

    for filename, (title, heading) in expected.items():
        markup = (STATIC / filename).read_text()
        assert f"<title>{title} · SessionBuddy</title>" in markup
        assert "<h1" in markup and f">{heading}</h1>" in markup


def test_source_wiring_shared_muted_utility_uses_the_product_token() -> None:
    stylesheet = (STATIC / "product.css").read_text()
    assert ".muted { color: var(--muted); }" in stylesheet


def test_source_wiring_onboarding_uses_product_tokens_and_complete_focus_selector() -> None:
    markup = (STATIC / "admin_onboarding.html").read_text()
    stylesheet = (STATIC / "admin_onboarding.css").read_text()
    product = (STATIC / "product.css").read_text()

    assert "/product/assets/product.css" in markup
    assert ":root" not in stylesheet
    assert "button {" not in stylesheet
    assert "focus-visible" not in stylesheet
    assert ":where(a, button, input, textarea, select, summary):focus-visible" in product


def test_source_wiring_event_messages_has_exactly_one_home_in_navigation() -> None:
    """Messages is an event destination, not a speaker-hub area.

    It was reachable only through the Speakers hub, which is why an organizer
    finishing a decision round reported having no notification screen at all.
    Promoting it to the main nav must not leave a second copy behind.
    """
    shell = (STATIC / "app_shell.js").read_text()
    messages = (STATIC / "speaker_messages.html").read_text()

    assert '["Messages", `${prefix}/messages`, "message", [`${prefix}/messages`]],' in shell
    hub_tabs = shell.split("function speakerHubTabs(")[1].split("function ")[0]
    assert "/messages" not in hub_tabs
    assert "data-speaker-hub-tabs" not in messages
    assert 'if (location.pathname.includes("/messages")) return "Messages";' in shell

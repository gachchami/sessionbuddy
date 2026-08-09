from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_cfp_settings_summary_has_one_coherent_label_and_indicator() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")
    summary = page.split('<details id="publish-settings"', 1)[1].split(
        "</summary>", 1
    )[0]

    assert '<span class="eyebrow">Event CFP</span>' in summary
    assert '<strong id="publish-title">Proposal form settings</strong>' in summary
    assert "Configure" not in summary
    assert "disclosure-action" not in summary

    summary_rule = stylesheet.split(".action-disclosure > summary {", 1)[1].split(
        "}", 1
    )[0]
    assert "display: flex" in summary_rule
    assert "justify-content: space-between" in summary_rule
    assert '.action-disclosure > summary::after { content: "+"' in stylesheet
    assert '.action-disclosure[open] > summary::after { content: "−"' in stylesheet

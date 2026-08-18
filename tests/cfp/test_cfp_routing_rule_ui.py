from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_source_wiring_routing_rules_use_compact_addable_rows() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")

    assert "Each row is one rule." in page
    assert '>+ Add rule</button>' in page
    assert 'card.className = "routing-rule"' in script
    assert 'make("button", "−")' in script
    assert 'aria-label", `Remove routing rule ${index + 1}`' in script
    assert ".routing-rule { display: grid;" in stylesheet


def test_source_wiring_each_rule_has_one_clear_destination() -> None:
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'destinationType.name = "routing_destination_type"' in script
    assert 'textInput("routing_destination"' in script
    assert 'category: destinationType === "category" ? destination : null' in script
    assert 'track: destinationType === "track" ? destination : null' in script
    assert 'review_queue: destinationType === "review_queue" ? destination : null' in script

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_cfp_settings_use_a_compact_sectioned_editor() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")

    assert '<section id="publish-settings" class="cfp-builder workflow-stage"' in page
    assert '<nav class="cfp-section-nav"' in page
    for section in ("basics", "availability", "questions", "confirmation", "routing"):
        assert f'id="cfp-{section}"' in page
    assert "Proposal form settings" not in page
    assert ".cfp-editor-layout" in stylesheet
    assert ".cfp-editor-actions { position: sticky;" in stylesheet


def test_cfp_url_keeps_the_application_route_fixed() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert 'id="cfp-slug-prefix"' in page
    assert 'name="slug" aria-label="Public URL slug"' in page
    assert 'byId("cfp-slug-prefix").textContent = `${location.host}/cfp/`' in script
    assert 'name="slug" type="url"' not in page

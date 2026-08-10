from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sessionbuddy.cfp.models import FormPublish

ROOT = Path(__file__).parents[2]


def form_settings(**overrides: int | None) -> dict[str, object]:
    settings: dict[str, object] = {
        "slug": "future-cfp",
        "welcome_text": "Send us your proposal.",
    }
    settings.update(overrides)
    return settings


def test_cfp_opening_time_may_be_in_the_past() -> None:
    FormPublish(**form_settings(opens_at_ms=1, closes_at_ms=10_001))


def test_cfp_closing_time_must_be_strictly_after_opening_time() -> None:
    FormPublish(**form_settings(opens_at_ms=10_000, closes_at_ms=10_001))

    for closes_at_ms in (9_999, 10_000):
        with pytest.raises(ValidationError, match="after its opening time"):
            FormPublish(
                **form_settings(opens_at_ms=10_000, closes_at_ms=closes_at_ms)
            )


def test_admin_and_api_apply_all_cfp_availability_boundaries() -> None:
    router = (ROOT / "src" / "sessionbuddy" / "cfp" / "router.py").read_text()
    script = (
        ROOT / "src" / "sessionbuddy" / "static" / "admin_programs.js"
    ).read_text()

    assert "_validate_cfp_opening" not in router
    assert router.count("_validate_cfp_deadline(body.closes_at_ms") == 2
    assert "opensAt !== null && opensAt < Date.now()" not in script
    assert "opensAt !== null && opensAt >= state.eventStartsAtMs" not in script
    assert "closesAt !== null && closesAt <= opensAt" in script
    assert "closesAt !== null && closesAt >= state.eventStartsAtMs" in script
    assert 'opens.removeAttribute("min")' in script
    assert 'opens.removeAttribute("max")' in script
    assert "toLocalInput(state.eventStartsAtMs - 1)" in script

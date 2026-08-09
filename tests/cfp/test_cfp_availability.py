from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sessionbuddy.cfp.models import FormPublish
from sessionbuddy.cfp.router import _validate_cfp_opening

ROOT = Path(__file__).parents[2]


def form_settings(**overrides: int | None) -> dict[str, object]:
    settings: dict[str, object] = {
        "slug": "future-cfp",
        "welcome_text": "Send us your proposal.",
    }
    settings.update(overrides)
    return settings


def test_cfp_opening_time_accepts_now_and_rejects_before_now() -> None:
    now_ms = 10_000
    event_starts_at_ms = 20_000

    _validate_cfp_opening(None, now_ms, event_starts_at_ms)
    _validate_cfp_opening(now_ms, now_ms, event_starts_at_ms)
    _validate_cfp_opening(now_ms + 1, now_ms, event_starts_at_ms)

    with pytest.raises(HTTPException) as rejected:
        _validate_cfp_opening(now_ms - 1, now_ms, event_starts_at_ms)

    assert rejected.value.status_code == 422
    assert "cannot be in the past" in str(rejected.value.detail)


def test_cfp_opening_time_must_be_strictly_before_event_start() -> None:
    now_ms = 10_000
    event_starts_at_ms = 20_000

    _validate_cfp_opening(event_starts_at_ms - 1, now_ms, event_starts_at_ms)
    for opens_at_ms in (event_starts_at_ms, event_starts_at_ms + 1):
        with pytest.raises(HTTPException) as rejected:
            _validate_cfp_opening(opens_at_ms, now_ms, event_starts_at_ms)
        assert rejected.value.status_code == 422
        assert "must open before" in str(rejected.value.detail)


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

    assert router.count("_validate_cfp_opening(body.opens_at_ms, now,") == 2
    assert router.count("_validate_cfp_deadline(body.closes_at_ms") == 2
    assert "opensAt !== null && opensAt < Date.now()" in script
    assert "opensAt !== null && opensAt >= state.eventStartsAtMs" in script
    assert "closesAt !== null && closesAt <= opensAt" in script
    assert "closesAt !== null && closesAt >= state.eventStartsAtMs" in script
    assert "opens.min = toLocalInput(earliestOpeningMs())" in script
    assert "opens.max = state.eventStartsAtMs" in script
    assert "toLocalInput(state.eventStartsAtMs - 1)" in script

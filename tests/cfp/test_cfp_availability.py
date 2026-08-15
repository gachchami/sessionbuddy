from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from sessionbuddy.cfp.availability import availability_state, form_availability
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


def test_availability_state_closes_on_the_boundary_millisecond() -> None:
    assert availability_state(None, None, 10_000) == "open"
    assert availability_state(20_000, None, 10_000) == "scheduled"
    assert availability_state(None, 10_000, 9_999) == "open"
    # Inclusive close: the admin badge and the public form must not disagree
    # for the millisecond a strict comparison would leave open.
    assert availability_state(None, 10_000, 10_000) == "closed"
    assert availability_state(None, 10_000, 10_001) == "closed"


def test_form_availability_is_derived_from_the_same_state() -> None:
    for opens, closes, now in (
        (None, None, 5),
        (10, None, 5),
        (None, 10, 10),
        (1, 10, 5),
    ):
        result = form_availability(opens, closes, now)
        state = availability_state(opens, closes, now)
        assert result.accepting is (state == "open")
        assert result.state == state
        assert result.message.startswith("Applications")


def test_form_availability_identifies_the_boundary_that_explains_the_state() -> None:
    scheduled = form_availability(20_000, 40_000, 10_000)
    assert (scheduled.boundary_kind, scheduled.boundary_at_ms) == ("opens", 20_000)

    open_call = form_availability(None, 40_000, 10_000)
    assert (open_call.boundary_kind, open_call.boundary_at_ms) == ("closes", 40_000)

    closed = form_availability(None, 40_000, 40_000)
    assert (closed.boundary_kind, closed.boundary_at_ms) == ("closes", 40_000)

    always_open = form_availability(None, None, 10_000)
    assert (always_open.boundary_kind, always_open.boundary_at_ms) == (None, None)


def test_organizer_surfaces_render_the_api_availability_state() -> None:
    """The badge bug: two organizer surfaces each re-derived availability.

    The overview tile ignored the dates entirely and read "Open" forever; the
    CFP page compared timestamps in the browser with a strict `>`. Both must
    render `published_form.availability_state`, which the API computes once.
    """
    static = ROOT / "src" / "sessionbuddy" / "static"
    programs = (static / "admin_programs.js").read_text()
    overview = (static / "event_overview.js").read_text()
    router = (ROOT / "src" / "sessionbuddy" / "cfp" / "router.py").read_text()

    assert '"availability_state": availability.state' in router
    assert "detail=availability.message" in router
    assert '"X-CFP-Availability-Boundary-At-Ms"' in router
    assert '"X-CFP-Availability-Boundary-Kind"' in router
    for script in (programs, overview):
        assert "availability_state" in script
        assert "now < form.opens_at_ms" not in script
        assert "now > form.closes_at_ms" not in script
    assert "Boolean(cfp.published_form)" in overview  # publish nudge, not availability
    # A page left open across the deadline re-reads the server answer, and a
    # boundary further out than one timer hop is walked toward rather than
    # skipped: returning early there left the badge stale forever.
    assert "scheduleAvailabilityRefresh" in programs
    assert "Math.min(boundary - now + 1000, MAX_REFRESH_DELAY_MS)" in programs
    assert "if (boundary - now > 86_400_000) return" not in programs
    assert "scheduleAvailabilityRefresh(form);" in programs
    # A failed refresh retries instead of stranding the badge on the last answer.
    assert "window.setTimeout(refreshPublishedForm, REFRESH_RETRY_MS)" in programs
    markup = (static / "admin_programs.html").read_text()
    assert ">Published</span>" in markup
    assert ">Live</span>" not in markup
    assert "Leave blank to open immediately when you publish." in markup
    assert 'id="open-cfp-immediately"' in markup
    assert "cfpAvailabilityDetail(published)" in programs

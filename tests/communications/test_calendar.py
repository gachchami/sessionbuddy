from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from sessionbuddy.communications.calendar import CalendarInvitation, render_ics


def invitation(sequence: int = 0) -> CalendarInvitation:
    start = datetime(2026, 9, 1, 10, tzinfo=UTC)
    return CalendarInvitation(
        uid="stable-invitation@example.test",
        sequence=sequence,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        summary="AI, Safety; Now",
        description="Line 1\nLine 2",
        location="Room 1",
        organizer_email="events@example.test",
        attendee_email="speaker@example.test",
    )


def test_ics_is_rfc5545_shaped_stable_and_updates_sequence() -> None:
    generated = datetime(2026, 8, 9, tzinfo=UTC)
    first = render_ics(invitation(0), generated_at=generated)
    update = render_ics(invitation(1), generated_at=generated)
    assert first.startswith("BEGIN:VCALENDAR\r\n") and first.endswith("END:VCALENDAR\r\n")
    assert "UID:stable-invitation@example.test\r\n" in first
    assert "SEQUENCE:0\r\n" in first and "SEQUENCE:1\r\n" in update
    assert "SUMMARY:AI\\, Safety\\; Now" in first
    assert "DESCRIPTION:Line 1\\nLine 2" in first


def test_calendar_requires_valid_aware_interval() -> None:
    item = invitation()
    with pytest.raises(ValueError):
        render_ics(replace(item, ends_at=item.starts_at), generated_at=datetime.now(UTC))

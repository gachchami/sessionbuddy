"""Shared rules for when a call for papers accepts proposals.

The public form page, the speaker portal, and every organizer surface decide
whether the call is open from this one definition. Keeping one definition here
stops the surfaces from disagreeing about an open call, which a speaker would
experience as a submit button that fails, and which an organizer would
experience as an admin badge still reading "Open" after the deadline the public
page already enforced.
"""

import re
from typing import Literal
from urllib.parse import quote

AvailabilityState = Literal["scheduled", "open", "closed"]

OPEN_MESSAGE = "Applications are open."
NOT_YET_OPEN_MESSAGE = "Applications have not opened yet."
CLOSED_MESSAGE = "Applications are closed."

STATE_MESSAGES: dict[str, str] = {
    "scheduled": NOT_YET_OPEN_MESSAGE,
    "open": OPEN_MESSAGE,
    "closed": CLOSED_MESSAGE,
}


def availability_state(
    opens_at_ms: int | None, closes_at_ms: int | None, now_ms: int
) -> AvailabilityState:
    """Return the single canonical availability state for a published call.

    The closing boundary is inclusive: at exactly ``closes_at_ms`` the call is
    closed, so an organizer badge and the public form never disagree by the one
    millisecond a strict comparison would leave open.
    """
    if opens_at_ms is not None and now_ms < opens_at_ms:
        return "scheduled"
    if closes_at_ms is not None and now_ms >= closes_at_ms:
        return "closed"
    return "open"


def form_availability(
    opens_at_ms: int | None, closes_at_ms: int | None, now_ms: int
) -> tuple[bool, str]:
    """Return whether the call accepts proposals now, plus a speaker-facing reason."""
    state = availability_state(opens_at_ms, closes_at_ms, now_ms)
    return state == "open", STATE_MESSAGES[state]


def public_event_key(event_id: str) -> str:
    """Derive the short, non-guessable-free public path segment for an event."""
    return re.sub(r"[^a-z0-9]", "", event_id.casefold())[:6]


def public_form_path(event_id: str, slug: str) -> str:
    """Build the canonical public URL for a published form."""
    return f"/cfp/{public_event_key(event_id)}/{quote(slug)}"

"""Shared rules for when a call for papers accepts proposals.

The public form page and the speaker portal both decide whether to offer a
submission control, and both render the same explanation when they do not.
Keeping one definition here stops the two surfaces from disagreeing about an
open call, which a speaker would experience as a submit button that fails.
"""

import re
from urllib.parse import quote

OPEN_MESSAGE = "Applications are open."
NOT_YET_OPEN_MESSAGE = "Applications have not opened yet."
CLOSED_MESSAGE = "Applications are closed."


def form_availability(
    opens_at_ms: int | None, closes_at_ms: int | None, now_ms: int
) -> tuple[bool, str]:
    """Return whether the call accepts proposals now, plus a speaker-facing reason."""
    if opens_at_ms is not None and now_ms < opens_at_ms:
        return False, NOT_YET_OPEN_MESSAGE
    if closes_at_ms is not None and now_ms >= closes_at_ms:
        return False, CLOSED_MESSAGE
    return True, OPEN_MESSAGE


def public_event_key(event_id: str) -> str:
    """Derive the short, non-guessable-free public path segment for an event."""
    return re.sub(r"[^a-z0-9]", "", event_id.casefold())[:6]


def public_form_path(event_id: str, slug: str) -> str:
    """Build the canonical public URL for a published form."""
    return f"/cfp/{public_event_key(event_id)}/{quote(slug)}"

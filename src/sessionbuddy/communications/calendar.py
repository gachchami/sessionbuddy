from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class CalendarInvitation:
    uid: str
    sequence: int
    starts_at: datetime
    ends_at: datetime
    summary: str
    description: str
    location: str
    organizer_email: str
    attendee_email: str
    cancelled: bool = False


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("calendar instants must be timezone aware")
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    chunks: list[str] = []
    remaining = line
    while len(remaining.encode()) > 75:
        cut = 75
        while len(remaining[:cut].encode()) > 75:
            cut -= 1
        chunks.append(remaining[:cut])
        remaining = " " + remaining[cut:]
    chunks.append(remaining)
    return "\r\n".join(chunks)


def render_ics(invitation: CalendarInvitation, *, generated_at: datetime) -> str:
    if invitation.sequence < 0 or invitation.ends_at <= invitation.starts_at:
        raise ValueError("invalid calendar invitation")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Sessionbuddy//Events//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:CANCEL" if invitation.cancelled else "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{_escape(invitation.uid)}",
        f"SEQUENCE:{invitation.sequence}",
        f"DTSTAMP:{_utc(generated_at)}",
        f"DTSTART:{_utc(invitation.starts_at)}",
        f"DTEND:{_utc(invitation.ends_at)}",
        f"SUMMARY:{_escape(invitation.summary)}",
        f"DESCRIPTION:{_escape(invitation.description)}",
        f"LOCATION:{_escape(invitation.location)}",
        f"ORGANIZER:mailto:{invitation.organizer_email}",
        f"ATTENDEE;RSVP=TRUE:mailto:{invitation.attendee_email}",
        *(["STATUS:CANCELLED"] if invitation.cancelled else []),
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"

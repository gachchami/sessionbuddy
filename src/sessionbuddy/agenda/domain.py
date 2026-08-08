from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True, kw_only=True)
class AgendaSlot:
    organization_id: str
    event_id: str
    revision_id: str
    room_id: str
    track_id: str | None
    event_date: str
    event_time_zone: str
    starts_at_ms: int
    ends_at_ms: int
    speaker_ids: tuple[str, ...] = ()
    item_id: str | None = None

    def validate(self) -> None:
        if self.starts_at_ms >= self.ends_at_ms:
            raise ValueError("agenda start must precede end")
        try:
            parsed_date = datetime.strptime(self.event_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("invalid event date or time zone") from exc
        # CPython validates the local date when tzdata is present. Cloudflare's
        # Pyodide runtime does not preload tzdata, so the API supplies the date
        # derived with browser Intl while the database still pins the event's
        # server-owned IANA timezone and event bounds.
        try:
            zone = ZoneInfo(self.event_time_zone)
        except ZoneInfoNotFoundError:
            zone = None
        if zone is not None:
            local_start = datetime.fromtimestamp(self.starts_at_ms / 1000, zone).date()
            if local_start != parsed_date:
                raise ValueError("event date must match local start date")
        if len(set(self.speaker_ids)) != len(self.speaker_ids):
            raise ValueError("speaker identifiers must be unique")


@dataclass(frozen=True, slots=True)
class AgendaConflict:
    kind: str
    item_id: str

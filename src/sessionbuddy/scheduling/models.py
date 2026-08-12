from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgendaSetup(StrictModel):
    room_names: list[str] = Field(min_length=1, max_length=100)
    track_names: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("room_names", "track_names")
    @classmethod
    def validate_names(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 200 for value in cleaned):
            raise ValueError("agenda names must contain 1 to 200 characters")
        normalized = [value.casefold() for value in cleaned]
        if len(normalized) != len(set(normalized)):
            raise ValueError("agenda names must be unique")
        return cleaned


class AgendaCandidate(StrictModel):
    item_id: str | None = Field(default=None, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    start_at_ms: int = Field(ge=0)
    end_at_ms: int = Field(ge=0)
    room_id: str = Field(min_length=1, max_length=128)
    track_id: str | None = Field(default=None, max_length=128)
    version: int = Field(default=0, ge=0)
    event_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")

    @model_validator(mode="after")
    def validate_time_range(self) -> "AgendaCandidate":
        if self.end_at_ms <= self.start_at_ms:
            raise ValueError("agenda item end must be after its start")
        return self


class AgendaPublish(StrictModel):
    revision_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)


class AgendaResourceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name is required")
        return cleaned


class AgendaResourceUpdate(StrictModel):
    status: Literal["active", "archived"]
    version: int = Field(ge=1)


class EventTrackView(StrictModel):
    id: str
    name: str
    status: Literal["active", "archived"]
    version: int = Field(ge=1)


class EventTrackList(StrictModel):
    event_id: str
    data: list[EventTrackView]


class EventLabelCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("name")
    @classmethod
    def clean_label_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("label name is required")
        return cleaned

    @field_validator("color")
    @classmethod
    def normalize_color(cls, value: str) -> str:
        return value.upper()


class EventLabelUpdate(EventLabelCreate):
    status: Literal["active", "archived"]
    version: int = Field(ge=1)


class EventLabelView(StrictModel):
    id: str
    name: str
    color: str
    status: Literal["active", "archived"]
    version: int = Field(ge=1)
    can_manage: bool


class EventLabelList(StrictModel):
    event_id: str
    data: list[EventLabelView]


class SessionLabelAssignmentUpdate(StrictModel):
    label_ids: list[str] = Field(default_factory=list, max_length=20)
    version: int = Field(ge=1)

    @field_validator("label_ids")
    @classmethod
    def validate_label_ids(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 128 for value in values):
            raise ValueError("invalid label id")
        if len(values) != len(set(values)):
            raise ValueError("label ids must be unique")
        return values


class SessionLabelAssignmentView(StrictModel):
    session_id: str
    version: int = Field(ge=1)
    labels: list[EventLabelView]


class AgendaAutoSchedule(StrictModel):
    start_at_ms: int | None = Field(default=None, ge=0)
    session_minutes: int = Field(default=45, ge=10, le=240)
    gap_minutes: int = Field(default=15, ge=0, le=120)
    room_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("room_ids")
    @classmethod
    def validate_room_ids(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 128 for value in values):
            raise ValueError("invalid room id")
        if len(values) != len(set(values)):
            raise ValueError("room ids must be unique")
        return values


class ManualSessionCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    abstract: str = Field(min_length=1, max_length=5000)
    participant_ids: list[str] = Field(min_length=1, max_length=50)

    @field_validator("title", "abstract")
    @classmethod
    def clean_content(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("session content is required")
        return cleaned

    @field_validator("participant_ids")
    @classmethod
    def validate_participants(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 128 for value in values):
            raise ValueError("invalid participant id")
        if len(values) != len(set(values)):
            raise ValueError("participants must be unique")
        return values


class ManualSessionParticipantView(StrictModel):
    id: str
    display_name: str
    recipient_state: Literal["active", "invited"]


class AgendaEventView(StrictModel):
    id: str
    name: str
    time_zone: str
    starts_at_ms: int = Field(ge=0)
    ends_at_ms: int = Field(ge=0)


class AgendaRevisionView(StrictModel):
    id: str
    version: int = Field(ge=1)
    state: Literal["draft", "published", "superseded"]


class PublishedAgendaRevisionView(StrictModel):
    id: str
    revision_number: int = Field(ge=1)
    version: int = Field(ge=1)


class AgendaResourceView(StrictModel):
    id: str
    name: str
    status: Literal["active", "archived"]
    version: int = Field(ge=1)


class ScheduleLabelView(StrictModel):
    id: str
    name: str
    color: str


class AgendaScheduledItemView(StrictModel):
    id: str
    session_id: str
    source_type: Literal["accepted_proposal", "organizer_created"]
    title: str
    abstract: str
    content_status: Literal["draft", "approved"]
    content_version: int = Field(ge=1)
    label_version: int = Field(ge=1)
    start_at_ms: int = Field(ge=0)
    end_at_ms: int = Field(ge=0)
    room_id: str
    room_name: str
    track_id: str | None
    track_name: str | None
    speaker_names: str
    participants: list[ManualSessionParticipantView] = Field(default_factory=list)
    version: int = Field(ge=1)
    labels: list[EventLabelView]
    label_ids: list[str]


class AgendaUnscheduledSessionView(StrictModel):
    session_id: str
    source_type: Literal["accepted_proposal", "organizer_created"]
    title: str
    abstract: str
    content_status: Literal["draft", "approved"]
    content_version: int = Field(ge=1)
    label_version: int = Field(ge=1)
    track_id: str | None = None
    track_name: str | None = None
    speaker_names: str
    participants: list[ManualSessionParticipantView] = Field(default_factory=list)
    labels: list[EventLabelView]
    label_ids: list[str]


class AdminAgendaView(StrictModel):
    event: AgendaEventView
    revision: AgendaRevisionView
    published_revision: PublishedAgendaRevisionView | None
    items: list[AgendaScheduledItemView]
    unscheduled_sessions: list[AgendaUnscheduledSessionView]
    rooms: list[AgendaResourceView]
    tracks: list[AgendaResourceView]
    labels: list[EventLabelView]
    archived_rooms: list[AgendaResourceView]
    archived_tracks: list[AgendaResourceView]
    archived_labels: list[EventLabelView]
    can_manage_resource_lifecycle: bool
    session_participants: list[ManualSessionParticipantView]


class AgendaAutoScheduleResult(StrictModel):
    scheduled_count: int = Field(ge=0)
    remaining_count: int = Field(ge=0)


class AutoScheduledAgendaView(AdminAgendaView):
    # Idempotent replays historically return the agenda without this transient
    # summary. The route excludes unset fields to preserve that exact wire shape.
    auto_schedule: AgendaAutoScheduleResult | None = None


class AgendaConflictView(StrictModel):
    code: Literal["room", "speaker", "track"]
    message: str
    conflicting_item_id: str


class AgendaPreviewView(StrictModel):
    valid: bool
    conflicts: list[AgendaConflictView]


class AgendaItemView(StrictModel):
    id: str
    session_id: str
    title: str
    start_at_ms: int = Field(ge=0)
    end_at_ms: int = Field(ge=0)
    room_id: str
    room_name: str
    track_id: str | None
    track_name: str | None
    version: int = Field(ge=1)


class AgendaPublishView(StrictModel):
    published_revision_id: str
    published_version: int = Field(ge=1)
    draft_revision_id: str


class ScheduleEventView(StrictModel):
    id: str
    name: str
    time_zone: str


class ScheduleRevisionView(StrictModel):
    id: str
    version: int = Field(ge=1)
    revision_number: int = Field(ge=1)


class ScheduleItemView(StrictModel):
    id: str
    session_id: str
    title: str
    start_at_ms: int = Field(ge=0)
    end_at_ms: int = Field(ge=0)
    room_name: str
    track_name: str | None
    speaker_names: str
    labels: list[ScheduleLabelView]
    label_ids: list[str]


class ScheduleView(StrictModel):
    event: ScheduleEventView
    revision: ScheduleRevisionView
    items: list[ScheduleItemView]


class PublicScheduleEventView(ScheduleEventView):
    accent_color: str | None
    logo_url: str | None
    cover_image_url: str | None
    website_url: str | None


class PublicScheduleItemView(ScheduleItemView):
    description: str
    format_name: str
    speaker_details: str


class PublicScheduleView(StrictModel):
    event: PublicScheduleEventView
    revision: ScheduleRevisionView | None
    items: list[PublicScheduleItemView]

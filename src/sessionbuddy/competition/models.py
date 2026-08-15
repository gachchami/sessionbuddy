from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


def _absolute_web_link(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError("link must use an absolute HTTP or HTTPS URL")
    return value


AbsoluteWebLink = Annotated[str, Field(max_length=2000), AfterValidator(_absolute_web_link)]


class ResourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    summary: str = Field(default="", max_length=500)
    body_text: str = Field(default="", max_length=20_000)
    embed_url: str | None = Field(default=None, max_length=2000)
    status: Literal["draft", "published"] = "published"
    sort_order: int = Field(default=0, ge=-1000, le=1000)

    @field_validator("embed_url")
    @classmethod
    def validate_embed_url(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        parsed = urlparse(value)
        allowed = {
            "www.youtube.com",
            "youtube.com",
            "www.youtube-nocookie.com",
            "player.vimeo.com",
            "docs.google.com",
            "drive.google.com",
            "calendar.google.com",
        }
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in allowed
            or parsed.username
            or parsed.password
        ):
            raise ValueError("embed URL must use an approved HTTPS provider")
        return value


class ResourceView(ResourceCreate):
    id: str
    event_id: str
    version: int
    updated_at_ms: int


class ResourceList(BaseModel):
    data: list[ResourceView]


class TaskFormField(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=200)
    type: Literal["text", "textarea", "url", "checkbox", "select"]
    required: bool = False
    choices: tuple[str, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def validate_choices(self) -> "TaskFormField":
        if self.type == "select" and len(self.choices) < 2:
            raise ValueError("select tasks require at least two choices")
        if self.type != "select" and self.choices:
            raise ValueError("only select tasks accept choices")
        if any(not choice or len(choice) > 200 for choice in self.choices):
            raise ValueError("task choices must contain 1 to 200 characters")
        if len(self.choices) != len(set(self.choices)):
            raise ValueError("task choices must be unique")
        return self


class SpeakerTarget(BaseModel):
    event_speaker_id: str
    person_id: str | None
    user_id: str | None
    email: str
    display_name: str
    job_title: str
    company: str
    biography: str
    biography_source: Literal["account", "organization"] = "account"
    biography_override: str | None = None
    location: str
    links: list[str]
    version: int
    participation_version: int = 1
    lifecycle_status: Literal["onboarding", "complete", "withdrawn"] | None = None
    selection_status: Literal["invited", "submitted", "accepted", "rejected"]
    confirmation_status: Literal["invited", "pending", "confirmed", "declined"]
    proposal_title: str


class SpeakerTargetList(BaseModel):
    data: list[SpeakerTarget]


class OrganizerSpeakerNote(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(default="", max_length=5000)


class OrganizerSpeakerNotes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[OrganizerSpeakerNote] = Field(default_factory=list, max_length=25)
    version: int = Field(ge=1)


class OrganizationSpeakerParticipation(BaseModel):
    event_id: str
    event_name: str
    event_speaker_id: str
    selection_status: Literal["invited", "submitted", "accepted", "rejected"]
    confirmation_status: Literal["invited", "pending", "confirmed", "declined"]
    proposal_title: str


class OrganizationPersonEventAssociation(BaseModel):
    event_id: str
    event_name: str
    role: Literal["Reviewer", "Speaker"]
    status: str


class OrganizationSpeaker(BaseModel):
    person_id: str
    user_id: str | None
    public_profile_enabled: bool = False
    email: str
    display_name: str
    job_title: str
    company: str
    biography: str
    biography_source: Literal["account", "organization"] = "account"
    biography_override: str | None = None
    location: str
    links: list[str]
    version: int
    organization_roles: list[Literal["Organizer", "Reviewer", "Speaker"]] = Field(
        default_factory=list
    )
    event_associations: list[OrganizationPersonEventAssociation] = Field(default_factory=list)
    participations: list[OrganizationSpeakerParticipation]


class OrganizationSpeakerList(BaseModel):
    organization_id: str
    data: list[OrganizationSpeaker]


class SpeakerProfilePageView(OrganizationSpeaker):
    can_edit: bool


class AdminSpeakerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    display_name: str = Field(min_length=1, max_length=200)
    job_title: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    biography_override: str | None = Field(
        max_length=5000,
        validation_alias=AliasChoices("biography_override", "biography"),
    )
    location: str = Field(default="", max_length=300)
    links: list[AbsoluteWebLink] = Field(default_factory=list, max_length=10)
    version: int = Field(ge=1)

    @field_validator("links")
    @classmethod
    def validate_links(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("links must be unique")
        return values


class AdminEventSpeakerUpdate(AdminSpeakerUpdate):
    participation_version: int = Field(ge=1)
    confirmation_status: Literal["pending", "confirmed", "declined"] | None = None


class EventSpeakerRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participation_version: int = Field(ge=1)


class EventSpeakerRestoreView(BaseModel):
    status: Literal["onboarding", "complete"]
    participation_version: int
    reactivated_session_count: int


class SessionContentVersionView(BaseModel):
    version: int
    title: str
    abstract: str
    content_status: Literal["draft", "approved"]
    changed_by: str
    created_at_ms: int


class AdminSessionContentView(BaseModel):
    accepted_session_id: str
    title: str
    abstract: str
    content_status: Literal["draft", "approved"]
    version: int
    history: list[SessionContentVersionView]


class AdminSessionContentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    abstract: str = Field(min_length=1, max_length=5000)
    content_status: Literal["draft", "approved"]
    version: int = Field(ge=1)


class AdminSessionContentRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history_version: int = Field(ge=1)
    current_version: int = Field(ge=1)


class SpeakerTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_speaker_id: str = Field(min_length=1, max_length=100)
    submission_id: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    help_text: str = Field(default="", max_length=2000)
    due_at_ms: int | None = Field(default=None, ge=0)
    task_type: Literal["custom", "headshot", "slides", "supporting_document"] = "custom"
    upload_enabled: bool = False
    allowed_content_types: tuple[str, ...] = Field(default=(), max_length=12)
    max_file_bytes: int | None = Field(default=None, ge=1, le=50 * 1024 * 1024)
    fields: tuple[TaskFormField, ...] = Field(default=(), max_length=40)

    @model_validator(mode="after")
    def validate_fields(self) -> "SpeakerTaskCreate":
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("task form field keys must be unique")
        file_task = self.task_type in {"headshot", "slides", "supporting_document"}
        if file_task != self.upload_enabled:
            raise ValueError("file request tasks must enable uploads")
        if file_task and (not self.allowed_content_types or self.max_file_bytes is None):
            raise ValueError("file request tasks require upload constraints")
        if not file_task and (self.allowed_content_types or self.max_file_bytes is not None):
            raise ValueError("custom response tasks cannot define upload constraints")
        if file_task and self.fields:
            raise ValueError("file request tasks cannot define response fields")
        if any(
            not value
            or len(value) > 150
            or "/" not in value
            or value.lower() != value
            for value in self.allowed_content_types
        ):
            raise ValueError("allowed content types must be lowercase MIME types")
        return self


class AdminSpeakerTaskView(BaseModel):
    id: str
    owner_type: Literal["event_speaker", "invitation"]
    event_speaker_id: str | None = None
    invitation_id: str | None = None
    task_type: Literal["custom", "headshot", "slides", "supporting_document"]
    title: str
    state: Literal["open", "completed", "waived"]
    due_at_ms: int | None


class IntegrationTokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(default="Accelevents", min_length=1, max_length=120)


class IntegrationTokenView(BaseModel):
    id: str
    event_id: str
    label: str
    token: str
    created_at_ms: int


class PublicSpeaker(BaseModel):
    id: str
    display_name: str
    job_title: str
    company: str
    biography: str
    location: str
    links: list[str]
    headshot_url: str | None
    sessions: list[dict[str, str | int]]


class PublicSpeakerGallery(BaseModel):
    event: dict[str, str | None]
    data: list[PublicSpeaker]


class PublicEventSummary(BaseModel):
    id: str
    name: str
    starts_at_ms: int
    ends_at_ms: int
    time_zone: str
    location: str
    delivery_mode: Literal["in_person", "virtual", "hybrid"]
    cfp_slug: str | None
    cfp_state: Literal["scheduled", "open", "closed"] | None = None
    cfp_boundary_at_ms: int | None = None
    cfp_boundary_kind: Literal["opens", "closes"] | None = None
    schedule_published: bool
    speaker_count: int


class PublicEventList(BaseModel):
    data: list[PublicEventSummary]


class PublicCallSummary(BaseModel):
    id: str
    name: str
    starts_at_ms: int
    ends_at_ms: int
    time_zone: str
    location: str
    delivery_mode: Literal["in_person", "virtual", "hybrid"]
    form_id: str
    cfp_slug: str
    cfp_state: Literal["scheduled", "open", "closed"]
    cfp_boundary_at_ms: int | None = None
    cfp_boundary_kind: Literal["opens", "closes"] | None = None


class PublicCallList(BaseModel):
    data: list[PublicCallSummary]

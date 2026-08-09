from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    display_name: str
    selection_status: Literal["submitted", "accepted", "rejected"]
    proposal_title: str


class SpeakerTargetList(BaseModel):
    data: list[SpeakerTarget]


class SpeakerTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    event_speaker_id: str = Field(min_length=1, max_length=100)
    submission_id: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    help_text: str = Field(default="", max_length=2000)
    due_at_ms: int | None = Field(default=None, ge=0)
    fields: tuple[TaskFormField, ...] = Field(default=(), max_length=40)

    @model_validator(mode="after")
    def validate_fields(self) -> "SpeakerTaskCreate":
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("task form field keys must be unique")
        return self


class AdminSpeakerTaskView(BaseModel):
    id: str
    event_speaker_id: str
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
    sessions: list[dict[str, str]]


class PublicSpeakerGallery(BaseModel):
    event: dict[str, str | None]
    data: list[PublicSpeaker]

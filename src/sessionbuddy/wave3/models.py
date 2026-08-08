from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SpeakerProfileView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    job_title: str
    company: str
    biography: str
    location: str
    links: list[str]
    version: int


class SpeakerProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=200)
    job_title: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    biography: str = Field(min_length=1, max_length=5000)
    location: str = Field(default="", max_length=200)
    links: list[str] = Field(default_factory=list, max_length=10)
    version: int = Field(ge=1)

    @field_validator("links")
    @classmethod
    def validate_links(cls, values: list[str]) -> list[str]:
        for value in values:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("links must use an absolute HTTP or HTTPS URL")
        return values


class SpeakerTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task_type: str
    title: str
    help_text: str
    destination_path: str
    state: Literal["open", "completed", "waived"]
    due_at_ms: int | None
    completed_at_ms: int | None


class SpeakerSubmissionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    proposal_title: str
    status: str


class SpeakerEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    starts_at_ms: int
    ends_at_ms: int
    time_zone: str


class SpeakerPortalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: SpeakerEventView
    profile: SpeakerProfileView
    tasks: list[SpeakerTaskView]
    submissions: list[SpeakerSubmissionView]
    completed_tasks: int
    total_tasks: int


class DemoSpeakerSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[True] = True
    csrf_token: str


class OnboardingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    complete: int
    incomplete: int
    overdue: int
    due_soon: int
    submitted: int
    accepted: int
    rejected: int
    evaluations_finalized: int
    evaluations_total: int


class OnboardingRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_speaker_id: str
    display_name: str
    proposal_title: str
    task_id: str
    task_type: str
    task_title: str
    state: Literal["open", "completed", "overdue", "due_soon", "waived"]
    due_at_ms: int | None
    last_activity_at_ms: int


class OnboardingDashboardView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: str
    time_zone: str
    generated_at_ms: int
    summary: OnboardingSummary
    data: list[OnboardingRow]
    next_cursor: str | None


class UploadAuthorizationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["headshot", "slides", "supporting_document"]
    submission_id: str | None = Field(default=None, max_length=100)
    task_id: str | None = Field(default=None, max_length=100)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    byte_size: int = Field(gt=0, le=50 * 1024 * 1024)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("filename must not contain a path")
        return value


class UploadAuthorizationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_at_ms: int


class UploadCompletionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str
    state: Literal["uploaded", "scanning", "clean", "rejected"]


class SpeakerAssetView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["headshot", "slides", "supporting_document"]
    filename: str
    content_type: str
    byte_size: int
    state: Literal["clean"]
    generation: int


class SpeakerAssetList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[SpeakerAssetView]

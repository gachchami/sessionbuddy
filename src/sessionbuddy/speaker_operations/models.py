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
        if len(values) != len(set(values)):
            raise ValueError("links must be unique")
        for value in values:
            parsed = urlparse(value)
            if (
                len(value) > 2000
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
            ):
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
    form_fields: list[dict[str, object]] = Field(default_factory=list)
    response: dict[str, object] = Field(default_factory=dict)
    version: int = 1


class SpeakerTaskResponseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str | bool | list[str] | None] = Field(max_length=40)
    version: int = Field(ge=1)


class SpeakerTaskResponseView(BaseModel):
    id: str
    state: Literal["completed"] = "completed"
    response: dict[str, object]
    version: int


class SpeakerSubmissionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    speaker_name: str
    speaker_email: str
    proposal_title: str
    proposal_abstract: str
    answers: dict[str, object]
    status: str
    form_slug: str
    version: int
    editable: bool = False


class SpeakerEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    starts_at_ms: int
    ends_at_ms: int
    time_zone: str


class SpeakerOpenCallView(BaseModel):
    """The event's published call for papers, as the speaker portal needs it.

    This is a summary for deciding whether to offer a submit control and what
    to say when it is unavailable. The portal fetches the full field schema
    from the public form endpoint before composing a proposal.
    """

    model_config = ConfigDict(extra="forbid")

    form_id: str
    slug: str
    accepting_submissions: bool
    availability_message: str
    opens_at_ms: int | None = None
    closes_at_ms: int | None = None
    submission_limit: int | None = None
    submitted_count: int = 0
    remaining_submissions: int | None = None


class SpeakerNotificationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    subject: str
    delivered_at_ms: int
    body_text: str
    links: list[str] = Field(default_factory=list)


class SpeakerPortalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: SpeakerEventView
    events: list[SpeakerEventView]
    event_speaker_id: str
    public_profile_url: str | None = None
    profile: SpeakerProfileView
    tasks: list[SpeakerTaskView]
    submissions: list[SpeakerSubmissionView]
    notifications: list[SpeakerNotificationView] = Field(default_factory=list)
    open_call: SpeakerOpenCallView | None = None
    completed_tasks: int
    total_tasks: int


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
    version_comment: str = Field(min_length=1, max_length=1000)

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
    submission_id: str | None = None
    filename: str
    content_type: str
    byte_size: int
    state: Literal["clean"]
    generation: int
    uploaded_at_ms: int
    version_count: int = 1
    version_comment: str
    versions: list["SpeakerAssetVersionView"] = Field(default_factory=list)


class SpeakerAssetVersionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    generation: int
    filename: str
    content_type: str
    byte_size: int
    state: Literal["current", "superseded"]
    uploaded_at_ms: int
    version_comment: str


class SpeakerAssetList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[SpeakerAssetView]


class AdminSpeakerAssetView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    event_speaker_id: str
    speaker_name: str
    kind: Literal["headshot", "slides", "supporting_document"]
    filename: str
    content_type: str
    byte_size: int
    generation: int
    version_count: int
    uploaded_at_ms: int
    version_comment: str
    versions: list[SpeakerAssetVersionView] = Field(default_factory=list)


class AdminSpeakerAssetList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[AdminSpeakerAssetView]


class AssetDownloadGrantView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    expires_at_ms: int


class AssetDownloadToken(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token: str = Field(min_length=32, max_length=255)

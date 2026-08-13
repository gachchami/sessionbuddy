from email.headerregistry import Address
from html import escape
from html.parser import HTMLParser
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from .availability import AvailabilityState


class _RichTextSanitizer(HTMLParser):
    allowed = {"p", "br", "strong", "em", "ul", "ol", "li", "a", "h2", "h3", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "div":
            self.parts.append("<p>")
            return
        if tag not in self.allowed:
            return
        if tag == "a":
            href = next((value for name, value in attrs if name == "href"), None)
            if href and href.startswith(("https://", "http://", "mailto:")):
                self.parts.append(f'<a href="{escape(href, quote=True)}" rel="noopener">')
                return
            self.parts.append("<a>")
            return
        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag == "div":
            self.parts.append("</p>")
            return
        if tag in self.allowed and tag != "br":
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(escape(data))


def _sanitize_rich_text(value: str | None) -> str | None:
    if not value:
        return None
    parser = _RichTextSanitizer()
    parser.feed(value)
    cleaned = "".join(parser.parts).strip()
    return cleaned or None


class FormFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal[
        "text",
        "textarea",
        "email",
        "url",
        "phone",
        "select",
        "multiselect",
        "checkbox",
        "file",
        "image",
    ]
    label: str = Field(min_length=1, max_length=200)
    help_text: str = Field(default="", max_length=1000)
    placeholder: str = Field(default="", max_length=300)
    required: bool = False
    choices: tuple[str, ...] = Field(default=(), max_length=100)
    blind_visible: bool = False

    @model_validator(mode="after")
    def validate_choices(self) -> "FormFieldDefinition":
        minimum_choices = 1 if self.key == "track" else 2
        if self.type in {"select", "multiselect"} and len(self.choices) < minimum_choices:
            raise ValueError("choice fields require at least two choices")
        if self.type not in {"select", "multiselect"} and self.choices:
            raise ValueError("only select and multiselect fields accept choices")
        if any(not choice or len(choice) > 200 for choice in self.choices):
            raise ValueError("field choices must contain 1 to 200 characters")
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("field choices must be unique")
        return self


class FormCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_key: str = Field(min_length=1, max_length=80)
    operator: Literal["equals", "not_equals"]
    value: str = Field(max_length=500)
    target_key: str = Field(min_length=1, max_length=80)


class FormRoutingRule(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_key: str = Field(min_length=1, max_length=80)
    operator: Literal["equals", "not_equals", "contains"]
    value: str = Field(max_length=500)
    category: str | None = Field(default=None, min_length=1, max_length=120)
    track: str | None = Field(default=None, min_length=1, max_length=120)
    review_queue: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def validate_destination(self) -> "FormRoutingRule":
        if not any((self.category, self.track, self.review_queue)):
            raise ValueError("routing rules require a category, track, or review queue")
        return self


DEFAULT_FORM_FIELDS = (
    FormFieldDefinition(key="speaker_name", type="text", label="Speaker name", required=True),
    FormFieldDefinition(key="speaker_email", type="email", label="Email", required=True),
    FormFieldDefinition(key="proposal_title", type="text", label="Proposal title", required=True),
    FormFieldDefinition(
        key="proposal_abstract", type="textarea", label="Proposal abstract", required=True
    ),
)


def _validate_email_address(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("email must be one valid address")
    try:
        address = Address(addr_spec=value)
    except (IndexError, ValueError) as exc:
        raise ValueError("email must be one valid address") from exc
    if not address.username or not address.domain or "." not in address.domain:
        raise ValueError("email must be one valid address")
    return value


ContributorRole = Literal["co_speaker", "co_author", "moderator", "panelist", "other"]
CONTRIBUTOR_ROLE_LABELS: dict[ContributorRole, str] = {
    "co_speaker": "Co-speaker",
    "co_author": "Co-author",
    "moderator": "Moderator",
    "panelist": "Panelist",
    "other": "Other participant",
}


def contributor_role_label(role: str) -> str:
    return CONTRIBUTOR_ROLE_LABELS.get(role, "Participant")  # type: ignore[arg-type]


class ContributorRoleOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: ContributorRole
    label: str = Field(min_length=1, max_length=80)


DEFAULT_CONTRIBUTOR_ROLE_OPTIONS = tuple(
    ContributorRoleOption(value=value, label=label)
    for value, label in CONTRIBUTOR_ROLE_LABELS.items()
)


class ImportantDate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=120)
    at_ms: int = Field(ge=0)


class FormSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    slug: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    welcome_text: str = Field(min_length=1, max_length=1000)
    description_html: str | None = Field(default=None, max_length=10_000)
    important_dates: tuple[ImportantDate, ...] = Field(default=(), max_length=12)
    fields: tuple[FormFieldDefinition, ...] = Field(
        default=DEFAULT_FORM_FIELDS, min_length=1, max_length=100
    )
    conditions: tuple[FormCondition, ...] = Field(default=(), max_length=200)
    routing_rules: tuple[FormRoutingRule, ...] = Field(default=(), max_length=200)
    opens_at_ms: int | None = Field(default=None, ge=0)
    closes_at_ms: int | None = Field(default=None, ge=0)
    submission_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    co_speaker_limit: int = Field(default=1, ge=0, le=10)
    success_title: str = Field(default="Proposal received", min_length=1, max_length=200)
    success_message: str = Field(
        default="We sent a confirmation to your email address.", min_length=1, max_length=2000
    )
    redirect_to_portal: bool = True

    @field_validator("description_html", mode="before")
    @classmethod
    def sanitize_description(cls, value: object) -> str | None:
        return _sanitize_rich_text(str(value)) if value is not None else None

    @model_validator(mode="after")
    def validate_schema(self) -> "FormSettings":
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("field keys must be unique")
        required_core = {
            "speaker_name": "text",
            "speaker_email": "email",
            "proposal_title": "text",
            "proposal_abstract": "textarea",
        }
        configured = {field.key: field for field in self.fields}
        if not required_core.keys() <= configured.keys():
            raise ValueError("published forms require speaker identity and proposal fields")
        if any(
            not configured[key].required or configured[key].type != field_type
            for key, field_type in required_core.items()
        ):
            raise ValueError(
                "speaker identity and proposal fields must retain their required types"
            )
        for condition in self.conditions:
            if condition.source_key not in keys or condition.target_key not in keys:
                raise ValueError("conditions must reference existing fields")
            if condition.source_key == condition.target_key:
                raise ValueError("conditions cannot target their source")
            if keys.index(condition.source_key) >= keys.index(condition.target_key):
                raise ValueError("conditions must reference an earlier field")
            if condition.target_key in required_core:
                raise ValueError("speaker identity and proposal fields cannot be conditional")
            source = configured[condition.source_key]
            if source.choices and condition.value not in source.choices:
                raise ValueError("condition values must match a configured source choice")
        graph: dict[str, set[str]] = {key: set() for key in keys}
        for condition in self.conditions:
            graph[condition.source_key].add(condition.target_key)

        def cyclic(key: str, visiting: set[str], visited: set[str]) -> bool:
            if key in visiting:
                return True
            if key in visited:
                return False
            visiting.add(key)
            if any(cyclic(target, visiting, visited) for target in graph[key]):
                return True
            visiting.remove(key)
            visited.add(key)
            return False

        visited: set[str] = set()
        if any(cyclic(key, set(), visited) for key in keys):
            raise ValueError("field conditions cannot contain cycles")
        for rule in self.routing_rules:
            if rule.source_key not in keys:
                raise ValueError("routing rules must reference an existing field")
        for upload_type in ("file", "image"):
            if sum(field.type == upload_type for field in self.fields) > 1:
                raise ValueError(f"published forms support one {upload_type} field")
        if (
            self.opens_at_ms is not None
            and self.closes_at_ms is not None
            and self.closes_at_ms <= self.opens_at_ms
        ):
            raise ValueError("form closing time must be after its opening time")
        return self


class FormPublish(FormSettings):
    confirmation_subject: str = Field(
        default="We received your proposal", min_length=1, max_length=200
    )
    confirmation_body: str = Field(
        default="Thank you for submitting. Your proposal is now ready for review.",
        min_length=1,
        max_length=4000,
    )


class FormUpdate(FormPublish):
    version: int = Field(ge=1)


class PublishedFormView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    event_id: str
    event_name: str = "Event"
    event_starts_at_ms: int | None = None
    event_ends_at_ms: int | None = None
    event_time_zone: str | None = None
    event_location: str | None = None
    event_delivery_mode: Literal["in_person", "virtual", "hybrid"] | None = None
    event_website_url: str | None = None
    accent_color: str = "#3159d9"
    logo_url: str | None = None
    cover_image_url: str | None = None
    version: int
    slug: str
    welcome_text: str
    description_html: str | None = None
    important_dates: tuple[ImportantDate, ...] = ()
    fields: tuple[FormFieldDefinition, ...]
    conditions: tuple[FormCondition, ...] = ()
    routing_rules: tuple[FormRoutingRule, ...] = ()
    opens_at_ms: int | None = None
    closes_at_ms: int | None = None
    submission_limit: int | None = None
    co_speaker_limit: int = 1
    participant_roles: tuple[ContributorRoleOption, ...] = DEFAULT_CONTRIBUTOR_ROLE_OPTIONS
    submissions_received: int = 0
    accepting_submissions: bool = True
    # The one availability answer every surface renders. Organizer badges must
    # never re-derive this from the timestamps: that is how an admin badge came
    # to read "open" while the public form the same record drives was closed.
    availability_state: AvailabilityState = "open"
    availability_message: str = "Applications are open."
    success_title: str = "Proposal received"
    success_message: str = "We sent a confirmation to your email address."
    redirect_to_portal: bool = True


class AdminPublishedFormView(PublishedFormView):
    confirmation_subject: str = Field(min_length=1, max_length=200)
    confirmation_body: str = Field(min_length=1, max_length=4000)


class CfpWorkspaceView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    event_id: str
    event_name: str
    event_starts_at_ms: int
    published_form: AdminPublishedFormView | None = None


class CoSpeakerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    role: ContributorRole = "co_speaker"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validate_email_address(value)


class CoSpeakerView(CoSpeakerInput):
    id: str
    invitation_status: Literal["pending", "accepted", "declined", "removed"]
    expires_at_ms: int | None = None

    @computed_field
    @property
    def role_label(self) -> str:
        return contributor_role_label(self.role)


class CoSpeakerInvitationView(CoSpeakerView):
    submission_id: str
    proposal_title: str
    event_name: str


class CoSpeakerInvitationCreated(BaseModel):
    co_speaker: CoSpeakerView
    invitation_url: str | None = None


class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    speaker_name: str = Field(min_length=1, max_length=200)
    speaker_email: str = Field(min_length=3, max_length=320)
    proposal_title: str = Field(min_length=1, max_length=200)
    proposal_abstract: str = Field(min_length=1, max_length=5000)
    answers: dict[str, str | list[str] | bool | int | float | None] = Field(
        default_factory=dict, max_length=100
    )
    co_speakers: list[CoSpeakerInput] = Field(default_factory=list, max_length=10)

    @field_validator("speaker_email")
    @classmethod
    def validate_speaker_email(cls, value: str) -> str:
        return _validate_email_address(value)

    @model_validator(mode="after")
    def validate_co_speakers(self):
        emails = [item.email.casefold() for item in self.co_speakers]
        if len(emails) != len(set(emails)) or self.speaker_email.casefold() in emails:
            raise ValueError("speaker emails must be unique")
        return self


class SubmissionUpdate(SubmissionCreate):
    version: int = Field(ge=1)


class SubmissionView(SubmissionCreate):
    co_speakers: list[CoSpeakerView] = Field(default_factory=list, max_length=10)
    answer_labels: dict[str, str] = Field(default_factory=dict, max_length=100)
    id: str
    status: Literal["submitted", "withdrawn", "accepted", "rejected"]
    submitted_at_ms: int
    version: int = 1
    routed_category: str | None = None
    routed_track: str | None = None
    routed_review_queue: str | None = None
    evaluation_round_id: str | None = None
    evaluation_round_name: str | None = None


class PrivateSubmissionView(SubmissionView):
    editable: bool


class SubmissionList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    event_id: str
    data: list[SubmissionView]
    total: int = 0
    next_cursor: str | None = None


class OwnedSubmissionList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[PrivateSubmissionView]


class SubmissionTitleMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    proposal_title: str
    submitted_at_ms: int
    status: Literal["submitted", "withdrawn", "accepted", "rejected"]


class SubmissionDraftUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str | list[str] | bool | int | float | None] = Field(max_length=100)
    version: int = Field(default=0, ge=0)


class SubmissionDraftView(BaseModel):
    id: str
    form_id: str
    answers: dict[str, str | list[str] | bool | int | float | None]
    version: int
    updated_at_ms: int


class SpeakerProposalDraftSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    form_id: str
    event_id: str
    event_name: str
    form_slug: str
    proposal_title: str
    updated_at_ms: int
    edit_path: str


class SpeakerProposalDraftList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[SpeakerProposalDraftSummary]


class StagedUploadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["headshot", "supporting_document"]
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    byte_size: int = Field(gt=0, le=20 * 1024 * 1024)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("filename must not contain a path")
        return value


class StagedUploadAuthorizationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staged_id: str
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_at_ms: int


class StagedUploadCompletionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staged_id: str
    state: Literal["pending_upload", "uploaded", "scanning", "staged", "rejected", "claimed"]

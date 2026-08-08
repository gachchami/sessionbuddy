from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="after")
    def validate_choices(self) -> "FormFieldDefinition":
        if self.type in {"select", "multiselect"} and len(self.choices) < 2:
            raise ValueError("choice fields require at least two choices")
        if self.type not in {"select", "multiselect"} and self.choices:
            raise ValueError("only select and multiselect fields accept choices")
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


class ProgramCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    organization_id: str = Field(min_length=36, max_length=36)
    event_id: str = Field(min_length=36, max_length=36)
    name: str = Field(min_length=1, max_length=200)


class ProgramView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    organization_id: str
    event_id: str
    name: str
    status: Literal["draft", "open", "closed", "archived"]


class FormPublish(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    slug: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    welcome_text: str = Field(min_length=1, max_length=1000)
    fields: tuple[FormFieldDefinition, ...] = Field(
        default=DEFAULT_FORM_FIELDS, min_length=1, max_length=100
    )
    conditions: tuple[FormCondition, ...] = Field(default=(), max_length=200)
    routing_rules: tuple[FormRoutingRule, ...] = Field(default=(), max_length=200)
    opens_at_ms: int | None = Field(default=None, ge=0)
    closes_at_ms: int | None = Field(default=None, ge=0)
    submission_limit: int | None = Field(default=None, ge=1, le=1_000_000)
    success_title: str = Field(default="Proposal received", min_length=1, max_length=200)
    success_message: str = Field(
        default="We sent a confirmation to your email address.", min_length=1, max_length=2000
    )
    redirect_to_portal: bool = True
    confirmation_subject: str = Field(
        default="We received your proposal", min_length=1, max_length=200
    )
    confirmation_body: str = Field(
        default="Thank you for submitting. Your proposal is now ready for review.",
        min_length=1,
        max_length=4000,
    )

    @model_validator(mode="after")
    def validate_schema(self) -> "FormPublish":
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("field keys must be unique")
        required_core = {"speaker_name", "speaker_email", "proposal_title", "proposal_abstract"}
        if not required_core <= set(keys):
            raise ValueError("published forms require speaker identity and proposal fields")
        for condition in self.conditions:
            if condition.source_key not in keys or condition.target_key not in keys:
                raise ValueError("conditions must reference existing fields")
            if condition.source_key == condition.target_key:
                raise ValueError("conditions cannot target their source")
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


class PublishedFormView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    program_id: str
    event_id: str
    event_name: str = "Event"
    accent_color: str = "#3159d9"
    logo_url: str | None = None
    version: int
    slug: str
    welcome_text: str
    fields: tuple[FormFieldDefinition, ...]
    conditions: tuple[FormCondition, ...] = ()
    routing_rules: tuple[FormRoutingRule, ...] = ()
    opens_at_ms: int | None = None
    closes_at_ms: int | None = None
    submission_limit: int | None = None
    submissions_received: int = 0
    accepting_submissions: bool = True
    availability_message: str = "Applications are open."
    success_title: str = "Proposal received"
    success_message: str = "We sent a confirmation to your email address."
    redirect_to_portal: bool = True


class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    speaker_name: str = Field(min_length=1, max_length=200)
    speaker_email: str = Field(default="", max_length=320)
    proposal_title: str = Field(min_length=1, max_length=200)
    proposal_abstract: str = Field(min_length=1, max_length=5000)
    answers: dict[str, str | list[str] | bool | int | float | None] = Field(
        default_factory=dict, max_length=100
    )


class SubmissionView(SubmissionCreate):
    id: str
    program_id: str
    status: Literal["submitted", "withdrawn"]
    submitted_at_ms: int
    routed_category: str | None = None
    routed_track: str | None = None
    routed_review_queue: str | None = None


class SubmissionList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[SubmissionView]


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

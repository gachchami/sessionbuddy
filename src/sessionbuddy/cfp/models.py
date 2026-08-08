from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FormFieldDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    type: Literal["text", "textarea", "email", "url", "select"]
    label: str = Field(min_length=1, max_length=200)
    help_text: str = Field(default="", max_length=1000)
    required: bool = False
    choices: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_choices(self) -> "FormFieldDefinition":
        if self.type == "select" and len(self.choices) < 2:
            raise ValueError("select fields require at least two choices")
        if self.type != "select" and self.choices:
            raise ValueError("only select fields accept choices")
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("field choices must be unique")
        return self


class FormCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_key: str = Field(min_length=1, max_length=80)
    operator: Literal["equals", "not_equals"]
    value: str = Field(max_length=500)
    target_key: str = Field(min_length=1, max_length=80)


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
        return self


class PublishedFormView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    program_id: str
    version: int
    slug: str
    welcome_text: str
    fields: tuple[FormFieldDefinition, ...]
    conditions: tuple[FormCondition, ...] = ()


class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    speaker_name: str = Field(min_length=1, max_length=200)
    speaker_email: str = Field(default="", max_length=320)
    proposal_title: str = Field(min_length=1, max_length=200)
    proposal_abstract: str = Field(min_length=1, max_length=5000)
    answers: dict[str, str | list[str] | bool | int | float | None] = Field(default_factory=dict)


class SubmissionView(SubmissionCreate):
    id: str
    program_id: str
    status: Literal["submitted", "withdrawn"]
    submitted_at_ms: int


class SubmissionList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[SubmissionView]


class SubmissionDraftUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, str | list[str] | bool | int | float | None]
    version: int = Field(default=0, ge=0)


class SubmissionDraftView(BaseModel):
    id: str
    form_id: str
    answers: dict[str, str | list[str] | bool | int | float | None]
    version: int
    updated_at_ms: int


class DemoContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    event_id: str


class DemoSession(DemoContext):
    user_id: str
    csrf_token: str
    role: Literal["organization_admin"] = "organization_admin"

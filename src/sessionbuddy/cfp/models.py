from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class PublishedFormView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    program_id: str
    version: int
    slug: str
    welcome_text: str
    fields: tuple[Literal["speaker_name", "proposal_title", "proposal_abstract"], ...]


class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    speaker_name: str = Field(min_length=1, max_length=200)
    proposal_title: str = Field(min_length=1, max_length=200)
    proposal_abstract: str = Field(min_length=1, max_length=5000)


class SubmissionView(SubmissionCreate):
    id: str
    program_id: str
    status: Literal["submitted", "withdrawn"]
    submitted_at_ms: int


class SubmissionList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: list[SubmissionView]


class DemoContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organization_id: str
    event_id: str


class DemoSession(DemoContext):
    user_id: str
    csrf_token: str
    role: Literal["organization_admin"] = "organization_admin"

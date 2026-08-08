from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationRoundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    rating_min: int = Field(ge=0, le=10)
    rating_max: int = Field(ge=1, le=10)
    recommendations: list[str] = Field(min_length=2, max_length=8)
    evaluator_guidance: str = Field(default="", max_length=1000)
    submission_ids: list[str] = Field(min_length=1, max_length=100)
    evaluator_user_ids: list[str] = Field(min_length=1, max_length=50)
    assignment_strategy: Literal["all", "balanced"]

    @model_validator(mode="after")
    def valid_rubric(self):
        if self.rating_max <= self.rating_min:
            raise ValueError("rating_max must be greater than rating_min")
        if len(set(self.recommendations)) != len(self.recommendations):
            raise ValueError("recommendations must be unique")
        if len(set(self.submission_ids)) != len(self.submission_ids):
            raise ValueError("submission_ids must be unique")
        if len(set(self.evaluator_user_ids)) != len(self.evaluator_user_ids):
            raise ValueError("evaluator_user_ids must be unique")
        return self


class EvaluationRoundView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    program_id: str
    name: str
    status: Literal["open"]
    assignment_count: int
    evaluator_count: int


class EvaluatorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    display_name: str


class EvaluatorList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[EvaluatorView]


class EvaluationAssignmentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    round_id: str
    round_name: str
    submission_id: str
    proposal_title: str
    proposal_abstract: str
    speaker_name: str
    rating_min: int
    rating_max: int
    recommendations: list[str]
    evaluator_guidance: str = ""
    evaluation_state: Literal["not_started", "draft", "final"]
    rating: int | None = None
    recommendation: str | None = None
    internal_comment: str = ""


class ConflictDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conflict_type: Literal["speaker_relationship", "same_company", "financial", "other"]
    explanation: str = Field(min_length=1, max_length=1000)


class ConflictView(ConflictDeclaration):
    id: str
    assignment_id: str
    status: Literal["declared"] = "declared"


class AssignmentReassign(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evaluator_user_id: str = Field(min_length=36, max_length=36)


class ReassignmentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    evaluator_user_id: str
    status: Literal["assigned"] = "assigned"


class EvaluatorProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluator_user_id: str
    display_name: str
    assigned_count: int
    completed_count: int
    conflict_count: int


class ConflictProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    evaluator_user_id: str
    evaluator_name: str
    proposal_title: str
    conflict_type: str
    replacement_required: bool


class EvaluationAssignmentList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[EvaluationAssignmentView]


class EvaluationSave(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rating: int = Field(ge=0, le=10)
    recommendation: str = Field(min_length=1, max_length=80)
    internal_comment: str = Field(default="", max_length=5000)
    state: Literal["draft", "final"]


class EvaluationView(EvaluationSave):
    id: str
    assignment_id: str
    version: int


class SubmissionDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["accepted", "rejected"]
    internal_reason: str = Field(default="", max_length=2000)


class SubmissionDecisionView(SubmissionDecisionCreate):
    id: str
    submission_id: str
    round_id: str
    version: int


class SubmissionEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str
    speaker_name: str
    proposal_title: str
    assigned_count: int
    completed_count: int
    average_rating: float | None
    decision: Literal["accepted", "rejected"] | None


class EvaluationRoundResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    round_name: str
    status: Literal["draft", "open", "closed"]
    assigned_count: int
    completed_count: int
    average_rating: float | None
    submissions: list[SubmissionEvaluationResult]
    evaluators: list[EvaluatorProgress]
    conflicts: list[ConflictProgress]


class EvaluationRoundClosed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    status: Literal["closed"] = "closed"

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=120)
    weight: int = Field(ge=1, le=100)


class EvaluationRoundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    rating_min: int = Field(ge=0, le=10)
    rating_max: int = Field(ge=1, le=10)
    recommendations: list[str] = Field(min_length=2, max_length=8)
    evaluator_guidance: str = Field(default="", max_length=1000)
    comment_required: bool = False
    criteria: list[EvaluationCriterion] = Field(default_factory=list, max_length=8)
    blind_review: bool = True
    review_opens_at_ms: int | None = Field(default=None, ge=0)
    review_closes_at_ms: int | None = Field(default=None, ge=0)
    submission_ids: list[str] = Field(min_length=1, max_length=100)
    evaluator_user_ids: list[str] = Field(min_length=1, max_length=50)
    assignment_strategy: Literal["all", "balanced"]

    @model_validator(mode="after")
    def valid_rubric(self):
        if self.rating_max <= self.rating_min:
            raise ValueError("rating_max must be greater than rating_min")
        if any(not value or len(value) > 80 for value in self.recommendations):
            raise ValueError("recommendations must contain 1 to 80 characters")
        if len(set(self.recommendations)) != len(self.recommendations):
            raise ValueError("recommendations must be unique")
        if any(len(value) != 36 for value in self.submission_ids):
            raise ValueError("submission_ids must contain UUIDs")
        if any(len(value) != 36 for value in self.evaluator_user_ids):
            raise ValueError("evaluator_user_ids must contain UUIDs")
        if len(set(self.submission_ids)) != len(self.submission_ids):
            raise ValueError("submission_ids must be unique")
        if len(set(self.evaluator_user_ids)) != len(self.evaluator_user_ids):
            raise ValueError("evaluator_user_ids must be unique")
        if len({criterion.key for criterion in self.criteria}) != len(self.criteria):
            raise ValueError("criteria keys must be unique")
        if self.criteria and sum(criterion.weight for criterion in self.criteria) != 100:
            raise ValueError("criteria weights must total 100")
        if (
            self.review_opens_at_ms is not None
            and self.review_closes_at_ms is not None
            and self.review_closes_at_ms <= self.review_opens_at_ms
        ):
            raise ValueError("review close must be after review open")
        return self


class EvaluationRoundView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    program_id: str
    name: str
    status: Literal["draft", "open", "closed"]
    assignment_count: int
    evaluator_count: int


class EvaluationRoundList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[EvaluationRoundView]


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
    comment_required: bool = False
    criteria: list[EvaluationCriterion] = Field(default_factory=list)
    criterion_scores: dict[str, int] = Field(default_factory=dict)
    blind_review: bool = False
    review_closes_at_ms: int | None = None
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


class RoundEvaluatorAdd(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evaluator_user_id: str = Field(min_length=36, max_length=36)


class RoundEvaluatorChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    evaluator_user_id: str
    assignment_count: int = Field(ge=0)


class RoundSubmissionAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_ids: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_submission_ids(self):
        if any(len(value) != 36 for value in self.submission_ids):
            raise ValueError("submission_ids must contain UUIDs")
        if len(set(self.submission_ids)) != len(self.submission_ids):
            raise ValueError("submission_ids must be unique")
        return self


class RoundSubmissionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    submission_count: int = Field(ge=0)
    assignment_count: int = Field(ge=0)


class EvaluatorReminderQueued(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str
    status: Literal["queued"] = "queued"


class AiTriageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str
    source: Literal["workers_ai"] = "workers_ai"
    model: str
    score: int = Field(ge=0, le=10)
    recommendation: str = Field(min_length=1, max_length=80)
    rationale: str = Field(min_length=1, max_length=4000)
    generated_at_ms: int = Field(ge=0)


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
    criterion_scores: dict[str, int] = Field(default_factory=dict, max_length=8)
    state: Literal["draft", "final"]


class EvaluationView(EvaluationSave):
    id: str
    assignment_id: str
    version: int


class SubmissionDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["accepted", "rejected"]
    internal_reason: str = Field(default="", max_length=2000)
    send_email: bool = False
    speaker_message: str = Field(default="", max_length=4000)
    override_incomplete_reviews: bool = False

    @model_validator(mode="after")
    def valid_override(self):
        if self.override_incomplete_reviews and not self.internal_reason:
            raise ValueError("an organizer override requires an internal reason")
        return self


class SubmissionDecisionView(SubmissionDecisionCreate):
    id: str
    submission_id: str
    round_id: str
    version: int
    communication_queued: bool = False


class EvaluationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluator_name: str
    state: Literal["not_started", "draft", "final"]
    rating: int | None = None
    recommendation: str | None = None
    internal_comment: str = ""


class SubmissionEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str
    speaker_name: str
    proposal_title: str
    assigned_count: int
    completed_count: int
    average_rating: float | None
    decision: Literal["accepted", "rejected"] | None
    internal_reason: str = ""
    reviews: list[EvaluationDetail] = Field(default_factory=list)


class EvaluationRoundResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    event_id: str
    program_id: str
    round_name: str
    status: Literal["draft", "open", "closed"]
    assigned_count: int
    completed_count: int
    average_rating: float | None
    submissions: list[SubmissionEvaluationResult]
    evaluators: list[EvaluatorProgress]
    available_evaluators: list[EvaluatorView] = Field(default_factory=list)
    conflicts: list[ConflictProgress]


class EvaluationRoundClosed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    status: Literal["closed"] = "closed"


class EvaluationRoundCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    force: bool = False
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def valid_force(self):
        if self.force and not self.reason:
            raise ValueError("a forced close requires a reason")
        return self

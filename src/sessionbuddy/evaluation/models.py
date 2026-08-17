from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _decision_subject(value: str) -> str:
    if any(character in value for character in ("\r", "\n", "\u2028", "\u2029")):
        raise ValueError("speaker subjects must be a single line")
    return value


class EvaluationCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=120)
    response_type: Literal["score", "select", "text"] = "score"
    required: bool = True
    weight: int | None = Field(default=None, ge=1, le=100)
    options: list[str] = Field(default_factory=list, max_length=20)
    purpose: Literal["recommendation", "comment"] | None = None

    @model_validator(mode="after")
    def valid_type_configuration(self):
        if self.response_type == "score":
            if self.weight is None:
                raise ValueError("scored criteria require a weight")
            if self.options:
                raise ValueError("scored criteria cannot define options")
        elif self.response_type == "select":
            if self.weight is not None:
                raise ValueError("choice criteria cannot define a weight")
            if not 2 <= len(self.options) <= 20:
                raise ValueError("choice criteria require 2 to 20 options")
            if any(not option or len(option) > 120 for option in self.options):
                raise ValueError("criterion options must contain 1 to 120 characters")
            if len(set(self.options)) != len(self.options):
                raise ValueError("criterion options must be unique")
        elif self.weight is not None or self.options:
            raise ValueError("text criteria cannot define weights or options")
        if self.purpose == "recommendation":
            if self.response_type != "select":
                raise ValueError("recommendation-purpose criteria must be choice criteria")
            if not self.required:
                raise ValueError("recommendation-purpose criteria must be required")
            if len(self.options) > 8 or any(len(option) > 80 for option in self.options):
                raise ValueError(
                    "recommendation-purpose criteria require 2 to 8 choices of at most "
                    "80 characters during compatibility"
                )
        if self.purpose == "comment" and self.response_type != "text":
            raise ValueError("comment-purpose criteria must be text criteria")
        return self


class RoundAssignment(BaseModel):
    """One reviewer against one proposal.

    The unit the organizer actually manipulates. Two flat lists plus a strategy could only
    describe a matrix, so "Sam reviews A and B but not C" was unrepresentable -- adding Sam
    fanned him out across every proposal in the round.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    submission_id: str = Field(min_length=36, max_length=36)
    evaluator_user_id: str = Field(min_length=36, max_length=36)

    def pair(self) -> tuple[str, str]:
        return (self.submission_id, self.evaluator_user_id)


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
    submission_ids: list[str] = Field(default_factory=list, max_length=100)
    evaluator_user_ids: list[str] = Field(default_factory=list, max_length=50)
    assignment_strategy: Literal["all", "balanced"]
    # None means "generate from assignment_strategy". A list is authoritative and is never
    # overridden by the strategy -- that is what makes a hand-tailored matrix survive a save.
    assignments: list[RoundAssignment] | None = None
    status: Literal["draft", "open"] = "open"

    @model_validator(mode="after")
    def valid_rubric(self):
        if self.rating_max <= self.rating_min:
            raise ValueError("rating_max must be greater than rating_min")
        if any(not value or len(value) > 80 for value in self.recommendations):
            raise ValueError("recommendations must contain 1 to 80 characters")
        if self.status == "open" and not self.submission_ids:
            raise ValueError("an open round requires at least one submission")
        if self.status == "open" and not self.evaluator_user_ids:
            raise ValueError("an open round requires at least one evaluator")
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
        purposes = [criterion.purpose for criterion in self.criteria if criterion.purpose]
        if len(set(purposes)) != len(purposes):
            raise ValueError("criterion purposes must be unique")
        scored = [criterion for criterion in self.criteria if criterion.response_type == "score"]
        if self.criteria and not scored:
            raise ValueError("a scorecard requires at least one scored criterion")
        if scored and sum(criterion.weight or 0 for criterion in scored) != 100:
            raise ValueError("scored criteria weights must total 100")
        if (
            self.review_opens_at_ms is not None
            and self.review_closes_at_ms is not None
            and self.review_closes_at_ms <= self.review_opens_at_ms
        ):
            raise ValueError("review close must be after review open")
        if self.assignments is not None:
            pairs = [item.pair() for item in self.assignments]
            # An empty list is a matrix, not a missing one: it is stored verbatim, and the
            # strategy is deliberately not consulted to replace it. A round holding both
            # proposals and reviewers with no pair between them is therefore a round nobody
            # can review -- and, because the ledger and the draft view read membership and
            # assignments from different tables, one that reads as if the selection had
            # never been saved. Drafts do not reach the coverage rule below, so this is the
            # only place the state is refused. `null` remains the way to ask for generation.
            if not pairs and self.submission_ids and self.evaluator_user_ids:
                raise ValueError(
                    "assignments cannot be empty when the round has both proposals and "
                    "reviewers; assign at least one reviewer to a proposal, or send null "
                    "to generate the assignments from assignment_strategy"
                )
            if len(set(pairs)) != len(pairs):
                raise ValueError("assignments must be unique")
            submissions, evaluators = set(self.submission_ids), set(self.evaluator_user_ids)
            if any(submission_id not in submissions for submission_id, _ in pairs):
                raise ValueError("assignments reference a submission that is not in the round")
            if any(evaluator_id not in evaluators for _, evaluator_id in pairs):
                raise ValueError("assignments reference an evaluator that is not in the round")
            # A proposal nobody reviews can never be decided, so it cannot start reviewing.
            # Draft rounds may sit half-built; only opening demands full coverage.
            if self.status == "open":
                covered = {submission_id for submission_id, _ in pairs}
                if covered != submissions:
                    raise ValueError(
                        "an open round requires every submission to have an assignment"
                    )
        return self


class EvaluationRoundDraft(EvaluationRoundCreate):
    """The stored configuration of one draft round, plus its version token.

    Editors carry ``version`` unchanged into every mutation they submit; the
    server refuses the mutation when another save has moved the round forward.
    """

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class EvaluationRoundDraftUpdate(EvaluationRoundCreate):
    """A draft-save payload carrying the version its editor read.

    ``expected_version`` is deliberately optional at the validation layer and
    enforced in the route: an absent version is not a malformed document, it is
    an editor too old to participate in the concurrency contract, and it gets
    the same actionable 409 as an editor that lost a race rather than a 422
    that reads as "fix your input".
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)


class RoundOpenRequest(BaseModel):
    """Body of the open-a-draft-round request."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)


class RoundProposalView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str
    proposal_title: str


class EvaluationRoundView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    event_id: str
    name: str
    status: Literal["draft", "open", "closed"]
    version: int = Field(ge=1)
    review_opens_at_ms: int | None = Field(default=None, ge=0)
    review_closes_at_ms: int | None = Field(default=None, ge=0)
    assignment_count: int
    evaluator_count: int
    proposals: list[RoundProposalView] = Field(default_factory=list)


class EvaluationRoundList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[EvaluationRoundView]


class EvaluatorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    display_name: str


class PendingEvaluatorView(BaseModel):
    """A reviewer invitation that exists but has not been accepted yet.

    Eligibility requires `identity_invitations.status='accepted'`, and only the
    invited person can set it -- acceptance happens when they consume their own
    access link. Reporting the pending invitation separately from `data` is what
    lets the organizer surfaces tell "never invited" apart from "invited, still
    waiting"; without it both cases arrive as an empty list and the UI has to
    guess, which is how organizers ended up being told to re-invite someone they
    had already invited.

    Deliberately thin. This rides on a lookup gated by SUBMISSION_MANAGE, which an
    event grant of `edit` satisfies, while the invitation list at
    GET /admin/events/{event_id}/invitations is gated by RESOURCE_ACCESS_MANAGE,
    which needs `manage`. Since the lookup is keyed by a guessable email, anything
    added here becomes probeable by a collaborator who is not allowed to read the
    invitation roster -- so it carries only the state, plus the address the caller
    already supplied. A surface holding `manage` (the Reviewers page) resolves the
    invitee's name and invitation id from the roster it is entitled to read.
    """

    model_config = ConfigDict(extra="forbid")

    email: str
    expired: bool


class EvaluatorList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[EvaluatorView]
    # Populated only when `data` is empty: an eligible reviewer answers the
    # question on its own, and a pending invitation is never the answer when one
    # already exists.
    pending: list[PendingEvaluatorView] = Field(default_factory=list)


class SubmissionAnswerView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str


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
    criterion_responses: dict[str, int | str] = Field(default_factory=dict)
    blind_review: bool = False
    review_closes_at_ms: int | None = None
    assigned_after_close: bool = False
    evaluation_state: Literal["not_started", "draft", "final"]
    rating: int | None = None
    recommendation: str | None = None
    internal_comment: str = ""
    answers: list[SubmissionAnswerView] = Field(default_factory=list)
    hidden_answer_count: int = 0


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
    # Required, not optional-meaning-all. An implicit default of "every proposal in the
    # round" is the behaviour this change exists to remove; the UI preselects them instead,
    # so the common case stays one click while the payload stays explicit.
    submission_ids: list[str] = Field(min_length=1, max_length=100)
    # The round-level optimistic-concurrency token. Optional here for the same
    # reason as on the draft update: enforcement lives in the route so a missing
    # token gets the actionable 409 rather than a validation 422.
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_submission_ids(self):
        if any(len(value) != 36 for value in self.submission_ids):
            raise ValueError("submission_ids must contain UUIDs")
        if len(set(self.submission_ids)) != len(self.submission_ids):
            raise ValueError("submission_ids must be unique")
        return self


class RoundEvaluatorRemove(BaseModel):
    """Body of the remove-a-reviewer request."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int | None = Field(default=None, ge=1)


class RoundEvaluatorChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    evaluator_user_id: str
    assignment_count: int = Field(ge=0)


class RoundSubmissionAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_ids: list[str] = Field(min_length=1, max_length=100)
    # Round-level optimistic-concurrency token; enforced in the route so an
    # absent token gets the actionable 409 rather than a validation 422.
    expected_version: int | None = Field(default=None, ge=1)

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
    late_assignment_count: int = 0
    late_completed_count: int = 0


class ConflictProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    submission_id: str
    evaluator_user_id: str
    evaluator_name: str
    proposal_title: str
    conflict_type: str
    replacement_required: bool


class EvaluationAssignmentList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[EvaluationAssignmentView]
    next_cursor: str | None = None
    total: int = Field(ge=0)
    completed_count: int = Field(ge=0)


class EvaluationSave(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    rating: int | None = Field(default=None, ge=0, le=10)
    recommendation: str | None = Field(default=None, min_length=1, max_length=80)
    internal_comment: str = Field(default="", max_length=5000)
    criterion_responses: dict[str, int | str] = Field(default_factory=dict, max_length=8)
    state: Literal["draft", "final"]

    @model_validator(mode="after")
    def validate_final_completeness(self) -> "EvaluationSave":
        # The canonical recommendation can live in a rubric-designated criterion. This
        # payload does not contain enough rubric metadata to identify that key; the save
        # route enforces final completeness after loading the round's rubric.
        if self.state == "final":
            if self.rating is None and not self.criterion_responses:
                raise ValueError("final evaluations require a rating")
            if any(
                isinstance(response, str) and not response.strip()
                for response in self.criterion_responses.values()
            ):
                raise ValueError("final criterion responses cannot be blank")
        return self


class EvaluationView(EvaluationSave):
    id: str
    assignment_id: str
    version: int


class SubmissionDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["accepted", "rejected"]
    internal_reason: str = Field(default="", max_length=2000)
    send_email: bool = False
    speaker_subject: str = Field(default="", max_length=200)
    speaker_message: str = Field(default="", max_length=4000)
    override_incomplete_reviews: bool = False

    _valid_speaker_subject = field_validator("speaker_subject")(_decision_subject)

    @model_validator(mode="after")
    def valid_override(self):
        if self.override_incomplete_reviews and not self.internal_reason:
            raise ValueError("an organizer override requires an internal reason")
        return self


class SubmissionDecisionView(SubmissionDecisionCreate):
    id: str
    submission_id: str
    round_id: str | None
    version: int
    communication_queued: bool = False


class SubmissionDecisionCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    corrected_decision: Literal["accepted", "rejected"]
    reason: str = Field(min_length=1, max_length=2000)
    send_email: bool = False
    speaker_subject: str = Field(default="", max_length=200)
    speaker_message: str = Field(default="", max_length=4000)

    _valid_speaker_subject = field_validator("speaker_subject")(_decision_subject)


class SubmissionDecisionMessagePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["accepted", "rejected"]
    correction: bool = False
    speaker_subject: str = Field(default="", max_length=200)
    speaker_message: str = Field(default="", max_length=4000)

    _valid_speaker_subject = field_validator("speaker_subject")(_decision_subject)


class SubmissionDecisionMessagePreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_subject: str
    resolved_body: str
    proposal_title: str
    recipient_available: bool


class SubmissionDecisionCorrectionView(SubmissionDecisionCorrectionCreate):
    id: str
    submission_id: str
    original_decision_id: str
    previous_decision: Literal["accepted", "rejected"]
    corrected_at_ms: int
    accepted_session_id: str | None = None
    accepted_session_lifecycle_status: Literal["active", "withdrawn"] | None = None
    communication_queued: bool = False


class EvaluationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluator_user_id: str
    evaluator_name: str
    state: Literal["not_started", "draft", "final"]
    rating: int | None = None
    weighted_score: float | None = None
    recommendation: str | None = None
    internal_comment: str = ""
    criterion_responses: dict[str, int | str] = Field(default_factory=dict)


class SubmissionEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str
    speaker_name: str
    proposal_title: str
    assigned_count: int
    # True when an active round member has no live assignment left -- typically a conflict
    # declared on its only reviewer. Opening a round forbids this state; it can still arise
    # afterwards, so it is surfaced rather than treated as an impossible condition.
    needs_reassignment: bool = False
    completed_count: int
    average_rating: float | None
    decision: Literal["accepted", "rejected"] | None
    decision_round_id: str | None = None
    internal_reason: str = ""
    correction_reason: str = ""
    reviews: list[EvaluationDetail] = Field(default_factory=list)


class EvaluationRoundResults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round_id: str
    event_id: str
    event_name: str
    round_name: str
    status: Literal["draft", "open", "closed"]
    assigned_count: int
    completed_count: int
    average_rating: float | None
    # The round-level optimistic-concurrency token. Every mutation this page
    # offers sends it back; a 409 means another editor moved the round first.
    version: int = Field(ge=1)
    criteria: list[EvaluationCriterion] = Field(default_factory=list)
    submissions: list[SubmissionEvaluationResult]
    submission_count: int = Field(ge=0)
    next_cursor: str | None = None
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
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_force(self):
        if self.force and not self.reason:
            raise ValueError("a forced close requires a reason")
        return self

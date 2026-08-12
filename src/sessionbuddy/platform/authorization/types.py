from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    ORGANIZATION_ADMIN = "organization_admin"
    EVENT_ADMIN = "event_admin"
    SPEAKER = "speaker"


class Persona(StrEnum):
    ORGANIZER = "organizer"
    REVIEWER = "reviewer"
    SPEAKER = "speaker"


class ResourceGrant(StrEnum):
    VIEW = "view"
    EDIT = "edit"
    MANAGE = "manage"


class Permission(StrEnum):
    ORGANIZATION_MANAGE = "organization.manage"
    EVENT_MANAGE = "event.manage"
    RESOURCE_ACCESS_MANAGE = "resource.access.manage"
    FORM_MANAGE = "form.manage"
    SUBMISSION_MANAGE = "submission.manage"
    SUBMISSION_READ_FOR_EVALUATION = "submission.read_for_evaluation"
    SUBMISSION_READ_OWN = "submission.read_own"
    EVALUATION_SAVE = "evaluation.save"
    EVALUATION_RESULTS_READ = "evaluation.results.read"
    EVALUATION_OWN_READ = "evaluation.own.read"
    SPEAKER_MANAGE = "speaker.manage"
    SPEAKER_PROFILE_READ_OWN = "speaker.profile.read_own"
    SPEAKER_PROFILE_EDIT_OWN = "speaker.profile.edit_own"
    SPEAKER_ASSET_READ = "speaker.asset.read"
    SPEAKER_ASSET_COMMENT = "speaker.asset.comment"
    SPEAKER_ASSET_EXPORT = "speaker.asset.export"
    SPEAKER_ASSET_READ_OWN = "speaker.asset.read_own"
    SPEAKER_ASSET_UPLOAD_OWN = "speaker.asset.upload_own"
    SPEAKER_ASSET_REPLACE_OWN = "speaker.asset.replace_own"
    SPEAKER_ASSET_COMMENT_OWN = "speaker.asset.comment_own"
    SPEAKER_TASK_READ_OWN = "speaker.task.read_own"
    AGENDA_MANAGE = "agenda.manage"
    LABEL_MANAGE = "label.manage"
    COMMUNICATION_SEND = "communication.send"
    DASHBOARD_READ = "dashboard.read"


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: str
    active: bool = True
    session_active: bool = True
    active_persona: Persona | None = None
    owned_resource_ids: frozenset[str] = frozenset()
    resource_grants: dict[str, frozenset[ResourceGrant]] = field(default_factory=dict)
    organization_roles: dict[str, frozenset[Role]] = field(default_factory=dict)
    event_roles: dict[tuple[str, str], frozenset[Role]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResourceContext:
    organization_id: str
    event_id: str | None = None
    resource_owner_user_id: str | None = None
    # These facts must be copied from the selected evaluation_assignments row.
    # Keeping the assignee identity (instead of accepting a caller-supplied
    # boolean) lets the policy enforce the actor-to-assignment relationship.
    evaluator_user_id: str | None = None
    evaluator_assignment_status: str | None = None
    evaluation_round_open: bool = False
    resource_exists: bool = True
    resource_id: str | None = None

    @property
    def authorization_resource_id(self) -> str:
        return self.resource_id or self.event_id or self.organization_id


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: str

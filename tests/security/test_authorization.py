import pytest

from sessionbuddy.platform.authorization.policy import ROLE_GRANTS, authorize
from sessionbuddy.platform.authorization.types import Actor, Permission, ResourceContext, Role

ORG, OTHER_ORG, EVENT, OTHER_EVENT = "org-a", "org-b", "event-a", "event-b"


def actor(role: Role, *, org: str = ORG, event: str | None = EVENT) -> Actor:
    if role is Role.ORGANIZATION_ADMIN:
        return Actor("user-a", organization_roles={org: frozenset({role})})
    assert event
    return Actor("user-a", event_roles={(org, event): frozenset({role})})


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("permission", list(Permission))
def test_permission_matrix_is_closed_and_deny_by_default(
    role: Role, permission: Permission
) -> None:
    subject = actor(role)
    context = ResourceContext(ORG, EVENT, "user-a", True, True)
    assert authorize(subject, permission, context).allowed is (permission in ROLE_GRANTS[role])


@pytest.mark.parametrize("role", list(Role))
def test_changing_organization_or_event_never_grants_access(role: Role) -> None:
    subject = actor(role)
    assert not authorize(
        subject, Permission.DASHBOARD_READ, ResourceContext(OTHER_ORG, EVENT)
    ).allowed
    if role is not Role.ORGANIZATION_ADMIN:
        assert not authorize(
            subject, Permission.DASHBOARD_READ, ResourceContext(ORG, OTHER_EVENT)
        ).allowed


def test_speaker_cannot_change_owner_identifier() -> None:
    subject = actor(Role.SPEAKER)
    decision = authorize(
        subject, Permission.SPEAKER_ASSET_REPLACE_OWN, ResourceContext(ORG, EVENT, "other")
    )
    assert not decision.allowed
    assert decision.reason == "ownership_required"


@pytest.mark.parametrize(
    "permission",
    [
        Permission.SPEAKER_ASSET_READ_OWN,
        Permission.SPEAKER_ASSET_UPLOAD_OWN,
        Permission.SPEAKER_ASSET_REPLACE_OWN,
    ],
)
def test_speaker_asset_permissions_require_ownership(permission: Permission) -> None:
    subject = actor(Role.SPEAKER)
    own = authorize(subject, permission, ResourceContext(ORG, EVENT, subject.user_id))
    foreign = authorize(subject, permission, ResourceContext(ORG, EVENT, "another-user"))
    assert own.allowed
    assert (foreign.allowed, foreign.reason) == (False, "ownership_required")


@pytest.mark.parametrize(
    "permission",
    [Permission.SPEAKER_ASSET_READ_OWN, Permission.SPEAKER_ASSET_UPLOAD_OWN],
)
def test_staff_roles_do_not_inherit_speaker_own_permissions(permission: Permission) -> None:
    assert not authorize(
        actor(Role.ORGANIZATION_ADMIN), permission, ResourceContext(ORG, EVENT, "user-a")
    ).allowed
    assert not authorize(
        actor(Role.EVENT_ADMIN), permission, ResourceContext(ORG, EVENT, "user-a")
    ).allowed


def test_evaluator_needs_assignment_and_open_round() -> None:
    subject = actor(Role.EVALUATOR)
    missing = authorize(subject, Permission.EVALUATION_SAVE, ResourceContext(ORG, EVENT))
    closed = authorize(
        subject, Permission.EVALUATION_SAVE, ResourceContext(ORG, EVENT, evaluator_assigned=True)
    )
    allowed = authorize(
        subject,
        Permission.EVALUATION_SAVE,
        ResourceContext(ORG, EVENT, evaluator_assigned=True, evaluation_round_open=True),
    )
    assert (missing.reason, closed.reason, allowed.allowed) == (
        "assignment_required",
        "lifecycle_forbidden",
        True,
    )


def test_admin_cannot_save_evaluation_without_separate_assignment() -> None:
    subject = actor(Role.ORGANIZATION_ADMIN)
    assert not authorize(subject, Permission.EVALUATION_SAVE, ResourceContext(ORG, EVENT)).allowed


def test_inactive_actor_and_absent_resource_are_safe_denials() -> None:
    inactive = Actor(
        "user-a", active=False, organization_roles={ORG: frozenset({Role.ORGANIZATION_ADMIN})}
    )
    assert (
        authorize(inactive, Permission.EVENT_MANAGE, ResourceContext(ORG, EVENT)).reason
        == "inactive_principal"
    )
    active = actor(Role.ORGANIZATION_ADMIN)
    assert (
        authorize(
            active, Permission.EVENT_MANAGE, ResourceContext(ORG, EVENT, resource_exists=False)
        ).reason
        == "resource_not_found"
    )

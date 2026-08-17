import pytest

from sessionbuddy.platform.authorization.policy import ORGANIZER_PERMISSIONS, authorize
from sessionbuddy.platform.authorization.types import (
    Actor,
    Permission,
    Persona,
    ResourceContext,
    ResourceGrant,
    Role,
)

ORG, OTHER_ORG, EVENT, OTHER_EVENT = "org-a", "org-b", "event-a", "event-b"


def organizer(*resources: str, grants: dict[str, frozenset[ResourceGrant]] | None = None) -> Actor:
    return Actor(
        "user-a",
        active_persona=Persona.ORGANIZER,
        owned_resource_ids=frozenset(resources),
        resource_grants=grants or {},
    )


def reviewer(*, assigned: bool = True) -> tuple[Actor, ResourceContext]:
    return (
        Actor("user-a", active_persona=Persona.REVIEWER),
        ResourceContext(
            ORG,
            EVENT,
            evaluator_user_id="user-a" if assigned else None,
            evaluator_assignment_status="assigned" if assigned else None,
            evaluation_round_open=True,
        ),
    )


def speaker(*, owner: str = "user-a", assigned: bool = True) -> tuple[Actor, ResourceContext]:
    roles = {(ORG, EVENT): frozenset({Role.SPEAKER})} if assigned else {}
    return (
        Actor("user-a", active_persona=Persona.SPEAKER, event_roles=roles),
        ResourceContext(ORG, EVENT, resource_owner_user_id=owner),
    )


@pytest.mark.parametrize(
    "permission",
    [
        Permission.EVENT_MANAGE,
        Permission.FORM_MANAGE,
        Permission.SUBMISSION_MANAGE,
        Permission.SPEAKER_MANAGE,
        Permission.AGENDA_MANAGE,
        Permission.COMMUNICATION_SEND,
        Permission.DASHBOARD_READ,
    ],
)
def test_organizer_owner_can_use_organizer_artifacts(permission: Permission) -> None:
    assert authorize(organizer(ORG), permission, ResourceContext(ORG, EVENT)).allowed


def test_speaker_active_persona_cannot_use_owned_organizer_artifact() -> None:
    subject = Actor(
        "user-a",
        active_persona=Persona.SPEAKER,
        owned_resource_ids=frozenset({EVENT}),
        event_roles={(ORG, EVENT): frozenset({Role.SPEAKER})},
    )
    decision = authorize(subject, Permission.EVENT_MANAGE, ResourceContext(ORG, EVENT))
    assert (decision.allowed, decision.reason) == (False, "permission_not_granted")


def test_reviewer_active_persona_cannot_use_owned_organizer_artifact() -> None:
    subject = Actor(
        "user-a",
        active_persona=Persona.REVIEWER,
        owned_resource_ids=frozenset({EVENT}),
        event_roles={},
    )
    assert not authorize(subject, Permission.EVENT_MANAGE, ResourceContext(ORG, EVENT)).allowed


def test_organizer_requires_authority_over_the_event_organization() -> None:
    subject = organizer(ORG)
    allowed = authorize(subject, Permission.EVENT_MANAGE, ResourceContext(ORG, EVENT))
    foreign = authorize(subject, Permission.EVENT_MANAGE, ResourceContext(OTHER_ORG, OTHER_EVENT))
    assert allowed.allowed
    assert (foreign.allowed, foreign.reason) == (False, "resource_access_required")


def test_label_management_cascades_from_organization_authority() -> None:
    label_id = "label-a"
    context = ResourceContext(ORG, EVENT, resource_id=label_id)
    assert authorize(organizer(ORG), Permission.LABEL_MANAGE, context).allowed
    denied = authorize(organizer(OTHER_ORG), Permission.LABEL_MANAGE, context)
    assert (denied.allowed, denied.reason) == (False, "resource_access_required")


def test_label_creator_does_not_gain_event_authority_from_legacy_ownership() -> None:
    label_id = "label-a"
    context = ResourceContext(ORG, EVENT, resource_id=label_id)
    decision = authorize(organizer(label_id), Permission.LABEL_MANAGE, context)
    assert (decision.allowed, decision.reason) == (False, "resource_access_required")


def test_only_owner_or_manage_grant_can_delegate_resource_access() -> None:
    context = ResourceContext(ORG, EVENT)

    assert authorize(
        organizer(ORG), Permission.RESOURCE_ACCESS_MANAGE, context
    ).allowed
    assert authorize(
        organizer(grants={ORG: frozenset({ResourceGrant.MANAGE})}),
        Permission.RESOURCE_ACCESS_MANAGE,
        context,
    ).allowed
    manager = authorize(
        organizer(grants={ORG: frozenset({ResourceGrant.MANAGE})}),
        Permission.ORGANIZATION_MANAGE,
        ResourceContext(ORG),
    )
    assert manager.allowed


def test_organization_ownership_cascades_to_every_event() -> None:
    subject = organizer(ORG)
    assert authorize(subject, Permission.ORGANIZATION_MANAGE, ResourceContext(ORG)).allowed
    event = authorize(subject, Permission.EVENT_MANAGE, ResourceContext(ORG, EVENT))
    assert event.allowed


def test_organization_manage_grant_cascades_to_event_operations() -> None:
    context = ResourceContext(ORG, EVENT)
    manager = organizer(grants={ORG: frozenset({ResourceGrant.MANAGE})})
    assert authorize(manager, Permission.EVENT_MANAGE, context).allowed


@pytest.mark.parametrize("permission", sorted(Permission, key=lambda item: item.value))
def test_organization_manage_grant_has_the_declared_permission_matrix(
    permission: Permission,
) -> None:
    decision = authorize(
        organizer(grants={ORG: frozenset({ResourceGrant.MANAGE})}),
        permission,
        ResourceContext(ORG),
    )
    expected = permission in ORGANIZER_PERMISSIONS
    assert decision.allowed is expected


def test_speaker_permissions_require_active_assignment_and_ownership() -> None:
    subject, own = speaker()
    assert authorize(subject, Permission.SPEAKER_ASSET_UPLOAD_OWN, own).allowed
    foreign = ResourceContext(ORG, EVENT, resource_owner_user_id="other")
    assert (
        authorize(subject, Permission.SPEAKER_ASSET_UPLOAD_OWN, foreign).reason
        == "ownership_required"
    )
    unassigned, context = speaker(assigned=False)
    assert (
        authorize(unassigned, Permission.SPEAKER_ASSET_UPLOAD_OWN, context).reason
        == "assignment_required"
    )


def test_reviewer_needs_assignment_and_open_round() -> None:
    subject, context = reviewer()
    assert authorize(subject, Permission.EVALUATION_SAVE, context).allowed
    closed = ResourceContext(
        ORG,
        EVENT,
        evaluator_user_id="user-a",
        evaluator_assignment_status="assigned",
    )
    assert authorize(subject, Permission.EVALUATION_SAVE, closed).reason == "lifecycle_forbidden"
    unassigned, missing = reviewer(assigned=False)
    assert (
        authorize(unassigned, Permission.EVALUATION_SAVE, missing).reason
        == "assignment_required"
    )


@pytest.mark.parametrize("status", [None, "revoked"])
def test_reviewer_assignment_fact_fails_closed(status: str | None) -> None:
    subject = Actor("user-a", active_persona=Persona.REVIEWER)
    context = ResourceContext(
        ORG,
        EVENT,
        evaluator_user_id="user-a",
        evaluator_assignment_status=status,
        evaluation_round_open=True,
    )
    assert authorize(subject, Permission.EVALUATION_SAVE, context).reason == "assignment_required"


def test_reviewer_assignment_must_name_exact_actor() -> None:
    subject = Actor("user-a", active_persona=Persona.REVIEWER)
    context = ResourceContext(
        ORG,
        EVENT,
        evaluator_user_id="user-b",
        evaluator_assignment_status="assigned",
        evaluation_round_open=True,
    )
    assert authorize(subject, Permission.EVALUATION_SAVE, context).reason == "assignment_required"


def test_organizer_cannot_submit_a_review_without_reviewer_persona() -> None:
    decision = authorize(
        organizer(EVENT),
        Permission.EVALUATION_SAVE,
        ResourceContext(
            ORG,
            EVENT,
            evaluator_user_id="user-a",
            evaluator_assignment_status="assigned",
            evaluation_round_open=True,
        ),
    )
    assert (decision.allowed, decision.reason) == (False, "permission_not_granted")


def test_speaker_cannot_submit_a_review_despite_an_assignment_context() -> None:
    subject, _ = speaker()
    decision = authorize(
        subject,
        Permission.EVALUATION_SAVE,
        ResourceContext(
            ORG,
            EVENT,
            evaluator_user_id="user-a",
            evaluator_assignment_status="assigned",
            evaluation_round_open=True,
        ),
    )
    assert (decision.allowed, decision.reason) == (False, "permission_not_granted")


def test_inactive_actor_and_absent_resource_are_safe_denials() -> None:
    inactive = Actor(
        "user-a", active=False, active_persona=Persona.ORGANIZER,
        owned_resource_ids=frozenset({EVENT}),
    )
    assert (
        authorize(inactive, Permission.EVENT_MANAGE, ResourceContext(ORG, EVENT)).reason
        == "inactive_principal"
    )
    assert authorize(
        organizer(EVENT), Permission.EVENT_MANAGE,
        ResourceContext(ORG, EVENT, resource_exists=False),
    ).reason == "resource_not_found"

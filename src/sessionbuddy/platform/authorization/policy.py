from .types import (
    Actor,
    AuthorizationDecision,
    Permission,
    Persona,
    ResourceContext,
    ResourceGrant,
    Role,
)

ORGANIZER_PERMISSIONS = frozenset(
    {
        Permission.ORGANIZATION_MANAGE,
        Permission.EVENT_MANAGE,
        Permission.RESOURCE_ACCESS_MANAGE,
        Permission.FORM_MANAGE,
        Permission.SUBMISSION_MANAGE,
        Permission.SUBMISSION_READ_FOR_EVALUATION,
        Permission.EVALUATION_RESULTS_READ,
        Permission.SPEAKER_MANAGE,
        Permission.SPEAKER_ASSET_READ,
        Permission.AGENDA_MANAGE,
        Permission.LABEL_MANAGE,
        Permission.COMMUNICATION_SEND,
        Permission.DASHBOARD_READ,
    }
)
REVIEWER_PERMISSIONS = frozenset(
    {
        Permission.SUBMISSION_READ_FOR_EVALUATION,
        Permission.EVALUATION_SAVE,
        Permission.EVALUATION_OWN_READ,
    }
)
SPEAKER_PERMISSIONS = frozenset(
    {
        Permission.SUBMISSION_READ_OWN,
        Permission.SPEAKER_PROFILE_READ_OWN,
        Permission.SPEAKER_PROFILE_EDIT_OWN,
        Permission.SPEAKER_ASSET_READ_OWN,
        Permission.SPEAKER_ASSET_UPLOAD_OWN,
        Permission.SPEAKER_ASSET_REPLACE_OWN,
        Permission.SPEAKER_TASK_READ_OWN,
    }
)

# Kept as a compatibility export for callers/tests that enumerate the legacy
# membership vocabulary. Administrative membership roles no longer authorize.
ROLE_GRANTS = {
    Role.ORGANIZATION_ADMIN: ORGANIZER_PERMISSIONS,
    Role.EVENT_ADMIN: ORGANIZER_PERMISSIONS - {Permission.ORGANIZATION_MANAGE},
    Role.SPEAKER: SPEAKER_PERMISSIONS,
}

OWNERSHIP_PERMISSIONS = SPEAKER_PERMISSIONS


def _resource_authority(
    actor: Actor, permission: Permission, context: ResourceContext
) -> bool:
    resource_id = context.authorization_resource_id
    if resource_id in actor.owned_resource_ids:
        return True
    grants = actor.resource_grants.get(resource_id, frozenset())
    # Organizer authority is organization-scoped. Owners and managers of an
    # organization administer every event in that organization; events do not
    # carry a second, parallel administrator role or generic access grant.
    if context.event_id is not None:
        if context.organization_id in actor.owned_resource_ids:
            return True
        organization_grants = actor.resource_grants.get(
            context.organization_id, frozenset()
        )
        if ResourceGrant.MANAGE in organization_grants:
            return True
    if permission in {
        Permission.RESOURCE_ACCESS_MANAGE,
        Permission.ORGANIZATION_MANAGE,
    }:
        return ResourceGrant.MANAGE in grants
    return bool(grants & {ResourceGrant.EDIT, ResourceGrant.MANAGE})


def authorize(
    actor: Actor, permission: Permission, context: ResourceContext
) -> AuthorizationDecision:
    """Require an active persona and an exact resource fact for every request."""
    if not actor.active or not actor.session_active:
        return AuthorizationDecision(False, "inactive_principal")
    if not context.resource_exists:
        return AuthorizationDecision(False, "resource_not_found")

    if actor.active_persona is Persona.ORGANIZER:
        if permission not in ORGANIZER_PERMISSIONS:
            return AuthorizationDecision(False, "permission_not_granted")
        if not _resource_authority(actor, permission, context):
            return AuthorizationDecision(False, "resource_access_required")
        return AuthorizationDecision(True, "allowed")

    if actor.active_persona is Persona.REVIEWER:
        if permission not in REVIEWER_PERMISSIONS:
            return AuthorizationDecision(False, "permission_not_granted")
        if (
            context.evaluator_user_id != actor.user_id
            or context.evaluator_assignment_status == "revoked"
            or context.evaluator_assignment_status is None
        ):
            return AuthorizationDecision(False, "assignment_required")
        if permission is Permission.EVALUATION_SAVE and not context.evaluation_round_open:
            return AuthorizationDecision(False, "lifecycle_forbidden")
        return AuthorizationDecision(True, "allowed")

    if actor.active_persona is Persona.SPEAKER:
        if permission not in SPEAKER_PERMISSIONS:
            return AuthorizationDecision(False, "permission_not_granted")
        # A submitter may read their own draft before the first event-speaker
        # assignment exists. Every other speaker operation requires assignment.
        roles = actor.event_roles.get(
            (context.organization_id, context.event_id), frozenset()
        )
        if permission is not Permission.SUBMISSION_READ_OWN and Role.SPEAKER not in roles:
            return AuthorizationDecision(False, "assignment_required")
        if context.resource_owner_user_id != actor.user_id:
            return AuthorizationDecision(False, "ownership_required")
        return AuthorizationDecision(True, "allowed")

    return AuthorizationDecision(False, "active_persona_required")

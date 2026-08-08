from .types import Actor, AuthorizationDecision, Permission, ResourceContext, Role

ORG_ADMIN_GRANTS = frozenset(
    {
        Permission.ORGANIZATION_MANAGE,
        Permission.EVENT_MANAGE,
        Permission.PROGRAM_MANAGE,
        Permission.FORM_MANAGE,
        Permission.SUBMISSION_MANAGE,
        Permission.SUBMISSION_READ_FOR_EVALUATION,
        Permission.EVALUATION_RESULTS_READ,
        Permission.SPEAKER_MANAGE,
        Permission.SPEAKER_ASSET_READ,
        Permission.AGENDA_MANAGE,
        Permission.COMMUNICATION_SEND,
        Permission.DASHBOARD_READ,
    }
)
EVENT_ADMIN_GRANTS = ORG_ADMIN_GRANTS - {Permission.ORGANIZATION_MANAGE}
EVALUATOR_GRANTS = frozenset(
    {
        Permission.SUBMISSION_READ_FOR_EVALUATION,
        Permission.EVALUATION_SAVE,
        Permission.EVALUATION_OWN_READ,
    }
)
SPEAKER_GRANTS = frozenset(
    {
        Permission.SUBMISSION_READ_OWN,
        Permission.SPEAKER_PROFILE_READ_OWN,
        Permission.SPEAKER_PROFILE_EDIT_OWN,
        Permission.SPEAKER_ASSET_REPLACE_OWN,
        Permission.SPEAKER_TASK_READ_OWN,
    }
)
ROLE_GRANTS = {
    Role.ORGANIZATION_ADMIN: ORG_ADMIN_GRANTS,
    Role.EVENT_ADMIN: EVENT_ADMIN_GRANTS,
    Role.EVALUATOR: EVALUATOR_GRANTS,
    Role.SPEAKER: SPEAKER_GRANTS,
}

OWNERSHIP_PERMISSIONS = SPEAKER_GRANTS
ASSIGNMENT_PERMISSIONS = EVALUATOR_GRANTS


def authorize(
    actor: Actor, permission: Permission, context: ResourceContext
) -> AuthorizationDecision:
    """Evaluate role candidates plus server-resolved tenant/resource facts."""
    if not actor.active or not actor.session_active:
        return AuthorizationDecision(False, "inactive_principal")
    if not context.resource_exists:
        return AuthorizationDecision(False, "resource_not_found")

    org_roles = actor.organization_roles.get(context.organization_id, frozenset())
    event_roles = (
        actor.event_roles.get((context.organization_id, context.event_id), frozenset())
        if context.event_id
        else frozenset()
    )
    roles = org_roles | event_roles
    if not roles:
        return AuthorizationDecision(False, "tenant_membership_required")
    if not any(permission in ROLE_GRANTS[role] for role in roles):
        return AuthorizationDecision(False, "permission_not_granted")

    if permission in OWNERSHIP_PERMISSIONS and Role.SPEAKER in roles:
        if context.resource_owner_user_id != actor.user_id:
            return AuthorizationDecision(False, "ownership_required")
    if permission in ASSIGNMENT_PERMISSIONS and Role.EVALUATOR in roles:
        # Admin grants can satisfy reads independently, but never evaluation writes.
        admin_grant = bool(roles & {Role.ORGANIZATION_ADMIN, Role.EVENT_ADMIN})
        if not context.evaluator_assigned and (
            permission is Permission.EVALUATION_SAVE or not admin_grant
        ):
            return AuthorizationDecision(False, "assignment_required")
        if permission is Permission.EVALUATION_SAVE and not context.evaluation_round_open:
            return AuthorizationDecision(False, "lifecycle_forbidden")
    return AuthorizationDecision(True, "allowed")

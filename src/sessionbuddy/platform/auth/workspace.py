"""One authoritative mapping from session facts to a browser workspace."""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from sessionbuddy.platform.db.d1 import result_rows

WorkspaceRole = Literal["organizer", "reviewer", "speaker"]
WorkspaceState = Literal[
    "ready",
    "roleless",
    "profile_incomplete",
    "active_role_invalid",
    "organizer_authority_missing",
]

ROLE_WORKSPACES: dict[WorkspaceRole, str] = {
    "organizer": "/admin",
    "reviewer": "/reviews",
    "speaker": "/speaker",
}


@dataclass(frozen=True, slots=True)
class WorkspaceResolution:
    state: WorkspaceState
    path: str | None


@dataclass(frozen=True, slots=True)
class UserWorkspaceContract:
    resolution: WorkspaceResolution
    personas: tuple[tuple[WorkspaceRole, WorkspaceResolution], ...]


def resolve_workspace(
    *,
    active_role: str | None,
    account_roles: Collection[str],
    profile_complete: bool,
    manages_organization: bool,
) -> WorkspaceResolution:
    """Resolve every authenticated session without asking the browser to authorize it."""
    if active_role is None or not active_role.strip():
        return WorkspaceResolution("roleless", "/calls")
    if active_role not in ROLE_WORKSPACES or active_role not in account_roles:
        return WorkspaceResolution("active_role_invalid", None)
    if not profile_complete:
        return WorkspaceResolution("profile_incomplete", "/account")
    if active_role == "organizer" and not manages_organization:
        return WorkspaceResolution("organizer_authority_missing", None)
    return WorkspaceResolution("ready", ROLE_WORKSPACES[active_role])


def usable_personas(
    *,
    account_roles: Collection[str],
    profile_complete: bool,
    manages_organization: bool,
) -> tuple[tuple[WorkspaceRole, WorkspaceResolution], ...]:
    """Return only personas whose workspace the server can make usable."""
    resolved: list[tuple[WorkspaceRole, WorkspaceResolution]] = []
    for role in ROLE_WORKSPACES:
        if role not in account_roles or (role == "organizer" and not manages_organization):
            continue
        resolved.append(
            (
                role,
                resolve_workspace(
                    active_role=role,
                    account_roles=account_roles,
                    profile_complete=profile_complete,
                    manages_organization=manages_organization,
                ),
            )
        )
    return tuple(resolved)


async def user_workspace_contract(
    db, *, user_id: str, active_role: str | None
) -> UserWorkspaceContract | None:
    """Load the current facts needed by sign-in and persona-selection callers."""
    rows = result_rows(
        await db.prepare(
            """SELECT roles.role,
                      users.profile_completed_at_ms IS NOT NULL AS profile_complete,
                      EXISTS (
                        SELECT 1 FROM owned_resources resources
                        LEFT JOIN resource_access_grants grants
                          ON grants.resource_id=resources.id AND grants.user_id=?1
                         AND grants.status='active' AND grants.permission='manage'
                        WHERE resources.resource_type='organization'
                          AND resources.status='active'
                          AND (resources.owner_user_id=?1 OR grants.id IS NOT NULL)
                      ) AS manages_organization
               FROM users JOIN user_roles roles ON roles.user_id=users.id
               WHERE users.id=?1 AND users.status='active' AND users.deleted_at_ms IS NULL
                 AND roles.status='active'
               ORDER BY roles.role"""
        )
        .bind(user_id)
        .all()
    )
    if not rows:
        return None
    account_roles = [str(row["role"]) for row in rows]
    profile_complete = bool(rows[0]["profile_complete"])
    manages_organization = bool(rows[0]["manages_organization"])
    return UserWorkspaceContract(
        resolution=resolve_workspace(
            active_role=active_role,
            account_roles=account_roles,
            profile_complete=profile_complete,
            manages_organization=manages_organization,
        ),
        personas=usable_personas(
            account_roles=account_roles,
            profile_complete=profile_complete,
            manages_organization=manages_organization,
        ),
    )

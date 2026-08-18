from itertools import product

import pytest

from sessionbuddy.platform.auth.workspace import resolve_workspace, usable_personas


@pytest.mark.parametrize(
    ("active_role", "account_roles", "profile_complete", "manages_organization"),
    list(product(
        [None, "", "organizer", "reviewer", "speaker", "unknown"],
        [[], ["organizer"], ["reviewer"], ["speaker"], ["organizer", "speaker"]],
        [False, True],
        [False, True],
    )),
)
def test_workspace_resolution_is_total_and_internally_consistent(
    active_role, account_roles, profile_complete, manages_organization
) -> None:
    resolved = resolve_workspace(
        active_role=active_role,
        account_roles=account_roles,
        profile_complete=profile_complete,
        manages_organization=manages_organization,
    )

    assert resolved.state in {
        "ready",
        "roleless",
        "profile_incomplete",
        "active_role_invalid",
        "organizer_authority_missing",
    }
    if resolved.state in {"active_role_invalid", "organizer_authority_missing"}:
        assert resolved.path is None
    else:
        assert resolved.path in {"/calls", "/account", "/admin", "/reviews", "/speaker"}
    if resolved.state == "profile_incomplete":
        assert resolved.path == "/account"


def test_usable_personas_excludes_an_organizer_without_organization_authority() -> None:
    personas = usable_personas(
        account_roles=["organizer", "reviewer", "speaker"],
        profile_complete=True,
        manages_organization=False,
    )

    assert [(role, resolution.path) for role, resolution in personas] == [
        ("reviewer", "/reviews"),
        ("speaker", "/speaker"),
    ]

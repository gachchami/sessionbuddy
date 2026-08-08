import pytest

from sessionbuddy.platform.auth.http import authorization_denial_status


@pytest.mark.parametrize(
    "reason",
    [
        "resource_not_found",
        "tenant_membership_required",
        "ownership_required",
        "assignment_required",
    ],
)
def test_record_scope_denials_are_non_disclosing(reason: str) -> None:
    assert authorization_denial_status(reason) == 404


@pytest.mark.parametrize(
    "reason",
    ["permission_not_granted", "inactive_principal", "lifecycle_forbidden"],
)
def test_known_non_resource_denials_remain_forbidden(reason: str) -> None:
    assert authorization_denial_status(reason) == 403


def test_unknown_denial_reason_fails_closed_without_hiding_typo() -> None:
    assert authorization_denial_status("unexpected_policy_reason") == 403

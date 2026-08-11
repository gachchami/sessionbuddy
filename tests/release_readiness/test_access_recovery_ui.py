from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_account_organization_access_uses_exact_grant_contract_and_scope_copy() -> None:
    markup = (STATIC / "account.html").read_text(encoding="utf-8")
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert "Manage organization details and exact organization access" in markup
    assert "Organization access" in javascript
    assert "They do not grant, revoke, or change access to any event." in javascript
    assert "data-organization-grant-create" in javascript
    assert "/access-grants`" in javascript
    assert "/access-grants/${encodeURIComponent(grant.user_id)}`" in javascript
    assert 'method: "POST"' in javascript
    assert 'method: "PATCH"' in javascript
    assert 'method: "DELETE"' in javascript
    assert "Confirm revoke organization access" in javascript
    assert "Event access was not changed." in javascript


def test_event_ownership_transfer_is_explicit_non_cascading_and_double_confirmed() -> None:
    markup = (STATIC / "access_admin.html").read_text(encoding="utf-8")
    javascript = (STATIC / "access_admin.js").read_text(encoding="utf-8")

    assert 'id="ownership-panel"' in markup
    assert "does not delete, move, archive, or rewrite sessions" in markup
    assert 'id="ownership-confirmation"' in markup
    assert "Review ownership transfer" in markup
    assert "Confirm transfer" in markup
    assert "/ownership-transfers`" in javascript
    assert "grant_previous_owner_manage" in javascript
    assert 'method: "POST"' in javascript
    assert "(entry.permissions || []).includes(\"owner\")" in javascript
    assert "owner?.user_id === session.user_id" in javascript
    assert "[\"owner\", \"manage\"].includes" not in javascript.split(
        "function renderOwnershipTransfer", 1
    )[1].split("async function load", 1)[0]


def test_account_owner_recovery_discovery_is_narrow_paginated_and_owner_only() -> None:
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert "Event ownership recovery" in javascript
    assert "it does not grant you access to the event or its content" in javascript
    assert "Event content and other access were not changed." in javascript
    assert (
        "/ownership-recovery/events?limit=50${suffix}`"
        in javascript
    )
    assert "page.next_cursor" in javascript
    assert "Load more recovery events" in javascript
    assert "current_owner_user_id" in javascript
    assert "current_owner_email" in javascript
    assert 'loadOwnershipRecoveryEvents(organization.id, true)' in javascript
    assert '.filter((item) => (item.permissions || []).includes("owner"))' in javascript
    assert "Review ownership transfer" in javascript
    assert "Confirm transfer" in javascript

from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


def test_source_wiring_account_organizers_are_organization_scoped_and_cascade_to_events() -> None:
    markup = (STATIC / "account.html").read_text(encoding="utf-8")
    javascript = (STATIC / "organization_admin.js").read_text(encoding="utf-8")

    assert "Organization details and admins." in markup
    assert '<h4>Organizers</h4>' in javascript
    assert "can manage every event in this organization" in javascript
    assert 'body: JSON.stringify({ email })' in javascript
    assert 'permission.value = "manage"' not in javascript
    assert '>Invite admin</button>' in javascript
    assert "data-organization-grant-create" in javascript
    assert "/access-grants`" in javascript
    assert "/admin-invitations`" in javascript
    assert "/access-grants/${encodeURIComponent(grant.user_id)}`" in javascript
    assert 'method: "POST"' in javascript
    assert 'method: "DELETE"' in javascript
    assert "Confirm revoke organization access" in javascript


def test_reviewer_page_excludes_legacy_admin_and_resource_grant_controls() -> None:
    markup = (STATIC / "access_admin.html").read_text(encoding="utf-8")
    javascript = (STATIC / "access_admin.js").read_text(encoding="utf-8")

    assert "Reviewers" in markup
    assert "Accepted reviewers and invitations for this event" in markup
    assert 'role="table" aria-label="Event reviewers"' in markup
    assert 'id="reviewer-list"' in markup
    assert 'name="role" type="hidden" value="evaluator"' in markup
    assert 'value="speaker"' not in markup
    assert "event_admin" not in markup
    assert "organization_admin" not in markup
    assert "access-grants" not in javascript
    assert "ownership-transfers" not in javascript
    assert "/evaluators?email=" in javascript
    assert 'invitation.role === "evaluator"' in javascript
    assert 'method: "POST"' in javascript
    assert "Confirm revoke reviewer eligibility" in javascript
    assert 'visible.map(invitationRow)' in javascript
    assert '["accepted", "pending"].includes(invitation.status)' in javascript
    assert 'method: "DELETE"' in javascript


def test_source_wiring_account_has_no_event_ownership_recovery_surface() -> None:
    javascript = (STATIC / "account.js").read_text(encoding="utf-8")

    assert "Event ownership recovery" not in javascript
    assert "/ownership-recovery/events" not in javascript
    assert "current_owner_user_id" not in javascript
    assert "current_owner_email" not in javascript
    assert "data-event-owner-transfer" not in javascript

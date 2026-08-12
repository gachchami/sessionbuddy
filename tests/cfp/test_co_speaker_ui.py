from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


def test_public_cfp_collects_repeatable_distinct_co_speaker_contacts() -> None:
    page = (STATIC / "public_cfp.html").read_text(encoding="utf-8")
    script = (STATIC / "public_cfp.js").read_text(encoding="utf-8")

    assert 'id="co-speaker-rows"' in page
    assert 'id="add-co-speaker"' in page
    assert 'name = "co_speaker_name"' in script
    assert 'name = "co_speaker_email"' in script
    assert 'role.name = "co_speaker_role"' in script
    assert "state.form?.participant_roles" in script
    assert "role: row.querySelector('[name=\"co_speaker_role\"]')" in script
    assert "rows.length <= limit" in script
    assert "state.form?.co_speaker_limit ?? 1" in script
    assert "An additional participant must use a different email" in script
    assert "Each additional participant must use a different email" in script
    assert "const readyToReview = Boolean(restored.readyToSubmit)" in script
    assert "&& !needsFiles" in script
    assert '&& byId("proposal-form").checkValidity()' in script
    assert "showReview(readyToReview)" in script
    assert "queueMicrotask(() => byId(\"proposal-form\").requestSubmit())" not in script
    assert "validateCoSpeakers(form)" in script


def test_admin_cfp_builder_discloses_system_managed_co_speaker_question() -> None:
    page = (STATIC / "admin_programs.html").read_text(encoding="utf-8")
    script = (STATIC / "admin_programs.js").read_text(encoding="utf-8")

    assert "Additional participants are optional" in page
    assert 'make("strong", "Additional participants")' in script
    assert "Name, email, and role for each additional participant" in script
    assert 'make("span", "Optional")' in script
    assert 'make("span", "Repeatable")' in script
    assert 'name="co_speaker_limit" type="number" value="1"' in page
    assert "admin-programs.js?v=63" in page


def test_primary_speaker_can_manage_co_speaker_invitation_statuses() -> None:
    page = (STATIC / "public_cfp.html").read_text(encoding="utf-8")
    script = (STATIC / "public_cfp.js").read_text(encoding="utf-8")

    assert 'id="co-speaker-invitations"' in page
    for label in ("Pending", "Accepted", "Declined"):
        assert f'{label.lower()}: "{label}"' in script
    assert "invitation.expires_at_ms" in script
    endpoint = "/co-speakers/${encodeURIComponent(invitation.id)}"
    assert endpoint in script
    assert "`${invitationEndpoint(submission, invitation)}/resend`" in script
    assert 'method: "DELETE"' in script
    assert "!submission.editable" in script


def test_co_speaker_rows_and_statuses_have_scoped_layout_styles() -> None:
    stylesheet = (STATIC / "product.css").read_text(encoding="utf-8")

    assert ".co-speaker-row__actions" in stylesheet
    assert ".co-speaker-invitation" in stylesheet
    assert "grid-template-columns: minmax(0, 1fr) auto auto" in stylesheet


def test_duplicate_titles_warn_inline_without_blocking_and_are_disambiguated() -> None:
    cfp_script = (STATIC / "public_cfp.js").read_text(encoding="utf-8")
    portal_script = (STATIC / "speaker_portal.js").read_text(encoding="utf-8")
    admin_script = (STATIC / "admin_submissions.js").read_text(encoding="utf-8")

    assert 'input.addEventListener("blur"' in cfp_script
    assert "You already have a proposal with this title, submitted" in cfp_script
    assert "You can still use this title." in cfp_script
    assert "saveSubmissionWithDuplicateWarning" not in cfp_script
    assert "x-allow-duplicate-title" not in cfp_script
    assert "submission.submitted_at_ms" in portal_script
    assert "Receipt ${submission.id.slice(0, 8)}" in portal_script
    assert "item.submitted_at_ms" in admin_script
    assert "Receipt ${item.id.slice(0, 8)}" in admin_script


def test_speaker_portal_editor_preserves_additional_participant_roles() -> None:
    script = (STATIC / "speaker_portal.js").read_text(encoding="utf-8")

    assert 'role: row.querySelector("[data-co-speaker-role]").value' in script
    assert 'role.dataset.coSpeakerRole = "true"' in script
    assert "state.form?.participant_roles" in script
    assert 'value = entry.role || "co_speaker"' in script
    assert 'make("h4", "Additional participants")' in script


def test_clients_render_server_supplied_participant_role_labels() -> None:
    public_script = (STATIC / "public_cfp.js").read_text(encoding="utf-8")
    invitation_script = (STATIC / "co_speaker_invitation.js").read_text(
        encoding="utf-8"
    )
    admin_script = (STATIC / "admin_submissions.js").read_text(encoding="utf-8")

    assert "invitation.role_label" in public_script
    assert "invitation.role_label" in invitation_script
    assert "person.role_label" in admin_script
    assert "function participantRole" not in public_script
    assert "function participantRole" not in invitation_script


def test_fresh_schema_persists_additional_participant_roles() -> None:
    baseline = (ROOT / "migrations_baseline/0001_baseline.sql").read_text(
        encoding="utf-8"
    )

    role_check = "'co_speaker','co_author','moderator','panelist','other'"
    assert baseline.count(role_check) == 2


def test_public_invitation_page_uses_token_detail_and_response_endpoints() -> None:
    page = (STATIC / "co_speaker_invitation.html").read_text(encoding="utf-8")
    script = (STATIC / "co_speaker_invitation.js").read_text(encoding="utf-8")
    routes = (
        ROOT / "src" / "sessionbuddy" / "platform" / "auth" / "access.py"
    ).read_text(encoding="utf-8")

    assert 'id="accept-invitation"' in page
    assert 'id="decline-invitation"' in page
    assert 'id="confirm-decline"' in page
    assert "primary speaker must send a new invitation" in page
    assert 'content="noindex,nofollow"' in page
    assert "/api/v1/co-speaker-invitations/${encodeURIComponent(token)}" in script
    assert 'respond("accept")' in script
    assert 'respond("decline")' in script
    assert 'declineDialog.showModal()' in script
    assert 'declineDialog.addEventListener("close"' in script
    assert '"/co-speaker-invitations/{token}"' in routes
    assert '"/co-speaker-invitations/assets/invitation.js"' in routes

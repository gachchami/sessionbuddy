import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from sessionbuddy.api.app import app
from sessionbuddy.cfp import router as cfp_routes
from sessionbuddy.cfp.models import (
    CoSpeakerInput,
    FormFieldDefinition,
    FormPublish,
    PrivateSubmissionView,
    SubmissionCreate,
)
from sessionbuddy.cfp.router import (
    _condition_matches,
    _form_availability,
    _published_form_view,
    _submission_answer_labels,
    _timed_first,
    _validate_cfp_deadline,
    _validate_draft_schema,
    _validate_submission_schema,
    _validate_upload_answers,
    create_submission,
)
from sessionbuddy.console.models import BrowserTelemetryPayload


def test_organizer_submission_answers_keep_configured_question_labels() -> None:
    schema = json.dumps(
        {
            "fields": [
                {"key": "question_4", "type": "text", "label": "Key takeaway"},
                {"key": "question_5", "type": "select", "label": "Audience level"},
                {
                    "key": "question_6",
                    "type": "textarea",
                    "label": "Workshop prerequisites",
                },
            ]
        }
    )

    assert _submission_answer_labels(
        schema,
        {"question_4": "Framework", "question_5": "Intermediate"},
    ) == {
        "question_4": "Key takeaway",
        "question_5": "Audience level",
    }


def test_organizer_submission_label_projection_fails_closed() -> None:
    assert _submission_answer_labels("not-json", {"question_4": "Framework"}) == {}


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "result"),
    [
        ("equals", True, "true", True),
        ("equals", False, "true", False),
        ("not_equals", False, "true", True),
        ("equals", ["beginner", "advanced"], "advanced", True),
        ("not_equals", ["beginner", "advanced"], "advanced", False),
    ],
)
def test_submission_conditions_match_browser_checkbox_and_multiselect_semantics(
    operator: str, actual: object, expected: str, result: bool
) -> None:
    assert _condition_matches(operator, actual, expected) is result


def test_cfp_description_sanitizes_rich_text_and_keeps_important_dates() -> None:
    form = FormPublish(
        slug="event-cfp",
        welcome_text="Safe fallback",
        description_html=(
            '<h2>What we want</h2><p>Hello <strong>speaker</strong><script>alert(1)</script>'
            '<a href="javascript:alert(2)">bad link</a></p>'
        ),
        important_dates=[{"label": "Wave 1 decisions", "at_ms": 1_900_000_000_000}],
    )

    assert form.description_html == (
        "<h2>What we want</h2><p>Hello <strong>speaker</strong>alert(1)<a>bad link</a></p>"
    )
    assert form.important_dates[0].label == "Wave 1 decisions"


def test_cfp_description_preserves_contenteditable_block_boundaries() -> None:
    form = FormPublish(
        slug="event-cfp",
        welcome_text="Welcome",
        description_html="<div>Opening summary</div><div>Session formats</div>",
        fields=[
            {"key": "speaker_name", "type": "text", "label": "Name", "required": True},
            {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
            {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
            {"key": "proposal_abstract", "type": "textarea", "label": "Abstract", "required": True},
        ],
    )

    assert form.description_html == "<p>Opening summary</p><p>Session formats</p>"


class CloudflareFirstStatement:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def first(self, *args: object):
        self.calls.append(args)
        if args == (None,):
            raise RuntimeError("D1_COLUMN_NOTFOUND: Column not found (null)")
        return {"id": "event-1"} if not args else "event-1"


class AllowingRateLimiter:
    async def limit(self, options: dict[str, str]) -> dict[str, bool]:
        assert options["key"]
        return {"success": True}


class UploadValidationDatabase:
    def prepare(self, _query: str):
        raise AssertionError("inactive or missing upload validation must not query storage")


async def test_hidden_required_upload_is_not_validated() -> None:
    schema = {
        "fields": [
            {"key": "format", "type": "select", "required": True},
            {"key": "deck", "type": "file", "required": True},
        ],
        "conditions": [
            {
                "source_key": "format",
                "operator": "equals",
                "value": "Workshop",
                "target_key": "deck",
            }
        ],
    }

    await _validate_upload_answers(
        UploadValidationDatabase(),
        schema,
        {"format": "Talk", "deck": ""},
        form_id="form-a",
        event_id="event-a",
        user_id="user-a",
    )


async def test_visible_required_upload_error_names_the_field() -> None:
    schema = {
        "fields": [{"key": "deck", "type": "file", "required": True}],
        "conditions": [],
    }

    with pytest.raises(HTTPException) as raised:
        await _validate_upload_answers(
            UploadValidationDatabase(),
            schema,
            {"deck": ""},
            form_id="form-a",
            event_id="event-a",
            user_id="user-a",
        )

    assert raised.value.status_code == 422
    assert raised.value.detail == {
        "field": "deck",
        "message": "Upload this file before submitting.",
    }


async def test_timed_first_omits_the_column_argument_for_full_rows() -> None:
    request = SimpleNamespace(state=SimpleNamespace(timings={}))
    statement = CloudflareFirstStatement()

    row = await _timed_first(request, statement)
    identifier = await _timed_first(request, statement, "id")

    assert row == {"id": "event-1"}
    assert identifier == "event-1"
    assert statement.calls == [(), ("id",)]
    assert request.state.timings["db"] >= 0


def test_cfp_write_models_are_strict_and_bounded() -> None:
    with pytest.raises(ValidationError):
        FormPublish(slug="Not A Slug", welcome_text="Welcome")
    with pytest.raises(ValidationError):
        FormPublish(
            slug="unsafe-core",
            welcome_text="Welcome",
            fields=[
                {"key": "speaker_name", "type": "text", "label": "Name"},
                {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
                {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
                {
                    "key": "proposal_abstract",
                    "type": "textarea",
                    "label": "Abstract",
                    "required": True,
                },
            ],
        )
    with pytest.raises(ValidationError):
        SubmissionCreate(
            speaker_name="Speaker",
            proposal_title="Title",
            proposal_abstract="Abstract",
            unexpected="private data",
        )

    telemetry = BrowserTelemetryPayload(
        schema_version=1,
        page_template="/admin/events/{event_id}/cfp",
        navigation_type="navigate",
        device_class="desktop",
        sampled=False,
        critical_api_ms=25,
    )
    assert telemetry.page_template == "/admin/events/{event_id}/cfp"


def test_cfp_deadline_must_be_strictly_before_event_start() -> None:
    _validate_cfp_deadline(None, 10_000)
    _validate_cfp_deadline(9_999, 10_000)
    for invalid in (10_000, 10_001):
        with pytest.raises(HTTPException) as rejected:
            _validate_cfp_deadline(invalid, 10_000)
        assert rejected.value.status_code == 422
        assert "before the event starts" in str(rejected.value.detail)
    router = (Path(__file__).parents[2] / "src/sessionbuddy/cfp/router.py").read_text()
    assert router.count("_validate_cfp_deadline(body.closes_at_ms") == 2


def test_event_owned_cfp_builder_has_no_program_creation_step() -> None:
    static = Path(__file__).parents[2] / "src/sessionbuddy/static"
    page = (static / "admin_programs.html").read_text()
    script = (static / "admin_programs.js").read_text()
    assert "Program name" not in page
    assert "Create program" not in page
    assert 'id="program-result"' not in page
    assert 'placeholder="Tell speakers what kinds of proposals' in page
    assert 'id="cfp-routing"' in page
    assert 'id="add-field"' in page
    assert page.index('id="add-field"') < page.index('id="form-fields"')
    assert ".cfp-editor-section--single-question > #add-field" not in (
        static / "product.css"
    ).read_text()
    assert 'const add = byId("add-field")' in script
    assert 'make("button", "Done editing question")' in script
    assert 'state.selectedOutline = "custom"' in script
    assert "state.collapsedFieldKeys.add(field.key)" in script
    assert 'byId("add-field").focus()' in script
    assert "state.program" not in script
    assert "toLocalInput(state.eventStartsAtMs - 1)" in script
    assert "The Call for Proposals must close before the event starts." in script
    assert 'summaryIdentity.append(make("strong", field.label))' in script
    assert 'if (!system) summaryIdentity.append(make("small", field.key))' in script


def test_cfp_builder_uses_configurable_formats_for_display_rules() -> None:
    script = (
        Path(__file__).parents[2] / "src/sessionbuddy/static/admin_programs.js"
    ).read_text()

    for session_format in (
        "Keynote (45 min)",
        "Talk (30 min)",
        "Lightning Talk (10 min)",
        "Workshop (120 min)",
        "Panel (45 min)",
    ):
        assert session_format in script
    assert 'label: "Session format"' in script
    assert '"Session formats",' in script
    assert 'conditionQuestion.addEventListener("change", () => {' in script
    assert 'conditionWarning.setAttribute("role", "alert")' in script
    assert 'renderConditionAnswer();' in script
    assert 'state.fields[index].choices = choices.value' in script
    assert 'choiceConditionValue.name = "condition_value"' in script
    assert 'choiceConditionValue.disabled = true' in script
    assert 'const option = new Option(candidate.label, candidate.label)' in script
    assert 'option.dataset.sourceKey = candidate.key' in script
    assert 'sourceControl?.selectedOptions[0]?.dataset.sourceKey' in script
    assert "const eventFields = [];" in script
    assert "const customFields = [];" in script
    assert "for (const option of [...eventFields, ...customFields])" in script


def test_form_conditions_accept_configured_format_and_reject_unknown_choice() -> None:
    fields = [
        {"key": "speaker_name", "type": "text", "label": "Name", "required": True},
        {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
        {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
        {
            "key": "proposal_abstract",
            "type": "textarea",
            "label": "Abstract",
            "required": True,
        },
        {
            "key": "session_type",
            "type": "select",
            "label": "Session format",
            "required": True,
            "choices": ["Talk (30 min)", "Workshop (120 min)"],
        },
        {
            "key": "workshop_prerequisites",
            "type": "textarea",
            "label": "Workshop prerequisites",
        },
    ]
    valid = FormPublish(
        slug="format-rules",
        welcome_text="Welcome",
        fields=fields,
        conditions=[
            {
                "source_key": "session_type",
                "operator": "equals",
                "value": "Workshop (120 min)",
                "target_key": "workshop_prerequisites",
            }
        ],
    )
    assert valid.conditions[0].value == "Workshop (120 min)"

    invalid = valid.model_dump()
    invalid["conditions"][0]["value"] = "Workshop (90 min)"
    with pytest.raises(ValidationError, match="configured source choice"):
        FormPublish.model_validate(invalid)


def test_form_conditions_only_reference_earlier_fields() -> None:
    with pytest.raises(ValidationError, match="earlier field"):
        FormPublish(
            slug="forward-rule",
            welcome_text="Welcome",
            fields=[
                {"key": "speaker_name", "type": "text", "label": "Name", "required": True},
                {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
                {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
                {
                    "key": "proposal_abstract",
                    "type": "textarea",
                    "label": "Abstract",
                    "required": True,
                },
                {"key": "dependent", "type": "textarea", "label": "Dependent"},
                {
                    "key": "later_source",
                    "type": "select",
                    "label": "Later source",
                    "choices": ["Yes", "No"],
                },
            ],
            conditions=[
                {
                    "source_key": "later_source",
                    "operator": "equals",
                    "value": "Yes",
                    "target_key": "dependent",
                }
            ],
        )


def test_form_co_speaker_limit_defaults_to_one_and_is_enforced() -> None:
    form = FormPublish(slug="speaker-limit", welcome_text="Welcome")
    assert form.co_speaker_limit == 1

    submission = SubmissionCreate(
        speaker_name="Primary",
        speaker_email="primary@example.test",
        proposal_title="Proposal",
        proposal_abstract="Abstract",
        co_speakers=[
            {"display_name": "One", "email": "one@example.test"},
            {"display_name": "Two", "email": "two@example.test"},
        ],
    )
    with pytest.raises(HTTPException) as exc_info:
        _validate_submission_schema(
            {"fields": [field.model_dump() for field in form.fields], "co_speaker_limit": 1},
            submission,
        )
    assert exc_info.value.status_code == 422


def test_additional_participant_roles_are_bounded() -> None:
    for role in ("co_speaker", "co_author", "moderator", "panelist", "other"):
        participant = CoSpeakerInput(
            display_name="Participant", email="participant@example.test", role=role
        )
        assert participant.role == role
    with pytest.raises(ValidationError):
        CoSpeakerInput(
            display_name="Participant",
            email="participant@example.test",
            role="organizer",
        )


def test_private_submission_access_distinguishes_primary_and_co_speaker() -> None:
    base = {
        "id": "submission-1",
        "speaker_name": "Primary Speaker",
        "speaker_email": "primary@example.com",
        "proposal_title": "Private proposal",
        "proposal_abstract": "Only named speakers may view this proposal.",
        "answers": {},
        "status": "submitted",
        "submitted_at_ms": 1,
        "version": 1,
    }
    assert PrivateSubmissionView(**base, editable=True).editable is True
    assert PrivateSubmissionView(**base, editable=False).editable is False
    with pytest.raises(ValidationError):
        PrivateSubmissionView(**base, editable=True, unexpected_private_field="private-value")
    router = (Path(__file__).parents[2] / "src/sessionbuddy/cfp/router.py").read_text()
    private_list = router.split("async def list_my_submissions", 1)[1].split(
        "@cfp_router.patch(", 1
    )[0]
    assert "s.submitter_user_id=?2 OR EXISTS" in private_list
    assert "c.normalized_email=?3" in private_list
    assert "AND s.status='submitted'" in private_list
    assert "AND d.submission_id IS NULL" in private_list
    primary_patch = router.split("async def update_submission(", 1)[1].split(
        "@cfp_router.patch(", 1
    )[0]
    assert 'str(row["submitter_user_id"] or "") != authenticated.actor.user_id' in primary_patch
    assert "raise HTTPException(status_code=404)" in primary_patch
    assert "return await _editable_submission_by_id" in primary_patch
    public_script = (
        Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.js"
    ).read_text()
    assert "const editable = submission.editable === true" in public_script
    assert "state.editingSubmission && saved?.submissionId === selected.id" in public_script
    assert 'error.code === "decision_conflict"' in public_script
    assert "Only the primary submitter can make changes." in public_script
    assert "The call for proposals is closed, so this proposal is read-only." in public_script
    assert 'submission.status === "withdrawn"' in public_script
    assert "state.submissions.find((submission) => submission.id === selectedId)" in public_script
    assert 'make("h2", "Your proposals")' not in public_script
    assert 'make("a", "Open in My proposals", "button")' not in public_script
    assert 'make("a", "Back to speaker portal", "button")' in public_script
    assert "const workspacePath" not in public_script


def test_admin_submission_inbox_links_open_and_closed_rounds_for_decisions() -> None:
    root = Path(__file__).parents[2]
    router = (root / "src/sessionbuddy/cfp/router.py").read_text()
    script = (root / "src/sessionbuddy/static/admin_submissions.js").read_text()

    admin_list = router.split("async def list_submissions(", 1)[1].split(
        "@cfp_router", 1
    )[0]
    assert "er.id AS evaluation_round_id" in admin_list
    assert "candidate.status!='draft'" in admin_list
    assert "candidate.status='open'" not in admin_list
    assert "a.status!='revoked'" in admin_list
    assert "item.status === \"submitted\" && item.evaluation_round_id" in script
    assert "Open ${item.evaluation_round_name || \"evaluation round\"} to decide" in script
    assert 'error.code === "round_conflict"' in script


def test_duplicate_title_lookup_is_owned_and_not_limited_to_recent_submissions() -> None:
    router = (
        Path(__file__).parents[2] / "src/sessionbuddy/cfp/router.py"
    ).read_text()
    lookup = router.split("async def find_my_submission_by_title", 1)[1].split(
        "@cfp_router", 1
    )[0]

    assert "s.submitter_user_id=?2" in lookup
    assert "lower(trim(s.proposal_title))=lower(trim(?3))" in lookup
    assert "LIMIT 1" in lookup
    assert "LIMIT 25" not in lookup


def test_cfp_contributors_have_an_explicit_role_and_edits_save_the_submission() -> None:
    submission = SubmissionCreate(
        speaker_name="Primary Speaker",
        speaker_email="primary@example.com",
        proposal_title="A proposal",
        proposal_abstract="A useful abstract",
        co_speakers=[{"display_name": "Co Speaker", "email": "co@example.com"}],
    )
    assert submission.co_speakers[0].role == "co_speaker"

    script = (Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.js").read_text()
    assert 'role.name = "co_speaker_role"' in script
    assert "state.form?.participant_roles" in script
    assert "if (state.editingSubmission)" in script
    assert 'method: "PATCH"' in script


def test_public_cfp_offers_password_and_email_link_sign_in() -> None:
    static = Path(__file__).parents[2] / "src/sessionbuddy/static"
    page = (static / "public_cfp.html").read_text()
    script = (static / "public_cfp.js").read_text()

    assert 'name="password" type="password" autocomplete="current-password"' in page
    assert 'id="cfp-password-sign-in"' in page
    assert 'id="cfp-send-sign-in-link"' in page
    assert '"/api/v1/auth/password/sign-in"' in script
    assert '"/api/v1/auth/magic-links"' in script


def test_public_cfp_formats_event_dates_in_the_event_time_zone() -> None:
    script = (Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.js").read_text()

    assert 'const timeZone = form.event_time_zone || "UTC"' in script
    assert 'year: "numeric", timeZone' in script


def test_dynamic_form_conditions_skip_hidden_required_fields() -> None:
    schema = FormPublish(
        slug="conditional-cfp",
        welcome_text="Welcome",
        fields=[
            {"key": "speaker_name", "type": "text", "label": "Name", "required": True},
            {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
            {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
            {
                "key": "proposal_abstract",
                "type": "textarea",
                "label": "Abstract",
                "required": True,
            },
            {
                "key": "format",
                "type": "select",
                "label": "Format",
                "choices": ["talk", "workshop"],
            },
            {"key": "materials", "type": "url", "label": "Materials", "required": True},
        ],
        conditions=[
            {
                "source_key": "format",
                "operator": "equals",
                "value": "workshop",
                "target_key": "materials",
            }
        ],
    ).model_dump(mode="json")
    submission = SubmissionCreate(
        speaker_name="Speaker",
        speaker_email="speaker@example.com",
        proposal_title="Title",
        proposal_abstract="Abstract",
        answers={"format": "talk"},
    )

    _validate_submission_schema(schema, submission)


def test_track_question_accepts_an_event_with_one_track() -> None:
    field = FormFieldDefinition(
        key="track",
        type="select",
        label="Track",
        required=True,
        choices=["Main stage"],
    )

    assert field.choices == ("Main stage",)


def test_public_cfp_defers_authentication_until_final_submission() -> None:
    script = (Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.js").read_text()
    page = (Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.html").read_text()

    assert 'byId("proposal-card").hidden = false' in script
    assert "saveBrowserDraft(true)" in script
    assert "saveBrowserDraft(false)" in script
    assert 'byId("proposal-form").checkValidity()' in script
    assert "Email verified. Review your restored proposal, then confirm submission." in script
    assert "form_slug: slug, redirect_path: location.pathname" in script
    assert "if (!state.authenticated)" in script
    assert '<h2 id="sign-in-title">Sign in</h2>' in page
    assert 'for="cfp-sign-in-email"' in page
    assert 'id="cfp-sign-in-email" name="email"' in page
    assert "Email me a signup link" in page
    assert 'aria-describedby="cfp-signup-help"' in page
    assert "Sign in to submit" not in page


def test_public_cfp_shows_the_form_to_a_visitor_who_is_not_signed_in() -> None:
    """A signed-out visitor reads the real questions, not only a sign-in card.

    ``GET /api/v1/forms/{slug}`` is public and already returns every field, its
    conditional logic, and its routing rules, so the page has nothing left to
    fetch before it can render them. Deferring the account to the final step is
    the documented intent of the test above; hiding the form on the 401 from the
    session lookup was the one thing contradicting it, and it left a speaker
    unable to judge what the call asks for before creating an account.

    A proposal workspace URL is the exception. It names one stored submission,
    so without a session there is nothing to show and the sign-in card stands
    alone.
    """
    script = (Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.js").read_text()
    page = (Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.html").read_text()

    assert 'byId("proposal-card").hidden = workspaceMode' in script
    assert 'byId("proposal-card").hidden = true' not in script
    assert 'byId("preview-note").hidden = workspaceMode' in script
    assert 'id="preview-note"' in page
    # The sign-in card remains the other way in. It no longer replaces the form.
    assert 'byId("sign-in-card").hidden = false' in script
    # Never submit an authentication form for the visitor: the password field may
    # be empty by right, and an unprompted one-time link would mail whichever
    # address the proposal happens to carry.
    assert "signIn.requestSubmit()" not in script


def test_cfp_summary_excludes_conditional_questions_until_they_apply() -> None:
    script = (Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.js").read_text()

    assert "conditionalTargets" in script
    assert "!conditionalTargets.has(field.key)" in script
    assert (
        "Additional questions may appear based on your answers."
        in (Path(__file__).parents[2] / "src/sessionbuddy/static/public_cfp.html").read_text()
    )


def test_organizer_can_view_but_cannot_edit_speaker_proposals() -> None:
    router = (Path(__file__).parents[2] / "src/sessionbuddy/cfp/router.py").read_text()
    admin = (Path(__file__).parents[2] / "src/sessionbuddy/static/admin_submissions.js").read_text()

    assert '"/api/v1/admin/submissions/{submission_id}"' not in router
    assert "Edit proposal" not in admin
    assert "Run AI first pass" not in admin
    assert "View proposal" in admin
    assert "create-proposal-link" in admin


def test_required_dynamic_answers_are_enforced_by_the_backend() -> None:
    schema = FormPublish(
        slug="server-validation",
        welcome_text="Welcome",
        fields=[
            {"key": "speaker_name", "type": "text", "label": "Name", "required": True},
            {"key": "speaker_email", "type": "email", "label": "Email", "required": True},
            {"key": "proposal_title", "type": "text", "label": "Title", "required": True},
            {
                "key": "proposal_abstract",
                "type": "textarea",
                "label": "Abstract",
                "required": True,
            },
            {"key": "terms", "type": "checkbox", "label": "Agree", "required": True},
            {"key": "notes", "type": "text", "label": "Notes", "required": True},
        ],
    ).model_dump(mode="json")
    base = {
        "speaker_name": "Speaker",
        "speaker_email": "speaker@example.com",
        "proposal_title": "Title",
        "proposal_abstract": "Abstract",
    }
    with pytest.raises(HTTPException) as unchecked:
        _validate_submission_schema(
            schema,
            SubmissionCreate(**base, answers={**base, "terms": False, "notes": "Ready"}),
        )
    assert unchecked.value.status_code == 422
    with pytest.raises(HTTPException) as whitespace:
        _validate_submission_schema(
            schema,
            SubmissionCreate(**base, answers={**base, "terms": True, "notes": "   "}),
        )
    assert whitespace.value.status_code == 422


def test_drafts_allow_incomplete_answers_but_reject_invalid_types_and_sizes() -> None:
    schema = FormPublish(slug="draft-validation", welcome_text="Welcome").model_dump(mode="json")
    _validate_draft_schema(schema, {"proposal_title": ""})
    with pytest.raises(HTTPException) as invalid:
        _validate_draft_schema(schema, {"speaker_email": "not-an-email"})
    assert invalid.value.status_code == 422


def test_published_form_uses_default_accent_for_pre_branding_events() -> None:
    form = _published_form_view(
        {
            "id": "form-1",
            "event_id": "event-1",
            "event_name": "Legacy Event",
            "accent_color": None,
            "logo_url": None,
            "version": 1,
            "slug": "legacy-event",
            "welcome_text": "Welcome",
            "schema_json": json.dumps({"fields": [], "conditions": [], "routing_rules": []}),
            "opens_at_ms": None,
            "closes_at_ms": None,
            "submission_limit": None,
            "success_title": "Proposal received",
            "success_message": "Thank you.",
            "redirect_to_portal": 1,
            "submissions_received": 0,
        },
        now_ms=1,
    )

    assert form.accent_color == "#3159d9"
    assert "program_id" not in form.model_dump()


def test_proposal_limit_is_per_speaker_not_a_global_cfp_cap() -> None:
    accepting, message = _form_availability(
        {
            "opens_at_ms": None,
            "closes_at_ms": None,
            "submission_limit": 3,
        },
        submissions_received=30,
        now_ms=1,
    )

    assert accepting is True
    assert message == "Applications are open."

    source = (
        Path(__file__).parents[2] / "src" / "sessionbuddy" / "cfp" / "router.py"
    ).read_text(encoding="utf-8")
    create = source.split("async def create_submission", 1)[1].split(
        "SUBMISSIONS_PAGE_LIMIT", 1
    )[0]
    assert "WHERE form_id=?1 AND submitter_user_id=?2 AND status='submitted'" in create
    assert "WHERE form_id=?4 AND submitter_user_id=?11 AND status='submitted'" in create
    assert "You have reached the proposal limit for this Call for Proposals." in create


async def test_admin_routes_require_authentication_outside_local() -> None:
    environment = SimpleNamespace(
        APP_ENV="development",
        DB=object(),
        SESSION_HMAC_KEY="s" * 32,
        CSRF_HMAC_KEY="c" * 32,
    )

    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="http://test"
    ) as client:
        admin = await client.post(
            "/api/v1/admin/events/22222222-2222-4222-8222-222222222222/cfp/publish",
            headers={"Idempotency-Key": "x" * 16},
            json={
                "slug": "example-event",
                "welcome_text": "Welcome",
            },
        )
        workspace = await client.get(
            "/api/v1/admin/events/22222222-2222-4222-8222-222222222222/cfp"
        )

    assert admin.status_code == 401
    assert workspace.status_code == 401


async def test_local_admin_route_requires_authenticated_session() -> None:
    environment = SimpleNamespace(
        APP_ENV="local",
        DB=object(),
        SESSION_HMAC_KEY="s" * 32,
        CSRF_HMAC_KEY="c" * 32,
    )

    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/admin/events/22222222-2222-4222-8222-222222222222/cfp/publish",
            headers={"Idempotency-Key": "x" * 16},
            json={
                "slug": "example-event",
                "welcome_text": "Welcome",
            },
        )

    assert response.status_code == 401


async def test_final_cfp_submission_requires_verified_session() -> None:
    environment = SimpleNamespace(
        APP_ENV="production",
        DB=object(),
        SESSION_HMAC_KEY="s" * 32,
        CSRF_HMAC_KEY="c" * 32,
        RATE_LIMIT_HMAC_KEY="r" * 32,
        PUBLIC_RATE_LIMITER=AllowingRateLimiter(),
    )

    async def inject_environment(scope, receive, send):
        scope["env"] = environment
        await app(scope, receive, send)

    async with AsyncClient(
        transport=ASGITransport(app=inject_environment), base_url="https://test"
    ) as client:
        response = await client.post(
            "/api/v1/forms/example-event/submissions",
            headers={
                "Idempotency-Key": "x" * 16,
                "X-Public-Session-ID": "browser-session-12345",
            },
            json={
                "speaker_name": "Ada Speaker",
                "speaker_email": "ada@example.com",
                "proposal_title": "Safe final submission",
                "proposal_abstract": "Authentication happens only after form completion.",
                "answers": {},
            },
        )

    assert response.status_code == 401


def test_public_submission_uses_csrf_guard_and_server_controlled_rate_limit_key() -> None:
    source = (
        Path(__file__).parents[2] / "src/sessionbuddy/cfp/router.py"
    ).read_text(encoding="utf-8")
    create = source.split("async def create_submission", 1)[1].split(
        "SUBMISSIONS_PAGE_LIMIT", 1
    )[0]

    assert "guard_mutation(request, authenticated.session_id)" in create
    assert 'subject=f"{authenticated.actor.user_id}:{_request_source(request)}"' in create
    assert 'subject=f"{public_session}:{_request_source(request)}"' not in create


async def test_public_submission_guard_and_rate_subject_ignore_rotated_browser_id(
    monkeypatch,
) -> None:
    authenticated = SimpleNamespace(
        session_id="session-id",
        actor=SimpleNamespace(user_id="speaker-id"),
    )
    guarded: list[str] = []
    subjects: list[str] = []

    async def authenticate(_request):
        return authenticated

    async def rate_limit(_request, **kwargs):
        subjects.append(kwargs["subject"])
        raise RuntimeError("stop before persistence")

    monkeypatch.setattr(cfp_routes, "authenticate_request", authenticate)
    monkeypatch.setattr(
        cfp_routes,
        "guard_mutation",
        lambda _request, session_id: guarded.append(session_id),
    )
    monkeypatch.setattr(cfp_routes, "enforce_rate_limit", rate_limit)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/forms/example/submissions",
            "headers": [],
            "client": ("192.0.2.10", 1234),
        }
    )
    body = SubmissionCreate(
        speaker_name="Speaker",
        speaker_email="speaker@example.test",
        proposal_title="Proposal",
        proposal_abstract="Abstract",
    )

    for public_session in ("a" * 16, "b" * 16):
        with pytest.raises(RuntimeError, match="stop before persistence"):
            await create_submission(
                "example",
                request,
                body,
                idempotency_key="i" * 16,
                public_session=public_session,
            )

    assert guarded == ["session-id", "session-id"]
    assert subjects == ["speaker-id:192.0.2.10", "speaker-id:192.0.2.10"]


async def test_product_pages_are_separate_safe_surfaces() -> None:
    static = Path(__file__).parents[2] / "src/sessionbuddy/static"
    submissions_source = (static / "admin_submissions.js").read_text()
    cfp_source = (static / "admin_programs.js").read_text()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        admin = await client.get("/admin/events/22222222-2222-4222-8222-222222222222/cfp")
        public = await client.get("/cfp/example-event")
        submissions = await client.get(
            "/admin/events/22222222-2222-4222-8222-222222222222/submissions"
        )
        admin_js = await client.get("/product/assets/admin-programs.js")
        public_js = await client.get("/product/assets/public-cfp.js")
        submissions_js = await client.get("/product/assets/admin-submissions.js")
        sign_in = await client.get("/sign-in")
        setup = await client.get("/setup")
        setup_css = await client.get("/setup/assets/setup.css")
        setup_js = await client.get("/setup/assets/setup.js")
        auth_link_confirm_js = await client.get("/auth/assets/auth-link-confirm.js")
        access = await client.get("/admin/events/22222222-2222-4222-8222-222222222222/access")
        events = await client.get("/admin/events")
        events_js = await client.get("/admin/events/assets/events.js")
        admin_home = await client.get("/admin")
        event_overview = await client.get("/admin/events/22222222-2222-4222-8222-222222222222")
        speaker_directory = await client.get("/admin/people")
        account = await client.get("/account")
        app_shell_js = await client.get("/app-shell/assets/app-shell.js")
        css = await client.get("/product/assets/product.css")

    assert {admin.status_code, public.status_code, submissions.status_code, css.status_code} == {
        200
    }
    assert sign_in.status_code == access.status_code == events.status_code == 200
    assert setup.status_code == setup_css.status_code == setup_js.status_code == 200
    assert auth_link_confirm_js.status_code == 200
    assert auth_link_confirm_js.headers["content-type"].startswith("text/javascript")
    assert "form.requestSubmit()" not in auth_link_confirm_js.text
    assert "window.history.replaceState" in auth_link_confirm_js.text
    assert 'window.location.hash.slice(1)' in auth_link_confirm_js.text
    assert "Create the first organization and administrator" in setup.text
    assert {
        admin_home.status_code,
        event_overview.status_code,
        speaker_directory.status_code,
        account.status_code,
        app_shell_js.status_code,
    } == {200}
    assert "Email me a sign-in link" in sign_in.text
    assert "Evaluation team" in access.text
    assert "Reviewers" in access.text
    assert "All events" in events.text
    assert "Edit organization name" not in events.text
    assert "Organization settings" in account.text
    assert "Create active event" in events.text
    assert "data-auth-shell" in events.text
    assert "Call for Proposals" in admin.text
    assert "Share your CFP" in admin.text
    assert "data-auth-shell" in admin.text
    assert "Submit a proposal" in public.text
    assert "Proposals" in submissions.text
    assert 'id="submission-detail"' in submissions.text
    for javascript in (admin_js.text, public_js.text, submissions_js.text):
        assert "innerHTML" not in javascript
        assert "__sessionbuddyTelemetryDraft" in javascript
    assert 'page_template: "/admin/events/{event_id}/cfp"' in admin_js.text
    assert 'const list = byId("form-fields")' in admin_js.text
    assert "conditions" in admin_js.text
    assert "/admin/events/${encodeURIComponent(event.id)}" in events_js.text
    assert 'button("Edit"' in events_js.text
    assert 'page_template: "/cfp/{event_key}/{slug}"' in public_js.text
    assert "const form = event.currentTarget" in public_js.text
    assert "event.currentTarget.querySelectorAll" not in public_js.text
    assert 'page_template: "/admin/events/{event_id}/submissions"' in submissions_source
    assert "/api/v1/admin/events/${encodeURIComponent(eventId)}/submissions" in submissions_source
    assert (
        "/api/v1/admin/events/${encodeURIComponent(state.context.event_id)}/cfp/publish"
        in cfp_source
    )
    assert 'formElement.getAttribute("aria-busy") === "true"' in cfp_source
    assert 'error.code === "slug_conflict"' in cfp_source
    assert 'error.code === "stale_conflict"' in cfp_source
    assert "View proposal" in submissions_js.text
    assert "item.answers" in submissions_js.text
    assert 'location.pathname.startsWith("/admin") && !organizer' in app_shell_js.text
    assert 'location.replace("/speaker")' in app_shell_js.text
    assert "@media (max-width: 48rem)" in css.text


def test_organizer_proposal_listing_resolves_the_speakers_company() -> None:
    """A proposal has no company of its own, so the listing resolves one.

    The organizer view could show everything the form asked for and still not answer
    "who does this person work for", because company lives on the account and on the
    organization's person record, never on the submission. Resolving it at read time
    keeps that true: no new column, no backfill, and no second copy to drift.

    Both sources are tenant-scoped. ``people`` is unique per ``(organization_id,
    user_id)``, so the join cannot multiply a submission into several rows, and a
    person record owned by another organization is never consulted. The account value
    wins because the proposal is the speaker's own artifact and their account is where
    they maintain their own affiliation; the curated person record is the fallback.
    """
    router = (Path(__file__).parents[2] / "src/sessionbuddy/cfp/router.py").read_text()
    models = (Path(__file__).parents[2] / "src/sessionbuddy/cfp/models.py").read_text()

    assert "speaker_company: str | None = Field(default=None, max_length=200)" in models
    assert "LEFT JOIN users author ON author.id=s.submitter_user_id" in router
    assert "LEFT JOIN people person ON person.user_id=s.submitter_user_id" in router
    assert "AND person.organization_id=s.organization_id" in router
    assert "AND person.archived_at_ms IS NULL" in router
    assert "NULLIF(TRIM(author.company),'')" in router
    assert "NULLIF(TRIM(person.company),'')" in router
    assert "AS speaker_company" in router


def test_speaker_company_sql_prefers_account_then_scoped_person_fallback() -> None:
    """Prove precedence and tenant isolation using SQLite's production SQL semantics."""
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """CREATE TABLE users (id TEXT PRIMARY KEY, company TEXT);
           CREATE TABLE submissions (
             id TEXT PRIMARY KEY, submitter_user_id TEXT, organization_id TEXT
           );
           CREATE TABLE people (
             user_id TEXT, organization_id TEXT, company TEXT, archived_at_ms INTEGER
           );
           INSERT INTO users VALUES ('account-wins', 'Account Co'), ('fallback', '');
           INSERT INTO submissions VALUES
             ('submission-1', 'account-wins', 'org-a'),
             ('submission-2', 'fallback', 'org-a');
           INSERT INTO people VALUES
             ('account-wins', 'org-a', 'Person Co', NULL),
             ('fallback', 'org-b', 'Wrong Tenant Co', NULL),
             ('fallback', 'org-a', 'Archived Co', 123),
             ('fallback', 'org-a', 'Scoped Fallback Co', NULL);"""
    )

    rows = connection.execute(
        """SELECT s.id,
                  COALESCE(
                    NULLIF(TRIM(author.company),''),
                    NULLIF(TRIM(person.company),'')
                  ) AS speaker_company
             FROM submissions s
             LEFT JOIN users author ON author.id=s.submitter_user_id
             LEFT JOIN people person ON person.user_id=s.submitter_user_id
               AND person.organization_id=s.organization_id
               AND person.archived_at_ms IS NULL
             ORDER BY s.id"""
    ).fetchall()

    assert rows == [
        ("submission-1", "Account Co"),
        ("submission-2", "Scoped Fallback Co"),
    ]


def test_the_proposal_detail_shows_the_company_beside_the_speaker() -> None:
    javascript = (
        Path(__file__).parents[2] / "src/sessionbuddy/static/admin_submissions.js"
    ).read_text()

    assert 'detailRow("Company", item.speaker_company)' in javascript
    # Directly under the speaker it belongs to, not appended after the routing fields.
    assert (
        'detailRow("Speaker", item.speaker_name),\n'
        '      detailRow("Company", item.speaker_company),'
    ) in javascript


def test_speaker_company_reaches_the_published_contract() -> None:
    """The field is only real once both generated artifacts carry it.

    `openapi/openapi.json` is the published document and `api/openapi_contract.py`
    is the bytes the running Worker serves. A response field added without
    regenerating them leaves the app describing itself incorrectly, and the two
    artifacts can disagree with each other as well as with the code.

    Optional with a default, so it belongs in `properties` and never in `required`:
    a speaker-facing response carries `speaker_company: null`, which is not a claim
    that the speaker has no company.
    """
    import json

    root = Path(__file__).parents[2]
    document = json.loads((root / "openapi/openapi.json").read_text())
    contract = json.loads(
        __import__("sessionbuddy.api.openapi_contract", fromlist=["OPENAPI_JSON"]).OPENAPI_JSON
    )

    assert document == contract
    for name in ("SubmissionView", "PrivateSubmissionView"):
        schema = document["components"]["schemas"][name]
        assert schema["properties"]["speaker_company"] == {
            "anyOf": [{"maxLength": 200, "type": "string"}, {"type": "null"}],
            "title": "Speaker Company",
        }
        assert "speaker_company" not in schema.get("required", [])


def test_a_repeated_reviewer_reminder_replays_instead_of_failing() -> None:
    """The hourly key makes the send idempotent; it must not make the call fail.

    `deterministic_key` is unique per `(organization_id, event_id, ...)`, so a second
    reminder inside the same hour hits the constraint. Left unhandled that surfaced as
    a 5xx, which reads as a reminder that never went out -- and from the bulk nudge on
    the round ledger it aborted the loop partway down the reviewer list, silently
    skipping everyone after the collision.

    The collision is the send having already happened, so the endpoint answers with
    the message that already exists. It does not republish: that row is still queued
    and the scheduled dispatcher republishes anything that stays queued.
    """
    router = (
        Path(__file__).parents[2] / "src/sessionbuddy/evaluation/router.py"
    ).read_text()
    javascript = (
        Path(__file__).parents[2] / "src/sessionbuddy/static/admin_submissions.js"
    ).read_text()

    assert "except PersistenceError:" in router
    assert "AND event_id=?2 AND deterministic_key=?3" in router
    assert "return EvaluatorReminderQueued(message_id=str(existing))" in router
    # A write that failed for any other reason is still a failure.
    assert "if existing is None:\n            raise" in router
    # And the caller no longer lets one reviewer strand the rest of the list.
    assert "const failures = [];" in javascript
    assert "else failures.push(window.SessionBuddyApi.message(error));" in javascript
    assert "could not be reminded" in javascript

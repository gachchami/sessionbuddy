from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel

from sessionbuddy.cfp.models import FormPublish, FormSettings, SubmissionCreate
from sessionbuddy.communications.models import SpeakerMessagePreviewRequest
from sessionbuddy.competition.models import (
    AdminSessionContentUpdate,
    AdminSpeakerUpdate,
    ResourceCreate,
    SpeakerTaskCreate,
)
from sessionbuddy.evaluation.models import (
    ConflictDeclaration,
    EvaluationRoundCloseRequest,
    EvaluationRoundCreate,
    EvaluationSave,
    SubmissionDecisionCreate,
)
from sessionbuddy.platform.auth.access import (
    AccountProfileUpdate,
    BootstrapCreate,
    EventCreate,
)
from sessionbuddy.speaker_operations.models import (
    SpeakerProfileUpdate,
    UploadAuthorizationCreate,
)

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "src" / "sessionbuddy" / "static"


class TextareaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.textareas: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "textarea":
            self.textareas.append(dict(attrs))


STATIC_LONG_TEXT_LIMITS = {
    ("account.html", "description"): 1000,
    ("admin_programs.html", "description_html"): 10000,
    ("admin_programs.html", "success_message"): 2000,
    ("admin_programs.html", "confirmation_body"): 4000,
    ("admin_submissions.html", "evaluator_guidance"): 1000,
    ("agenda_admin.html", "abstract"): 5000,
    ("speaker_content.html", "body_text"): 20000,
    ("speaker_content.html", "help_text"): 2000,
    ("events_admin.html", "description"): 2000,
    ("speaker_directory.html", "biography"): 5000,
    ("speaker_messages.html", "body_text"): 10000,
}


def _textarea_key(path: Path, attrs: dict[str, str | None]) -> tuple[str, str]:
    identifier = attrs.get("id") or attrs.get("name")
    assert identifier is not None
    return path.name, identifier


def _max_length(model: type[BaseModel], field_name: str) -> int:
    metadata = model.model_fields[field_name].metadata
    limits = [item.max_length for item in metadata if hasattr(item, "max_length")]
    assert len(limits) == 1
    return limits[0]


def test_every_static_long_text_control_has_the_expected_limit_and_counter() -> None:
    found: dict[tuple[str, str], int] = {}
    excluded: set[tuple[str, str]] = set()
    for path in sorted(STATIC.glob("*.html")):
        parser = TextareaParser()
        page = path.read_text(encoding="utf-8")
        parser.feed(page)
        for attrs in parser.textareas:
            key = _textarea_key(path, attrs)
            if "readonly" in attrs or key == ("speaker_directory.html", "links"):
                excluded.add(key)
                continue
            assert "maxlength" in attrs, f"{key} has no browser-enforced limit"
            found[key] = int(attrs["maxlength"] or "0")
            assert "api-client.js" in page, f"{key} has no live-counter runtime"

    assert found == STATIC_LONG_TEXT_LIMITS
    assert excluded == {
        ("event_workspace.html", "embed-code"),
        ("speaker_directory.html", "links"),
    }


def test_shared_counter_is_live_accessible_and_handles_dynamic_controls() -> None:
    client = (STATIC / "api_client.js").read_text(encoding="utf-8")
    assert 'textarea[maxlength]:not([readonly])' in client
    assert 'counter.setAttribute("aria-live", "polite")' in client
    assert 'control.addEventListener("input"' in client
    assert 'control.setAttribute("aria-describedby"' in client
    assert "installCharacterCounters(node);" in client
    assert "refreshCharacterCounters: formValidation.installCharacterCounters" in client
    assert "of ${control.maxLength.toLocaleString()} characters" in client

def test_dynamic_javascript_and_react_textareas_match_api_limits() -> None:
    public_cfp = (STATIC / "public_cfp.js").read_text(encoding="utf-8")
    assert 'if (field.type === "textarea")' in public_cfp
    assert "input.maxLength = 5000;" in public_cfp

    speaker_portal = (STATIC / "speaker_portal.js").read_text(encoding="utf-8")
    task_textarea = speaker_portal.split('if (field.type === "textarea")', 1)[1].split(
        'else if (field.type === "select")', 1
    )[0]
    assert "input.maxLength = 4000;" in task_textarea

    submissions = (STATIC / "admin_submissions.js").read_text(encoding="utf-8")
    assert 'detailRow("Full abstract", item.proposal_abstract)' in submissions
    assert 'name === "proposal_abstract"' not in submissions

    react = (ROOT / "frontend" / "src" / "main.tsx").read_text(encoding="utf-8")
    for marker in (
        r'name="internal_comment"\s+rows=\{4\}\s+maxLength=\{5000\}',
        r'id=\{`conflict-note-\$\{assignment\.id\}`\}\s+'
        r'rows=\{3\}\s+maxLength=\{1000\}',
        r'rows=\{3\}\s+maxLength=\{2000\}\s+value=\{forceCloseReason\}',
        r'id=\{`reason-\$\{submission\.submission_id\}`\}\s+'
        r'rows=\{3\}\s+maxLength=\{2000\}',
        r'rows=\{3\}\s+maxLength=\{4000\}\s+value=\{speakerMessage\}',
    ):
        assert re.search(marker, react)


def test_api_long_text_limits_match_the_user_interface_contract() -> None:
    limits = {
        (AccountProfileUpdate, "description"): 1000,
        (BootstrapCreate, "event_description"): 2000,
        (EventCreate, "description"): 2000,
        (FormSettings, "welcome_text"): 1000,
        (FormSettings, "success_message"): 2000,
        (FormPublish, "confirmation_body"): 4000,
        (SubmissionCreate, "proposal_abstract"): 5000,
        (EvaluationRoundCreate, "evaluator_guidance"): 1000,
        (ConflictDeclaration, "explanation"): 1000,
        (EvaluationSave, "internal_comment"): 5000,
        (SubmissionDecisionCreate, "internal_reason"): 2000,
        (SubmissionDecisionCreate, "speaker_message"): 4000,
        (EvaluationRoundCloseRequest, "reason"): 2000,
        (SpeakerProfileUpdate, "biography"): 5000,
        (AdminSpeakerUpdate, "biography"): 5000,
        (AdminSessionContentUpdate, "abstract"): 5000,
        (SpeakerTaskCreate, "help_text"): 2000,
        (UploadAuthorizationCreate, "version_comment"): 1000,
        (SpeakerMessagePreviewRequest, "body_text"): 10000,
        (ResourceCreate, "body_text"): 20000,
    }
    assert {
        (model, field_name): _max_length(model, field_name)
        for model, field_name in limits
    } == limits


def test_database_schema_covers_each_persisted_long_text_contract() -> None:
    schema = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "migrations_baseline").glob("*.sql"))
    )
    for invariant in (
        "length(trim(NEW.description)) NOT BETWEEN 1 AND 2000",
        "length(welcome_text) BETWEEN 1 AND 1000",
        "length(success_message) BETWEEN 1 AND 2000",
        "length(confirmation_body) BETWEEN 1 AND 4000",
        "length(proposal_abstract) BETWEEN 1 AND 5000",
        "length(internal_comment) <= 5000",
        "length(explanation) BETWEEN 1 AND 1000",
        "length(internal_reason) <= 2000",
        "length(biography) <= 5000",
        "length(abstract) BETWEEN 1 AND 5000",
        "length(help_text) <= 2000",
        "length(trim(version_comment)) BETWEEN 1 AND 1000",
        "length(body_text) <= 20000",
        "textarea answer exceeds 5000 characters",
        "task textarea answer exceeds 4000 characters",
        "evaluation guidance exceeds 1000 characters",
        "length(NEW.html_body) NOT BETWEEN 1 AND 100000",
    ):
        assert invariant in schema

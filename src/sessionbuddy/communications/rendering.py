"""Small, deterministic template renderer with an explicit variable catalog."""

import html
import re
from collections.abc import Mapping

VARIABLE = re.compile(r"{{\s*([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\s*}}")
ALLOWED_VARIABLES = frozenset(
    {
        "event.name",
        "event.deadline",
        "speaker.name",
        "speaker.first_name",
        "submission.title",
        "portal.link",
        "task.title",
        "task.deadline",
        "schedule.start",
        "schedule.room",
    }
)
SPEAKER_MESSAGE_VARIABLES = frozenset(
    {"event.name", "speaker.name", "speaker.first_name", "submission.title", "portal.link"}
)
SPEAKER_MESSAGE_VARIABLE_LABELS = {
    "event.name": "event name",
    "speaker.name": "speaker name",
    "speaker.first_name": "speaker first name",
    "submission.title": "proposal title",
    "portal.link": "speaker portal link",
}
DECISION_MESSAGE_VARIABLES = frozenset(
    {"event.name", "speaker.name", "submission.title"}
)
TEMPLATE_VARIABLE_ALIASES = {
    "event_name": "event.name",
    "event.portal_link": "portal.link",
    "portal_link": "portal.link",
    "speaker_name": "speaker.name",
    "speaker_first_name": "speaker.first_name",
    "talk_title": "submission.title",
}


class TemplateVariableError(ValueError):
    """A stable distinction between invented and context-inapplicable tokens."""

    def __init__(self, code: str, variables: list[str], available: frozenset[str]) -> None:
        self.code = code
        self.variables = tuple(variables)
        self.available = available
        self.suggestions = {
            variable: TEMPLATE_VARIABLE_ALIASES[variable]
            for variable in variables
            if variable in TEMPLATE_VARIABLE_ALIASES
            and TEMPLATE_VARIABLE_ALIASES[variable] in available
        }
        label = "unknown" if code == "unknown_template_variable" else "unavailable here"
        valid = ", ".join(sorted(available))
        suggestion = ""
        if self.suggestions:
            replacements = ", ".join(
                f"{{{{{source}}}}} → {{{{{target}}}}}"
                for source, target in self.suggestions.items()
            )
            suggestion = f" Suggested replacement: {replacements}."
        super().__init__(
            f"{label} template variable(s): {', '.join(variables)}. "
            f"Available variables: {valid}.{suggestion}"
        )


def validate_template(
    template: str, *, allowed_variables: frozenset[str] = ALLOWED_VARIABLES
) -> frozenset[str]:
    if len(template) > 50_000:
        raise ValueError("template is too large")
    variables = frozenset(VARIABLE.findall(template))
    unknown = sorted(variables - ALLOWED_VARIABLES)
    if unknown:
        raise TemplateVariableError("unknown_template_variable", unknown, allowed_variables)
    unavailable = sorted(variables - allowed_variables)
    if unavailable:
        raise TemplateVariableError(
            "template_variable_unavailable", unavailable, allowed_variables
        )
    residue = VARIABLE.sub("", template)
    if "{{" in residue or "}}" in residue:
        raise ValueError("template contains malformed variable syntax")
    return variables


def render_template(template: str, values: Mapping[str, str]) -> str:
    required = validate_template(template)
    if any(name not in values for name in required):
        raise ValueError("template variable is missing")
    return VARIABLE.sub(lambda match: html.escape(values[match.group(1)], quote=True), template)

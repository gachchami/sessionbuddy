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


def validate_template(
    template: str, *, allowed_variables: frozenset[str] = ALLOWED_VARIABLES
) -> frozenset[str]:
    if len(template) > 50_000:
        raise ValueError("template is too large")
    variables = frozenset(VARIABLE.findall(template))
    unknown = sorted(variables - allowed_variables)
    if unknown:
        valid = ", ".join(sorted(allowed_variables))
        raise ValueError(
            f"unknown template variable(s): {', '.join(unknown)}. "
            f"Available variables: {valid}"
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

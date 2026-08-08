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
        "submission.title",
        "task.title",
        "task.deadline",
        "schedule.start",
        "schedule.room",
    }
)


def validate_template(template: str) -> frozenset[str]:
    if len(template) > 50_000:
        raise ValueError("template is too large")
    variables = frozenset(VARIABLE.findall(template))
    if variables - ALLOWED_VARIABLES:
        raise ValueError("template contains an unknown variable")
    residue = VARIABLE.sub("", template)
    if "{{" in residue or "}}" in residue:
        raise ValueError("template contains malformed variable syntax")
    return variables


def render_template(template: str, values: Mapping[str, str]) -> str:
    required = validate_template(template)
    if any(name not in values for name in required):
        raise ValueError("template variable is missing")
    return VARIABLE.sub(lambda match: html.escape(values[match.group(1)], quote=True), template)

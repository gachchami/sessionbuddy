from html.parser import HTMLParser
from typing import Literal

MessageCategory = Literal[
    "invitation", "proposal", "reminder", "decision", "schedule", "announcement", "update"
]


def message_category(deterministic_key: str, subject: str) -> MessageCategory:
    key = deterministic_key.casefold()
    lowered_subject = subject.casefold()
    if key.startswith(("identity-invitation:", "co-speaker:")):
        return "invitation"
    if key.startswith("submission-confirmation:"):
        return "proposal"
    if key.startswith("submission-decision:") or key.startswith("decision:"):
        return "decision"
    if key.startswith(
        ("task-reminder:", "evaluation-reminder:", "reminder:")
    ) or lowered_subject.startswith("reminder:"):
        return "reminder"
    if key.startswith(("schedule:", "calendar:")):
        return "schedule"
    if key.startswith("speaker-bulk:"):
        return "announcement"
    return "update"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def message_preview(html_body: str, *, limit: int = 500) -> str:
    parser = _TextExtractor()
    parser.feed(html_body)
    text = " ".join(parser.parts)
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"

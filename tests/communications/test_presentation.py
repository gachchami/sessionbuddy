import re
from pathlib import Path

from sessionbuddy.communications.presentation import message_category, message_preview


def test_message_categories_use_deterministic_semantics_before_subject_copy() -> None:
    assert message_category(
        "identity-invitation:abc", "Reminder: confirm your invitation details"
    ) == "invitation"
    assert message_category("task-reminder:task-a:2026-08-18", "Next steps") == "reminder"
    assert message_category("speaker-bulk:event-a", "Program update") == "announcement"


def test_real_reminder_and_schedule_emitters_do_not_fall_through_to_update() -> None:
    root = Path(__file__).parents[2] / "src" / "sessionbuddy"
    sources = (
        root / "agenda" / "calendar_delivery.py",
        root / "communications" / "dispatch.py",
        root / "communications" / "d1.py",
        root / "evaluation" / "router.py",
    )
    prefixes: set[str] = set()
    pattern = re.compile(r'(?:deterministic\s*=\s*|return\s+)f?"([a-z][a-z-]+):')
    for source in sources:
        prefixes.update(pattern.findall(source.read_text(encoding="utf-8")))

    assert {"calendar", "reminder", "task-reminder", "evaluation-reminder"} <= prefixes
    categories = {
        prefix: message_category(f"{prefix}:fixture", "Neutral subject")
        for prefix in prefixes
    }
    assert categories["calendar"] == "schedule"
    assert all(categories[prefix] == "reminder" for prefix in (
        "reminder", "task-reminder", "evaluation-reminder"
    ))


def test_message_preview_is_plain_text_bounded_and_drops_links() -> None:
    preview = message_preview('<p>Hello <a href="https://secret.test/token">Priya</a></p>')
    assert preview == "Hello Priya"
    assert "secret.test" not in preview
    assert message_preview(f"<p>{'x' * 600}</p>").endswith("…")
    assert len(message_preview(f"<p>{'x' * 600}</p>")) == 500

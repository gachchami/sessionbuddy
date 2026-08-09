import re
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


@pytest.mark.parametrize(
    ("html_name", "javascript_name"),
    (
        ("landing.html", "app_shell.js"),
        ("setup.html", "setup.js"),
        ("sign_in.html", "sign_in.js"),
        ("admin_home.html", "admin_home.js"),
        ("events_admin.html", "events_admin.js"),
        ("event_overview.html", "event_overview.js"),
        ("speaker_directory.html", "speaker_directory.js"),
        ("account.html", "account.js"),
        ("admin_programs.html", "admin_programs.js"),
        ("public_cfp.html", "public_cfp.js"),
        ("admin_submissions.html", "admin_submissions.js"),
        ("access_admin.html", "access_admin.js"),
        ("admin_onboarding.html", "admin_onboarding.js"),
        ("event_workspace.html", "event_workspace.js"),
        ("agenda_admin.html", "agenda.js"),
        ("schedule.html", "schedule.js"),
        ("speaker_gallery.html", "speaker_gallery.js"),
        ("speaker_portal.html", "speaker_portal.js"),
    ),
)
def test_literal_javascript_element_references_are_wired(
    html_name: str, javascript_name: str
) -> None:
    html = (STATIC / html_name).read_text(encoding="utf-8")
    javascript = (STATIC / javascript_name).read_text(encoding="utf-8")
    declared = set(re.findall(r'\bid=["\']([^"\']+)', html))
    declared.update(re.findall(r'\.id\s*=\s*["\']([^"\']+)', javascript))
    referenced = set(re.findall(r'\bbyId\(["\']([^"\']+)', javascript))

    assert referenced <= declared, (
        f"{javascript_name} references missing IDs in {html_name}: "
        f"{sorted(referenced - declared)}"
    )

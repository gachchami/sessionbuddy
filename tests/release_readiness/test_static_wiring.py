import re
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"


@pytest.mark.parametrize(
    ("html_name", "javascript_name"),
    (
        ("landing.html", "app_shell.js"),
        ("engine_room.html", "console.js"),
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


def test_every_api_driven_page_loads_the_shared_client_first() -> None:
    for html_path in STATIC.glob("*.html"):
        html = html_path.read_text(encoding="utf-8")
        if "<script" not in html or "auth_link_error" in html_path.name:
            continue
        api_client = html.find('/app-shell/assets/api-client.js')
        assert api_client >= 0, f"{html_path.name} does not load the shared API client"
        page_scripts = [match.start() for match in re.finditer(r"<script\s+src=", html)]
        api_client_script = html.rfind("<script", 0, api_client)
        assert not page_scripts or api_client_script == page_scripts[0]


def test_browser_api_parsing_is_centralized() -> None:
    source_files = [
        path
        for path in STATIC.rglob("*.js")
        if path.name != "api_client.js" and "app/assets" not in path.as_posix()
    ]
    source_files.append(STATIC.parents[2] / "frontend" / "src" / "main.tsx")
    violations: list[str] = []
    for path in source_files:
        source = path.read_text(encoding="utf-8")
        if re.search(r"fetch\(\s*[`\"']/api/", source) or ".json()" in source:
            violations.append(str(path.relative_to(STATIC.parents[2])))
    assert not violations, f"API calls bypass the shared response handler: {violations}"

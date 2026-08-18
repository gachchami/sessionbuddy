"""Generate Python constants for assets bundled by Cloudflare Python Workers."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "sessionbuddy" / "static"
OUTPUT = ROOT / "src" / "sessionbuddy" / "console" / "embedded_assets.py"
ASSETS = {
    "landing.html": "LANDING_HTML",
    "landing.css": "LANDING_CSS",
    "sessionbuddy-favicon.svg": "SESSIONBUDDY_FAVICON_SVG",
    "engine_room.html": "ENGINE_ROOM_HTML",
    "console.css": "CONSOLE_CSS",
    "console.js": "CONSOLE_JS",
    "product.css": "PRODUCT_CSS",
    "setup.html": "SETUP_HTML",
    "setup.css": "SETUP_CSS",
    "setup.js": "SETUP_JS",
    "app_shell.css": "APP_SHELL_CSS",
    "app_shell.js": "APP_SHELL_JS",
    "api_client.js": "API_CLIENT_JS",
    "error_page.html": "ERROR_PAGE_HTML",
    "error_page.css": "ERROR_PAGE_CSS",
    "api_docs.html": "API_DOCS_HTML",
    "api_docs.css": "API_DOCS_CSS",
    "api_docs.js": "API_DOCS_JS",
    "admin_home.html": "ADMIN_HOME_HTML",
    "admin_home.js": "ADMIN_HOME_JS",
    "admin_home.css": "ADMIN_HOME_CSS",
    "activity_format.js": "ACTIVITY_FORMAT_JS",
    "event_editor.html": "EVENT_EDITOR_HTML",
    "event_editor.js": "EVENT_EDITOR_JS",
    "event_editor.css": "EVENT_EDITOR_CSS",
    "event_overview.html": "EVENT_OVERVIEW_HTML",
    "event_overview.js": "EVENT_OVERVIEW_JS",
    "speaker_directory.html": "SPEAKER_DIRECTORY_HTML",
    "speaker_directory.js": "SPEAKER_DIRECTORY_JS",
    "people_search.js": "PEOPLE_SEARCH_JS",
    "public_profile.html": "PUBLIC_PROFILE_HTML",
    "public_profile.js": "PUBLIC_PROFILE_JS",
    "speaker_messages.html": "SPEAKER_MESSAGES_HTML",
    "speaker_messages.js": "SPEAKER_MESSAGES_JS",
    "account.html": "ACCOUNT_HTML",
    "account.js": "ACCOUNT_JS",
    "organization_admin.html": "ORGANIZATION_ADMIN_HTML",
    "organization_admin.js": "ORGANIZATION_ADMIN_JS",
    "admin_programs.html": "ADMIN_PROGRAMS_HTML",
    "admin_programs.js": "ADMIN_PROGRAMS_JS",
    "public_cfp.html": "PUBLIC_CFP_HTML",
    "public_cfp.js": "PUBLIC_CFP_JS",
    "open_calls.html": "OPEN_CALLS_HTML",
    "open_calls.js": "OPEN_CALLS_JS",
    "co_speaker_invitation.html": "CO_SPEAKER_INVITATION_HTML",
    "co_speaker_invitation.js": "CO_SPEAKER_INVITATION_JS",
    "sign_in.html": "SIGN_IN_HTML",
    "auth_link_error.html": "AUTH_LINK_ERROR_HTML",
    "auth_link_confirm.html": "AUTH_LINK_CONFIRM_HTML",
    "auth_link_confirm.js": "AUTH_LINK_CONFIRM_JS",
    "sign_in.js": "SIGN_IN_JS",
    "demo_access.js": "DEMO_ACCESS_JS",
    "access_admin.html": "ACCESS_ADMIN_HTML",
    "access_admin.js": "ACCESS_ADMIN_JS",
    "admin_submissions.html": "ADMIN_SUBMISSIONS_HTML",
    "admin_submissions.js": "ADMIN_SUBMISSIONS_JS",
    "app/index.html": "REVIEWS_HTML",
    "app/assets/reviews.css": "REVIEWS_CSS",
    "app/assets/reviews.js": "REVIEWS_JS",
    "speaker_portal.html": "SPEAKER_PORTAL_HTML",
    "speaker_portal.js": "SPEAKER_PORTAL_JS",
    "speaker.css": "SPEAKER_CSS",
    "admin_onboarding.html": "ADMIN_ONBOARDING_HTML",
    "admin_onboarding.js": "ADMIN_ONBOARDING_JS",
    "admin_onboarding.css": "ADMIN_ONBOARDING_CSS",
    "agenda_admin.html": "AGENDA_ADMIN_HTML",
    "agenda.js": "AGENDA_JS",
    "agenda.css": "AGENDA_CSS",
    "schedule.html": "SCHEDULE_HTML",
    "schedule.js": "SCHEDULE_JS",
    "schedule.css": "SCHEDULE_CSS",
    "event_workspace.html": "EVENT_WORKSPACE_HTML",
    "speaker_content.html": "SPEAKER_CONTENT_HTML",
    "speaker_content.js": "SPEAKER_CONTENT_JS",
    "event_workspace.js": "EVENT_WORKSPACE_JS",
    "speaker_gallery.html": "SPEAKER_GALLERY_HTML",
    "speaker_gallery.js": "SPEAKER_GALLERY_JS",
    "public_event_masthead.js": "PUBLIC_EVENT_MASTHEAD_JS",
    "biography_disclosure.js": "BIOGRAPHY_DISCLOSURE_JS",
}
BINARY_ASSETS = {
    "aie-new-york-2026.jpg": "AIE_NEW_YORK_2026_JPG",
    "aie-code-sf-2026.jpg": "AIE_CODE_SF_2026_JPG",
    "fonts/dm-sans-latin-wght-normal.woff2": "DM_SANS_LATIN_WGHT_NORMAL_WOFF2",
}

CONTENT_ADDRESSED_ASSETS = (
    ("admin_home.html", "admin_home.js", "/admin/home/assets/home.js"),
    ("admin_home.html", "admin_home.css", "/admin/home/assets/home.css"),
    (
        "admin_home.html",
        "activity_format.js",
        "/app-shell/assets/activity-format.js",
    ),
    (
        "organization_admin.html",
        "activity_format.js",
        "/app-shell/assets/activity-format.js",
    ),
    (
        "event_overview.html",
        "activity_format.js",
        "/app-shell/assets/activity-format.js",
    ),
    ("access_admin.html", "access_admin.js", "/admin/access/assets/access.js"),
    ("admin_onboarding.html", "admin_onboarding.js", "/admin/onboarding/assets/onboarding.js"),
    ("admin_programs.html", "admin_programs.js", "/product/assets/admin-programs.js"),
    ("admin_submissions.html", "admin_submissions.js", "/product/assets/admin-submissions.js"),
    ("agenda_admin.html", "agenda.js", "/admin/agenda/assets/agenda.js"),
    ("event_overview.html", "event_overview.js", "/admin/event-overview/assets/event-overview.js"),
    (
        "event_editor.html",
        "event_editor.js",
        "/admin/event-editor/assets/event-editor.js",
    ),
    (
        "event_editor.html",
        "event_editor.css",
        "/admin/event-editor/assets/event-editor.css",
    ),
    ("event_workspace.html", "event_workspace.js", "/admin/workspace/assets/workspace.js"),
    (
        "speaker_content.html",
        "speaker_content.js",
        "/admin/speaker-content/assets/speaker-content.js",
    ),
    ("speaker_directory.html", "speaker_directory.js", "/admin/people/assets/people.js"),
    (
        "speaker_directory.html",
        "biography_disclosure.js",
        "/public/assets/biography-disclosure.js",
    ),
    ("speaker_messages.html", "speaker_messages.js", "/admin/speakers/assets/messages.js"),
    ("app/index.html", "app/assets/reviews.js", "/app/assets/reviews.js"),
    ("app/index.html", "app/assets/reviews.css", "/app/assets/reviews.css"),
    (
        "speaker_gallery.html",
        "public_event_masthead.js",
        "/public/assets/event-masthead.js",
    ),
    (
        "schedule.html",
        "public_event_masthead.js",
        "/public/assets/event-masthead.js",
    ),
    (
        "public_cfp.html",
        "public_event_masthead.js",
        "/public/assets/event-masthead.js",
    ),
    (
        "event_editor.html",
        "public_event_masthead.js",
        "/public/assets/event-masthead.js",
    ),
    (
        "admin_programs.html",
        "public_event_masthead.js",
        "/public/assets/event-masthead.js",
    ),
    (
        "speaker_gallery.html",
        "biography_disclosure.js",
        "/public/assets/biography-disclosure.js",
    ),
    (
        "public_profile.html",
        "biography_disclosure.js",
        "/public/assets/biography-disclosure.js",
    ),
)
CONTENT_ADDRESSED_CSS_ASSETS = (
    (
        "admin_home.css",
        "fonts/dm-sans-latin-wght-normal.woff2",
        "/admin/home/assets/dm-sans.woff2",
    ),
)
SHARED_ASSET_VERSIONS = {
    "/product/assets/product.css": "90",
    "/app-shell/assets/app-shell.css": "27",
    "/app-shell/assets/api-client.js": "8",
    "/app-shell/assets/app-shell.js": "34",
}


def _versioned_html(filename: str, content: str) -> str:
    for html_name, asset_name, asset_path in CONTENT_ADDRESSED_ASSETS:
        if html_name != filename:
            continue
        digest = hashlib.sha256((STATIC / asset_name).read_bytes()).hexdigest()[:12]
        pattern = re.escape(asset_path) + r"(?:\?v=[A-Za-z0-9._-]+)?"
        content, count = re.subn(pattern, f"{asset_path}?v={digest}", content)
        if count != 1:
            raise RuntimeError(f"expected one {asset_path} reference in {html_name}; found {count}")
    return content


def _versioned_css(filename: str, content: str) -> str:
    for css_name, asset_name, asset_path in CONTENT_ADDRESSED_CSS_ASSETS:
        if css_name != filename:
            continue
        digest = hashlib.sha256((STATIC / asset_name).read_bytes()).hexdigest()[:12]
        pattern = re.escape(asset_path) + r"(?:\?v=[A-Za-z0-9._-]+)?"
        content, count = re.subn(pattern, f"{asset_path}?v={digest}", content)
        if count != 1:
            raise RuntimeError(f"expected one {asset_path} reference in {css_name}; found {count}")
    return content


def sync_content_addresses(*, check: bool) -> None:
    for css_name, _, _ in CONTENT_ADDRESSED_CSS_ASSETS:
        path = STATIC / css_name
        current = path.read_text(encoding="utf-8")
        expected = _versioned_css(css_name, current)
        if current == expected:
            continue
        if check:
            raise SystemExit(
                f"{path} has stale asset identities; run this script without --check"
            )
        path.write_text(expected, encoding="utf-8")

    html_paths = {STATIC / item[0] for item in CONTENT_ADDRESSED_ASSETS}
    html_paths.update(STATIC.rglob("*.html"))
    html_paths.add(ROOT / "frontend" / "index.html")
    for path in html_paths:
        current = path.read_text(encoding="utf-8")
        expected = _versioned_html(
            str(path.relative_to(STATIC)) if path.is_relative_to(STATIC) else path.name,
            current,
        )
        for asset_path, version in SHARED_ASSET_VERSIONS.items():
            pattern = re.escape(asset_path) + r"(?:\?v=[A-Za-z0-9._-]+)?"
            expected = re.sub(pattern, f"{asset_path}?v={version}", expected)
        if current == expected:
            continue
        if check:
            raise SystemExit(
                f"{path} has stale asset identities; run this script without --check"
            )
        path.write_text(expected, encoding="utf-8")


def render() -> str:
    lines = [
        "# ruff: noqa: E501",
        '"""Generated Worker-safe console assets; do not edit by hand."""',
        "",
    ]
    for filename, constant in ASSETS.items():
        content = (STATIC / filename).read_text(encoding="utf-8")
        lines.extend((f"{constant} = {content!r}", ""))
    for filename, constant in BINARY_ASSETS.items():
        content = (STATIC / filename).read_bytes()
        lines.extend((f"{constant} = {content!r}", ""))
    lines.append(f"ASSETS = {dict(zip(ASSETS, ASSETS.values(), strict=True))!r}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    sync_content_addresses(check=args.check)
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("embedded console assets are stale; run this script without --check")
        return
    # Workerd watches this module in local development. Replacing it atomically keeps a
    # reload from importing the half-written Python string that write_text() can expose.
    descriptor, temporary_name = tempfile.mkstemp(
        dir=OUTPUT.parent, prefix=f".{OUTPUT.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, OUTPUT)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

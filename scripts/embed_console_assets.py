"""Generate Python constants for assets bundled by Cloudflare Python Workers."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "sessionbuddy" / "static"
OUTPUT = ROOT / "src" / "sessionbuddy" / "console" / "embedded_assets.py"
ASSETS = {
    "landing.html": "LANDING_HTML",
    "landing.css": "LANDING_CSS",
    "engine_room.html": "ENGINE_ROOM_HTML",
    "console.css": "CONSOLE_CSS",
    "console.js": "CONSOLE_JS",
    "cfp_integration.html": "CFP_INTEGRATION_HTML",
    "cfp_integration.css": "CFP_INTEGRATION_CSS",
    "cfp_integration.js": "CFP_INTEGRATION_JS",
    "product.css": "PRODUCT_CSS",
    "admin_programs.html": "ADMIN_PROGRAMS_HTML",
    "admin_programs.js": "ADMIN_PROGRAMS_JS",
    "public_cfp.html": "PUBLIC_CFP_HTML",
    "public_cfp.js": "PUBLIC_CFP_JS",
    "sign_in.html": "SIGN_IN_HTML",
    "auth_link_error.html": "AUTH_LINK_ERROR_HTML",
    "sign_in.js": "SIGN_IN_JS",
    "access_admin.html": "ACCESS_ADMIN_HTML",
    "access_admin.js": "ACCESS_ADMIN_JS",
    "events_admin.html": "EVENTS_ADMIN_HTML",
    "events_admin.js": "EVENTS_ADMIN_JS",
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
    "event_workspace.js": "EVENT_WORKSPACE_JS",
    "speaker_gallery.html": "SPEAKER_GALLERY_HTML",
    "speaker_gallery.js": "SPEAKER_GALLERY_JS",
}


def render() -> str:
    lines = [
        "# ruff: noqa: E501",
        '"""Generated Worker-safe console assets; do not edit by hand."""',
        "",
    ]
    for filename, constant in ASSETS.items():
        content = (STATIC / filename).read_text(encoding="utf-8")
        lines.extend((f"{constant} = {content!r}", ""))
    lines.append(f"ASSETS = {dict(zip(ASSETS, ASSETS.values(), strict=True))!r}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("embedded console assets are stale; run this script without --check")
        return
    OUTPUT.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()

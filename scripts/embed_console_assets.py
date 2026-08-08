"""Generate Python constants for assets bundled by Cloudflare Python Workers."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "sessionbuddy" / "static"
OUTPUT = ROOT / "src" / "sessionbuddy" / "console" / "embedded_assets.py"
ASSETS = {
    "foundation.html": "FOUNDATION_HTML",
    "console.css": "CONSOLE_CSS",
    "console.js": "CONSOLE_JS",
    "wave1.html": "WAVE1_HTML",
    "wave1.css": "WAVE1_CSS",
    "wave1.js": "WAVE1_JS",
    "product.css": "PRODUCT_CSS",
    "admin_programs.html": "ADMIN_PROGRAMS_HTML",
    "admin_programs.js": "ADMIN_PROGRAMS_JS",
    "public_cfp.html": "PUBLIC_CFP_HTML",
    "public_cfp.js": "PUBLIC_CFP_JS",
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

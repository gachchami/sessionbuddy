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

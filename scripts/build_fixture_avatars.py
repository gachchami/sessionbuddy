"""Build deterministic, original initials avatars for the Day-N fixture pack."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "fixtures" / "day_n" / "assets" / "speaker-headshots"
AVATARS = {
    "aria-speaker": "AS",
    "ben-speaker": "BS",
    "chen-speaker": "CS",
    "dana-speaker": "DS",
    "ellis-cospeaker": "EC",
    "finley-cospeaker": "FC",
    "gray-switcher": "GS",
}


def avatar_bytes(slug: str, initials: str) -> bytes:
    digest = hashlib.sha256(slug.encode("utf-8")).digest()
    background = tuple(48 + component % 128 for component in digest[:3])
    accent = tuple(min(255, component + 52) for component in background)
    image = Image.new("RGB", (512, 512), background)
    draw = ImageDraw.Draw(image)
    draw.ellipse((-96, -128, 352, 320), fill=accent)
    draw.ellipse((272, 304, 592, 624), fill=tuple(max(0, value - 32) for value in background))
    draw.rounded_rectangle((48, 48, 464, 464), radius=84, outline=(255, 255, 255), width=8)
    font = ImageFont.load_default(size=176)
    bounds = draw.textbbox((0, 0), initials, font=font, stroke_width=2)
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    draw.text(
        ((512 - width) / 2, (512 - height) / 2 - bounds[1]),
        initials,
        font=font,
        fill=(255, 255, 255),
        stroke_width=2,
        stroke_fill=(18, 24, 38),
    )
    output = BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def build(*, check: bool) -> None:
    if not check:
        OUTPUT.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for slug, initials in AVATARS.items():
        path = OUTPUT / f"{slug}.png"
        expected = avatar_bytes(slug, initials)
        if check:
            if not path.is_file() or path.read_bytes() != expected:
                stale.append(path.name)
        else:
            path.write_bytes(expected)
    if stale:
        raise SystemExit(f"fixture avatars are stale or missing: {', '.join(stale)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    build(check=arguments.check)


if __name__ == "__main__":
    main()

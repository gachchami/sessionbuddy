"""Regenerate every logo derivative from the SVG masters in assets/logo/svg.

The SVG masters are the only hand-maintained artwork. Everything under png/,
ico/, and pdf/ is generated from them by this script, so a change to a master
propagates with one command:

    uv run python scripts/build_logo_assets.py

CI can assert the tree is current with:

    uv run python scripts/build_logo_assets.py --check

Requires cairosvg and Pillow (see the ``logo`` optional dependency group).
The wordmark in the lockup masters is already converted to outlines, so no
font needs to be installed to build these.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "assets" / "logo"
SVG = LOGO / "svg"
PNG = LOGO / "png"
ICO = LOGO / "ico"
PDF = LOGO / "pdf"

# master -> square sizes rendered into png/
ICON_SIZES = (16, 32, 48, 64, 128, 180, 192, 256, 512, 1024)
ICON_PNGS: dict[str, tuple[int, ...]] = {
    "sessionbuddy-icon-color": ICON_SIZES,
    "sessionbuddy-icon-mono": (512,),
    "sessionbuddy-icon-reversed": (512,),
}
# master -> widths rendered into png/ (height follows the master's aspect ratio)
LOCKUP_PNGS: dict[str, tuple[int, ...]] = {
    "sessionbuddy-lockup-color": (340, 680, 1360),
    "sessionbuddy-lockup-mono": (680,),
    "sessionbuddy-lockup-reversed": (680,),
}
PDFS = (
    "sessionbuddy-icon-color",
    "sessionbuddy-lockup-color",
    "sessionbuddy-lockup-mono",
    "sessionbuddy-lockup-reversed",
)
ICO_SOURCE = "sessionbuddy-icon-color"
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def _png_name(stem: str, size: int) -> str:
    """png/ keeps the historical naming: icons carry a square size, lockups a width."""
    if stem.startswith("sessionbuddy-icon-color"):
        return f"sessionbuddy-icon-{size}.png"
    if stem.startswith("sessionbuddy-icon-"):
        variant = stem.removeprefix("sessionbuddy-icon-")
        return f"sessionbuddy-icon-{variant}-{size}.png"
    variant = stem.removeprefix("sessionbuddy-lockup-")
    if variant == "color":
        return f"sessionbuddy-lockup-{size}.png"
    return f"sessionbuddy-lockup-{variant}-{size}.png"


def render() -> dict[Path, bytes]:
    import cairosvg
    from PIL import Image

    out: dict[Path, bytes] = {}

    for stem, sizes in ICON_PNGS.items():
        src = (SVG / f"{stem}.svg").read_bytes()
        for size in sizes:
            out[PNG / _png_name(stem, size)] = cairosvg.svg2png(
                bytestring=src, output_width=size, output_height=size
            )

    for stem, widths in LOCKUP_PNGS.items():
        src = (SVG / f"{stem}.svg").read_bytes()
        for width in widths:
            out[PNG / _png_name(stem, width)] = cairosvg.svg2png(
                bytestring=src, output_width=width
            )

    for stem in PDFS:
        src = (SVG / f"{stem}.svg").read_bytes()
        out[PDF / f"{stem}.pdf"] = cairosvg.svg2pdf(bytestring=src)

    # Every .ico frame is rendered natively from the SVG at its own size rather
    # than downsampled from one bitmap, so the 16 px frame keeps its pixel
    # snapping. Pillow uses the base image's size as the ceiling, so the base
    # must be the largest frame or the smaller ones are silently dropped.
    frames = [
        Image.open(io.BytesIO(out[PNG / _png_name(ICO_SOURCE, size)])).convert("RGBA")
        for size in sorted(ICO_SIZES)
    ]
    buffer = io.BytesIO()
    frames[-1].save(
        buffer,
        format="ICO",
        sizes=[(s, s) for s in sorted(ICO_SIZES)],
        append_images=frames[:-1],
    )
    out[ICO / "sessionbuddy-favicon.ico"] = buffer.getvalue()

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if any generated file is missing or stale",
    )
    args = parser.parse_args()

    try:
        expected = render()
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise SystemExit(
            f"missing dependency {exc.name!r}; install with: uv sync --group logo"
        ) from exc

    if args.check:
        stale = [
            path.relative_to(ROOT)
            for path, data in expected.items()
            if not path.exists() or path.read_bytes() != data
        ]
        if stale:
            listing = "\n  ".join(str(p) for p in sorted(stale))
            raise SystemExit(
                f"logo assets are stale; run scripts/build_logo_assets.py\n  {listing}"
            )
        print(f"logo assets are current ({len(expected)} files)")
        return

    for directory in (PNG, ICO, PDF):
        directory.mkdir(parents=True, exist_ok=True)
    for path, data in expected.items():
        path.write_bytes(data)
    print(f"wrote {len(expected)} files under {LOGO.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()

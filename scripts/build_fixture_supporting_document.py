"""Build the original synthetic PDF used by Day-N upload workflows."""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    ROOT
    / "fixtures"
    / "day_n"
    / "assets"
    / "documents"
    / "day-n-speaker-briefing.pdf"
)


def document_bytes() -> bytes:
    stream = BytesIO()
    canvas = Canvas(
        stream,
        pagesize=letter,
        pageCompression=1,
        invariant=1,
    )
    width, height = letter
    canvas.setTitle("SessionBuddy Day-N Speaker Briefing")
    canvas.setAuthor("SessionBuddy synthetic fixture generator")
    canvas.setFillColor(HexColor("#18243A"))
    canvas.rect(0, height - 156, width, 156, fill=1, stroke=0)
    canvas.setFillColor(white)
    canvas.setFont("Helvetica-Bold", 26)
    canvas.drawString(54, height - 78, "Day-N Speaker Briefing")
    canvas.setFont("Helvetica", 12)
    canvas.drawString(
        54,
        height - 106,
        "Synthetic upload fixture - no real attendee or speaker data",
    )

    canvas.setFillColor(HexColor("#18243A"))
    canvas.setFont("Helvetica-Bold", 16)
    canvas.drawString(54, height - 204, "Arrival and room readiness")
    canvas.setFont("Helvetica", 11)
    checklist = [
        "Arrive 45 minutes before the scheduled session.",
        "Check in at the speaker desk using the synthetic event pass.",
        "Confirm captions, presentation aspect ratio, and microphone placement.",
        "Keep private attendee details out of slides, notes, and demonstrations.",
        "Report schedule changes through the SessionBuddy speaker portal.",
    ]
    y = height - 234
    for item in checklist:
        canvas.setFillColor(HexColor("#4F63C1"))
        canvas.circle(61, y + 3, 3, fill=1, stroke=0)
        canvas.setFillColor(HexColor("#28354A"))
        canvas.drawString(75, y, item)
        y -= 28

    canvas.setFillColor(HexColor("#EEF2FF"))
    canvas.roundRect(54, 150, width - 108, 112, 12, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#18243A"))
    canvas.setFont("Helvetica-Bold", 14)
    canvas.drawString(72, 228, "Synthetic operations contact")
    canvas.setFont("Helvetica", 11)
    canvas.drawString(72, 204, "Operations desk: operations@example.test")
    canvas.drawString(72, 184, "Emergency exercise line: +1 202 555 0147")
    canvas.drawString(72, 164, "This document is generated solely for isolated SessionBuddy tests.")

    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(HexColor("#5D687A"))
    canvas.drawRightString(width - 54, 42, "SessionBuddy fixture asset - page 1 of 1")
    canvas.showPage()
    canvas.save()
    return stream.getvalue()


def build(*, check: bool) -> None:
    expected = document_bytes()
    if check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != expected:
            raise SystemExit("fixture supporting document is stale or missing")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(expected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    build(check=arguments.check)


if __name__ == "__main__":
    main()

"""Fail when a Worker dry-run package contains fixture material."""

from __future__ import annotations

import argparse
from pathlib import Path

FORBIDDEN_PATH_PARTS = frozenset({"fixtures"})
FORBIDDEN_CONTENT_MARKERS = (
    b"fixtures/day_n",
    b"alex.organizer@example.test",
    b"Day-N AI Operations Summit 2026",
)


def validate(package: Path) -> int:
    if not package.is_dir():
        raise ValueError(f"Worker package directory does not exist: {package}")
    files = sorted(path for path in package.rglob("*") if path.is_file())
    violations: list[str] = []
    for path in files:
        relative = path.relative_to(package)
        if FORBIDDEN_PATH_PARTS.intersection(relative.parts):
            violations.append(f"forbidden package path: {relative}")
            continue
        data = path.read_bytes()
        for marker in FORBIDDEN_CONTENT_MARKERS:
            if marker in data:
                violations.append(
                    f"fixture marker {marker.decode('utf-8')!r} in package file: {relative}"
                )
    if violations:
        raise ValueError("\n".join(violations))
    return len(files)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    arguments = parser.parse_args()
    count = validate(arguments.package)
    print(f"Worker package boundary passed ({count} files; no fixture material).")


if __name__ == "__main__":
    main()

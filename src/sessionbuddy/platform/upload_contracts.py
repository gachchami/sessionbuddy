"""Shared upload policy and task-schema contracts.

File-task writers, authenticated uploads, and anonymous staged uploads all
consume these values. Keeping the format and size policy here prevents a task
from advertising one contract while the upload boundary enforces another.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Final

from fastapi import HTTPException

AssetRule = tuple[frozenset[str], int]

ASSET_UPLOAD_RULES: Final[dict[str, AssetRule]] = {
    "headshot": (
        frozenset({"image/jpeg", "image/png", "image/webp"}),
        5 * 1024 * 1024,
    ),
    "slides": (
        frozenset(
            {
                "application/pdf",
                "application/vnd.ms-powerpoint",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "application/vnd.oasis.opendocument.presentation",
            }
        ),
        50 * 1024 * 1024,
    ),
    "supporting_document": (frozenset({"application/pdf"}), 20 * 1024 * 1024),
}

FILE_TASK_TYPES: Final[frozenset[str]] = frozenset(ASSET_UPLOAD_RULES)
MAX_ASSET_UPLOAD_BYTES: Final[int] = max(limit for _, limit in ASSET_UPLOAD_RULES.values())


def task_form_schema(
    task_type: str,
    *,
    fields: Iterable[Mapping[str, object]] = (),
    upload_enabled: bool | None = None,
    allowed_content_types: Iterable[str] | None = None,
    max_file_bytes: int | None = None,
) -> dict[str, object]:
    """Build the one persisted task schema used by every writer.

    System file tasks inherit the canonical policy. Organizer-authored custom
    tasks must pass their explicit non-upload configuration; there is no
    implicit upload policy for ``custom`` or ``profile`` tasks.
    """

    field_values = [dict(field) for field in fields]
    if upload_enabled is None:
        if task_type in ASSET_UPLOAD_RULES:
            allowed, maximum = ASSET_UPLOAD_RULES[task_type]
            upload_enabled = True
            allowed_content_types = sorted(allowed)
            max_file_bytes = maximum
        elif task_type == "profile":
            upload_enabled = False
            allowed_content_types = ()
            max_file_bytes = None
        else:
            raise ValueError(f"{task_type} tasks require an explicit schema")

    return {
        "fields": field_values,
        "upload": {
            "enabled": upload_enabled,
            "allowed_content_types": sorted(allowed_content_types or ()),
            "max_file_bytes": max_file_bytes,
        },
    }


def task_form_schema_json(task_type: str) -> str:
    """Serialize the canonical schema for a system-created speaker task."""

    return json.dumps(task_form_schema(task_type), separators=(",", ":"))


def asset_upload_rule_views() -> dict[str, dict[str, object]]:
    """Return JSON-safe canonical rules for authenticated asset uploads."""

    return {
        kind: dict(task_form_schema(kind)["upload"])
        for kind in sorted(ASSET_UPLOAD_RULES)
    }


class UploadPolicyError(HTTPException):
    """Stable upload refusal whose code determines the caller's recovery."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.code = code
        super().__init__(status_code=status_code, detail=message)

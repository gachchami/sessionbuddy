import re
from pathlib import Path

import pytest

from sessionbuddy.cfp.staged_uploads import STAGED_ASSET_KINDS, STAGED_ASSET_RULES
from sessionbuddy.platform.upload_contracts import (
    ASSET_UPLOAD_RULES,
    FILE_TASK_TYPES,
    MAX_ASSET_UPLOAD_BYTES,
    task_form_schema,
)


def test_file_task_schemas_are_derived_from_the_canonical_asset_policy() -> None:
    assert FILE_TASK_TYPES == frozenset(ASSET_UPLOAD_RULES)
    assert MAX_ASSET_UPLOAD_BYTES == 50 * 1024 * 1024

    for task_type, (allowed_types, max_bytes) in ASSET_UPLOAD_RULES.items():
        schema = task_form_schema(task_type)
        assert schema == {
            "fields": [],
            "upload": {
                "enabled": True,
                "allowed_content_types": sorted(allowed_types),
                "max_file_bytes": max_bytes,
            },
        }


def test_migration_upload_ceiling_matches_the_canonical_asset_policy() -> None:
    migration = (
        Path(__file__).parents[2]
        / "migrations_baseline"
        / "0002_speaker_task_upload_contract.sql"
    ).read_text(encoding="utf-8")
    ceiling_values = re.findall(r"max_file_bytes'\) > (\d+)", migration)
    # Repair selector, post-backfill guard, and insert/update triggers.
    assert len(ceiling_values) == 4
    ceilings = {int(value) for value in ceiling_values}
    assert ceilings == {MAX_ASSET_UPLOAD_BYTES}


def test_non_file_tasks_never_inherit_an_upload_policy() -> None:
    assert task_form_schema("profile") == {
        "fields": [],
        "upload": {
            "enabled": False,
            "allowed_content_types": [],
            "max_file_bytes": None,
        },
    }
    with pytest.raises(ValueError, match="custom tasks require an explicit schema"):
        task_form_schema("custom")


def test_anonymous_staged_uploads_are_an_explicit_policy_subset() -> None:
    assert STAGED_ASSET_KINDS == frozenset({"headshot", "supporting_document"})
    assert STAGED_ASSET_KINDS < FILE_TASK_TYPES
    assert STAGED_ASSET_RULES == {
        kind: ASSET_UPLOAD_RULES[kind] for kind in STAGED_ASSET_KINDS
    }

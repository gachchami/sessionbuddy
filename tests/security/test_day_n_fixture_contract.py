"""Contract checks for the UI-only Day-N fixture worksheets."""

from __future__ import annotations

import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures" / "day_n"


def load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_day_n_fixture_preserves_agreed_people_and_event_counts() -> None:
    manifest = load("00-manifest.json")["counts"]
    users = load("01-users.json")["users"]
    events = load("02-events.json")["events"]

    assert len([user for user in users if user["key"].startswith("user-organizer-")]) == 4
    assert len([user for user in users if user["key"].startswith("user-speaker-")]) == 4
    assert len([user for user in users if user["key"].startswith("user-cospeaker-")]) == 2
    assert len([user for user in users if user["key"].startswith("user-reviewer-")]) == 3
    assert len([user for user in users if user["personas"] == ["organizer", "speaker"]]) == 1
    assert len([event for event in events if event["classification"] == "current"]) == 3
    assert len([event for event in events if event["classification"] == "historic"]) == 4
    assert len([event for event in events if event["fully_populated"]]) == 2
    assert manifest == {
        "organizers": 4,
        "primary_speakers": 4,
        "co_speakers": 2,
        "role_switch_users": 1,
        "reviewers": 3,
        "current_events": 3,
        "historic_events": 4,
        "fully_populated_events": 2,
    }


def test_day_n_fixture_uses_personas_exact_grants_and_assignments() -> None:
    accounts = load("01-users.json")
    allowed_personas = {"organizer", "reviewer", "speaker"}

    for user in accounts["users"]:
        assert set(user["personas"]) <= allowed_personas
        assert user["default_persona"] is None or user["default_persona"] in user["personas"]

    event_keys = {event["key"] for event in load("02-events.json")["events"]}
    ownership = accounts["resource_ownership"]
    event_ownership = [item for item in ownership if item["resource_type"] == "event"]
    assert {item["resource"] for item in event_ownership} == event_keys
    assert all(item["created_by"] == "user-organizer-1" for item in ownership)
    assert all(item["owner"] == "user-organizer-1" for item in ownership)
    assert all(
        grant["permission"] in {"view", "edit", "manage"}
        for grant in accounts["resource_grants"]
    )
    assert not any(
        grant["user"] == owner["owner"]
        and grant["resource_type"] == owner["resource_type"]
        and grant["resource"] == owner["resource"]
        for grant in accounts["resource_grants"]
        for owner in ownership
    )
    assert {item["event"] for item in accounts["speaker_assignments"]} <= event_keys
    assert {item["event"] for item in accounts["reviewer_assignments"]} <= event_keys


def test_day_n_ui_worksheets_have_no_legacy_admin_subroles() -> None:
    ui_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((FIXTURES / "ui").glob("*"))
        if path.suffix in {".json", ".md"}
    ).lower()

    assert "organization_admin" not in ui_text
    assert "event_admin" not in ui_text
    assert "organization administrator" not in ui_text
    assert "event administrator" not in ui_text
    assert "memberships still govern" not in ui_text
    assert "can manage this event" in ui_text
    assert "reviewer assignment" in ui_text
    assert "speaker assignment" in ui_text


def test_every_fixture_event_has_existing_logo_and_cover_assets() -> None:
    events = load("02-events.json")["events"]
    ui_events = load("ui/02-events.json")["events"]

    assert len(events) == len(ui_events) == 7
    for event in events:
        branding = event["branding"]
        logo = FIXTURES / branding["logo"]
        cover = FIXTURES / branding["cover"]
        assert logo.is_file()
        assert cover.is_file()
        assert logo.stat().st_size <= 2 * 1024 * 1024
        assert cover.stat().st_size <= 2 * 1024 * 1024

    for event in ui_events:
        uploads = event["uploads"]
        assert (FIXTURES / "ui" / uploads["Logo"]).resolve().is_file()
        assert (FIXTURES / "ui" / uploads["Cover"]).resolve().is_file()


def test_portable_ui_instance_map_uses_fixture_keys_without_secrets() -> None:
    manifest = load("00-manifest.json")
    events = load("02-events.json")["events"]
    instance_map = load("ui/instance-map.example.json")
    serialized = json.dumps(instance_map).lower()

    assert manifest["production_forbidden"] is True
    assert set(instance_map["events"]) == {event["key"] for event in events}
    assert instance_map["completed_phases"] == []
    for forbidden in ("password", "cookie", "csrf", "token", "magic_link"):
        assert forbidden not in serialized


def test_overdue_and_asset_safety_ui_checkpoints_are_environment_accurate() -> None:
    operations = load("ui/06-speaker-operations.json")
    runbook = load("ui/09-runbook.json")
    ben = next(
        item for item in operations["task_assignments"]
        if item["fields"]["Speakers"] == ["Ben Speaker"]
    )
    onboarding = next(phase for phase in runbook["phases"] if phase["order"] == 5)

    assert ben["fields"]["Due date"] == "2026-08-15T17:00"
    assert onboarding["checkpoint"]["overdue_tasks"] == 1
    assert onboarding["checkpoint"]["asset_safety_boundaries_verified"] == 1
    assert onboarding["checkpoint"]["persistent_quarantine_required"] is False


def test_fixture_pack_contains_only_original_synthetic_people_assets() -> None:
    source_directory = FIXTURES / "source"
    assert not source_directory.exists()
    ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "fixtures/day_n/source/" in ignore_rules

    serialized = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(FIXTURES.rglob("*.json"))
    ).lower()
    for forbidden in ("source_person", "/source/", "speaker-photos/", "spk_"):
        assert forbidden not in serialized

    assets = load("08-assets.json")
    assert assets["reference_source"]["third_party_material_shipped"] is False
    assert assets["usage_rules"]["third_party_people_or_assets_forbidden"] is True
    avatars = assets["generated_speaker_avatars"]
    assert len(avatars) == 7
    for avatar in avatars:
        path = FIXTURES / avatar["local_path"]
        data = path.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack(">II", data[16:24]) == (512, 512)
        assert path.stat().st_size <= 2 * 1024 * 1024

    documents = assets["generated_supporting_documents"]
    assert len(documents) == 1
    document = FIXTURES / documents[0]["local_path"]
    assert document.read_bytes().startswith(b"%PDF-")
    assert document.stat().st_size <= 10 * 1024 * 1024

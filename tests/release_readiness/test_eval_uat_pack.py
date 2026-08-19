"""Completeness guard for the evaluation-derived UAT source pack."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[2]
UAT = ROOT / "uat" / "sessionboard"

UPSTREAM_SHA256 = {
    "specs/01-call-for-papers.yaml": (
        "225a5b18a405cded11ed35a4379836a1c30e06558ddd14cbf9020214c564e42c"
    ),
    "specs/02-abstract-management.yaml": (
        "3e55a38d1f4c7a34870f8d4eb13eb43cfb06f259dd4aac9db84459f546dfd6d6"
    ),
    "specs/03-speaker-management.yaml": (
        "203459248ca58ebe37af3aba8675890f9b1efe88a5cd696ea5759d0b9573524f"
    ),
    "specs/04-content-management.yaml": (
        "171335037b2fb4aeaa93f32f7b8d033f975a598e3f70d74d189c6db16b66b8de"
    ),
    "specs/05-ai-agenda.yaml": "0de0bc8ad3e57ea48fbd285e725ccd34ce716c22165265839221b267fd498ef2",
    "specs/06-public-widgets.yaml": (
        "af5f8e3f4cd748486291059029cc8065172dc9add4e06d7cc038c25610d81dfd"
    ),
    "specs/07-speaker-crm.yaml": "60e8f47b69046d3b66df5913d1e2c254b2f1fb8b130851ef325bc5b46a1b5f3d",
    "fixtures/aie-europe.json": "bc0cc3a240410242e6752546a5042c678d4fdc371d2569ae663afe31d095e4eb",
    "fixtures/headshot.png": "9727e98b19375716494cffa46f09edc60624d8a381199cc63a420a6c0f7174fc",
    "fixtures/sample-data.json": "14d93a843e060be18f6341058b4e994d0d7c16c46c5419904df04484ea4c6564",
    "fixtures/slides.pdf": "ffc81c3487a25fb311ecba34beaa9a99e88815e87fd9d7b7a46e7c301da42484",
    "fixtures/speakers.csv": "bc27669313590e0730f8bb976ba231e9576f56c28f73f9f82e5fdb48ce6dcfbc",
}


def _ids(block: str) -> list[str]:
    return re.findall(r"^  - id: ([A-Z]+-(?:S)?\d+[A-Z]*)$", block, flags=re.MULTILINE)


def test_eval_uat_pack_preserves_every_scenario_step_and_rubric() -> None:
    manifest = json.loads((UAT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["commit"] == "81099583ff4c878f310dc3c48e4916318678425d"

    scenario_count = 0
    step_count = 0
    rubric_count = 0
    all_ids: set[str] = set()

    for area in manifest["areas"].values():
        text = (UAT / "specs" / area["file"]).read_text(encoding="utf-8")
        scenario_text, rubric_text = text.split("\nrubric:\n", maxsplit=1)
        scenario_text = scenario_text.split("\nscenarios:\n", maxsplit=1)[1]
        scenario_ids = _ids(scenario_text)
        rubric_ids = _ids(rubric_text)

        assert scenario_ids == [item["id"] for item in area["scenarios"]]
        assert rubric_ids == area["rubric_ids"]
        assert not all_ids.intersection(scenario_ids + rubric_ids)
        all_ids.update(scenario_ids + rubric_ids)

        for index, scenario in enumerate(area["scenarios"]):
            start = scenario_text.index(f"  - id: {scenario['id']}")
            next_id = (
                area["scenarios"][index + 1]["id"] if index + 1 < len(area["scenarios"]) else None
            )
            end = (
                scenario_text.index(f"  - id: {next_id}", start) if next_id else len(scenario_text)
            )
            scenario_block = scenario_text[start:end]
            assert "    name:" in scenario_block
            assert "    persona:" in scenario_block
            assert "    steps: |" in scenario_block
            assert "    success_signals:" in scenario_block
            numbered_steps = re.findall(r"^      (\d+)\.", scenario_block, flags=re.MULTILINE)
            assert numbered_steps == [str(value) for value in range(1, scenario["steps"] + 1)]
            step_count += len(numbered_steps)

        for rubric_id in rubric_ids:
            start = rubric_text.index(f"  - id: {rubric_id}")
            following = rubric_ids[rubric_ids.index(rubric_id) + 1 :]
            end = (
                rubric_text.index(f"  - id: {following[0]}", start)
                if following
                else len(rubric_text)
            )
            rubric_block = rubric_text[start:end]
            for required in (
                "criterion:",
                "weight:",
                "type:",
                "testability:",
                "pass_criteria:",
                "evidence:",
            ):
                assert f"    {required}" in rubric_block, f"{rubric_id} lacks {required}"

        scenario_count += len(scenario_ids)
        rubric_count += len(rubric_ids)

    assert scenario_count == manifest["totals"]["scenarios"]
    assert step_count == manifest["totals"]["scenario_steps"]
    assert rubric_count == manifest["totals"]["rubric_tests"]


SANITIZED_SHA256 = {
    "specs/06-public-widgets.yaml":
        "8f98d5641db737c196e88842dbe472468e055dada2c9c9a11595a5cde2bc6f0a",
    "fixtures/sample-data.json":
        "f93a35599fcc850908a4c46498c714a87c10b51ddbeaf7e501f9ee53206c6073",
}


def test_packaging_preserves_sanitized_derivation_and_unchanged_source_bytes() -> None:
    derivation = json.loads((UAT / "derivation.json").read_text(encoding="utf-8"))
    assert set(derivation["files"]) == set(UPSTREAM_SHA256)
    for relative_path, upstream in UPSTREAM_SHA256.items():
        record = derivation["files"][relative_path]
        expected = SANITIZED_SHA256.get(relative_path, upstream)
        assert record["upstream_sha256"] == upstream
        assert record["packaged_sha256"] == expected
        assert bool(record["redactions"]) == (relative_path in SANITIZED_SHA256)
        payload = (UAT / relative_path).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == expected, relative_path

    json.loads((UAT / "fixtures" / "sample-data.json").read_text(encoding="utf-8"))
    json.loads((UAT / "fixtures" / "aie-europe.json").read_text(encoding="utf-8"))
    assert (UAT / "fixtures" / "headshot.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (UAT / "fixtures" / "slides.pdf").read_bytes().startswith(b"%PDF-")


def test_packaging_documents_exact_credential_redactions_without_old_values() -> None:
    derivation = json.loads((UAT / "derivation.json").read_text(encoding="utf-8"))
    sample = json.loads((UAT / "fixtures/sample-data.json").read_text(encoding="utf-8"))
    assert all(person["password"] is None for person in sample["identities"].values())
    assert derivation["files"]["fixtures/sample-data.json"]["redactions"] == [
        {"json_pointer": f"/identities/{role}/password", "replacement": None}
        for role in ("organizer", "speaker", "speaker2", "reviewer")
    ]
    changes = derivation["files"]["specs/06-public-widgets.yaml"]["redactions"]
    assert [item["source_ref"] for item in changes] == ["EMB-S1:2", "EMB-S2:1"]
    assert all(item["replacement"] == "[RUNTIME_ONLY]" for item in changes)
    source = (UAT / "specs/06-public-widgets.yaml").read_text(encoding="utf-8")
    assert source.count("password [RUNTIME_ONLY]") == 2


def test_eval_uat_result_template_requires_one_result_per_test() -> None:
    manifest = json.loads((UAT / "manifest.json").read_text(encoding="utf-8"))
    results = json.loads((UAT / "results.template.json").read_text(encoding="utf-8"))

    expected_steps = [
        f"{scenario['id']}:{number}"
        for area in manifest["areas"].values()
        for scenario in area["scenarios"]
        for number in range(1, scenario["steps"] + 1)
    ]
    source_refs = [
        step["source_ref"]
        for scenario in results["scenario_results"]
        for step in scenario["steps"]
        if step["source_ref"]
    ]
    assert source_refs == expected_steps
    rubric_ids = [r["rubric_id"] for r in results["rubric_results"]]
    assert rubric_ids == [rid for area in manifest["areas"].values() for rid in area["rubric_ids"]]


def test_source_wiring_suffixed_scenario_ids_are_supported() -> None:
    assert _ids("  - id: CFP-S1B\n  - id: CFP-S1C\n") == ["CFP-S1B", "CFP-S1C"]

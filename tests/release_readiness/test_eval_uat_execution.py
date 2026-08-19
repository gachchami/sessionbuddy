"""Behavioral tests of the offline UAT compiler, fixtures and verdict validator."""

import csv
import io
import json
from copy import deepcopy

import pytest

from scripts import eval_uat as uat


def passed() -> dict:
    return {
        "status": "pass",
        "failure_class": None,
        "block_cause": None,
        "notes": "Observed expected state in the test fixture",
        "evidence": ["safe-capture.png"],
    }


def complete_report() -> dict:
    _, report = uat.build()
    context = report["context"]
    context.update(
        run_id="test-run",
        target_url="https://uat.example.test",
        deployment_version="test-version",
        run_date="2028-01-01",
        organization_id="org-primary",
        primary_event_id="event-primary",
        secondary_event_id="event-secondary",
        cfp_url="https://uat.example.test/cfp/test",
        fixture_preflight_evidence=["preflight.json"],
    )
    for role, person in context["personas"].items():
        person.update(
            account_id=f"account-{role}",
            email=f"{role}@example.test",
            display_name=f"Mapped {role}",
            verified_role=role,
            evidence=["identity.png"],
        )
    for scenario in report["scenario_results"]:
        scenario.update(status="pass", precondition=passed(), handoff=passed())
        for step in scenario["steps"]:
            step.update(passed(), identity_used="organizer", event_id="event-primary")
            if step["step_id"] == "CFP-S1:12":
                step["event_id"] = "event-secondary"
    for rubric in report["rubric_results"]:
        rubric.update(passed())
        for part in ("automatic", "manual"):
            if rubric[part] is not None:
                rubric[part] = passed()
    return report


def test_compiler_is_self_contained_and_generated_files_are_current() -> None:
    plan, report = uat.build()
    assert uat.read_json(uat.PACK / "execution.json") == plan
    assert uat.read_json(uat.PACK / "results.template.json") == report
    assert uat.read_json(uat.PACK / "bindings.template.json") == report["context"]
    manifest = uat.read_json(uat.PACK / "manifest.json")
    policy = plan["policy"]
    assert len(plan["scenarios"]) == (
        manifest["totals"]["scenarios"]
        + sum(len(parts) - 1 for parts in policy["splits"].values())
        + len(policy["acceptance_checks"])
    )
    assert sum(len(s["steps"]) for s in plan["scenarios"]) == (
        manifest["totals"]["scenario_steps"]
        + sum(len(check["steps"]) for check in policy["acceptance_checks"])
    )
    assert all(r["manual_required"] for r in plan["rubric"] if r["testability"] != "auto")
    uat.validate(report)
    uat.validate(complete_report(), final=True)


def test_split_carries_source_steps_and_orders_dependency_handoffs() -> None:
    plan, _ = uat.build()
    by_id = {s["id"]: s for s in plan["scenarios"]}
    assert [s["id"] for s in plan["scenarios"][:3]] == ["CFP-S1A", "CFP-S1B", "CFP-S1C"]
    assert by_id["CFP-S1B"]["depends_on"] == ["CFP-S1A"]
    assert by_id["CFP-S1C"]["depends_on"] == ["CFP-S1B"]
    assert by_id["CFP-S2"]["depends_on"] == ["CFP-S1C"]
    assert by_id["SPK-S2"]["depends_on"] == ["SPK-S1"]
    assert by_id["SPK-S3"]["depends_on"] == ["SPK-S2"]
    assert "click Accept" in by_id["SPK-S2"]["before"]
    assert "Never execute" in by_id["SPK-S2"]["steps"][0]["execution_correction"]
    seen = set()
    for scenario in plan["scenarios"]:
        assert set(scenario["depends_on"]) <= seen
        seen.add(scenario["id"])


@pytest.mark.parametrize(
    "change",
    [
        "duplicate_scenario",
        "duplicate_step",
        "duplicate_rubric",
        "wrong_source",
        "missing_manual",
        "pending_manual",
        "failed_manual",
        "unmet_gate",
        "bad_identity",
        "bad_event",
        "missing_class",
        "missing_block_cause",
        "pending_final",
        "secret",
    ],
)
def test_validator_rejects_incomplete_or_contradictory_results(change: str) -> None:
    report = complete_report()
    first = report["scenario_results"][0]
    step = first["steps"][0]
    partial = next(r for r in report["rubric_results"] if r["rubric_id"] == "CFP-14")
    if change == "duplicate_scenario":
        report["scenario_results"][1] = deepcopy(first)
    elif change == "duplicate_step":
        first["steps"][1] = deepcopy(step)
    elif change == "duplicate_rubric":
        report["rubric_results"][1] = deepcopy(report["rubric_results"][0])
    elif change == "wrong_source":
        step["source_ref"] = "CFP-S1:99"
    elif change == "missing_manual":
        del partial["manual"]
    elif change == "pending_manual":
        partial["manual"] = uat.outcome()
    elif change == "failed_manual":
        partial["manual"].update(status="fail", failure_class="product")
    elif change == "unmet_gate":
        first["handoff"].update(
            status="blocked", failure_class="identity_fixture", block_cause="Missing event"
        )
    elif change == "bad_identity":
        step["identity_used"] = "unmapped-new-priya"
    elif change == "bad_event":
        step["event_id"] = "same-name-replacement-event"
    elif change == "missing_class":
        step.update(status="fail")
    elif change == "missing_block_cause":
        step.update(status="blocked", failure_class="environment")
    elif change == "pending_final":
        step["status"] = "pending"
    elif change == "secret":
        report["context"]["personas"]["speaker"]["password"] = "synthetic"  # noqa: S105
    with pytest.raises(ValueError):
        uat.validate(report, final=True)


def test_materialized_fixtures_align_csv_and_primary_identity_without_credentials() -> None:
    bindings = complete_report()["context"]
    bindings["personas"]["speaker2"]["account_id"] = None
    bindings["personas"]["speaker2"]["verified_role"] = "pending_invitation"
    files = uat.materialize(bindings)
    data = json.loads(files["sample-data.json"])
    rows = list(csv.DictReader(io.StringIO(files["speakers.csv"])))
    assert rows[0]["name"] == data["identities"]["speaker"]["name"] == "Mapped speaker"
    assert rows[0]["email"] == data["identities"]["speaker"]["email"] == "speaker@example.test"
    assert rows[0]["bio"] == data["identities"]["speaker"]["bio"]
    assert rows[1]["email"] == data["identities"]["speaker2"]["email"]
    assert rows[2]["email"] == "import_contact@example.test"
    assert all("password" not in identity for identity in data["identities"].values())
    assert data["event"]["dates"] == "2028-02-15 to 2028-02-17"
    uat.validate(json.loads(files["results.json"]))


def test_relative_calendar_keeps_review_windows_valid_after_snapshot_expiry() -> None:
    calendar = uat.dates("2028-02-28")
    assert calendar["initial_open"] == "2028-02-27"
    assert calendar["initial_close"] == "2028-03-13"
    assert calendar["final_open"] == "2028-03-14"
    assert calendar["final_close"] == "2028-03-29"


def test_materialization_rejects_unresolved_primary_or_shared_accounts() -> None:
    bindings = complete_report()["context"]
    bindings["personas"]["speaker"]["account_id"] = None
    with pytest.raises(ValueError, match="Existing login"):
        uat.materialize(bindings)
    bindings = complete_report()["context"]
    bindings["personas"]["speaker"]["account_id"] = bindings["personas"]["organizer"]["account_id"]
    with pytest.raises(ValueError, match="Distinct personas"):
        uat.materialize(bindings)


def test_validator_accepts_an_evidenced_fully_blocked_run_without_created_events() -> None:
    report = complete_report()
    report["context"].update(organization_id=None, primary_event_id=None, secondary_event_id=None)
    blocked = {
        **passed(),
        "status": "blocked",
        "failure_class": "environment",
        "block_cause": "Target unavailable before fixture creation",
    }
    for scenario in report["scenario_results"]:
        scenario.update(status="blocked", precondition=deepcopy(blocked), handoff=deepcopy(blocked))
        for step in scenario["steps"]:
            step.update(deepcopy(blocked), identity_used=None, event_id=None)
    for rubric in report["rubric_results"]:
        rubric.update(deepcopy(blocked))
        for part in ("automatic", "manual"):
            if rubric[part] is not None:
                rubric[part] = deepcopy(blocked)
    uat.validate(report, final=True)
    report["scenario_results"][0]["handoff"] = passed()
    with pytest.raises(ValueError, match="Handoff cannot pass"):
        uat.validate(report, final=True)


def test_registered_secondary_event_cannot_replace_primary_event_in_other_steps() -> None:
    report = complete_report()
    report["scenario_results"][0]["steps"][1]["event_id"] = "event-secondary"
    with pytest.raises(ValueError, match="canonical primary"):
        uat.validate(report, final=True)


def test_compiler_runs_without_eval_checkout_or_current_directory_dependency(
    tmp_path, monkeypatch
) -> None:
    expected = uat.build()
    monkeypatch.chdir(tmp_path)
    assert uat.build() == expected


def test_cli_materialize_refuses_existing_evidence_directory(tmp_path, monkeypatch) -> None:
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps(complete_report()["context"]), encoding="utf-8")
    output = tmp_path / "run"
    monkeypatch.setattr(
        "sys.argv", ["eval_uat.py", "materialize", str(bindings), "--output", str(output)]
    )
    assert uat.main() == 0
    before = (output / "results.json").read_bytes()
    assert uat.main() == 1
    assert (output / "results.json").read_bytes() == before


def test_overlay_source_wiring_covers_review_regressions_and_preconditions() -> None:
    plan, _ = uat.build()
    scenarios = {s["id"]: s for s in plan["scenarios"]}
    assert "AIA-S2" in scenarios["SB-TZ-01"]["depends_on"]
    assert all(s["before"] and "precondition" in s["before"] for s in scenarios.values())
    required = {
        "SB-TZ-01": ["wall-clock", "UTC instants", "409", "digest", "audit event"],
        "SB-EMBED-01": ["Basic HTML link", "iframe", "JSON", "XML", "calendar",
                        "matched:false", "no-store", "registry row"],
        "SB-SCORE-01": ["weight 0", "Free text", "null", "restore draft value 35",
                        "absent optional key", "Zero or any retained numeric weight",
                        "persisted rubric evidence plus reload evidence"],
    }
    for scenario_id, phrases in required.items():
        instructions = " ".join(s["source_instruction"] for s in scenarios[scenario_id]["steps"])
        assert all(phrase in instructions for phrase in phrases)
    corrections = plan["policy"]["step_corrections"]
    for ref in ("CFP-S2:2", "ABS-S1:2", "SPK-S1:4"):
        assert "existing mapped primary speaker" in corrections[ref]
    for ref in ("ABS-S1:1", "SPK-S1:10", "SPK-S1:11", "CNT-S1:6", "CNT-S1:7",
                "AIA-S1:4", "AIA-S1:7", "AIA-S1:8", "AIA-S1:9", "AIA-S1:10"):
        assert "calendar." in corrections[ref]


def test_materialization_resolves_task_dates_and_communications() -> None:
    files = uat.materialize(complete_report()["context"])
    calendar = json.loads(files["calendar.json"])
    fixture = json.loads(files["sample-data.json"])
    assert calendar["day_1"] == calendar["event_start"]
    assert calendar["day_3"] == calendar["event_end"]
    assert calendar["confirmation_due"] == calendar["biography_due"] == "2028-01-11"
    assert calendar["day_1"] < calendar["day_2"] < calendar["day_3"]
    assert all("2028-01-01" < value < calendar["event_start"]
               for key, value in calendar.items() if key.endswith("_due"))
    assert calendar["confirmation_due"] in fixture["communications"]["acceptance_body"]
    assert calendar["slides_due"] in " ".join(fixture["tasks_for_speakers"])
    assert fixture["task_due_dates"]["headshot_due"] == calendar["headshot_due"]
    assert "2027-" not in files["sample-data.json"]
    assert "April 1, 2027" not in files["sample-data.json"]


def test_conditional_abs14_absence_requires_consistent_evidenced_verdicts() -> None:
    report = complete_report()
    rubric = next(r for r in report["rubric_results"] if r["rubric_id"] == "ABS-14")
    for result in (rubric, rubric["automatic"], rubric["manual"]):
        result.update(status="not_applicable", notes="Inspected UI and claims: no AI review claim",
                      evidence=["claims-and-ui-review.md"])
    uat.validate(report, final=True)
    rubric["manual"]["status"] = "pass"
    with pytest.raises(ValueError, match="absence must agree"):
        uat.validate(report, final=True)
    rubric["manual"]["status"] = "not_applicable"
    rubric["evidence"] = []
    with pytest.raises(ValueError, match="requires evidence"):
        uat.validate(report, final=True)
    other = complete_report()
    other["rubric_results"][0]["status"] = "not_applicable"
    with pytest.raises(ValueError, match="explicitly conditional"):
        uat.validate(other, final=True)


@pytest.mark.parametrize("rubric_id", ["SPK-07", "CFP-18"])
def test_conditional_manual_fallback_can_be_inapplicable_after_automatic_pass(rubric_id) -> None:
    report = complete_report()
    rubric = next(r for r in report["rubric_results"] if r["rubric_id"] == rubric_id)
    rubric["manual"].update(
        status="not_applicable",
        notes="Automatic execution verified the surface; manual fallback condition is false.",
        evidence=["automatic-surface-verification.png"],
    )
    uat.validate(report, final=True)
    rubric["status"] = "not_applicable"
    with pytest.raises(ValueError, match="explicitly conditional"):
        uat.validate(report, final=True)


@pytest.mark.parametrize("field,value", [("evidence", []), ("notes", " ")])
def test_conditional_manual_absence_requires_evidence_and_observations(field, value) -> None:
    report = complete_report()
    rubric = next(r for r in report["rubric_results"] if r["rubric_id"] == "SPK-07")
    rubric["manual"].update(status="not_applicable")
    rubric["manual"][field] = value
    with pytest.raises(ValueError, match="requires evidence and observations"):
        uat.validate(report, final=True)


@pytest.mark.parametrize("testability", ["auto-partial", "auto"])
def test_required_manual_verdict_cannot_be_waived_by_testability(monkeypatch, testability) -> None:
    report = complete_report()
    plan, template = uat.build()
    expected = next(r for r in plan["rubric"] if r["id"] == "CFP-08")
    assert expected["manual_required"]
    expected["testability"] = testability
    monkeypatch.setattr(uat, "build", lambda: (plan, template))
    rubric = next(r for r in report["rubric_results"] if r["rubric_id"] == "CFP-08")
    rubric["automatic"] = passed()
    rubric["manual"].update(status="not_applicable")
    with pytest.raises(ValueError, match="explicitly conditional"):
        uat.validate(report, final=True)


@pytest.mark.parametrize("content", [
    "password: synthetic", "Authorization: Bearer synthetic",
    "https://example.test/login?token=synthetic",
    "https://example.test/login?%74oken%3Dsynthetic",
    "https://example.test/login?code=synthetic",
    "https://user:synthetic@example.test/",
])
def test_validator_rejects_credential_values_in_notes_and_evidence(content: str) -> None:
    for field, value in (("notes", content), ("evidence", [content])):
        report = complete_report()
        report["scenario_results"][0]["steps"][0][field] = value
        with pytest.raises(ValueError, match="Credential-like"):
            uat.validate(report, final=True)


def test_reserved_secondary_accounts_allow_materialization_but_not_execution() -> None:
    bindings = complete_report()["context"]
    for role in ("reviewer2", "attendee"):
        bindings["personas"][role].update(account_id=None, verified_role="reserved",
                                          evidence=["controlled-address-reserved.md"])
    uat.materialize(bindings)
    report = complete_report()
    report["context"] = bindings
    report["scenario_results"][0]["steps"][0]["identity_used"] = "reviewer2"
    with pytest.raises(ValueError, match="Unverified execution identity"):
        uat.validate(report, final=True)


def test_scenario_without_special_before_still_requires_preflight_evidence() -> None:
    report = complete_report()
    scenario = next(s for s in report["scenario_results"] if s["scenario_id"] == "CFP-S2")
    scenario["precondition"]["evidence"] = []
    with pytest.raises(ValueError, match="requires evidence"):
        uat.validate(report, final=True)


@pytest.mark.parametrize("status", ["pending", "fail", "blocked"])
def test_executed_step_rejected_when_own_precondition_has_not_passed(status: str) -> None:
    report = complete_report()
    scenario = report["scenario_results"][0]
    scenario["precondition"].update(
        status=status,
        failure_class="identity_fixture" if status != "pending" else None,
        block_cause="Fixture not ready" if status == "blocked" else None,
    )
    # Non-final validation allows a pending precondition but must still forbid
    # an executed step. There is no upstream gate on this first scenario.
    with pytest.raises(ValueError, match="Scenario executed before precondition passed"):
        uat.validate(report)


def test_redaction_guidance_is_accepted_without_weakening_credential_screening() -> None:
    report = complete_report()
    report["scenario_results"][0]["steps"][0]["notes"] = "Credentials removed from capture"
    uat.validate(report, final=True)
    report["scenario_results"][0]["steps"][0]["notes"] = "token: redacted"
    with pytest.raises(ValueError, match="Credential-like"):
        uat.validate(report, final=True)


def test_runtime_credentials_do_not_enter_compiled_or_materialized_artifacts(
    monkeypatch, capsys
) -> None:
    sentinel = "NON_CREDENTIAL_LEAK_TEST_SENTINEL"
    monkeypatch.setenv("UAT_SPEAKER_PASSWORD", sentinel)
    plan, report = uat.build()
    files = uat.materialize(complete_report()["context"])
    artifacts = json.dumps((plan, report, files))
    assert sentinel not in artifacts
    assert sentinel not in capsys.readouterr().out
    sample = json.loads(files["sample-data.json"])
    assert all("password" not in identity for identity in sample["identities"].values())
    public_steps = [step for scenario in plan["scenarios"] for step in scenario["steps"]
                    if step["source_ref"] in {"EMB-S1:2", "EMB-S2:1"}]
    assert len(public_steps) == 2
    assert all("[RUNTIME_ONLY]" in step["source_instruction"] for step in public_steps)


def test_materialization_rejects_reintroduced_fixture_credentials_without_echo(
    monkeypatch, capsys
) -> None:
    bindings = complete_report()["context"]
    original_read = uat.read_json
    sentinel = "NON_CREDENTIAL_LEAK_TEST_SENTINEL"

    def injected_fixture(path):
        value = original_read(path)
        if path == uat.PACK / "fixtures" / "sample-data.json":
            value["identities"]["speaker"]["password"] = sentinel
        return value

    monkeypatch.setattr(uat, "read_json", injected_fixture)
    with pytest.raises(ValueError, match="null placeholders") as failure:
        uat.materialize(bindings)
    assert "NON_CREDENTIAL_LEAK_TEST_SENTINEL" not in str(failure.value)
    assert "NON_CREDENTIAL_LEAK_TEST_SENTINEL" not in capsys.readouterr().out


def test_build_rejects_changed_packaged_digest_without_exposing_content(monkeypatch) -> None:
    original_read = uat.read_json

    def changed_manifest(path):
        value = original_read(path)
        if path == uat.PACK / "derivation.json":
            value["files"]["fixtures/sample-data.json"]["packaged_sha256"] = "0" * 64
        return value

    monkeypatch.setattr(uat, "read_json", changed_manifest)
    with pytest.raises(ValueError, match="sanitized provenance"):
        uat.build()

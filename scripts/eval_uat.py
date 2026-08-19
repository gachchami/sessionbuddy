"""Build and validate the self-contained UAT packet; never contact the application."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

PACK = Path(__file__).resolve().parents[1] / "uat" / "sessionboard"
STATES = {"pending", "pass", "fail", "blocked", "not_applicable"}
FAILURES = {"product", "harness", "identity_fixture", "provider", "environment"}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def blocks(text: str) -> dict[str, str]:
    """Index this frozen YAML layout without interpreting or rewriting its prose.

    This is not a general YAML parser. Sanitized snapshot hashes are independently
    guarded. Unknown IDs, step layouts or testability values fail the build.
    """
    matches = list(re.finditer(r"^  - id: ([A-Z]+-(?:S)?\d+[A-Z]*)$", text, re.M))
    return {
        match[1]: text[
            match.start() : matches[i + 1].start() if i + 1 < len(matches) else len(text)
        ]
        for i, match in enumerate(matches)
    }


def outcome() -> dict:
    return {
        "status": "pending",
        "failure_class": None,
        "block_cause": None,
        "notes": "",
        "evidence": [],
    }


def reject_credentials(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if any(word in key.lower() for word in ("password", "cookie", "token", "secret")):
                raise ValueError("Credential fields must not be stored in UAT records")
            reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            reject_credentials(child)
    elif isinstance(value, str):
        # Defense in depth for prose and evidence URLs, not a secret detector.
        decoded = unquote(value)
        if re.search(
            r"(?i)(?:\b(?:password|cookie|authorization|[\w-]*token|secret)\s*[:=]\s*\S+"
            r"|\bbearer\s+\S+|https?://[^\s/@]+:[^\s/@]+@"
            r"|[?&](?:code|signature|sig|key|x-amz-signature)=\S+)",
            decoded,
        ):
            raise ValueError("Credential-like content must be redacted from UAT records")


def verify_packaged_snapshot() -> None:
    """Fail before compiling credentials accidentally restored to source fixtures."""
    derivation = read_json(PACK / "derivation.json")
    for relative, record in derivation["files"].items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or path.parts[0] not in {"specs", "fixtures"}:
            raise ValueError("Invalid packaged snapshot path")
        if hashlib.sha256((PACK / path).read_bytes()).hexdigest() != record["packaged_sha256"]:
            raise ValueError("Packaged snapshot differs from sanitized provenance")
    fixture = read_json(PACK / "fixtures" / "sample-data.json")
    for identity in fixture["identities"].values():
        if identity.get("password") is not None:
            raise ValueError("Packaged credentials must remain null placeholders")


def build() -> tuple[dict, dict]:
    verify_packaged_snapshot()
    manifest = read_json(PACK / "manifest.json")
    overlay = read_json(PACK / "overlay.json")
    plan = {"overlay_version": overlay["version"], "policy": overlay, "scenarios": [], "rubric": []}
    aliases: dict[str, str] = {}
    for area_name, area in manifest["areas"].items():
        text = (PACK / "specs" / area["file"]).read_text(encoding="utf-8")
        source_scenarios, source_rubric = text.split("\nrubric:\n", 1)
        source_blocks = blocks(source_scenarios)
        if list(source_blocks) != [s["id"] for s in area["scenarios"]]:
            raise ValueError(f"Scenario inventory mismatch: {area_name}")
        previous = None
        for source in area["scenarios"]:
            source_id = source["id"]
            block = source_blocks[source_id]
            step_text = block.split("    steps: |\n", 1)[1].split("    success_signals:", 1)[0]
            matches = list(re.finditer(r"^      (\d+)\. ", step_text, re.M))
            if [int(m[1]) for m in matches] != list(range(1, source["steps"] + 1)):
                raise ValueError(f"Step inventory mismatch: {source_id}")
            steps = {}
            for i, match in enumerate(matches):
                ref = f"{source_id}:{match[1]}"
                end = matches[i + 1].start() if i + 1 < len(matches) else len(step_text)
                steps[int(match[1])] = {
                    "id": ref,
                    "source_ref": ref,
                    "source_instruction": step_text[match.end() : end].rstrip(),
                    "execution_correction": overlay["step_corrections"].get(ref),
                }
            segments = overlay["splits"].get(
                source_id,
                [
                    {
                        "id": source_id,
                        "steps": list(steps),
                        "purpose": source_id,
                    }
                ],
            )
            if [n for segment in segments for n in segment["steps"]] != list(steps):
                raise ValueError(f"Split drops or repeats source steps: {source_id}")
            for segment in segments:
                dependencies = (
                    [previous] if previous else overlay["area_dependencies"].get(source_id, [])
                )
                plan["scenarios"].append(
                    {
                        "id": segment["id"],
                        "area": area_name,
                        "source_scenario": source_id,
                        "purpose": segment["purpose"],
                        "starting_persona": re.search(r"^    persona: (.+)$", block, re.M)[1],
                        "depends_on": dependencies,
                        "before": " ".join(
                            filter(None, (overlay["default_precondition"],
                                          overlay["before_scenarios"].get(source_id)))
                        ),
                        "success_signals_source": block.split("    success_signals:\n", 1)[
                            1
                        ].rstrip(),
                        "steps": [steps[n] for n in segment["steps"]],
                    }
                )
                previous = segment["id"]
            aliases[source_id] = previous
        rubric_blocks = blocks(source_rubric)
        if list(rubric_blocks) != area["rubric_ids"]:
            raise ValueError(f"Rubric inventory mismatch: {area_name}")
        for rubric_id, block in rubric_blocks.items():
            testability = re.search(r"^    testability: (.+)$", block, re.M)[1].strip()
            if testability not in {"auto", "auto-partial", "manual"}:
                raise ValueError(f"Unknown testability: {rubric_id}")
            references = re.search(r"^    scenarios: \[(.*)\]", block, re.M)
            plan["rubric"].append(
                {
                    "id": rubric_id,
                    "area": area_name,
                    "testability": testability,
                    "manual_required": testability != "auto" or "    manual_instructions:" in block,
                    "source_scenarios": references[1].replace(" ", "").split(",")
                    if references
                    else [],
                    "source_text": block.rstrip(),
                }
            )
    for scenario in plan["scenarios"]:
        scenario["depends_on"] = [aliases.get(dep, dep) for dep in scenario["depends_on"]]
    for check in overlay["acceptance_checks"]:
        plan["scenarios"].append(
            {
                "id": check["id"],
                "area": "sessionbuddy",
                "source_scenario": None,
                "purpose": check["name"],
                "starting_persona": "organizer",
                "depends_on": check["depends_on"],
                "before": overlay["default_precondition"]
                + " Preserve fixture state; record and restore any temporary edits.",
                "success_signals_source": "Evidence for every asserted behavior in every step.",
                "steps": [
                    {
                        "id": f"{check['id']}:{i}",
                        "source_ref": None,
                        "source_instruction": instruction,
                        "execution_correction": None,
                    }
                    for i, instruction in enumerate(check["steps"], 1)
                ],
            }
        )
    result = {
        "overlay_version": overlay["version"],
        "context": {
            "run_id": None,
            "target_url": None,
            "deployment_version": None,
            "run_date": None,
            "organization_id": None,
            "primary_event_id": None,
            "secondary_event_id": None,
            "cfp_url": None,
            "fixture_preflight_evidence": [],
            "personas": {
                role: {
                    "account_id": None,
                    "email": None,
                    "display_name": None,
                    "verified_role": None,
                    "evidence": [],
                }
                for role in overlay["identity_policy"]
            },
        },
        "scenario_results": [],
        "rubric_results": [],
    }
    for scenario in plan["scenarios"]:
        result["scenario_results"].append(
            {
                "scenario_id": scenario["id"],
                "status": "pending",
                "precondition": outcome(),
                "handoff": outcome(),
                "steps": [
                    {
                        "step_id": step["id"],
                        "source_ref": step["source_ref"],
                        "identity_used": None,
                        "event_id": None,
                        "no_event_reason": None,
                        **outcome(),
                    }
                    for step in scenario["steps"]
                ],
            }
        )
    for rubric in plan["rubric"]:
        result["rubric_results"].append(
            {
                "rubric_id": rubric["id"],
                **outcome(),
                "automatic": outcome() if rubric["testability"] != "manual" else None,
                "manual": outcome() if rubric["manual_required"] else None,
            }
        )
    return plan, result


def indexed(items: list[dict], key: str, expected: list[str]) -> dict:
    ids = [item[key] for item in items]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ValueError(f"Missing, duplicate or unknown {key}")
    return {item[key]: item for item in items}


def check_outcome(item: dict, *, final: bool, allow_na: bool = False) -> None:
    required = {"status", "failure_class", "block_cause", "notes", "evidence"}
    if not required <= item.keys():
        raise ValueError("Incomplete outcome record")
    status = item["status"]
    if status not in STATES or (final and status == "pending"):
        raise ValueError("Invalid or pending final outcome")
    if status == "not_applicable" and not allow_na:
        raise ValueError("not_applicable requires an explicitly conditional source criterion")
    if not isinstance(item["evidence"], list) or not isinstance(item["notes"], str):
        raise ValueError("Evidence must be a list and notes must be text")
    if status != "pending" and (not item["evidence"] or not item["notes"].strip()):
        raise ValueError("An assessed outcome requires evidence and observations")
    if status in {"fail", "blocked"} and item["failure_class"] not in FAILURES:
        raise ValueError("Failure class is required")
    if status == "blocked" and not item["block_cause"]:
        raise ValueError("Block cause is required")


def validate(report: dict, *, final: bool = False) -> None:
    reject_credentials(report)
    plan, _ = build()
    if report["overlay_version"] != plan["overlay_version"]:
        raise ValueError("Overlay version mismatch")
    context = report["context"]
    required_context = {
        "run_id",
        "target_url",
        "deployment_version",
        "run_date",
        "organization_id",
        "primary_event_id",
        "secondary_event_id",
        "cfp_url",
        "fixture_preflight_evidence",
        "personas",
    }
    if not required_context <= context.keys():
        raise ValueError("Missing run context")
    if final:
        # A fully blocked preflight may never create events; missing records must
        # not prevent handing off an honest failure report. Executed steps below
        # still require resolved context and cannot claim no-event arbitrarily.
        for key in (
            "run_id",
            "target_url",
            "deployment_version",
            "run_date",
            "fixture_preflight_evidence",
        ):
            if not context[key]:
                raise ValueError(f"Unresolved run context: {key}")
        date.fromisoformat(context["run_date"])
        if (
            context["primary_event_id"]
            and context["primary_event_id"] == context["secondary_event_id"]
        ):
            raise ValueError("Primary and secondary events must differ")
    if set(context["personas"]) != set(plan["policy"]["identity_policy"]):
        raise ValueError("Persona table differs from execution policy")
    accounts = [p["account_id"] for p in context["personas"].values() if p["account_id"]]
    if len(accounts) != len(set(accounts)):
        raise ValueError("Personas must not silently share an account")
    emails = [p["email"].strip().lower() for p in context["personas"].values() if p["email"]]
    if len(emails) != len(set(emails)):
        raise ValueError("Personas must not silently share an email")
    scenarios = indexed(
        report["scenario_results"], "scenario_id", [s["id"] for s in plan["scenarios"]]
    )
    for expected in plan["scenarios"]:
        actual = scenarios[expected["id"]]
        if actual["status"] not in STATES - {"not_applicable"}:
            raise ValueError("Invalid scenario status")
        check_outcome(actual["precondition"], final=final)
        check_outcome(actual["handoff"], final=final)
        steps = indexed(actual["steps"], "step_id", [s["id"] for s in expected["steps"]])
        for step in expected["steps"]:
            observed = steps[step["id"]]
            if not {"identity_used", "event_id", "no_event_reason"} <= observed.keys():
                raise ValueError("Missing step identity/event fields")
            if observed["source_ref"] != step["source_ref"]:
                raise ValueError("Source traceability changed")
            check_outcome(observed, final=final)
            if observed["status"] not in {"pending", "blocked"}:
                if actual["precondition"]["status"] != "pass":
                    raise ValueError("Scenario executed before precondition passed")
                if any(scenarios[d]["handoff"]["status"] != "pass" for d in expected["depends_on"]):
                    raise ValueError("Scenario executed before upstream handoff")
                identity = observed["identity_used"]
                if identity != "anonymous":
                    persona = context["personas"].get(identity)
                    if not persona or not all(
                        persona.get(k)
                        for k in (
                            "account_id",
                            "email",
                            "display_name",
                            "verified_role",
                            "evidence",
                        )
                    ):
                        raise ValueError("Unverified execution identity")
                event_id = observed["event_id"]
                if event_id:
                    if event_id not in {context["primary_event_id"], context["secondary_event_id"]}:
                        raise ValueError("Step used an unregistered event")
                    if not context["organization_id"]:
                        raise ValueError("Event step lacks organization binding")
                    if step["id"] == "CFP-S1:12":
                        if event_id != context["secondary_event_id"]:
                            raise ValueError("Isolation probe must identify the secondary event")
                    elif event_id != context["primary_event_id"]:
                        raise ValueError("Step left the canonical primary event")
                elif not observed["no_event_reason"]:
                    raise ValueError("Event ID or explicit no-event reason required")
                elif not (step["id"].endswith(":1") or expected["area"] == "speaker-crm"):
                    raise ValueError("Event-scoped step cannot bypass canonical event binding")
        statuses = [s["status"] for s in actual["steps"]]
        if actual["handoff"]["status"] == "pass" and (
            actual["precondition"]["status"] != "pass" or "pass" not in statuses
        ):
            raise ValueError("Handoff cannot pass without an executed, prepared fixture")
        if actual["status"] == "pass" and any(s != "pass" for s in statuses):
            raise ValueError("Scenario pass contradicts step results")
        if final and actual["status"] == "pending":
            raise ValueError("Pending final scenario")
    rubrics = indexed(report["rubric_results"], "rubric_id", [r["id"] for r in plan["rubric"]])
    for expected in plan["rubric"]:
        item = rubrics[expected["id"]]
        conditional = expected["id"] in plan["policy"]["conditional_rubrics"]
        check_outcome(item, final=final, allow_na=conditional)
        parts = []
        for kind, needed in (
            ("automatic", expected["testability"] != "manual"),
            ("manual", expected["manual_required"]),
        ):
            if needed:
                if not isinstance(item.get(kind), dict):
                    raise ValueError(f"Missing {kind} verdict: {expected['id']}")
                allow_na = conditional or (kind == "manual" and expected["testability"] == "auto")
                check_outcome(item[kind], final=final, allow_na=allow_na)
                parts.append(item[kind]["status"])
            elif item.get(kind) is not None:
                raise ValueError(f"Unexpected {kind} verdict")
        if item["status"] == "pass" and any(s not in {"pass", "not_applicable"} for s in parts):
            raise ValueError("Rubric pass lacks completed automatic/manual verdicts")
        if conditional and "not_applicable" in [item["status"], *parts]:
            if any(s != "not_applicable" for s in [item["status"], *parts]):
                raise ValueError("Conditional absence must agree across all verdicts")


def dates(run_date: str) -> dict:
    anchor = date.fromisoformat(run_date)
    offsets = {
        "cfp_open": -1,
        "cfp_close": 30,
        "initial_open": -1,
        "initial_close": 14,
        "final_open": 15,
        "final_close": 30,
        "event_start": 45,
        "event_end": 47,
        "day_1": 45,
        "day_2": 46,
        "day_3": 47,
        "confirmation_due": 10,
        "biography_due": 10,
        "release_due": 18,
        "headshot_due": 17,
        "slides_due": 34,
        "secondary_start": 60,
        "secondary_end": 61,
    }
    return {
        "time_zone": "America/Los_Angeles",
        **{key: (anchor + timedelta(days=days)).isoformat() for key, days in offsets.items()},
    }


def materialize(bindings: dict) -> dict[str, str]:
    """Produce only run-local public fixture data; credentials are never copied."""
    reject_credentials(bindings)
    verify_packaged_snapshot()
    if not bindings.get("run_date") or not bindings.get("target_url"):
        raise ValueError("run_date and exact target_url are required")
    personas = bindings["personas"]
    for role in read_json(PACK / "overlay.json")["identity_policy"]:
        if not all(
            personas.get(role, {}).get(k)
            for k in ("email", "display_name", "verified_role", "evidence")
        ):
            raise ValueError(f"Resolve persona at preflight: {role}")
        if role in {"organizer", "speaker", "reviewer"} and not personas[role].get("account_id"):
            raise ValueError(f"Existing login account required: {role}")
    accounts = [p["account_id"] for p in personas.values() if p.get("account_id")]
    if len(set(accounts)) != len(accounts):
        raise ValueError("Distinct personas require distinct accounts")
    fixture = read_json(PACK / "fixtures" / "sample-data.json")
    for role, identity in fixture["identities"].items():
        identity.pop("password", None)
        identity.update(name=personas[role]["display_name"], email=personas[role]["email"])
    calendar = dates(bindings["run_date"])
    fixture["event"]["dates"] = f"{calendar['event_start']} to {calendar['event_end']}"
    fixture["event"]["time_zone"] = calendar["time_zone"]
    fixture["communications"]["acceptance_body"] = (
        fixture["communications"]["acceptance_body"].replace(
            "April 1, 2027", calendar["confirmation_due"]
        )
    )
    fixture["tasks_for_speakers"] = [
        task.replace("2027-05-01", calendar["slides_due"])
        for task in fixture["tasks_for_speakers"]
    ]
    fixture["task_due_dates"] = {
        key: value for key, value in calendar.items() if key.endswith("_due")
    }
    fixture["_email_note"] = (
        "Identities are resolved from this run's bindings. No external eval config is used."
    )
    rows = list(csv.DictReader((PACK / "fixtures" / "speakers.csv").read_text().splitlines()))
    for row, role in zip(rows, ("speaker", "speaker2", "import_contact"), strict=True):
        row.update(name=personas[role]["display_name"], email=personas[role]["email"])
        if role in fixture["identities"]:
            identity = fixture["identities"][role]
            row.update(title=identity["title"], company=identity["company"], bio=identity["bio"])
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    _, result = build()
    result["context"].update(bindings)
    validate(result)
    return {
        "sample-data.json": encoded(fixture),
        "speakers.csv": output.getvalue(),
        "calendar.json": encoded(calendar),
        "results.json": encoded(result),
    }


def encoded(value: dict) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "check", "validate", "materialize"))
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--final", action="store_true")
    args = parser.parse_args()
    try:
        if args.command in {"build", "check"}:
            plan, result = build()
            for name, payload in (
                ("execution.json", plan),
                ("results.template.json", result),
                ("bindings.template.json", result["context"]),
            ):
                content = encoded(payload)
                if args.command == "build":
                    (PACK / name).write_text(content, encoding="utf-8")
                elif (PACK / name).read_text(encoding="utf-8") != content:
                    raise ValueError(f"Generated file out of date: {name}")
            validate(result)
        elif args.command == "materialize":
            if not args.path or not args.output:
                raise ValueError("materialize requires bindings file and --output")
            files = materialize(read_json(args.path))
            if args.output.exists():
                raise ValueError(
                    "Use a new output directory; existing evidence is never overwritten"
                )
            args.output.mkdir(parents=True)
            for name, content in files.items():
                (args.output / name).write_text(content, encoding="utf-8")
        else:
            if not args.path:
                raise ValueError("validate requires a result file")
            validate(read_json(args.path), final=args.final)
        print("UAT validation passed; this does not assert product acceptance.")
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"UAT validation failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

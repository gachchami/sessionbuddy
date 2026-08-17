import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from route_inventory import document_routes

from sessionbuddy.api.app import app
from sessionbuddy.observability import record_failure, record_integrity_signal, record_timing

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_timing_rejects_unbounded_metric_names() -> None:
    request = SimpleNamespace(state=SimpleNamespace(timings={}))

    with pytest.raises(ValueError, match="Unsupported timing phase"):
        record_timing(request, "tenant-secret", 1.0)


def test_timing_accumulates_safe_phase() -> None:
    request = SimpleNamespace(state=SimpleNamespace(timings={}))

    record_timing(request, "db", 2.5)
    record_timing(request, "db", 1.5)

    assert request.state.timings == {"db": 4.0}


def test_every_api_route_has_observability_registration() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "observability" / "manifest.json").read_text(encoding="utf-8")
    )
    registered = set(manifest["api_routes"])
    openapi = app.openapi()
    runtime_routes = {
        f"{method.upper()} {path}"
        for path, operations in openapi["paths"].items()
        for method in operations
    }

    assert runtime_routes == registered


def test_observability_registration_is_human_actionable() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "observability" / "manifest.json").read_text(encoding="utf-8")
    )

    for registration in manifest["api_routes"].values():
        assert registration["owner"]
        assert registration["benchmark"]
        assert registration["dashboards"]
        assert (PROJECT_ROOT / registration["runbook"].split("#", maxsplit=1)[0]).is_file()
        assert registration["slo"]["p95_ms"] > 0


def test_integrity_signals_are_bounded_and_actionable() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "observability" / "manifest.json").read_text(encoding="utf-8")
    )
    signals = manifest["integrity_signals"]
    cursor = signals["sessionbuddy.signed_cursor.shape_failure"]
    assert cursor["alert_condition"] == "count > 0 in 5m"
    assert cursor["group_by"] == ["cursor_contract", "field", "constraint"]
    assert set(cursor["group_by"]) <= set(cursor["dimensions"])
    assert cursor["owner"]
    assert cursor["dashboards"]
    assert (PROJECT_ROOT / cursor["runbook"].split("#", maxsplit=1)[0]).is_file()


def test_integrity_signal_sanitizes_drift_without_changing_control_flow(capsys) -> None:
    request = SimpleNamespace(
        scope={},
        state=SimpleNamespace(request_id="request-a"),
    )

    record_integrity_signal(
        request,
        "sessionbuddy.signed_cursor.shape_failure",
        cursor_contract="new-contract",
        field="new-field",
        constraint="new-constraint",
    )

    event = json.loads(capsys.readouterr().out)
    assert event["cursor_contract"] == "other"
    assert event["field"] == "other"
    assert event["constraint"] == "other"


def test_failure_evidence_groups_the_stack_without_logging_the_message() -> None:
    request = SimpleNamespace(state=SimpleNamespace())
    try:
        raise RuntimeError("private proposal content must not reach telemetry")
    except RuntimeError as exception:
        record_failure(request, exception)

    assert request.state.failure["exception_type"] == "RuntimeError"
    assert request.state.failure["location"].endswith(
        ":test_failure_evidence_groups_the_stack_without_logging_the_message"
    )
    assert len(request.state.failure["fingerprint"]) == 16
    assert "private proposal" not in json.dumps(request.state.failure)


def test_failure_fingerprint_disambiguates_same_named_modules() -> None:
    def evidence(filename: str) -> dict[str, str]:
        request = SimpleNamespace(state=SimpleNamespace())
        try:
            # Synthetic code objects let the test prove two deployed router.py
            # modules do not collapse without importing either application module.
            exec(  # noqa: S102 - fixed test source, never user-controlled
                compile("raise RuntimeError('private')", filename, "exec"), {}
            )
        except RuntimeError as exception:
            record_failure(request, exception)
        return request.state.failure

    evaluation = evidence("/workspace/src/sessionbuddy/evaluation/router.py")
    scheduling = evidence("/workspace/src/sessionbuddy/scheduling/router.py")

    assert evaluation["location"].startswith("sessionbuddy/evaluation/router.py:")
    assert scheduling["location"].startswith("sessionbuddy/scheduling/router.py:")
    assert evaluation["fingerprint"] != scheduling["fingerprint"]


def test_every_document_route_is_registered_or_explicitly_excluded() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "observability" / "manifest.json").read_text(encoding="utf-8")
    )
    pages = set(manifest["pages"])
    excluded = set(manifest["excluded_routes"])

    discovered = document_routes(app)
    assert pages & excluded == set(), "a route cannot be both registered and excluded"
    missing = discovered - pages - excluded
    stale = (pages | excluded) - discovered
    assert missing == set(), f"unregistered document routes: {sorted(missing)}"
    assert stale == set(), f"manifest entries for routes that no longer exist: {sorted(stale)}"
    for path, entry in manifest["excluded_routes"].items():
        assert entry["reason"].strip(), path


def test_page_registrations_are_human_actionable() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "observability" / "manifest.json").read_text(encoding="utf-8")
    )

    for path, registration in manifest["pages"].items():
        assert registration["owner"], path
        assert registration["classification"] in {
            "public",
            "authenticated",
            "synthetic/local",
            "personalized/local",
        }, path
        assert registration["journeys"], path
        vitals = registration["web_vitals"]
        assert vitals["lcp_p75_ms"] > 0 and vitals["inp_p75_ms"] > 0, path
        assert vitals["cls_p75_max"] > 0, path
        assert registration["telemetry_schema"], path
        assert registration["dashboards"], path
        assert (PROJECT_ROOT / registration["runbook"].split("#", maxsplit=1)[0]).is_file(), path


def test_async_handlers_cover_every_queue_cron_and_workflow_entrypoint() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "observability" / "manifest.json").read_text(encoding="utf-8")
    )
    handlers = manifest["async_handlers"]
    wrangler = json.loads((PROJECT_ROOT / "wrangler.jsonc").read_text(encoding="utf-8"))
    entry_source = (PROJECT_ROOT / "src" / "entry.py").read_text(encoding="utf-8")

    # Every consumer function wired in entry.py belongs to exactly one entry.
    consumer_functions = (
        "dispatch_stuck_deliveries",
        "purge_expired_staged_assets",
        "purge_pending_branding_assets",
        "purge_expired_speaker_uploads",
        "consume_scan_job",
        "consume_reminder",
        "consume_delivery",
    )
    for function_name in consumer_functions:
        assert function_name in entry_source, function_name
        owners = [
            key for key, entry in handlers.items() if function_name in entry["handler"]
        ]
        assert len(owners) == 1, (function_name, owners)

    # Every configured queue consumer and cron trigger is represented, so a
    # rename or a new consumer cannot silently escape the manifest.
    for consumer in wrangler["queues"]["consumers"]:
        matching = [
            key
            for key, entry in handlers.items()
            if entry["kind"] == "queue" and consumer["queue"] in entry["trigger"]
        ]
        assert matching, f"queue {consumer['queue']} has no async_handlers entry"
        for key in matching:
            assert consumer["dead_letter_queue"] in handlers[key]["trigger"], key
    for cron in wrangler["triggers"]["crons"]:
        assert any(
            cron in entry["trigger"]
            for entry in handlers.values()
            if entry["kind"] == "cron"
        ), f"cron {cron} has no async_handlers entry"
    for workflow in wrangler["workflows"]:
        assert any(
            workflow["class_name"] in entry["trigger"]
            for entry in handlers.values()
            if entry["kind"] == "workflow"
        ), f"workflow {workflow['class_name']} has no async_handlers entry"

    for key, entry in handlers.items():
        assert entry["kind"] in {"cron", "queue", "workflow"}, key
        assert entry["owner"], key
        assert entry["trigger"], key
        assert entry["handler"], key
        assert entry["success_signal"], key
        assert entry["alert_condition"], key
        assert (PROJECT_ROOT / entry["runbook"].split("#", maxsplit=1)[0]).is_file(), key

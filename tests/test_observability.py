import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from route_inventory import document_routes

from sessionbuddy.api.app import app
from sessionbuddy.observability import record_timing

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

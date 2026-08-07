import json
from pathlib import Path
from types import SimpleNamespace

import pytest

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
    runtime_routes = {
        f"{method} {route.path}"
        for route in app.routes
        for method in (route.methods or set())
        if method not in {"HEAD", "OPTIONS"} and route.path not in {app.openapi_url}
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

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sessionbuddy.api.app import app  # noqa: E402


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("At least one measurement is required")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


async def benchmark(requests: int, warmup: int) -> dict[str, Any]:
    durations_ms: list[float] = []
    failures = 0
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://benchmark") as client:
        for _ in range(warmup):
            await client.get("/api/v1/health")
        started = time.perf_counter()
        for _ in range(requests):
            request_started = time.perf_counter_ns()
            response = await client.get("/api/v1/health")
            durations_ms.append((time.perf_counter_ns() - request_started) / 1_000_000)
            failures += int(response.status_code != 200)
        elapsed = time.perf_counter() - started

    return {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "runtime": "host-asgi",
        "route": "/api/v1/health",
        "requests": requests,
        "warmup_requests": warmup,
        "error_rate": failures / requests,
        "throughput_rps": round(requests / elapsed, 3),
        "latency_ms": {
            "mean": round(statistics.fmean(durations_ms), 3),
            "p50": round(percentile(durations_ms, 0.50), 3),
            "p75": round(percentile(durations_ms, 0.75), 3),
            "p95": round(percentile(durations_ms, 0.95), 3),
            "p99": round(percentile(durations_ms, 0.99), 3),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the foundation API smoke benchmark")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.warmup < 0:
        parser.error("--requests must be positive and --warmup cannot be negative")
    return args


def main() -> None:
    args = parse_args()
    result = asyncio.run(benchmark(args.requests, args.warmup))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

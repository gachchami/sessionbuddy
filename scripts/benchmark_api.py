from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from httpx import ASGITransport, AsyncClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sessionbuddy.api.app import app  # noqa: E402

SERVER_TIMING_METRIC = re.compile(r"^([a-z]+);dur=([0-9]+(?:\.[0-9]+)?)$")


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("At least one measurement is required")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def parse_server_timing(value: str | None) -> dict[str, float]:
    phases: dict[str, float] = {}
    for item in (value or "").split(","):
        match = SERVER_TIMING_METRIC.fullmatch(item.strip())
        if match:
            phases[match.group(1)] = float(match.group(2))
    return phases


def latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.fmean(values), 3),
        "p50": round(percentile(values, 0.50), 3),
        "p75": round(percentile(values, 0.75), 3),
        "p95": round(percentile(values, 0.95), 3),
        "p99": round(percentile(values, 0.99), 3),
    }


async def benchmark(
    requests: int,
    warmup: int,
    base_url: str | None = None,
    route: str = "/api/v1/health",
    local_demo_session: bool = False,
    concurrency: int = 1,
) -> dict[str, Any]:
    durations_ms: list[float] = []
    server_phases: dict[str, list[float]] = {}
    failures = 0
    transport = None if base_url else ASGITransport(app=app)
    target_url = base_url.rstrip("/") if base_url else "http://benchmark"
    async with AsyncClient(transport=transport, base_url=target_url) as client:
        if local_demo_session:
            session = await client.post("/api/v1/demo/session")
            session.raise_for_status()
        for _ in range(warmup):
            await client.get(route)
        semaphore = asyncio.Semaphore(concurrency)

        async def measure_one() -> None:
            nonlocal failures
            async with semaphore:
                request_started = time.perf_counter_ns()
                response = await client.get(route)
            durations_ms.append((time.perf_counter_ns() - request_started) / 1_000_000)
            failures += int(response.status_code != 200)
            timing_phases = parse_server_timing(response.headers.get("server-timing"))
            for name, duration in timing_phases.items():
                server_phases.setdefault(name, []).append(duration)

        started = time.perf_counter()
        if concurrency == 1:
            for _ in range(requests):
                await measure_one()
        else:
            await asyncio.gather(*(measure_one() for _ in range(requests)))
        elapsed = time.perf_counter() - started

    hostname = urlparse(target_url).hostname
    runtime = "host-asgi"
    if base_url:
        runtime = (
            "cloudflare-worker-local"
            if hostname in {"localhost", "127.0.0.1", "::1", "worker"}
            else "cloudflare-worker-remote"
        )

    return {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "runtime": runtime,
        "route": route,
        "requests": requests,
        "warmup_requests": warmup,
        "concurrency": concurrency,
        "error_rate": failures / requests,
        "throughput_rps": round(requests / elapsed, 3),
        "latency_ms": latency_summary(durations_ms),
        "server_timing_ms": {
            name: latency_summary(values) for name, values in sorted(server_phases.items())
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the foundation API smoke benchmark")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument(
        "--base-url",
        help="Benchmark a running Worker, for example http://127.0.0.1:8787",
    )
    parser.add_argument("--route", default="/api/v1/health")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Maximum in-flight requests (default: 1)",
    )
    parser.add_argument(
        "--local-demo-session",
        action="store_true",
        help="Establish the local-only demo cookie before benchmarking protected GET routes",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.warmup < 0 or args.concurrency < 1:
        parser.error("--requests/--concurrency must be positive; --warmup cannot be negative")
    if args.concurrency > args.requests:
        parser.error("--concurrency cannot exceed --requests")
    if not args.route.startswith("/") or args.route.startswith("//"):
        parser.error("--route must be an absolute application path")
    if args.local_demo_session and not args.base_url:
        parser.error("--local-demo-session requires --base-url")
    return args


def main() -> None:
    args = parse_args()
    result = asyncio.run(
        benchmark(
            args.requests,
            args.warmup,
            args.base_url,
            args.route,
            args.local_demo_session,
            args.concurrency,
        )
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

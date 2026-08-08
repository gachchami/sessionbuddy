import pytest

from scripts.benchmark_api import benchmark, parse_server_timing, percentile


def test_percentile_is_stable_for_small_samples() -> None:
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.50) == 3.0
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.95) == 4.0


def test_percentile_rejects_empty_measurements() -> None:
    with pytest.raises(ValueError, match="At least one measurement"):
        percentile([], 0.95)


def test_server_timing_parser_is_allow_listed() -> None:
    assert parse_server_timing("app;dur=12.5, db;dur=4, unsafe name;dur=9") == {
        "app": 12.5,
        "db": 4.0,
    }


@pytest.mark.asyncio
async def test_benchmark_records_selected_route() -> None:
    result = await benchmark(2, 1, route="/health")

    assert result["route"] == "/health"
    assert result["concurrency"] == 1
    assert result["error_rate"] == 0


@pytest.mark.asyncio
async def test_benchmark_supports_bounded_concurrency() -> None:
    result = await benchmark(8, 1, route="/health", concurrency=4)

    assert result["requests"] == 8
    assert result["concurrency"] == 4
    assert result["error_rate"] == 0
    assert result["throughput_rps"] > 0

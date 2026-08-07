import pytest

from scripts.benchmark_api import percentile


def test_percentile_is_stable_for_small_samples() -> None:
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.50) == 3.0
    assert percentile([4.0, 1.0, 3.0, 2.0], 0.95) == 4.0


def test_percentile_rejects_empty_measurements() -> None:
    with pytest.raises(ValueError, match="At least one measurement"):
        percentile([], 0.95)

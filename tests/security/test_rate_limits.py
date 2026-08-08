import pytest

from sessionbuddy.platform.rate_limits import DeterministicRateLimiter, RateLimitPolicy


@pytest.mark.asyncio
async def test_deterministic_rate_limiter_denies_and_recovers() -> None:
    limiter = DeterministicRateLimiter()
    policy = RateLimitPolicy("auth.request", limit=2, window_seconds=60)

    assert (await limiter.check(policy, "digest-a", 100)).allowed
    assert (await limiter.check(policy, "digest-a", 101)).allowed
    denied = await limiter.check(policy, "digest-a", 102)
    assert not denied.allowed
    assert denied.retry_after_seconds == 18
    assert (await limiter.check(policy, "digest-a", 120)).allowed


@pytest.mark.asyncio
async def test_rate_limit_subjects_are_isolated_and_opaque() -> None:
    limiter = DeterministicRateLimiter()
    policy = RateLimitPolicy("auth.verify", limit=1, window_seconds=60)

    assert (await limiter.check(policy, "digest-a", 0)).allowed
    assert (await limiter.check(policy, "digest-b", 0)).allowed
    with pytest.raises(ValueError, match="opaque digests"):
        await limiter.check(policy, "", 0)


def test_invalid_rate_limit_policy_is_rejected() -> None:
    with pytest.raises(ValueError):
        RateLimitPolicy("auth.request", limit=0, window_seconds=60)

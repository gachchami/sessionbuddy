import pytest

from sessionbuddy.platform.rate_limits import (
    CloudflareRateLimiter,
    DeterministicRateLimiter,
    RateLimitPolicy,
    subject_digest,
)


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


class Binding:
    def __init__(self, success: bool) -> None:
        self.success = success
        self.keys: list[str] = []

    async def limit(self, options):
        self.keys.append(options["key"])
        return {"success": self.success}


@pytest.mark.asyncio
async def test_cloudflare_binding_receives_only_opaque_bounded_key() -> None:
    binding = Binding(False)
    policy = RateLimitPolicy("public.submit", limit=50, window_seconds=60)
    decision = await CloudflareRateLimiter(binding).check(policy, "a" * 64, 0)

    assert not decision.allowed
    assert decision.retry_after_seconds == 60
    assert binding.keys == [f"public.submit:{'a' * 64}"]


def test_rate_limit_subject_digest_is_keyed_and_does_not_expose_input() -> None:
    digest = subject_digest("private@example.test:192.0.2.1", b"s" * 32)

    assert len(digest) == 64
    assert "private" not in digest
    assert digest != subject_digest("private@example.test:192.0.2.1", b"x" * 32)

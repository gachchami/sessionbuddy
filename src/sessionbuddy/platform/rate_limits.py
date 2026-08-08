"""Provider-neutral rate-limit contract and deterministic local adapter."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    name: str
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if not self.name or self.limit < 1 or self.window_seconds < 1:
            raise ValueError("rate-limit policy values must be positive")


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    async def check(
        self, policy: RateLimitPolicy, subject_digest: str, now_seconds: int
    ) -> RateLimitDecision: ...


class DeterministicRateLimiter:
    """Fixed-window adapter for tests/local development, never production globals."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, str, int], int] = {}

    async def check(
        self, policy: RateLimitPolicy, subject_digest: str, now_seconds: int
    ) -> RateLimitDecision:
        if not subject_digest:
            raise ValueError("rate-limit subjects must be opaque digests")
        window = now_seconds // policy.window_seconds
        key = (policy.name, subject_digest, window)
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        if count <= policy.limit:
            return RateLimitDecision(True)
        retry_after = ((window + 1) * policy.window_seconds) - now_seconds
        return RateLimitDecision(False, max(1, retry_after))

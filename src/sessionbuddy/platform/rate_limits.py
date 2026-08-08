"""Provider-neutral rate-limit contract and deterministic local adapter."""

import hashlib
import hmac
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request

from sessionbuddy.platform.db.d1 import row_mapping


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


class CloudflareRateLimiter:
    """Adapter for the Workers Rate Limiting binding."""

    def __init__(self, binding) -> None:
        self._binding = binding

    async def check(
        self, policy: RateLimitPolicy, subject_digest: str, now_seconds: int
    ) -> RateLimitDecision:
        if not subject_digest:
            raise ValueError("rate-limit subjects must be opaque digests")
        result = row_mapping(
            await self._binding.limit({"key": f"{policy.name}:{subject_digest}"})
        )
        if result is None or not isinstance(result.get("success"), bool):
            raise RuntimeError("rate-limit binding returned an invalid response")
        return RateLimitDecision(
            allowed=bool(result["success"]),
            retry_after_seconds=0 if result["success"] else policy.window_seconds,
        )


def subject_digest(value: str, secret: bytes) -> str:
    if not value or len(secret) < 32:
        raise ValueError("rate-limit subjects and strong secrets are required")
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


async def enforce_rate_limit(
    request: Request,
    *,
    binding_name: str,
    policy: RateLimitPolicy,
    subject: str,
) -> None:
    env = request.scope.get("env")
    binding = getattr(env, binding_name, None)
    secret_value = str(getattr(env, "RATE_LIMIT_HMAC_KEY", "")).encode()
    if binding is None or len(secret_value) < 32:
        raise HTTPException(status_code=503)
    decision = await CloudflareRateLimiter(binding).check(
        policy,
        subject_digest(subject, secret_value),
        now_seconds=0,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )

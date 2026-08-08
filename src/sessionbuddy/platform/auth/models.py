"""Reviewable authentication policy values; no framework dependency."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    challenge_lifetime_seconds: int = 15 * 60
    idle_lifetime_seconds: int = 12 * 60 * 60
    absolute_lifetime_seconds: int = 30 * 24 * 60 * 60
    token_bytes: int = 32


@dataclass(frozen=True, slots=True)
class CookiePolicy:
    name: str = "__Host-session"
    path: str = "/"
    secure: bool = True
    http_only: bool = True
    same_site: str = "lax"
    domain: None = None

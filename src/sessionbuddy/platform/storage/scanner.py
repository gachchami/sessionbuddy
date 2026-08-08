"""Authenticated, content-minimizing contract for an isolated scanner service."""

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ScanResult:
    job_id: str
    verdict: Literal["clean", "malicious", "error"]
    engine: str
    signature: str | None


def scan_request_headers(
    secret: bytes, *, job_id: str, timestamp_ms: int, content: bytes
) -> dict[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    canonical = f"{timestamp_ms}\n{job_id}\n{digest}".encode()
    return {
        "content-type": "application/octet-stream",
        "x-content-sha256": digest,
        "x-scan-job-id": job_id,
        "x-scan-timestamp": str(timestamp_ms),
        "x-scan-signature": hmac.new(secret, canonical, hashlib.sha256).hexdigest(),
    }


def parse_signed_scan_response(secret: bytes, body: bytes, signature: str | None) -> ScanResult:
    if signature is None or not hmac.compare_digest(
        signature, hmac.new(secret, body, hashlib.sha256).hexdigest()
    ):
        raise ValueError("invalid scanner response signature")
    decoded = json.loads(body)
    if not isinstance(decoded, dict) or set(decoded) != {
        "engine",
        "job_id",
        "signature",
        "verdict",
    }:
        raise ValueError("invalid scanner response shape")
    if decoded["verdict"] not in {"clean", "malicious", "error"}:
        raise ValueError("invalid scanner verdict")
    if decoded["engine"] != "clamav":
        raise ValueError("unexpected scanner engine")
    if not isinstance(decoded["job_id"], str) or len(decoded["job_id"]) > 100:
        raise ValueError("invalid scan job")
    if decoded["signature"] is not None and (
        not isinstance(decoded["signature"], str) or len(decoded["signature"]) > 100
    ):
        raise ValueError("invalid malware signature")
    return ScanResult(
        job_id=decoded["job_id"],
        verdict=decoded["verdict"],
        engine=decoded["engine"],
        signature=decoded["signature"],
    )

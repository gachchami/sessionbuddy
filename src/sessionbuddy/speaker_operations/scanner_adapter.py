"""Cloudflare R2-to-malware-scanner streaming adapter."""

import hashlib
from contextlib import suppress
from typing import Any

from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.platform.storage import (
    parse_signed_scan_response,
    scan_request_headers,
    scan_request_headers_for_digest,
)

from .asset_boundary import ScanJob, ScanResult


class SignedScannerAdapter:
    """Send an authenticated scan request without buffering an R2 object."""

    def __init__(self, environment: Any) -> None:
        self.environment = environment

    async def scan(self, stored: Any, *, job: ScanJob) -> ScanResult:
        from workers import fetch

        scanner_url = str(getattr(self.environment, "SCANNER_URL", ""))
        app_env = str(getattr(self.environment, "APP_ENV", "production"))
        if not scanner_url.startswith("https://") and not (
            app_env == "local" and scanner_url.startswith("http://scanner:")
        ):
            raise RuntimeError("scanner binding is unavailable")
        scanner_secret = str(
            getattr(self.environment, "SCANNER_HMAC_KEY", "")
        ).encode()
        if len(scanner_secret) < 32:
            raise RuntimeError("scanner signing secret is unavailable")

        if isinstance(stored, (bytes, bytearray, memoryview)):
            content = bytes(stored)
            headers = scan_request_headers(
                scanner_secret,
                job_id=job.job_id,
                timestamp_ms=utc_now_ms(),
                content=content,
            )
            response = await fetch(
                scanner_url.rstrip("/") + "/scan",
                method="POST",
                headers=headers,
                body=content,
            )
        else:
            response = await self._scan_r2_object(
                stored,
                scanner_url=scanner_url,
                scanner_secret=scanner_secret,
                job=job,
            )

        response_body = await response.bytes()
        if response.status != 200 or len(response_body) > 4096:
            raise RuntimeError("scanner request failed")
        parsed = parse_signed_scan_response(
            scanner_secret, response_body, response.headers.get("x-scan-signature")
        )
        if parsed.job_id != job.job_id:
            raise RuntimeError("scanner job mismatch")
        return ScanResult(
            provider_event_id=hashlib.sha256(response_body).hexdigest(),
            verdict=parsed.verdict,
            engine=parsed.engine,
            signature_code=parsed.signature,
        )

    async def _scan_r2_object(
        self,
        stored: Any,
        *,
        scanner_url: str,
        scanner_secret: bytes,
        job: ScanJob,
    ) -> Any:
        from js import FixedLengthStream
        from workers import fetch

        body = getattr(stored, "body", None)
        size = getattr(stored, "size", None)
        if body is None or size is None or int(size) < 0:
            raise RuntimeError("scanner object body is unavailable")
        fixed_length = FixedLengthStream.new(int(size))
        pipe_promise = body.pipeTo(fixed_length.writable)
        headers = scan_request_headers_for_digest(
            scanner_secret,
            job_id=job.job_id,
            timestamp_ms=utc_now_ms(),
            checksum_sha256=job.checksum_sha256,
        )
        try:
            response = await fetch(
                scanner_url.rstrip("/") + "/scan",
                method="POST",
                headers=headers,
                body=fixed_length.readable,
            )
        except Exception:
            # Fetch cancels its request body when the request fails. Observe the
            # corresponding pipe rejection so it never becomes a floating promise.
            with suppress(Exception):
                await pipe_promise
            raise
        await pipe_promise
        return response

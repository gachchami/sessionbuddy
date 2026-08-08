"""Dependency-free, authenticated HTTP adapter for ClamAV clamd.

Request signatures are lowercase hex HMAC-SHA256 over
``timestamp_ms + "\n" + job_id + "\n" + lowercase_content_sha256``.
Response signatures are lowercase hex HMAC-SHA256 over the exact canonical JSON
response bytes. The shared secret is read from ``SCANNER_HMAC_SECRET``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import struct
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 50 * 1024 * 1024
MAX_CLOCK_SKEW_MS = 5 * 60 * 1000
CHUNK_BYTES = 64 * 1024
SOCKET_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class ScanResult:
    job_id: str
    verdict: str
    signature: str | None
    engine: str = "clamav"

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            {
                "job_id": self.job_id,
                "verdict": self.verdict,
                "signature": self.signature,
                "engine": self.engine,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


def request_signature(secret: bytes, job_id: str, timestamp: str, sha256: str) -> str:
    message = f"{timestamp}\n{job_id}\n{sha256.lower()}".encode("ascii")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def response_signature(secret: bytes, body: bytes) -> str:
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def valid_auth(
    *,
    secret: bytes,
    job_id: str,
    timestamp: str,
    sha256: str,
    signature: str,
    now: int | None = None,
) -> bool:
    if (
        not job_id
        or len(job_id) > 128
        or not all(char.isalnum() or char in "-_." for char in job_id)
    ):
        return False
    if len(sha256) != 64 or any(char not in "0123456789abcdefABCDEF" for char in sha256):
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    current = int(time.time() * 1000) if now is None else now
    if abs(current - sent_at) > MAX_CLOCK_SKEW_MS:
        return False
    expected = request_signature(secret, job_id, timestamp, sha256)
    return hmac.compare_digest(expected, signature.lower())


def parse_clamd_response(response: bytes) -> tuple[str, str | None]:
    text = response.rstrip(b"\0\r\n").decode("utf-8", errors="replace")
    if text.endswith(": OK"):
        return "clean", None
    if text.endswith(" FOUND") and ": " in text:
        signature = text.rsplit(": ", 1)[1].removesuffix(" FOUND")
        return ("malicious", signature[:512]) if signature else ("error", None)
    return "error", None


def scan_chunks(
    chunks: Iterable[bytes],
    *,
    host: str,
    port: int,
    socket_factory: Callable[..., socket.socket] = socket.create_connection,
) -> tuple[str, str | None, str, int]:
    """Stream chunks to clamd and return verdict, signature, digest, and size."""
    digest = hashlib.sha256()
    total = 0
    connection = socket_factory((host, port), timeout=SOCKET_TIMEOUT_SECONDS)
    try:
        connection.settimeout(SOCKET_TIMEOUT_SECONDS)
        connection.sendall(b"zINSTREAM\0")
        for chunk in chunks:
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("body_too_large")
            digest.update(chunk)
            connection.sendall(struct.pack("!I", len(chunk)))
            connection.sendall(chunk)
        connection.sendall(struct.pack("!I", 0))
        parts: list[bytes] = []
        received = 0
        while received <= 4096:
            part = connection.recv(min(1024, 4097 - received))
            if not part:
                break
            parts.append(part)
            received += len(part)
            if b"\0" in part:
                break
        if received > 4096:
            return "error", None, digest.hexdigest(), total
        verdict, signature = parse_clamd_response(b"".join(parts))
        return verdict, signature, digest.hexdigest(), total
    finally:
        connection.close()


def clamd_ping(
    host: str,
    port: int,
    socket_factory: Callable[..., socket.socket] = socket.create_connection,
) -> bool:
    connection = socket_factory((host, port), timeout=SOCKET_TIMEOUT_SECONDS)
    try:
        connection.settimeout(SOCKET_TIMEOUT_SECONDS)
        connection.sendall(b"zPING\0")
        return connection.recv(16).rstrip(b"\0\r\n") == b"PONG"
    finally:
        connection.close()


class ScannerHandler(BaseHTTPRequestHandler):
    server_version = "SessionbuddyScanner/1"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        """Disable BaseHTTPRequestHandler access logging to avoid metadata leakage."""

    @property
    def secret(self) -> bytes:
        return os.environ.get("SCANNER_HMAC_SECRET", "").encode()

    @property
    def clamd(self) -> tuple[str, int]:
        return os.environ.get("CLAMD_HOST", "clamav"), int(os.environ.get("CLAMD_PORT", "3310"))

    def _result(self, status: HTTPStatus, result: ScanResult) -> None:
        body = result.canonical_bytes()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Scan-Signature", response_signature(self.secret, body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            healthy = clamd_ping(*self.clamd)
        except (OSError, ValueError):
            healthy = False
        body = (
            b'{"status":"ok","engine":"clamav"}'
            if healthy
            else b'{"status":"unavailable","engine":"clamav"}'
        )
        self.send_response(HTTPStatus.OK if healthy else HTTPStatus.SERVICE_UNAVAILABLE)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/scan":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        job_id = self.headers.get("X-Scan-Job-ID", "")
        timestamp = self.headers.get("X-Scan-Timestamp", "")
        claimed_sha = self.headers.get("X-Content-SHA256", "")
        supplied_hmac = self.headers.get("X-Scan-Signature", "")
        if not self.secret or not valid_auth(
            secret=self.secret,
            job_id=job_id,
            timestamp=timestamp,
            sha256=claimed_sha,
            signature=supplied_hmac,
        ):
            self._result(HTTPStatus.UNAUTHORIZED, ScanResult(job_id, "error", None))
            return
        if self.headers.get("Transfer-Encoding"):
            self._result(HTTPStatus.BAD_REQUEST, ScanResult(job_id, "error", None))
            return
        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_BYTES:
            self._result(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, ScanResult(job_id, "error", None))
            return

        remaining = content_length

        def chunks() -> Iterable[bytes]:
            nonlocal remaining
            while remaining:
                chunk = self.rfile.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise EOFError("incomplete_body")
                remaining -= len(chunk)
                yield chunk

        try:
            verdict, signature, actual_sha, total = scan_chunks(
                chunks(), host=self.clamd[0], port=self.clamd[1]
            )
            if total != content_length or not hmac.compare_digest(actual_sha, claimed_sha.lower()):
                self._result(HTTPStatus.BAD_REQUEST, ScanResult(job_id, "error", None))
                return
            self._result(HTTPStatus.OK, ScanResult(job_id, verdict, signature))
        except (OSError, EOFError, TimeoutError, ValueError):
            self._result(HTTPStatus.BAD_GATEWAY, ScanResult(job_id, "error", None))


def main() -> None:
    if len(os.environ.get("SCANNER_HMAC_SECRET", "").encode()) < 32:
        raise RuntimeError("SCANNER_HMAC_SECRET must contain at least 32 bytes")
    host = os.environ.get("SCANNER_HOST", "0.0.0.0")  # noqa: S104 - container listener
    port = int(os.environ.get("SCANNER_PORT", "8080"))
    ThreadingHTTPServer((host, port), ScannerHandler).serve_forever()


if __name__ == "__main__":
    main()

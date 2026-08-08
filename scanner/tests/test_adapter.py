import hashlib
import hmac
import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from adapter import (  # noqa: E402
    MAX_BYTES,
    ScanResult,
    clamd_ping,
    parse_clamd_response,
    request_signature,
    response_signature,
    scan_chunks,
    valid_auth,
)


class FakeSocket:
    def __init__(self, response=b"stream: OK\0"):
        self.response = response
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def sendall(self, data):
        self.sent.extend(data)

    def recv(self, size):
        result, self.response = self.response[:size], self.response[size:]
        return result

    def close(self):
        self.closed = True


class ScannerTests(unittest.TestCase):
    def test_auth_signature_and_clock_window(self):
        secret = b"test-secret"
        sha = "a" * 64
        signature = request_signature(secret, "job-1", "1000000", sha)
        self.assertTrue(
            valid_auth(
                secret=secret,
                job_id="job-1",
                timestamp="1000000",
                sha256=sha,
                signature=signature,
                now=1200000,
            )
        )
        self.assertFalse(
            valid_auth(
                secret=secret,
                job_id="job-1",
                timestamp="1000000",
                sha256=sha,
                signature=signature,
                now=1300001,
            )
        )
        self.assertFalse(
            valid_auth(
                secret=secret,
                job_id="bad/job",
                timestamp="1000000",
                sha256=sha,
                signature=signature,
                now=1000000,
            )
        )

    def test_clamd_response_parser(self):
        self.assertEqual(parse_clamd_response(b"stream: OK\0"), ("clean", None))
        self.assertEqual(
            parse_clamd_response(b"stream: Eicar-Test-Signature FOUND\0"),
            ("malicious", "Eicar-Test-Signature"),
        )
        self.assertEqual(
            parse_clamd_response(b"stream: size limit exceeded ERROR\0"), ("error", None)
        )

    def test_scan_streams_clamd_instream_frames_and_hashes(self):
        fake = FakeSocket()
        verdict, signature, digest, size = scan_chunks(
            [b"abc", b"def"], host="clamd", port=3310, socket_factory=lambda *_args, **_kwargs: fake
        )
        expected = (
            b"zINSTREAM\0"
            + struct.pack("!I", 3)
            + b"abc"
            + struct.pack("!I", 3)
            + b"def"
            + struct.pack("!I", 0)
        )
        self.assertEqual(bytes(fake.sent), expected)
        self.assertEqual((verdict, signature), ("clean", None))
        self.assertEqual(digest, hashlib.sha256(b"abcdef").hexdigest())
        self.assertEqual(size, 6)
        self.assertTrue(fake.closed)

    def test_scan_rejects_more_than_limit(self):
        fake = FakeSocket()
        with self.assertRaisesRegex(ValueError, "body_too_large"):
            scan_chunks(
                [b"x" * (MAX_BYTES + 1)],
                host="clamd",
                port=3310,
                socket_factory=lambda *_args, **_kwargs: fake,
            )
        self.assertTrue(fake.closed)

    def test_ping_uses_null_terminated_protocol(self):
        fake = FakeSocket(b"PONG\0")
        self.assertTrue(clamd_ping("clamd", 3310, socket_factory=lambda *_args, **_kwargs: fake))
        self.assertEqual(bytes(fake.sent), b"zPING\0")

    def test_response_is_canonical_and_signed_over_exact_bytes(self):
        result = ScanResult("job-1", "malicious", "Eicar-Test-Signature")
        body = result.canonical_bytes()
        self.assertEqual(
            body,
            b'{"engine":"clamav","job_id":"job-1","signature":"Eicar-Test-Signature",'
            b'"verdict":"malicious"}',
        )
        self.assertEqual(
            json.loads(body),
            {
                "job_id": "job-1",
                "verdict": "malicious",
                "signature": "Eicar-Test-Signature",
                "engine": "clamav",
            },
        )
        self.assertEqual(
            response_signature(b"secret", body),
            hmac.new(b"secret", body, hashlib.sha256).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()

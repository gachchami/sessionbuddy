from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from sessionbuddy.platform.storage import malware_scan_disabled, presign_r2_put
from sessionbuddy.speaker_operations.models import UploadAuthorizationCreate


class _Environment:
    def __init__(self, app_env: str, mode: str) -> None:
        self.APP_ENV = app_env
        self.MALWARE_SCAN_MODE = mode


def test_malware_scan_bypass_is_explicit_and_non_production_only() -> None:
    assert malware_scan_disabled(_Environment("development", "disabled"))
    assert malware_scan_disabled(_Environment("local", "disabled"))
    assert not malware_scan_disabled(_Environment("development", "required"))
    assert not malware_scan_disabled(_Environment("production", "disabled"))
    assert not malware_scan_disabled(None)


def test_upload_contract_rejects_paths_and_invalid_checksums() -> None:
    with pytest.raises(ValidationError):
        UploadAuthorizationCreate(
            kind="headshot",
            filename="../private.png",
            content_type="image/png",
            byte_size=100,
            checksum_sha256="not-a-checksum",
        )


def test_r2_presigned_put_is_single_object_and_header_bound() -> None:
    url, headers = presign_r2_put(
        account_id="account",
        bucket="private-assets",
        object_key="private/version/object",
        access_key_id="access",
        secret_access_key="".join(("test", "-signing", "-material")),
        content_type="application/pdf",
        now=datetime(2026, 8, 9, tzinfo=UTC),
        expires_seconds=600,
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.hostname == "account.r2.cloudflarestorage.com"
    assert parsed.path == "/private-assets/private/version/object"
    assert query["X-Amz-Expires"] == ["600"]
    assert query["X-Amz-SignedHeaders"] == ["content-type;host"]
    assert len(query["X-Amz-Signature"][0]) == 64
    assert headers == {"content-type": "application/pdf"}

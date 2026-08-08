from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import ValidationError

from sessionbuddy.platform.storage import presign_r2_put
from sessionbuddy.wave3.models import UploadAuthorizationCreate


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

"""Small dependency-free AWS SigV4 signer for one private R2 object PUT."""

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import quote, urlencode


def _sign(key: bytes, value: str) -> bytes:
    return hmac.digest(key, value.encode(), "sha256")


def presign_r2_put(
    *,
    account_id: str,
    bucket: str,
    object_key: str,
    access_key_id: str,
    secret_access_key: str,
    content_type: str,
    content_length: int,
    now: datetime,
    expires_seconds: int = 600,
) -> tuple[str, dict[str, str]]:
    if not 1 <= expires_seconds <= 3600:
        raise ValueError("upload expiry must be between 1 and 3600 seconds")
    if content_length < 1:
        raise ValueError("upload content length must be positive")
    moment = now.astimezone(UTC)
    date = moment.strftime("%Y%m%d")
    timestamp = moment.strftime("%Y%m%dT%H%M%SZ")
    host = f"{account_id}.r2.cloudflarestorage.com"
    path = f"/{quote(bucket, safe='')}/{quote(object_key, safe='/')}"
    scope = f"{date}/auto/s3/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Content-Sha256": "UNSIGNED-PAYLOAD",
        "X-Amz-Credential": f"{access_key_id}/{scope}",
        "X-Amz-Date": timestamp,
        "X-Amz-Expires": str(expires_seconds),
        "X-Amz-SignedHeaders": "content-length;content-type;host",
    }
    canonical_query = urlencode(sorted(query.items()), quote_via=quote)
    canonical_headers = (
        f"content-length:{content_length}\ncontent-type:{content_type}\nhost:{host}\n"
    )
    canonical_request = "\n".join(
        (
            "PUT",
            path,
            canonical_query,
            canonical_headers,
            "content-length;content-type;host",
            "UNSIGNED-PAYLOAD",
        )
    )
    string_to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            timestamp,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        )
    )
    signing_key = _sign(
        _sign(_sign(_sign(f"AWS4{secret_access_key}".encode(), date), "auto"), "s3"),
        "aws4_request",
    )
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return (
        f"https://{host}{path}?{canonical_query}&X-Amz-Signature={signature}",
        {"content-length": str(content_length), "content-type": content_type},
    )

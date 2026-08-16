"""HTTP responses for content-addressed console assets."""

from __future__ import annotations

import hashlib

from fastapi import Request
from fastapi.responses import Response


def content_addressed_asset(request: Request, content: str, *, media_type: str) -> Response:
    """Cache an asset immutably only when its URL names the served content."""
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    cache_control = (
        "public, max-age=31536000, immutable"
        if request.query_params.get("v") == expected
        else "no-store"
    )
    return Response(content, media_type=media_type, headers={"Cache-Control": cache_control})

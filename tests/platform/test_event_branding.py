import pytest
from fastapi import HTTPException, Request

from sessionbuddy.platform.auth.access import _read_event_logo


def request_with_body(body: bytes, content_type: str) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/events/event/logo",
            "headers": [
                (b"content-type", content_type.encode()),
                (b"content-length", str(len(body)).encode()),
            ],
        },
        receive,
    )


async def test_event_image_upload_accepts_supported_magic_bytes() -> None:
    body = b"\x89PNG\r\n\x1a\n" + b"image-data"
    uploaded, content_type, extension = await _read_event_logo(
        request_with_body(body, "image/png")
    )
    assert uploaded == body
    assert content_type == "image/png"
    assert extension == "png"


async def test_event_image_upload_rejects_spoofed_content_type() -> None:
    with pytest.raises(HTTPException) as raised:
        await _read_event_logo(request_with_body(b"not-a-png", "image/png"))
    assert raised.value.status_code == 415


async def test_event_image_upload_rejects_unsupported_media() -> None:
    with pytest.raises(HTTPException) as raised:
        await _read_event_logo(request_with_body(b"<svg/>", "image/svg+xml"))
    assert raised.value.status_code == 415

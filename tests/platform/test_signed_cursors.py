from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sessionbuddy.cfp.router import _submissions_cursor, _submissions_next_cursor
from sessionbuddy.communications.d1 import _status_cursor, _status_next_cursor
from sessionbuddy.evaluation.router import _evaluation_cursor, _evaluation_next_cursor
from sessionbuddy.platform.auth.access import _events_cursor, _events_next_cursor
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.platform.signed_cursors import decode_signed_cursor, encode_signed_cursor
from sessionbuddy.speaker_operations.router import _cursor, _next_cursor


def _request():
    return SimpleNamespace(scope={"env": SimpleNamespace(CSRF_HMAC_KEY="c" * 32)})


def _tamper(value: str) -> str:
    payload, signature = value.split(".", 1)
    replacement = "A" if payload[0] != "A" else "B"
    return f"{replacement}{payload[1:]}.{signature}"


def test_shared_codec_binds_exact_scope_shape_signature_and_ttl() -> None:
    request = _request()
    token = encode_signed_cursor(
        request,
        scope={"event": "event-a", "view": "active"},
        position={"id": "row-a", "ts": 42},
        now_ms=100,
        ttl_ms=50,
    )

    decoded = decode_signed_cursor(
        request,
        token,
        scope={"event": "event-a", "view": "active"},
        position_fields={"id", "ts"},
        now_ms=150,
    )
    assert decoded == {
        "event": "event-a",
        "exp": 150,
        "id": "row-a",
        "ts": 42,
        "v": 1,
        "view": "active",
    }
    invalid_cases = [
        (_tamper(token), {"event": "event-a", "view": "active"}, 150),
        (token, {"event": "event-b", "view": "active"}, 150),
        (token, {"event": "event-a", "view": "active"}, 151),
    ]
    for invalid, scope, current in invalid_cases:
        with pytest.raises(HTTPException) as denied:
            decode_signed_cursor(
                request,
                invalid,
                scope=scope,
                position_fields={"id", "ts"},
                now_ms=current,
            )
        assert denied.value.status_code == 400
        assert denied.value.detail == "Invalid or expired cursor"


def _consumer_cases():
    request = _request()
    now = utc_now_ms()
    return [
        (
            "cfp",
            _submissions_next_cursor(
                request, event_id="event-a", submitted_at_ms=42, row_id="row-a"
            ),
            lambda token: _submissions_cursor(request, token, event_id="event-a"),
            lambda token: _submissions_cursor(request, token, event_id="event-b"),
            encode_signed_cursor(
                request,
                scope={"event": "event-a"},
                position={"id": "row-a", "sub": 42},
                expires_at_ms=1,
            ),
        ),
        (
            "evaluation",
            _evaluation_next_cursor(
                request, kind="round-results", scope_id="round-a", timestamp=42, row_id="row-a"
            ),
            lambda token: _evaluation_cursor(
                request, token, kind="round-results", scope_id="round-a"
            ),
            lambda token: _evaluation_cursor(
                request, token, kind="round-results", scope_id="round-b"
            ),
            encode_signed_cursor(
                request,
                scope={"kind": "round-results", "scope": "round-a"},
                position={"id": "row-a", "ts": 42},
                expires_at_ms=1,
            ),
        ),
        (
            "speaker_operations",
            _next_cursor(
                request,
                event_id="event-a",
                state="open",
                task_type="profile",
                due_at_ms=42,
                task_id="row-a",
                as_of=now,
            ),
            lambda token: _cursor(
                request, token, event_id="event-a", state="open", task_type="profile"
            ),
            lambda token: _cursor(
                request, token, event_id="event-a", state="completed", task_type="profile"
            ),
            encode_signed_cursor(
                request,
                scope={"event": "event-a", "state": "open", "task_type": "profile"},
                position={"as_of": 42, "due": 42, "id": "row-a"},
                expires_at_ms=1,
            ),
        ),
        (
            "access",
            _events_next_cursor(
                request,
                organization_id="org-a",
                view="active",
                search="",
                order="recent",
                starts_at_ms=42,
                row_id="row-a",
            ),
            lambda token: _events_cursor(
                request,
                token,
                organization_id="org-a",
                view="active",
                search="",
                order="recent",
            ),
            lambda token: _events_cursor(
                request,
                token,
                organization_id="org-a",
                view="past",
                search="",
                order="recent",
            ),
            encode_signed_cursor(
                request,
                scope={"org": "org-a", "order": "recent", "q": "", "view": "active"},
                position={"id": "row-a", "starts": 42},
                expires_at_ms=1,
            ),
        ),
        (
            "communications",
            _status_next_cursor(
                request,
                organization_id="org-a",
                event_id="event-a",
                timestamp=42,
                row_id="row-a",
            ),
            lambda token: _status_cursor(
                request, token, organization_id="org-a", event_id="event-a"
            ),
            lambda token: _status_cursor(
                request, token, organization_id="org-a", event_id="event-b"
            ),
            encode_signed_cursor(
                request,
                scope={"event": "event-a", "organization": "org-a"},
                position={"id": "row-a", "ts": 42},
                expires_at_ms=1,
            ),
        ),
    ]


@pytest.mark.parametrize(
    ("_name", "valid", "decode", "cross_scope", "expired"),
    _consumer_cases(),
)
def test_each_cursor_consumer_rejects_tamper_cross_scope_and_expiry(
    _name, valid, decode, cross_scope, expired
) -> None:
    assert decode(valid) is not None
    actions = (
        lambda: decode(_tamper(valid)),
        lambda: cross_scope(valid),
        lambda: decode(expired),
    )
    for action in actions:
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 400
        assert denied.value.detail == "Invalid or expired cursor"

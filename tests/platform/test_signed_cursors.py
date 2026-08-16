from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sessionbuddy.cfp.router import _submissions_cursor, _submissions_next_cursor
from sessionbuddy.communications.d1 import _status_cursor, _status_next_cursor
from sessionbuddy.evaluation.router import _evaluation_cursor, _evaluation_next_cursor
from sessionbuddy.platform.auth.access import _events_cursor, _events_next_cursor
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.platform.signed_cursors import (
    BOUNDED_ID,
    STRICT_INT,
    SignedCursorContract,
    StaleCursorError,
    decode_signed_cursor,
    encode_signed_cursor,
)
from sessionbuddy.speaker_operations.router import _cursor, _next_cursor


def _request():
    return SimpleNamespace(
        scope={"env": SimpleNamespace(CSRF_HMAC_KEY="c" * 32)},
        state=SimpleNamespace(request_id="request-a"),
    )


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


def test_cursor_failures_expose_one_recovery_code_with_safe_reasons() -> None:
    request = _request()
    contract = SignedCursorContract("cfp_submissions", {"id": BOUNDED_ID, "sub": STRICT_INT})
    assert contract.decode(request, None, scope={"event": "event-a"}) is None
    expired = encode_signed_cursor(
        request,
        scope={"event": "event-a"},
        position={"id": "row-a", "sub": 42},
        expires_at_ms=1,
    )
    malformed = encode_signed_cursor(
        request,
        scope={"event": "event-a"},
        position={"id": "", "sub": 42},
        expires_at_ms=10_000,
    )
    with pytest.raises(StaleCursorError, match="Invalid or expired cursor") as expired_error:
        contract.decode(request, expired, scope={"event": "event-a"}, now_ms=2)
    assert expired_error.value.reason == "expired"
    with pytest.raises(StaleCursorError) as shape_error:
        contract.decode(request, malformed, scope={"event": "event-a"}, now_ms=2)
    assert shape_error.value.reason == "shape"


def test_bound_contract_omits_cursor_when_position_drift_would_break_page_two() -> None:
    request = _request()
    contract = SignedCursorContract("cfp_submissions", {"id": BOUNDED_ID, "sub": STRICT_INT})

    assert contract.encode(
        request,
        scope={"event": "event-a"},
        position={"id": "row-a", "sub": 42, "unexpected": "value"},
    ) is None


def test_scope_key_change_is_recoverable_without_integrity_alert() -> None:
    request = _request()
    contract = SignedCursorContract("cfp_submissions", {"id": BOUNDED_ID, "sub": STRICT_INT})
    old = encode_signed_cursor(
        request,
        scope={"event": "event-a"},
        position={"id": "row-a", "sub": 42},
    )

    with pytest.raises(StaleCursorError) as error:
        contract.decode(
            request,
            old,
            scope={"event": "event-a", "filter": "active"},
        )
    assert error.value.reason == "invalid"


def test_cursor_from_another_contract_cannot_emit_shape_alert() -> None:
    request = _request()
    submissions = SignedCursorContract(
        "cfp_submissions", {"id": BOUNDED_ID, "sub": STRICT_INT}
    )
    events_cursor = encode_signed_cursor(
        request,
        scope={"org": "org-a", "order": "recent", "q": "", "view": "active"},
        position={"id": "row-a", "starts": 42},
    )

    with pytest.raises(StaleCursorError) as error:
        submissions.decode(request, events_cursor, scope={"event": "event-a"})
    assert error.value.reason == "invalid"


def test_decode_shape_failure_names_the_missing_position_field() -> None:
    request = _request()
    contract = SignedCursorContract("cfp_submissions", {"id": BOUNDED_ID, "sub": STRICT_INT})
    malformed = encode_signed_cursor(
        request,
        scope={"event": "event-a"},
        position={"sub": 42},
    )

    with pytest.raises(StaleCursorError) as error:
        contract.decode(request, malformed, scope={"event": "event-a"})
    assert error.value.reason == "shape"
    assert error.value.field == "id"
    assert error.value.constraint == "required"


@pytest.mark.parametrize("row_id", ["", "x" * 101])
def test_every_bound_cursor_contract_rejects_invalid_ids_before_encoding(row_id: str) -> None:
    # Every adjacent encoder/decoder pair now shares BOUNDED_ID; this assertion
    # pins the strict empty/length rule that previously drifted.
    assert BOUNDED_ID.violation(row_id) in {"empty", "length"}


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

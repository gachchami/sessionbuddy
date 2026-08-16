"""One signed, scope-bound cursor envelope for all keyset pagination."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, Request

from sessionbuddy.observability import record_integrity_signal
from sessionbuddy.platform.auth.http import secret
from sessionbuddy.platform.db.types import utc_now_ms

SIGNED_CURSOR_TTL_MS = 15 * 60 * 1000
_INVALID_CURSOR = "Invalid or expired cursor"
CursorReason = Literal["expired", "invalid", "shape"]


class StaleCursorError(HTTPException):
    """A cursor failure callers recover from by restarting pagination."""

    def __init__(
        self,
        reason: CursorReason,
        *,
        field: str = "position",
        constraint: str = "fields",
    ) -> None:
        super().__init__(status_code=400, detail=_INVALID_CURSOR)
        self.reason = reason
        self.field = field
        self.constraint = constraint


@dataclass(frozen=True)
class CursorField:
    kind: Literal["int", "str"]
    min_length: int | None = None
    max_length: int | None = None

    def violation(self, value: object) -> str | None:
        if self.kind == "int":
            return None if type(value) is int else "type"
        if not isinstance(value, str):
            return "type"
        if self.min_length is not None and len(value) < self.min_length:
            return "empty" if self.min_length == 1 and not value else "length"
        if self.max_length is not None and len(value) > self.max_length:
            return "length"
        return None


STRICT_INT = CursorField("int")
BOUNDED_ID = CursorField("str", min_length=1, max_length=100)


@dataclass(frozen=True)
class SignedCursorContract:
    """Bind one stable name and position schema to an encoder/decoder pair."""

    name: str
    position: Mapping[str, CursorField]

    def encode(
        self,
        request: Request,
        *,
        scope: Mapping[str, object],
        position: Mapping[str, object],
        ttl_ms: int = SIGNED_CURSOR_TTL_MS,
        now_ms: int | None = None,
        expires_at_ms: int | None = None,
    ) -> str | None:
        violation = self._position_violation(position)
        if violation is not None:
            field, constraint = violation
            self._record_shape(request, field, constraint)
            # Cursor generation is response metadata. A bad encoder must not
            # take the usable first page down with it; omit pagination and let
            # the integrity signal drive repair.
            return None
        return encode_signed_cursor(
            request,
            scope=scope,
            position=position,
            ttl_ms=ttl_ms,
            now_ms=now_ms,
            expires_at_ms=expires_at_ms,
        )

    def decode(
        self,
        request: Request,
        value: str | None,
        *,
        scope: Mapping[str, object],
        now_ms: int | None = None,
    ) -> dict[str, object] | None:
        try:
            decoded = decode_signed_cursor(
                request,
                value,
                scope=scope,
                position_fields=set(self.position),
                now_ms=now_ms,
            )
        except StaleCursorError as exc:
            if exc.reason == "shape":
                self._record_shape(request, exc.field, exc.constraint)
            raise
        if decoded is None:
            return None
        violation = self._position_violation(
            {name: decoded[name] for name in self.position}
        )
        if violation is not None:
            field, constraint = violation
            self._record_shape(request, field, constraint)
            raise StaleCursorError("shape", field=field, constraint=constraint)
        return decoded

    def _position_violation(
        self, values: Mapping[str, object]
    ) -> tuple[str, str] | None:
        # Exact-field drift is detected while encoding. Decode passes only the
        # declared position projection, then validates each field below.
        if set(values) != set(self.position):
            return "position", "fields"
        for name, field in self.position.items():
            constraint = field.violation(values[name])
            if constraint is not None:
                return name, constraint
        return None

    def _record_shape(self, request: Request, field: str, constraint: str) -> None:
        try:
            record_integrity_signal(
                request,
                "sessionbuddy.signed_cursor.shape_failure",
                cursor_contract=self.name,
                field=field,
                constraint=constraint,
            )
        except Exception:  # telemetry must never change the product response
            return


def encode_signed_cursor(
    request: Request,
    *,
    scope: Mapping[str, object],
    position: Mapping[str, object],
    ttl_ms: int = SIGNED_CURSOR_TTL_MS,
    now_ms: int | None = None,
    expires_at_ms: int | None = None,
) -> str:
    """Encode an exact versioned payload without changing consumer field names."""
    if set(scope).intersection(position) or "exp" in scope or "exp" in position:
        raise ValueError("Cursor scope and position fields must be distinct")
    issued_at = utc_now_ms() if now_ms is None else now_ms
    expires = issued_at + ttl_ms if expires_at_ms is None else expires_at_ms
    payload = json.dumps(
        {**scope, **position, "exp": expires, "v": 1},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.digest(secret(request, "CSRF_HMAC_KEY"), payload, "sha256")
    return ".".join((_encode(payload), _encode(signature)))


def decode_signed_cursor(
    request: Request,
    value: str | None,
    *,
    scope: Mapping[str, object],
    position_fields: set[str],
    now_ms: int | None = None,
) -> dict[str, object] | None:
    """Verify signature, exact scope/shape, version, and expiry or raise 400."""
    if value is None:
        return None
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        payload = _decode(encoded_payload)
        signature = _decode(encoded_signature)
        expected = hmac.digest(secret(request, "CSRF_HMAC_KEY"), payload, "sha256")
        if not hmac.compare_digest(signature, expected):
            raise StaleCursorError("invalid")
        decoded = json.loads(payload.decode())
        if not isinstance(decoded, dict):
            raise StaleCursorError("shape", field="position", constraint="type")
        payload_fields = set(decoded)
        base_fields = {"exp", "v"}
        # Check the caller-bound scope before position shape. Every contract
        # shares the signing key, so a valid cursor copied from another list
        # must be an ordinary invalid cursor, never an integrity alert.
        if payload_fields - position_fields - base_fields != set(scope):
            raise StaleCursorError("invalid")
        missing_positions = position_fields - payload_fields
        if missing_positions:
            raise StaleCursorError(
                "shape",
                field=sorted(missing_positions)[0],
                constraint="required",
            )
        # A changed filter value is an ordinary stale cursor, not schema drift.
        if any(decoded.get(name) != expected for name, expected in scope.items()):
            raise StaleCursorError("invalid")
        expires = decoded.get("exp")
        version = decoded.get("v")
        if type(expires) is not int or type(version) is not int or version != 1:
            # Version mismatch is compatibility telemetry, not a zero-tolerance
            # shape signal: rolling deploys may intentionally invalidate v1.
            raise StaleCursorError("invalid")
        current = utc_now_ms() if now_ms is None else now_ms
        if expires < current:
            raise StaleCursorError("expired")
        return decoded
    except StaleCursorError:
        raise
    except (
        ValueError,
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        raise StaleCursorError("invalid") from exc


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    if not value:
        raise ValueError
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)

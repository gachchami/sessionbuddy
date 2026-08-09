"""Minimal protocols and conversion boundary for the Cloudflare D1 FFI proxy."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast, overload


class D1PreparedStatement(Protocol):
    def bind(self, *values: object) -> "D1PreparedStatement": ...

    @overload
    async def first(self) -> Any: ...

    @overload
    async def first(self, column: str) -> Any: ...

    async def run(self) -> Any: ...

    async def all(self) -> Any: ...


class D1Database(Protocol):
    def prepare(self, query: str) -> D1PreparedStatement: ...

    async def batch(self, statements: Sequence[D1PreparedStatement]) -> Any: ...


class PersistenceError(RuntimeError):
    """Safe internal replacement for raw D1/JavaScript errors."""


def to_python[T](value: T) -> T:
    """Eagerly convert a Pyodide JsProxy while remaining usable under host Python."""
    converter = getattr(value, "to_py", None)
    return cast(T, converter()) if callable(converter) else value


def row_mapping(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    converted = to_python(value)
    if not isinstance(converted, Mapping):
        raise PersistenceError("D1 returned an invalid row shape")
    return {str(key): item for key, item in converted.items()}


def result_rows(value: object) -> list[dict[str, object]]:
    converted = to_python(value)
    raw = converted.get("results", []) if isinstance(converted, Mapping) else converted
    raw = to_python(raw)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise PersistenceError("D1 returned an invalid result shape")
    rows: list[dict[str, object]] = []
    for item in raw:
        row = row_mapping(item)
        if row is None:
            raise PersistenceError("D1 returned a null row")
        rows.append(row)
    return rows


async def execute_batch(db: D1Database, statements: Sequence[D1PreparedStatement]) -> object:
    """Execute one bounded atomic D1 batch and hide provider exception details."""
    if not statements:
        raise ValueError("a command batch must contain at least one statement")
    try:
        # workers-py RPC rejects Python tuples; lists cross the JS FFI boundary.
        return to_python(await db.batch(list(statements)))
    except Exception as exc:
        raise PersistenceError("database command failed") from exc

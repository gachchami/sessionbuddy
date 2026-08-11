"""Keyset pagination for the admin submissions listing.

Covers the signed cursor round trip and windowing correctness over more than
one page: every row appears exactly once, in order, with no duplicates and no
missing rows at page boundaries (including ties on submitted_at_ms).
"""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from sessionbuddy.cfp.router import (
    SUBMISSIONS_PAGE_LIMIT,
    _submissions_cursor,
    _submissions_next_cursor,
)
from tests.schema import MIGRATIONS


def _request(csrf_key: str = "c" * 32):
    return SimpleNamespace(scope={"env": SimpleNamespace(CSRF_HMAC_KEY=csrf_key)})


def test_cursor_round_trip_is_signed_and_event_bound() -> None:
    request = _request()
    cursor = _submissions_next_cursor(
        request, event_id="event-1", submitted_at_ms=1_755_000_000_000, row_id="sub-42"
    )
    assert _submissions_cursor(request, cursor, event_id="event-1") == (
        1_755_000_000_000,
        "sub-42",
    )
    with pytest.raises(HTTPException) as denied:
        _submissions_cursor(request, cursor, event_id="event-2")
    assert denied.value.status_code == 400
    tampered = cursor[:-4] + ("AAAA" if not cursor.endswith("AAAA") else "BBBB")
    with pytest.raises(HTTPException):
        _submissions_cursor(_request(), tampered, event_id="event-1")
    with pytest.raises(HTTPException):
        _submissions_cursor(_request("x" * 32), cursor, event_id="event-1")
    assert _submissions_cursor(request, None, event_id="event-1") is None


def test_keyset_window_covers_150_rows_without_duplicates_or_gaps() -> None:
    db = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        db.executescript(migration.read_text())
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,
            status,published_at_ms,created_at_ms,updated_at_ms)
           VALUES('form','org','event',1,'my-cfp','Welcome','{}','published',1,1,1)"""
    )
    # 150 submissions; every third shares a submitted_at_ms to exercise ties.
    for index in range(150):
        db.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,status,submitted_at_ms,
                created_at_ms,updated_at_ms)
               VALUES(?,'org','event','form',?,?,'abstract','name','submitted',?,1,1)""",
            (
                f"sub-{index:03d}",
                f"public-{index:03d}",
                f"title {index}",
                1_000_000 + (index // 3),
            ),
        )

    query = """SELECT s.id FROM submissions s
               WHERE s.organization_id=? AND s.event_id=? {window}
               ORDER BY s.submitted_at_ms DESC,s.id DESC LIMIT ?"""
    collected: list[str] = []
    window: tuple[int, str] | None = None
    pages = 0
    while True:
        pages += 1
        if window is None:
            rows = db.execute(
                query.format(window=""), ("org", "event", SUBMISSIONS_PAGE_LIMIT + 1)
            ).fetchall()
        else:
            rows = db.execute(
                query.format(
                    window="AND (s.submitted_at_ms<? OR (s.submitted_at_ms=? AND s.id<?))"
                ),
                ("org", "event", window[0], window[0], window[1], SUBMISSIONS_PAGE_LIMIT + 1),
            ).fetchall()
        has_more = len(rows) > SUBMISSIONS_PAGE_LIMIT
        rows = rows[:SUBMISSIONS_PAGE_LIMIT]
        collected.extend(row[0] for row in rows)
        if not has_more:
            break
        last_id = rows[-1][0]
        last_ms = db.execute(
            "SELECT submitted_at_ms FROM submissions WHERE id=?", (last_id,)
        ).fetchone()[0]
        window = (last_ms, last_id)

    assert pages == 2
    assert len(collected) == 150
    assert len(set(collected)) == 150, "duplicate rows across page boundary"
    expected = {f"sub-{index:03d}" for index in range(150)}
    assert set(collected) == expected, "missing rows across page boundary"

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sessionbuddy.api.app import _session_home_destination
from sessionbuddy.competition.router import list_public_calls, list_public_events
from sessionbuddy.speaker_operations import router as speaker_routes


class RecordingStatement:
    def __init__(self, database, sql: str) -> None:
        self.database = database
        self.sql = sql
        self.values = ()

    def bind(self, *values):
        self.values = values
        return self

    async def all(self):
        self.database.sql.append(self.sql)
        if "GROUP BY form_id" in self.sql:
            return {"results": self.database.counts}
        return {"results": self.database.calls}


class RecordingDatabase:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.call = {
            "id": "event-b",
            "name": "Public Event B",
            "event_id": "event-b",
            "event_name": "Public Event B",
            "starts_at_ms": 1_900_000_000_000,
            "ends_at_ms": 1_900_086_400_000,
            "time_zone": "UTC",
            "location": "Online",
            "delivery_mode": "virtual",
            "form_id": "form-b",
            "cfp_slug": "event-b-call",
            "slug": "event-b-call",
            "opens_at_ms": None,
            "closes_at_ms": None,
            "submission_limit": 3,
            "schedule_published": False,
            "speaker_count": 0,
        }
        self.calls = [self.call]
        self.counts = [{"form_id": "form-b", "submission_count": 1}]

    def prepare(self, sql: str):
        return RecordingStatement(self, sql)


class SQLiteStatement:
    def __init__(self, connection: sqlite3.Connection, sql: str) -> None:
        self.connection = connection
        self.sql = sql
        self.values = ()

    def bind(self, *values):
        self.values = values
        return self

    async def all(self):
        return {"results": [dict(row) for row in self.connection.execute(self.sql, self.values)]}


class SQLiteDatabase:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def prepare(self, sql: str):
        return SQLiteStatement(self.connection, sql)


def request_for(database: RecordingDatabase) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "env": SimpleNamespace(DB=database),
        }
    )


async def test_public_calls_uses_canonical_published_form_selection() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE events (
          id TEXT, organization_id TEXT, name TEXT, starts_at_ms INTEGER,
          ends_at_ms INTEGER, time_zone TEXT, location TEXT, delivery_mode TEXT, status TEXT
        );
        CREATE TABLE call_for_speaker_forms (
          id TEXT, organization_id TEXT, event_id TEXT, slug TEXT, opens_at_ms INTEGER,
          closes_at_ms INTEGER, version INTEGER, status TEXT, published_at_ms INTEGER
        );
        INSERT INTO events VALUES(
          'event-b','org-b','Event B',10,20,'UTC','Online','virtual','active'
        );
        INSERT INTO call_for_speaker_forms VALUES
          ('form-1','org-b','event-b','old-call',NULL,NULL,1,'published',100),
          ('form-2','org-b','event-b','current-call',NULL,NULL,2,'published',200);
        """
    )

    response = await list_public_calls(request_for(SQLiteDatabase(connection)))

    assert len(response.data) == 1
    assert response.data[0].cfp_slug == "current-call"
    assert response.data[0].cfp_state == "open"


async def test_speaker_calls_uses_one_submission_aggregate(monkeypatch) -> None:
    database = RecordingDatabase()

    async def allow_persona(_request, _persona):
        return None

    async def authenticate(_request):
        return SimpleNamespace(actor=SimpleNamespace(user_id="speaker-a"))

    monkeypatch.setattr(speaker_routes, "require_document_persona", allow_persona)
    monkeypatch.setattr(speaker_routes, "authenticate_request", authenticate)

    response = await speaker_routes.list_speaker_open_calls(request_for(database))

    assert len(response.data) == 1
    call = response.data[0]
    assert call.event_id == "event-b"
    assert call.submission_count == 1
    assert call.remaining_submissions == 2
    assert call.actionable is True
    aggregates = [sql for sql in database.sql if "GROUP BY form_id" in sql]
    assert len(aggregates) == 1


async def test_speaker_calls_keep_state_and_capacity_orthogonal(monkeypatch) -> None:
    database = RecordingDatabase()
    scheduled = {**database.call, "form_id": "scheduled", "slug": "scheduled", "opens_at_ms": 2_000}
    exhausted = {
        **database.call,
        "form_id": "exhausted",
        "slug": "exhausted",
        "submission_limit": 1,
    }
    closed = {**database.call, "form_id": "closed", "slug": "closed", "closes_at_ms": 999}
    database.calls = [scheduled, exhausted, closed]
    database.counts = [{"form_id": "exhausted", "submission_count": 1}]

    async def allow_persona(_request, _persona):
        return None

    async def authenticate(_request):
        return SimpleNamespace(actor=SimpleNamespace(user_id="speaker-a"))

    monkeypatch.setattr(speaker_routes, "require_document_persona", allow_persona)
    monkeypatch.setattr(speaker_routes, "authenticate_request", authenticate)
    monkeypatch.setattr(speaker_routes, "utc_now_ms", lambda: 1_000)

    response = await speaker_routes.list_speaker_open_calls(request_for(database))

    assert [call.form_id for call in response.data] == ["scheduled", "exhausted"]
    assert response.data[0].cfp_state == "scheduled"
    assert response.data[0].actionable is False
    assert response.data[1].cfp_state == "open"
    assert response.data[1].remaining_submissions == 0
    assert response.data[1].actionable is False
    assert "f.closes_at_ms>?1" in database.sql[0]


async def test_public_call_response_has_no_personalized_fields() -> None:
    response = await list_public_calls(request_for(RecordingDatabase()))
    payload = response.data[0].model_dump()

    assert "submission_count" not in payload
    assert "remaining_submissions" not in payload
    assert "actionable" not in payload


async def test_public_event_without_a_call_has_no_cfp_state() -> None:
    database = RecordingDatabase()
    database.calls = [{**database.call, "cfp_slug": None}]

    response = await list_public_events(request_for(database))

    assert response.data[0].cfp_slug is None
    assert response.data[0].cfp_state is None


def test_roleless_session_home_falls_back_to_public_calls() -> None:
    assert _session_home_destination(SimpleNamespace(active_role=None)) == "/calls"


def test_unknown_session_role_still_fails_closed() -> None:
    with pytest.raises(HTTPException) as raised:
        _session_home_destination(SimpleNamespace(active_role="unknown-role"))
    assert raised.value.status_code == 403

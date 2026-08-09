from types import SimpleNamespace

import pytest

from sessionbuddy.communications.d1 import D1CommunicationsService
from tests.speaker_operations.test_asset_boundary import AsyncSqlite
from tests.speaker_operations.test_speaker_onboarding_schema import MIGRATIONS


def seed_event(connection) -> None:
    connection.execute(
        """INSERT INTO organizations (id,name,status,created_at_ms,updated_at_ms)
           VALUES ('org-a','Org a','active',1000,1000)"""
    )
    connection.execute(
        """INSERT INTO events
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,delivery_mode,status,
            created_at_ms,updated_at_ms)
           VALUES ('event-a','org-a','Event a',1000,2000,'UTC','hybrid','active',1000,1000)"""
    )


@pytest.mark.asyncio
async def test_communication_history_uses_stable_cursor_pagination() -> None:
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event(connection)
    for number in range(5):
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES (?,?,?,?,?,?,?,'queued',?,?)""",
            (
                f"message-{number}", "org-a", "event-a", "speaker-a@example.test",
                f"Subject {number}", "Body", f"history:{number}", number + 1, number + 1,
            ),
        )
    request = SimpleNamespace(scope={"env": SimpleNamespace(DB=AsyncSqlite(connection))})
    service = D1CommunicationsService(request)
    service.organization_id = "org-a"

    first = await service.statuses("event-a", limit=2)
    second = await service.statuses("event-a", cursor=first.next_cursor, limit=2)
    third = await service.statuses("event-a", cursor=second.next_cursor, limit=2)

    assert [item.id for item in first.data] == ["message-4", "message-3"]
    assert [item.id for item in second.data] == ["message-2", "message-1"]
    assert [item.id for item in third.data] == ["message-0"]
    assert third.next_cursor is None


@pytest.mark.asyncio
async def test_first_communication_history_page_does_not_bind_an_integer_sentinel() -> None:
    class Statement:
        def __init__(self) -> None:
            self.values = ()

        def bind(self, *values):
            self.values = values
            return self

        async def all(self):
            assert len(self.values) == 3
            assert self.values == ("org-a", "event-a", 26)
            return {"results": []}

    class Database:
        def prepare(self, sql: str):
            assert "updated_at_ms<?3" not in sql
            return Statement()

    request = SimpleNamespace(scope={"env": SimpleNamespace(DB=Database())})
    service = D1CommunicationsService(request)
    service.organization_id = "org-a"

    page = await service.statuses("event-a")

    assert page.data == []
    assert page.next_cursor is None

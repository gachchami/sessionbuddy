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
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
            delivery_mode,description,status,created_at_ms,updated_at_ms)
           VALUES ('event-a','org-a','Event a',1000,2000,'UTC','Online','hybrid',
                   'Test event','active',1000,1000)"""
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
    request = SimpleNamespace(
        scope={
            "env": SimpleNamespace(
                DB=AsyncSqlite(connection),
                CSRF_HMAC_KEY="communications-cursor-test-key-0000001",
            )
        }
    )
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
async def test_history_excludes_authentication_mail_and_categorizes_event_mail() -> None:
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event(connection)
    for message_id, subject, key in (
        ("auth-message", "Your SessionBuddy sign-in link", "auth:challenge-a"),
        ("reminder", "Next steps for Event a", "task-reminder:task-a:2026-08-18"),
        ("schedule", "Schedule confirmation", "schedule:session-a"),
    ):
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES (?,?,?,?,?,?,?,'delivered',1000,1000)""",
            (message_id, "org-a", "event-a", "speaker@example.test", subject, "<p>Body</p>", key),
        )
    request = SimpleNamespace(scope={"env": SimpleNamespace(
        DB=AsyncSqlite(connection), CSRF_HMAC_KEY="communications-cursor-test-key-0000001"
    )})
    service = D1CommunicationsService(request)
    service.organization_id = "org-a"

    page = await service.statuses("event-a")

    assert {item.id for item in page.data} == {"reminder", "schedule"}
    assert {item.id: item.category for item in page.data} == {
        "reminder": "reminder", "schedule": "schedule"
    }
    assert all(item.body_preview == "Body" for item in page.data)


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

    request = SimpleNamespace(
        scope={
            "env": SimpleNamespace(
                DB=Database(),
                CSRF_HMAC_KEY="communications-cursor-test-key-0000001",
            )
        }
    )
    service = D1CommunicationsService(request)
    service.organization_id = "org-a"

    page = await service.statuses("event-a")

    assert page.data == []
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_communication_history_rejects_tampered_and_cross_event_cursors() -> None:
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event(connection)
    connection.execute(
        """INSERT INTO events
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
            delivery_mode,description,status,created_at_ms,updated_at_ms)
           VALUES ('event-b','org-a','Event b',1000,2000,'UTC','Online','hybrid',
                   'Test event','active',1000,1000)"""
    )
    for number in range(2):
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES (?,?,?,?,?,?,?,'queued',?,?)""",
            (
                f"message-{number}",
                "org-a",
                "event-a",
                "speaker-a@example.test",
                f"Subject {number}",
                "Body",
                f"history:{number}",
                number + 1,
                number + 1,
            ),
        )
    request = SimpleNamespace(
        scope={
            "env": SimpleNamespace(
                DB=AsyncSqlite(connection),
                CSRF_HMAC_KEY="communications-cursor-test-key-0000001",
            )
        }
    )
    service = D1CommunicationsService(request)
    service.organization_id = "org-a"
    first = await service.statuses("event-a", limit=1)
    assert first.next_cursor is not None

    replacement = "A" if first.next_cursor[0] != "A" else "B"
    tampered_cursor = replacement + first.next_cursor[1:]
    with pytest.raises(Exception) as tampered:
        await service.statuses("event-a", cursor=tampered_cursor, limit=1)
    assert getattr(tampered.value, "status_code", None) == 400

    with pytest.raises(Exception) as cross_event:
        await service.statuses("event-b", cursor=first.next_cursor, limit=1)
    assert getattr(cross_event.value, "status_code", None) == 400

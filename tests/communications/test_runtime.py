import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sessionbuddy.communications.runtime import (
    DeliveryClaim,
    DeliveryEnvelope,
    ProviderResult,
    ReminderEnvelope,
    ReminderWorkflow,
    ResendProvider,
    consume_delivery,
    consume_reminder,
)
from tests.wave3.test_asset_boundary import AsyncSqlite
from tests.wave3.test_speaker_onboarding_schema import MIGRATIONS, add_speaker, seed_foundation


def claim() -> DeliveryClaim:
    return DeliveryClaim(
        "message", "org", "event", "private@example.test", "Subject", "<p>Body</p>", "key", 1
    )


class Repository:
    def __init__(self, value=None):
        self.value = value
        self.events = []

    async def claim(self, message_id, now_ms):
        self.events.append(("claim", message_id, now_ms))
        return self.value

    async def delivered(self, item, provider_id, now_ms):
        self.events.append(("delivered", provider_id))

    async def failed(self, item, code, retryable, now_ms):
        self.events.append(("failed", code, retryable))


class Provider:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def send(self, item):
        self.calls += 1
        return self.result


def test_delivery_envelope_is_versioned_and_strict() -> None:
    assert DeliveryEnvelope.parse('{"schema_version":1,"message_id":"m"}').message_id == "m"
    with pytest.raises(ValueError):
        DeliveryEnvelope.parse('{"schema_version":1,"message_id":"m","email":"x"}')
    assert (
        DeliveryEnvelope.parse({"schema_version": 1, "message_id": "mapped"}).message_id == "mapped"
    )


def test_reminder_envelope_is_versioned_and_strict() -> None:
    envelope = ReminderEnvelope.parse(
        {"schema_version": 1, "schedule_id": "schedule", "schedule_version": 2}
    )
    assert (envelope.schedule_id, envelope.schedule_version) == ("schedule", 2)
    with pytest.raises(ValueError):
        ReminderEnvelope.parse(
            {"schema_version": 1, "schedule_id": "schedule", "schedule_version": 0}
        )


@pytest.mark.asyncio
async def test_consumer_is_replay_safe_and_records_safe_outcomes() -> None:
    repository = Repository(claim())
    assert await consume_delivery(
        json.dumps({"schema_version": 1, "message_id": "message"}),
        repository,
        Provider(ProviderResult(True, "provider")),
        10,
    )
    assert repository.events[-1] == ("delivered", "provider")
    replay, provider = Repository(None), Provider(ProviderResult(True, "provider"))
    assert await consume_delivery(
        '{"schema_version":1,"message_id":"message"}', replay, provider, 11
    )
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_retryable_provider_failure_is_not_acknowledged() -> None:
    repository = Repository(claim())
    acknowledged = await consume_delivery(
        '{"schema_version":1,"message_id":"message"}',
        repository,
        Provider(ProviderResult(False, retryable=True, error_code="provider_unavailable")),
        10,
    )
    assert not acknowledged
    assert repository.events[-1] == ("failed", "provider_unavailable", True)


class Response:
    status = 200

    async def json(self):
        return {"id": "resend-id"}


@pytest.mark.asyncio
async def test_resend_adapter_uses_deterministic_key_without_logging_payload() -> None:
    captured = {}

    async def fetcher(url, **options):
        captured.update(url=url, **options)
        return Response()

    result = await ResendProvider("secret", "Events <events@example.test>", fetcher=fetcher).send(
        claim()
    )
    assert result == ProviderResult(True, "resend-id")
    assert captured["headers"]["idempotency-key"] == "key"
    assert captured["headers"]["authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_resend_adapter_attaches_private_calendar_without_recipient_fanout() -> None:
    captured = {}

    async def fetcher(url, **options):
        captured.update(options)
        return Response()

    calendar_claim = DeliveryClaim(
        "message",
        "org",
        "event",
        "one@example.test",
        "Subject",
        "Body",
        "key",
        1,
        "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
    )
    await ResendProvider("secret", "events@example.test", fetcher=fetcher).send(calendar_claim)
    payload = json.loads(captured["body"])
    assert payload["to"] == ["one@example.test"]
    assert payload["attachments"][0]["filename"] == "session.ics"
    assert "one@example.test" not in payload["attachments"][0]["content"]


class Step:
    def __init__(self):
        self.timestamp = None

    async def sleep_until(self, name, timestamp):
        self.timestamp = (name, timestamp)


class Queue:
    def __init__(self):
        self.messages = []

    async def send(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_reminder_workflow_sleeps_then_enqueues_versioned_schedule() -> None:
    workflow = object.__new__(ReminderWorkflow)
    workflow.env = SimpleNamespace(COMMUNICATION_QUEUE=Queue())
    step = Step()
    await workflow.run(
        {
            "payload": {
                "schema_version": 1,
                "schedule_id": "schedule",
                "schedule_version": 2,
                "send_at_ms": 0,
            }
        },
        step,
    )
    assert step.timestamp == ("wait-until-reminder", datetime(1970, 1, 1, tzinfo=UTC))
    assert workflow.env.COMMUNICATION_QUEUE.messages == [
        {"schema_version": 1, "schedule_id": "schedule", "schedule_version": 2}
    ]


@pytest.mark.asyncio
async def test_reminder_consumer_materializes_due_version_once_and_marks_dispatched() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_foundation(connection)
    add_speaker(connection, "a")
    connection.execute(
        """INSERT INTO speaker_tasks
           (id,organization_id,event_id,event_speaker_id,task_type,title,destination_type,
            state,due_at_ms,created_at_ms,updated_at_ms)
           VALUES ('task','org-a','event-a','speaker-a','profile','Complete profile',
                   'profile','open',2000,1,1)"""
    )
    connection.execute(
        """INSERT INTO communication_templates VALUES
           ('template','org-a','event-a','Reminder','task_reminder',
            'Reminder: {{task.title}}',
            '<p>Hello {{speaker.name}}, {{task.title}} for {{event.name}}</p>',1,1,1)"""
    )
    connection.execute(
        """INSERT INTO reminder_schedules
           (id,organization_id,event_id,task_id,template_id,recipient_user_id,
            send_at_ms,schedule_version,state,updated_at_ms)
           VALUES ('schedule','org-a','event-a','task','template','user-a',1000,2,
                   'scheduled',1)"""
    )
    database = AsyncSqlite(connection)
    envelope = {"schema_version": 1, "schedule_id": "schedule", "schedule_version": 2}
    message_id = await consume_reminder(database, envelope, 1000)
    assert message_id is not None
    assert await consume_reminder(database, envelope, 1001) is None
    row = connection.execute(
        "SELECT status,deterministic_key FROM communication_messages WHERE id=?", (message_id,)
    ).fetchone()
    assert tuple(row) == ("queued", "reminder:schedule:v2")
    assert (
        connection.execute("SELECT state FROM reminder_schedules WHERE id='schedule'").fetchone()[0]
        == "dispatched"
    )
    assert (
        connection.execute(
            "SELECT count(*) FROM outbox_messages WHERE aggregate_id=?", (message_id,)
        ).fetchone()[0]
        == 1
    )

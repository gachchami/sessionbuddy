import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sessionbuddy.communications.runtime import (
    D1DeliveryRepository,
    DeliveryClaim,
    DeliveryEnvelope,
    DispatchResult,
    MailpitProvider,
    ProviderResult,
    ReminderEnvelope,
    ReminderWorkflow,
    RequeueResult,
    ResendProvider,
    consume_delivery,
    consume_reminder,
    dispatch_stuck_deliveries,
    park_unconfigured_delivery,
    requeue_exhausted_deliveries,
    retry_delay_ms,
)
from tests.speaker_operations.test_asset_boundary import AsyncSqlite
from tests.speaker_operations.test_speaker_onboarding_schema import (
    MIGRATIONS,
    add_speaker,
)


def seed_event_platform(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO organizations (id,name,status,created_at_ms,updated_at_ms)
           VALUES ('org-a','Org a','active',1000,1000)"""
    )
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES ('user-a','speaker-a@example.test','speaker-a@example.test','active',1000,1000)"""
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES ('org-member-a','org-a','user-a','member','active',1000,1000)"""
    )
    connection.execute(
        """INSERT INTO events
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
            delivery_mode,description,status,created_at_ms,updated_at_ms,created_by_user_id)
           VALUES ('event-a','org-a','Event a',1000,2000,'UTC','Online','hybrid',
                   'Test event','active',1000,1000,'user-a')"""
    )
    connection.execute(
        """INSERT INTO event_memberships
           (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES ('event-member-a','org-a','event-a','user-a','speaker','active',1000,1000)"""
    )


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


class MailpitResponse:
    status = 200

    async def json(self):
        return {"ID": "mailpit-id"}


@pytest.mark.asyncio
async def test_mailpit_adapter_uses_local_http_api_and_preserves_email_content() -> None:
    captured = {}

    async def fetcher(url, **options):
        captured.update(url=url, **options)
        return MailpitResponse()

    result = await MailpitProvider(
        "http://mailpit:8025", "SessionBuddy Local <events@example.test>", fetcher=fetcher
    ).send(claim())

    assert result == ProviderResult(True, "mailpit-id")
    assert captured["url"] == "http://mailpit:8025/api/v1/send"
    payload = json.loads(captured["body"])
    assert payload["From"] == {"Email": "events@example.test", "Name": "SessionBuddy Local"}
    assert payload["To"] == [{"Email": "private@example.test"}]
    assert payload["Subject"] == "Subject"
    assert payload["HTML"] == "<p>Body</p>"
    assert payload["Headers"]["X-SessionBuddy-Delivery-Key"] == "key"


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
async def test_resend_adapter_applies_event_identity_to_site_sender() -> None:
    captured = {}

    async def fetcher(url, **options):
        captured.update(url=url, **options)
        return Response()

    event_claim = DeliveryClaim(
        "message",
        "org",
        "event",
        "speaker@example.test",
        "Subject",
        "<p>Body</p>",
        "key",
        1,
        sender_name="Conference Team",
        reply_to_email="program@example.test",
    )
    result = await ResendProvider(
        "secret", "SessionBuddy <notifications@mail.noneli.com>", fetcher=fetcher
    ).send(event_claim)

    assert result == ProviderResult(True, "resend-id")
    payload = json.loads(captured["body"])
    assert payload["from"] == "Conference Team <notifications@mail.noneli.com>"
    assert payload["reply_to"] == "program@example.test"


@pytest.mark.asyncio
async def test_delivery_claim_loads_event_email_identity() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event_platform(connection)
    connection.execute(
        """UPDATE events SET email_sender_name='Conference Team',
                  email_reply_to='program@example.test' WHERE id='event-a'"""
    )
    connection.execute(
        """INSERT INTO communication_messages
           (id,organization_id,event_id,recipient_email,subject,html_body,
            deterministic_key,status,queued_at_ms,updated_at_ms)
           VALUES ('event-message','org-a','event-a','speaker@example.test','Subject',
                   '<p>Body</p>','event-message-key','queued',1,1)"""
    )

    item = await D1DeliveryRepository(AsyncSqlite(connection)).claim("event-message", 2)

    assert item is not None
    assert item.sender_name == "Conference Team"
    assert item.reply_to_email == "program@example.test"


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
async def test_dispatcher_republishes_queued_and_retryable_failed_messages() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event_platform(connection)
    messages = [
        ("queued", "queued", 0, None, 1),
        ("fresh-queued", "queued", 0, None, 499_000),
        ("retryable", "failed", 2, "provider_unavailable", 2),
        ("invalid", "failed", 1, "invalid_response", 3),
        ("permanent", "failed", 1, "provider_rejected", 4),
        ("exhausted", "failed", 12, "provider_unavailable", 5),
        ("delivered", "delivered", 1, None, 6),
    ]
    for message_id, status, attempts, error, updated in messages:
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,attempt_count,last_error_code,queued_at_ms,updated_at_ms)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                message_id,
                "org-a",
                "event-a",
                f"{message_id}@example.test",
                "Subject",
                "Body",
                f"key:{message_id}",
                status,
                attempts,
                error,
                1,
                updated,
            ),
        )
    queue = Queue()

    result = await dispatch_stuck_deliveries(AsyncSqlite(connection), queue, 500_000)

    assert result == DispatchResult(
        recovered_sending=0,
        published=3,
        publish_failures=0,
        exhausted=1,
        oldest_pending_age_ms=499_999,
    )
    assert {item["message_id"] for item in queue.messages} == {"queued", "retryable", "invalid"}


@pytest.mark.asyncio
async def test_dispatcher_backlog_age_excludes_permanent_and_exhausted_failures() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event_platform(connection)
    for values in (
        ("permanent", 1, "provider_rejected", 1),
        ("exhausted", 12, "provider_unavailable", 2),
        ("actionable", 1, "invalid_response", 490_000),
        ("parked", 1, "provider_unconfigured", 3),
    ):
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,attempt_count,last_error_code,queued_at_ms,updated_at_ms)
               VALUES
                 (?,'org-a','event-a','person@example.test','Subject','Body',
                  ?,'failed',?,?,1,?)""",
            (values[0], f"key:{values[0]}", values[1], values[2], values[3]),
        )

    result = await dispatch_stuck_deliveries(AsyncSqlite(connection), Queue(), 500_000)

    assert result.oldest_pending_age_ms == 10_000
    # Parked provider_unconfigured rows surface as exhausted (recoverable via
    # requeue) but never distort the pending-age signal.
    assert result.exhausted == 2


@pytest.mark.asyncio
async def test_stale_recovery_batches_twenty_five_messages_per_d1_round_trip() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event_platform(connection)
    for index in range(30):
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,attempt_count,queued_at_ms,updated_at_ms)
               VALUES
                 (?,'org-a','event-a','person@example.test','Subject','Body',
                  ?,'sending',1,1,1)""",
            (f"stale-{index}", f"key:stale-{index}"),
        )

    class CountingDatabase(AsyncSqlite):
        def __init__(self, value):
            super().__init__(value)
            self.batch_calls = 0

        async def batch(self, statements):
            self.batch_calls += 1
            return await super().batch(statements)

    db = CountingDatabase(connection)
    result = await dispatch_stuck_deliveries(db, Queue(), 500_000)

    assert result.recovered_sending == 30
    assert db.batch_calls == 2


@pytest.mark.asyncio
async def test_operator_requeue_only_resets_exhausted_transient_failures() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event_platform(connection)
    for values in (
        ("transient", "org-a", 12, "provider_unavailable"),
        ("permanent", "org-a", 12, "provider_rejected"),
        ("parked", "org-a", 1, "provider_unconfigured"),
    ):
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,attempt_count,last_error_code,queued_at_ms,updated_at_ms)
               VALUES (?,?,'event-a','person@example.test','Subject','Body',?,'failed',?,?,1,1)""",
            (values[0], values[1], f"key:{values[0]}", values[2], values[3]),
        )
    queue = Queue()

    result = await requeue_exhausted_deliveries(
        AsyncSqlite(connection), queue, "org-a", 500_000
    )

    assert result == RequeueResult(2, 2, 2, 0)
    assert {message["message_id"] for message in queue.messages} == {"transient", "parked"}
    state = connection.execute(
        """SELECT status,attempt_count,attempt_limit,last_error_code
           FROM communication_messages WHERE id='transient'"""
    ).fetchone()
    assert tuple(state) == ("queued", 12, 24, None)
    # Parked while unconfigured: recoverable through the SAME operator action,
    # no manual SQL required once credentials are restored.
    parked = connection.execute(
        """SELECT status,last_error_code FROM communication_messages
           WHERE id='parked'"""
    ).fetchone()
    assert tuple(parked) == ("queued", None)


def test_delivery_retry_backoff_is_exponential_and_capped() -> None:
    assert retry_delay_ms(0) == 60 * 1000
    assert retry_delay_ms(1) == 2 * 60 * 1000
    assert retry_delay_ms(8) == 256 * 60 * 1000
    assert retry_delay_ms(9) == 6 * 60 * 60 * 1000
    assert retry_delay_ms(100) == 6 * 60 * 60 * 1000


@pytest.mark.asyncio
async def test_dispatcher_recovers_stale_sending_but_not_active_claims() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_event_platform(connection)
    for message_id, updated in (("stale", 100_000), ("active", 900_000)):
        connection.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_email,subject,html_body,
                deterministic_key,status,attempt_count,queued_at_ms,updated_at_ms)
               VALUES (?,?,?,?,?,?,?,'sending',1,?,?)""",
            (
                message_id,
                "org-a",
                "event-a",
                f"{message_id}@example.test",
                "Subject",
                "Body",
                f"key:{message_id}",
                1,
                updated,
            ),
        )
        connection.execute(
            """INSERT INTO communication_delivery_attempts
               (id,organization_id,event_id,message_id,attempt_number,status,started_at_ms)
               VALUES (?,?,?,?,1,'started',?)""",
            (f"attempt-{message_id}", "org-a", "event-a", message_id, updated),
        )
    queue = Queue()

    result = await dispatch_stuck_deliveries(AsyncSqlite(connection), queue, 1_000_000)

    assert result == DispatchResult(
        recovered_sending=1,
        published=0,
        publish_failures=0,
        exhausted=0,
        oldest_pending_age_ms=100_000,
    )
    assert queue.messages == []
    assert connection.execute(
        "SELECT status FROM communication_messages WHERE id='active'"
    ).fetchone()[0] == "sending"
    assert connection.execute(
        "SELECT status FROM communication_delivery_attempts WHERE id='attempt-stale'"
    ).fetchone()[0] == "retryable_failure"
    assert connection.execute(
        "SELECT status FROM communication_delivery_attempts WHERE id='attempt-active'"
    ).fetchone()[0] == "started"


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
    seed_event_platform(connection)
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


async def test_unconfigured_provider_parks_delivery_permanently() -> None:
    """No credentials => permanent failure, acked; never a retry storm."""
    repository = Repository(claim())
    acknowledged = await park_unconfigured_delivery(
        '{"schema_version":1,"message_id":"m"}', repository, 1000
    )
    assert acknowledged is True
    assert ("claim", "m", 1000) in repository.events
    assert ("failed", "provider_unconfigured", False) in repository.events


async def test_unconfigured_provider_acks_unknown_or_claimed_messages() -> None:
    repository = Repository(None)
    assert await park_unconfigured_delivery(
        '{"schema_version":1,"message_id":"m"}', repository, 1000
    ) is True
    assert await park_unconfigured_delivery("not-json", Repository(None), 1000) is True

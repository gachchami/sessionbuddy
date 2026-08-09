import sqlite3

import pytest

from sessionbuddy.agenda import AgendaCalendarChange, ScheduleSpeaker, queue_calendar_changes
from tests.speaker_operations.test_asset_boundary import AsyncSqlite
from tests.speaker_operations.test_speaker_onboarding_schema import MIGRATIONS, seed_platform


@pytest.fixture
def database() -> tuple[AsyncSqlite, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text())
    seed_platform(connection)
    connection.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,program_id,name,rubric_json,status,
            created_at_ms,updated_at_ms)
           VALUES ('round-a','org-a','event-a','program-a','Round','{}','open',1,1)"""
    )
    connection.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES ('decision-a','org-a','event-a','round-a','submission-a','accepted','',
                   'user-a',1,1)"""
    )
    connection.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
           VALUES ('accepted-a','org-a','event-a','submission-a','decision-a',1)"""
    )
    connection.execute(
        """INSERT INTO event_rooms
           (id,organization_id,event_id,name,status,created_at_ms,updated_at_ms)
           VALUES ('room-a','org-a','event-a','Main Room','active',1,1)"""
    )
    connection.execute(
        """INSERT INTO schedule_revisions
           (id,organization_id,event_id,revision_number,name,status,created_by_user_id,
            created_at_ms,updated_at_ms,published_at_ms)
           VALUES ('revision-a','org-a','event-a',1,'Published','draft','user-a',1,1,NULL)"""
    )
    connection.execute(
        """INSERT INTO agenda_items
           (id,organization_id,event_id,revision_id,accepted_session_id,room_id,event_date,
            event_time_zone,starts_at_ms,ends_at_ms,created_at_ms,updated_at_ms)
           VALUES ('agenda-a','org-a','event-a','revision-a','accepted-a','room-a',
                   '1970-01-01','UTC',1100,1900,1,1)"""
    )
    connection.execute(
        """UPDATE schedule_revisions
           SET status='published',published_at_ms=1,updated_at_ms=1
           WHERE id='revision-a'"""
    )
    return AsyncSqlite(connection), connection


def change(**values) -> AgendaCalendarChange:
    defaults = {
        "organization_id": "org-a",
        "event_id": "event-a",
        "agenda_item_id": "agenda-a",
        "starts_at_ms": 2_000,
        "ends_at_ms": 3_000,
        "title": "Opening Session",
        "description": "Description",
        "room": "Main Room",
        "organizer_email": "events@example.test",
        "published": True,
    }
    defaults.update(values)
    return AgendaCalendarChange(**defaults)


def speaker() -> ScheduleSpeaker:
    return ScheduleSpeaker("user-a", "user-a@example.test", "Speaker <One>")


@pytest.mark.asyncio
async def test_stable_uid_sequence_and_retry_safe_delivery(database) -> None:
    db, connection = database
    first = await queue_calendar_changes(db, change(), [speaker()], now_ms=1_000)
    assert len(first) == 1
    invitation = connection.execute(
        "SELECT calendar_uid,sequence FROM calendar_invitations"
    ).fetchone()
    uid = invitation["calendar_uid"]
    assert invitation["sequence"] == 0
    assert await queue_calendar_changes(db, change(), [speaker()], now_ms=1_001) == []

    second = await queue_calendar_changes(db, change(room="Room 2"), [speaker()], now_ms=1_002)
    assert len(second) == 1
    updated = connection.execute(
        "SELECT calendar_uid,sequence FROM calendar_invitations"
    ).fetchone()
    assert (updated["calendar_uid"], updated["sequence"]) == (uid, 1)
    versions = connection.execute(
        "SELECT sequence,ics_content FROM calendar_invitation_versions ORDER BY sequence"
    ).fetchall()
    assert [row["sequence"] for row in versions] == [0, 1]
    assert f"UID:{uid}\r\n" in versions[1]["ics_content"]
    assert "SEQUENCE:1\r\n" in versions[1]["ics_content"]
    assert connection.execute("SELECT count(*) FROM communication_messages").fetchone()[0] == 2
    assert connection.execute("SELECT count(*) FROM outbox_messages").fetchone()[0] == 2


@pytest.mark.asyncio
async def test_draft_and_duplicate_recipient_are_rejected_or_noop(database) -> None:
    db, connection = database
    assert (
        await queue_calendar_changes(db, change(published=False), [speaker()], now_ms=1_000) == []
    )
    with pytest.raises(ValueError):
        await queue_calendar_changes(db, change(), [speaker(), speaker()], now_ms=1_000)
    assert connection.execute("SELECT count(*) FROM communication_messages").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_each_recipient_message_contains_no_other_recipient(database) -> None:
    db, connection = database
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,authorization_version,version,created_at_ms,updated_at_ms)
           VALUES ('user-c','c@example.test','c@example.test','active',1,1,1,1)"""
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES ('member-c','org-a','user-c','member','active',1,1)"""
    )
    connection.execute(
        """INSERT INTO event_memberships
           (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES ('event-member-c','org-a','event-a','user-c','speaker','active',1,1)"""
    )
    recipients = [speaker(), ScheduleSpeaker("user-c", "c@example.test", "Speaker Two")]
    await queue_calendar_changes(db, change(), recipients, now_ms=1_000)
    rows = connection.execute(
        "SELECT recipient_email,html_body FROM communication_messages ORDER BY recipient_email"
    ).fetchall()
    assert len(rows) == 2
    assert all("c@example.test" not in row["html_body"] for row in rows)
    assert all("user-a@example.test" not in row["html_body"] for row in rows)

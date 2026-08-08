import sqlite3

import pytest

from sessionbuddy.agenda import AgendaRepository, AgendaSlot
from sessionbuddy.platform.db.d1 import PersistenceError
from tests.speaker_operations.test_asset_boundary import AsyncSqlite
from tests.speaker_operations.test_speaker_onboarding_schema import (
    MIGRATIONS,
    add_speaker,
    seed_platform,
)


@pytest.fixture
def connection() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        db.executescript(migration.read_text(encoding="utf-8"))
    seed_platform(db)
    add_speaker(db, "a")
    db.execute(
        """INSERT INTO submission_speakers
           (id,organization_id,event_id,submission_id,event_speaker_id,role,
            snapshot_name,created_at_ms) VALUES
           ('submission-speaker-a','org-a','event-a','submission-a','speaker-a',
            'primary','Same Speaker',1000)"""
    )
    db.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,program_id,name,rubric_json,status,
            created_at_ms,updated_at_ms,closed_at_ms) VALUES
           ('round-a','org-a','event-a','program-a','Final','{}','closed',1000,1000,1000)"""
    )
    db.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
            decided_by_user_id,decided_at_ms,updated_at_ms) VALUES
           ('decision-a','org-a','event-a','round-a','submission-a','accepted','',
            'user-a',1000,1000)"""
    )
    db.execute(
        """INSERT INTO submissions
           (id,organization_id,event_id,program_id,form_id,public_session_id,
            proposal_title,proposal_abstract,speaker_name,status,submitted_at_ms,
            created_at_ms,updated_at_ms) VALUES
           ('submission-a2','org-a','event-a','program-a','form-a','public-a2',
            'Talk 2','Abstract','Same Speaker','submitted',1000,1000,1000)"""
    )
    db.execute(
        """INSERT INTO submission_speakers
           (id,organization_id,event_id,submission_id,event_speaker_id,role,
            snapshot_name,created_at_ms) VALUES
           ('submission-speaker-a2','org-a','event-a','submission-a2','speaker-a',
            'primary','Same Speaker',1000)"""
    )
    db.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
            decided_by_user_id,decided_at_ms,updated_at_ms) VALUES
           ('decision-a2','org-a','event-a','round-a','submission-a2','accepted','',
            'user-a',1000,1000)"""
    )
    db.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,submission_id,decision_id,created_at_ms) VALUES
           ('session-a','org-a','event-a','submission-a','decision-a',1000)"""
    )
    db.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,submission_id,decision_id,created_at_ms) VALUES
           ('session-a2','org-a','event-a','submission-a2','decision-a2',1000)"""
    )
    db.executemany(
        """INSERT INTO event_rooms
           (id,organization_id,event_id,name,status,created_at_ms,updated_at_ms)
           VALUES (?,'org-a','event-a',?,'active',1000,1000)""",
        (("room-a", "Room A"), ("room-b", "Room B")),
    )
    db.executemany(
        """INSERT INTO event_tracks
           (id,organization_id,event_id,name,is_exclusive,status,created_at_ms,updated_at_ms)
           VALUES (?,'org-a','event-a',?,?,'active',1000,1000)""",
        (("track-exclusive", "Keynote", 1), ("track-open", "General", 0)),
    )
    db.execute(
        """INSERT INTO schedule_revisions
           (id,organization_id,event_id,revision_number,name,status,created_by_user_id,
            created_at_ms,updated_at_ms) VALUES
           ('draft-a','org-a','event-a',1,'Draft','draft','user-a',1000,1000)"""
    )
    return db


def insert_item(
    db: sqlite3.Connection,
    item_id: str,
    *,
    room: str = "room-a",
    track: str | None = "track-open",
    start: int = 1_100,
    end: int = 1_200,
    revision: str = "draft-a",
    accepted_session: str = "session-a",
) -> None:
    db.execute(
        """INSERT INTO agenda_items
           (id,organization_id,event_id,revision_id,accepted_session_id,room_id,track_id,
            event_date,event_time_zone,starts_at_ms,ends_at_ms,created_at_ms,updated_at_ms)
           VALUES (?,'org-a','event-a',?,?,?,?,'1970-01-01','UTC',?,?,1000,1000)""",
        (item_id, revision, accepted_session, room, track, start, end),
    )


def test_only_accepted_decisions_create_schedulable_sessions(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """UPDATE submission_decisions SET decision='rejected' WHERE id='decision-a'"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="accepted decision"):
        connection.execute(
            """INSERT INTO accepted_sessions
               (id,organization_id,event_id,submission_id,decision_id,created_at_ms)
               VALUES ('invalid','org-a','event-a','submission-a','decision-a',1000)"""
        )


def test_bounds_room_and_exclusive_track_conflicts_are_transactional(
    connection: sqlite3.Connection,
) -> None:
    insert_item(connection, "item-a", track="track-exclusive")
    with pytest.raises(sqlite3.IntegrityError, match="room or exclusive track"):
        insert_item(connection, "same-room", room="room-a", start=1_150, end=1_250)
    with pytest.raises(sqlite3.IntegrityError, match="room or exclusive track"):
        insert_item(
            connection,
            "same-exclusive",
            room="room-b",
            track="track-exclusive",
            start=1_150,
            end=1_250,
            accepted_session="session-a2",
        )
    with pytest.raises(sqlite3.IntegrityError, match="outside draft event bounds"):
        insert_item(connection, "outside", room="room-b", start=900, end=1_050)


def test_nonexclusive_tracks_overlap_and_drafts_are_isolated(
    connection: sqlite3.Connection,
) -> None:
    insert_item(connection, "draft-item", room="room-a", track="track-open")
    connection.execute(
        """UPDATE schedule_revisions SET status='published',published_at_ms=1300
           WHERE id='draft-a'"""
    )
    connection.execute(
        """INSERT INTO schedule_revisions
           (id,organization_id,event_id,revision_number,name,status,created_by_user_id,
            created_at_ms,updated_at_ms) VALUES
           ('draft-b','org-a','event-a',2,'Next','draft','user-a',1400,1400)"""
    )
    insert_item(connection, "next-item", room="room-a", track="track-open", revision="draft-b")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("DELETE FROM agenda_items WHERE id='draft-item'")
    assert (
        connection.execute(
            "SELECT starts_at_ms FROM agenda_items WHERE id='draft-item'"
        ).fetchone()[0]
        == 1_100
    )


def test_speaker_overlap_is_rejected(connection: sqlite3.Connection) -> None:
    insert_item(connection, "item-a", room="room-a")
    connection.execute(
        """INSERT INTO agenda_item_speakers
           (id,organization_id,event_id,revision_id,agenda_item_id,event_speaker_id,created_at_ms)
           VALUES ('speaker-link-a','org-a','event-a','draft-a','item-a','speaker-a',1000)"""
    )
    insert_item(
        connection,
        "item-b",
        room="room-b",
        start=1_150,
        end=1_250,
        accepted_session="session-a2",
    )
    with pytest.raises(sqlite3.IntegrityError, match="speaker conflict"):
        connection.execute(
            """INSERT INTO agenda_item_speakers
               (id,organization_id,event_id,revision_id,agenda_item_id,event_speaker_id,created_at_ms)
               VALUES ('speaker-link-b','org-a','event-a','draft-a','item-b','speaker-a',1000)"""
        )


async def test_preview_and_optimistic_move_hooks(connection: sqlite3.Connection) -> None:
    insert_item(connection, "item-a", room="room-a", start=1_100, end=1_200)
    repository = AgendaRepository(AsyncSqlite(connection))
    conflicts = await repository.preview_conflicts(
        AgendaSlot(
            organization_id="org-a",
            event_id="event-a",
            revision_id="draft-a",
            room_id="room-a",
            track_id=None,
            event_date="1970-01-01",
            event_time_zone="UTC",
            starts_at_ms=1_150,
            ends_at_ms=1_250,
        )
    )
    assert [(conflict.kind, conflict.item_id) for conflict in conflicts] == [("room", "item-a")]
    version = await repository.move_item(
        AgendaSlot(
            organization_id="org-a",
            event_id="event-a",
            revision_id="draft-a",
            item_id="item-a",
            room_id="room-b",
            track_id=None,
            event_date="1970-01-01",
            event_time_zone="UTC",
            starts_at_ms=1_200,
            ends_at_ms=1_300,
        ),
        expected_version=1,
        now_ms=1_300,
    )
    assert version == 2
    with pytest.raises(PersistenceError, match="version conflict"):
        await repository.move_item(
            AgendaSlot(
                organization_id="org-a",
                event_id="event-a",
                revision_id="draft-a",
                item_id="item-a",
                room_id="room-a",
                track_id=None,
                event_date="1970-01-01",
                event_time_zone="UTC",
                starts_at_ms=1_300,
                ends_at_ms=1_400,
            ),
            expected_version=1,
            now_ms=1_400,
        )


async def test_publish_hook_is_version_guarded_and_freezes_revision(
    connection: sqlite3.Connection,
) -> None:
    insert_item(connection, "item-a")
    repository = AgendaRepository(AsyncSqlite(connection))
    assert await repository.publish_revision(
        organization_id="org-a",
        event_id="event-a",
        revision_id="draft-a",
        expected_version=1,
        now_ms=1_300,
    )
    assert not await repository.publish_revision(
        organization_id="org-a",
        event_id="event-a",
        revision_id="draft-a",
        expected_version=1,
        now_ms=1_400,
    )
    with pytest.raises(PersistenceError, match="agenda save rejected"):
        await repository.move_item(
            AgendaSlot(
                organization_id="org-a",
                event_id="event-a",
                revision_id="draft-a",
                item_id="item-a",
                room_id="room-b",
                track_id=None,
                event_date="1970-01-01",
                event_time_zone="UTC",
                starts_at_ms=1_200,
                ends_at_ms=1_300,
            ),
            expected_version=1,
            now_ms=1_400,
        )

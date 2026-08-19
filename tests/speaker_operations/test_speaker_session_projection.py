import sqlite3

import pytest

from sessionbuddy.speaker_operations.router import SPEAKER_SESSION_PROJECTION_SQL
from tests.schema import MIGRATIONS
from tests.speaker_operations.test_speaker_onboarding_schema import add_speaker, seed_platform


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    seed_platform(connection)
    add_speaker(connection, "a")
    add_speaker(connection, "b")
    return connection


def _project(db: sqlite3.Connection, speaker_id: str = "speaker-a") -> list[sqlite3.Row]:
    return db.execute(
        SPEAKER_SESSION_PROJECTION_SQL,
        ("org-a", "event-a", speaker_id),
    ).fetchall()


def _organizer_session(
    db: sqlite3.Connection,
    *,
    session_id: str,
    speaker_id: str | None = "speaker-a",
    pending_invitation_id: str | None = None,
    lifecycle_status: str = "active",
) -> None:
    db.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,source_type,organizer_title,
            organizer_abstract,created_at_ms,lifecycle_status,withdrawn_at_ms)
           VALUES (?, 'org-a','event-a','organizer_created',?,
                   'Organizer working abstract',1000,?,?)""",
        (
            session_id,
            f"Title {session_id}",
            lifecycle_status,
            1000 if lifecycle_status == "withdrawn" else None,
        ),
    )
    db.execute(
        """INSERT INTO accepted_session_participants
           (id,organization_id,event_id,accepted_session_id,event_speaker_id,
            pending_invitation_id,display_name_snapshot,created_at_ms,updated_at_ms)
           VALUES (?, 'org-a','event-a',?,?,?,?,1000,1000)""",
        (
            f"participant-{session_id}",
            session_id,
            speaker_id,
            pending_invitation_id,
            "Speaker",
        ),
    )


def test_projection_hides_working_content_and_unpublished_schedule(db) -> None:
    _organizer_session(db, session_id="direct-a")
    db.execute(
        """INSERT INTO schedule_revisions
           (id,organization_id,event_id,revision_number,name,status,created_by_user_id,
            created_at_ms,updated_at_ms)
           VALUES('draft-revision','org-a','event-a',1,'Draft','draft','user-a',1000,1000)"""
    )
    db.execute(
        """INSERT INTO event_rooms
           (id,organization_id,event_id,name,status,created_at_ms,updated_at_ms)
           VALUES('room-a','org-a','event-a','Room A','active',1000,1000)"""
    )
    db.execute(
        """INSERT INTO agenda_items
           (id,organization_id,event_id,revision_id,accepted_session_id,room_id,
            event_date,event_time_zone,starts_at_ms,ends_at_ms,created_at_ms,updated_at_ms)
           VALUES('item-a','org-a','event-a','draft-revision','direct-a','room-a',
                  '1970-01-01','UTC',1200,1300,1000,1000)"""
    )

    row = _project(db)[0]

    assert row["title"] == "Title direct-a"
    assert row["abstract"] is None
    assert row["content_status"] == "draft"
    assert row["starts_at_ms"] is None
    assert row["participant_role"] == "speaker"


def test_projection_exposes_only_approved_content_and_published_schedule(db) -> None:
    _organizer_session(db, session_id="direct-a")
    db.execute(
        """INSERT INTO session_content_versions
           (id,organization_id,event_id,accepted_session_id,version,title,abstract,
            content_status,changed_by_user_id,created_at_ms)
               VALUES('content-approved','org-a','event-a','direct-a',1,'Approved title',
                      'Approved abstract','approved','user-a',1000)"""
    )
    db.execute(
        """UPDATE accepted_sessions
           SET content_status='approved'
           WHERE id='direct-a'"""
    )
    db.execute(
        """INSERT INTO schedule_revisions
           (id,organization_id,event_id,revision_number,name,status,created_by_user_id,
            created_at_ms,updated_at_ms)
           VALUES('published-revision','org-a','event-a',1,'Published','draft',
                  'user-a',1000,1000)"""
    )
    db.execute(
        """INSERT INTO event_rooms
           (id,organization_id,event_id,name,status,created_at_ms,updated_at_ms)
           VALUES('room-a','org-a','event-a','Room A','active',1000,1000)"""
    )
    db.execute(
        """INSERT INTO event_tracks
           (id,organization_id,event_id,name,status,created_at_ms,updated_at_ms)
           VALUES('track-a','org-a','event-a','Track A','active',1000,1000)"""
    )
    db.execute(
        """INSERT INTO agenda_items
           (id,organization_id,event_id,revision_id,accepted_session_id,room_id,track_id,
            event_date,event_time_zone,starts_at_ms,ends_at_ms,created_at_ms,updated_at_ms)
           VALUES('item-a','org-a','event-a','published-revision','direct-a','room-a','track-a',
                  '1970-01-01','UTC',1200,1300,1000,1000)"""
    )
    db.execute(
        """UPDATE schedule_revisions
           SET status='published',published_at_ms=1000,updated_at_ms=1000
           WHERE id='published-revision'"""
    )

    row = _project(db)[0]

    assert (row["title"], row["abstract"], row["content_status"]) == (
        "Approved title",
        "Approved abstract",
        "approved",
    )
    assert (row["starts_at_ms"], row["ends_at_ms"], row["room_name"], row["track_name"]) == (
        1200,
        1300,
        "Room A",
        "Track A",
    )

    db.execute(
        """INSERT INTO session_content_versions
           (id,organization_id,event_id,accepted_session_id,version,title,abstract,
            content_status,changed_by_user_id,created_at_ms)
           VALUES('content-working','org-a','event-a','direct-a',2,'Working title',
                  'Working abstract','draft','user-a',2000)"""
    )
    db.execute(
        """UPDATE accepted_sessions
           SET content_status='draft',version=2
           WHERE id='direct-a'"""
    )

    working_row = _project(db)[0]
    assert (working_row["title"], working_row["abstract"], working_row["content_status"]) == (
        "Approved title",
        "Approved abstract",
        "draft",
    )


@pytest.mark.parametrize("explicit_participant", [False, True])
def test_projection_preserves_proposal_participant_role_and_scope(
    db, explicit_participant
) -> None:
    db.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES('decision-a','org-a','event-a','seed-round-a','submission-a','accepted','',
                  'user-a',1000,1000)"""
    )
    db.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,source_type,submission_id,decision_id,created_at_ms)
           VALUES('proposal-session','org-a','event-a','accepted_proposal',
                  'submission-a','decision-a',1000)"""
    )
    db.execute(
        """INSERT INTO submission_speakers
           (id,organization_id,event_id,submission_id,event_speaker_id,role,
            snapshot_name,created_at_ms)
           VALUES('submission-link','org-a','event-a','submission-a','speaker-a',
                  'co_speaker','Speaker A',1000)"""
    )
    if explicit_participant:
        db.execute(
            """INSERT INTO accepted_session_participants
               (id,organization_id,event_id,accepted_session_id,event_speaker_id,
                display_name_snapshot,created_at_ms,updated_at_ms)
               VALUES('proposal-participant','org-a','event-a','proposal-session','speaker-a',
                      'Speaker A',1000,1000)"""
        )
    _organizer_session(db, session_id="withdrawn", lifecycle_status="withdrawn")
    db.execute(
        """INSERT INTO people
           (id,organization_id,user_id,display_name,created_at_ms,updated_at_ms)
           VALUES('person-a2','org-a',NULL,'Other speaker',1000,1000)"""
    )
    db.execute(
        """INSERT INTO event_speakers
           (id,organization_id,event_id,person_id,status,accepted_at_ms,last_activity_at_ms,
            created_at_ms,updated_at_ms)
           VALUES('speaker-a2','org-a','event-a','person-a2','onboarding',1000,1000,1000,1000)"""
    )
    _organizer_session(db, session_id="other-speaker", speaker_id="speaker-a2")
    db.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,source_type,organizer_title,
            organizer_abstract,created_at_ms)
           VALUES('other-tenant-session','org-b','event-b','organizer_created',
                  'Other tenant title','Other tenant abstract',1000)"""
    )
    db.execute(
        """INSERT INTO accepted_session_participants
           (id,organization_id,event_id,accepted_session_id,event_speaker_id,
            display_name_snapshot,created_at_ms,updated_at_ms)
           VALUES('other-tenant-participant','org-b','event-b','other-tenant-session',
                  'speaker-b','Speaker B',1000,1000)"""
    )

    rows = _project(db)

    assert [row["id"] for row in rows] == ["proposal-session"]
    assert rows[0]["submission_id"] == "submission-a"
    assert rows[0]["participant_role"] == "co_speaker"

    db.execute("DELETE FROM submission_speakers WHERE id='submission-link'")
    if explicit_participant:
        assert _project(db)[0]["participant_role"] == "speaker"
    else:
        assert _project(db) == []


def test_projection_uses_the_participant_first_index(db) -> None:
    plan = " ".join(
        str(row[3])
        for row in db.execute(
            "EXPLAIN QUERY PLAN " + SPEAKER_SESSION_PROJECTION_SQL,
            ("org-a", "event-a", "speaker-a"),
        )
    )

    assert "idx_accepted_session_participants_speaker" in plan

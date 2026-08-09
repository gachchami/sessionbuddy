import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from sessionbuddy.platform.auth.access import BootstrapCreate, EventCreate

MIGRATIONS = sorted((Path(__file__).parents[2] / "migrations").glob("*.sql"))


@pytest.fixture
def database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    connection.execute(
        """INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms)
           VALUES('org-a','Organization A','active',1,1)"""
    )
    yield connection
    connection.close()


def insert_event(database: sqlite3.Connection, description: str) -> None:
    database.execute(
        """INSERT INTO events
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
            delivery_mode,description,status,created_at_ms,updated_at_ms)
           VALUES('event-a','org-a','Event A',10,20,'UTC','Online','virtual',
                  ?,'active',1,1)""",
        (description,),
    )


def insert_message(database: sqlite3.Connection, subject: str, html_body: str) -> None:
    database.execute(
        """INSERT INTO communication_messages
           (id,organization_id,event_id,recipient_email,subject,html_body,
            deterministic_key,status,queued_at_ms,updated_at_ms)
           VALUES('message-a','org-a',NULL,'speaker@example.test',?,?,
                  'message-a','queued',1,1)""",
        (subject, html_body),
    )


def event_payload(description: str) -> dict[str, object]:
    return {
        "name": "Event A",
        "starts_at_ms": 10,
        "ends_at_ms": 20,
        "time_zone": "UTC",
        "location": "Online",
        "delivery_mode": "virtual",
        "description": description,
    }


def test_event_description_cap_matches_request_models_and_database(
    database: sqlite3.Connection,
) -> None:
    EventCreate(**event_payload("x" * 2000))
    BootstrapCreate(
        organization_name="Organization A",
        admin_name="Admin",
        admin_email="admin@example.test",
        event_name="Event A",
        starts_at_ms=10,
        ends_at_ms=20,
        time_zone="UTC",
        event_location="Online",
        event_delivery_mode="virtual",
        event_description="x" * 2000,
    )
    insert_event(database, "x" * 2000)

    with pytest.raises(ValidationError):
        EventCreate(**event_payload("x" * 2001))
    with pytest.raises(ValidationError):
        BootstrapCreate(
            organization_name="Organization A",
            admin_name="Admin",
            admin_email="admin@example.test",
            event_name="Event A",
            starts_at_ms=10,
            ends_at_ms=20,
            time_zone="UTC",
            event_location="Online",
            event_delivery_mode="virtual",
            event_description="x" * 2001,
        )
    with pytest.raises(sqlite3.IntegrityError, match="event location and description"):
        database.execute("UPDATE events SET description=? WHERE id='event-a'", ("x" * 2001,))


@pytest.mark.parametrize(
    ("subject", "html_body"),
    (("x" * 501, "<p>Message</p>"), ("Subject", "x" * 100001)),
    ids=("subject-too-long", "rendered-body-too-long"),
)
def test_database_rejects_oversized_rendered_communications(
    database: sqlite3.Connection,
    subject: str,
    html_body: str,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="communication subject or body"):
        insert_message(database, subject, html_body)


def test_database_accepts_rendered_communication_boundaries(
    database: sqlite3.Connection,
) -> None:
    insert_message(database, "x" * 500, "x" * 100000)
    assert database.execute(
        "SELECT length(subject),length(html_body) FROM communication_messages"
    ).fetchone() == (500, 100000)
    with pytest.raises(sqlite3.IntegrityError, match="communication subject or body"):
        database.execute(
            "UPDATE communication_messages SET html_body=? WHERE id='message-a'",
            ("x" * 100001,),
        )


def insert_textarea_form(database: sqlite3.Connection) -> None:
    database.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
            published_at_ms,created_at_ms,updated_at_ms)
           VALUES('form-a','org-a','event-a',1,'event-a','Welcome',?,'published',1,1,1)""",
        (json.dumps({"fields": [{"key": "notes", "type": "textarea"}]}),),
    )


def test_database_rejects_oversized_schema_driven_submission_text(
    database: sqlite3.Connection,
) -> None:
    insert_event(database, "Description")
    insert_textarea_form(database)
    values = (
        "submission-a",
        "org-a",
        "event-a",
        "form-a",
        "public-a",
        "Proposal",
        "Abstract",
        "Speaker",
        "speaker@example.test",
        json.dumps({"notes": "x" * 5001}),
    )
    with pytest.raises(sqlite3.IntegrityError, match="textarea answer exceeds"):
        database.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,speaker_email,answers_json,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?,'submitted',1,1,1)""",
            values,
        )


def test_database_rejects_oversized_schema_driven_task_response(
    database: sqlite3.Connection,
) -> None:
    insert_event(database, "Description")
    database.execute(
        """INSERT INTO people
           (id,organization_id,display_name,links_json,created_at_ms,updated_at_ms)
           VALUES('person-a','org-a','Speaker','[]',1,1)"""
    )
    database.execute(
        """INSERT INTO event_speakers
           (id,organization_id,event_id,person_id,status,accepted_at_ms,
            last_activity_at_ms,created_at_ms,updated_at_ms)
           VALUES('speaker-a','org-a','event-a','person-a','onboarding',1,1,1,1)"""
    )
    schema = json.dumps({"fields": [{"key": "notes", "type": "textarea"}]})
    database.execute(
        """INSERT INTO speaker_tasks
           (id,organization_id,event_id,event_speaker_id,task_type,title,
            destination_type,state,version,created_at_ms,updated_at_ms,form_schema_json)
           VALUES('task-a','org-a','event-a','speaker-a','custom','Task',
                  'custom','open',1,1,1,?)""",
        (schema,),
    )
    database.execute(
        "UPDATE speaker_tasks SET response_json=? WHERE id='task-a'",
        (json.dumps({"notes": "x" * 4000}),),
    )
    with pytest.raises(sqlite3.IntegrityError, match="task textarea answer exceeds"):
        database.execute(
            "UPDATE speaker_tasks SET response_json=? WHERE id='task-a'",
            (json.dumps({"notes": "x" * 4001}),),
        )


def test_database_rejects_oversized_evaluation_guidance(
    database: sqlite3.Connection,
) -> None:
    insert_event(database, "Description")
    database.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms)
           VALUES('round-a','org-a','event-a','Review',?,'draft',1,1)""",
        (json.dumps({"guidance": "x" * 1000}),),
    )
    with pytest.raises(sqlite3.IntegrityError, match="evaluation guidance exceeds"):
        database.execute(
            "UPDATE evaluation_rounds SET rubric_json=? WHERE id='round-a'",
            (json.dumps({"guidance": "x" * 1001}),),
        )

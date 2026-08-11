import sqlite3

import pytest

from tests.schema import MIGRATIONS


@pytest.fixture
def db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    for suffix in ("a", "b"):
        connection.execute(
            "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
            "VALUES(?,?, 'active',1,1)",
            (f"org-{suffix}", f"Organization {suffix.upper()}"),
        )
        connection.execute(
            """INSERT INTO users
               (id,email,normalized_email,status,created_at_ms,updated_at_ms)
               VALUES(?,?,?,'active',1,1)""",
            (f"user-{suffix}", f"user-{suffix}@example.test", f"user-{suffix}@example.test"),
        )
        connection.execute(
            """INSERT INTO organization_memberships
               (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?,?,?,'member','active',1,1)""",
            (f"org-member-{suffix}", f"org-{suffix}", f"user-{suffix}"),
        )
        connection.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms)
               VALUES(?,?,?,10,20,'UTC','Online','hybrid','Test event','active',1,1)""",
            (f"event-{suffix}", f"org-{suffix}", f"Event {suffix.upper()}"),
        )
        connection.execute(
            """INSERT INTO event_memberships
               (id,organization_id,event_id,user_id,role,status,created_at_ms,updated_at_ms)
               VALUES(?,?,?,?, 'evaluator','active',1,1)""",
            (f"event-member-{suffix}", f"org-{suffix}", f"event-{suffix}", f"user-{suffix}"),
        )
        connection.execute(
            """INSERT INTO call_for_speaker_forms
               (id,organization_id,event_id,version,slug,welcome_text,schema_json,
                status,published_at_ms,created_at_ms,updated_at_ms)
               VALUES(?,?,?,1,?,?,'{}','published',1,1,1)""",
            (
                f"form-{suffix}",
                f"org-{suffix}",
                f"event-{suffix}",
                f"form-{suffix}",
                "Welcome",
            ),
        )
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,
                proposal_title,proposal_abstract,speaker_name,speaker_email,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES(?,?,?,?,?,?,'Abstract','Speaker',?,'submitted',1,1,1)""",
            (
                f"submission-{suffix}",
                f"org-{suffix}",
                f"event-{suffix}",
                f"form-{suffix}",
                f"public-{suffix}",
                f"Proposal {suffix}",
                f"speaker-{suffix}@example.test",
            ),
        )
        connection.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,rubric_json,status,
                created_at_ms,updated_at_ms)
               VALUES(?,?,?,?,'{}','open',1,1)""",
            (
                f"round-{suffix}",
                f"org-{suffix}",
                f"event-{suffix}",
                f"Round {suffix}",
            ),
        )
    yield connection
    connection.close()


def test_cross_tenant_form_cannot_be_attached_to_submission_or_draft(
    db: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="submission form scope mismatch"):
        db.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,
                proposal_title,proposal_abstract,speaker_name,speaker_email,status,
                submitted_at_ms,created_at_ms,updated_at_ms)
               VALUES('bad-submission','org-a','event-a','form-b','public-bad',
                      'Bad','Bad','Bad','bad@example.test','submitted',1,1,1)"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="submission draft scope mismatch"):
        db.execute(
            """INSERT INTO submission_drafts
               (id,organization_id,event_id,form_id,user_id,answers_json,
                created_at_ms,updated_at_ms)
               VALUES('bad-draft','org-a','event-a','form-b','user-a','{}',1,1)"""
        )


def test_evaluation_graph_cannot_cross_tenant_or_event_boundaries(
    db: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="evaluation assignment scope mismatch"):
        db.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,status,
                created_at_ms,updated_at_ms)
               VALUES('bad-assignment','org-a','event-a','round-a','submission-b','user-a',
                      'assigned',1,1)"""
        )
    db.execute(
        """INSERT INTO evaluation_assignments
           (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,status,
            created_at_ms,updated_at_ms)
           VALUES('assignment-a','org-a','event-a','round-a','submission-a','user-a',
                  'assigned',1,1)"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="evaluation scope mismatch"):
        db.execute(
            """INSERT INTO evaluations
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,rating,
                recommendation,internal_comment,state,created_at_ms,updated_at_ms)
               VALUES('bad-evaluation','org-b','event-b','round-a','assignment-a','user-a',3,
                      'accept','','draft',1,1)"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="evaluation conflict scope mismatch"):
        db.execute(
            """INSERT INTO evaluation_conflicts
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,
                conflict_type,explanation,declared_at_ms)
               VALUES('bad-conflict','org-b','event-b','round-a','assignment-a','user-a',
                      'other','Mismatch',1)"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="submission decision scope mismatch"):
        db.execute(
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
                decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES('bad-decision','org-a','event-a','round-a','submission-b','accepted','',
                      'user-a',1,1)"""
        )


def test_event_time_range_is_strict_at_database_boundary(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="event end must be after start"):
        db.execute(
            """INSERT INTO events
               (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
                delivery_mode,description,status,created_at_ms,updated_at_ms)
               VALUES('equal-time','org-a','Invalid',10,10,'UTC','Online','hybrid',
                      'Test event','active',1,1)"""
        )


def test_cfp_and_authentication_time_ranges_are_strict(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CFP closing time"):
        db.execute(
            "UPDATE call_for_speaker_forms SET opens_at_ms=20,closes_at_ms=20 "
            "WHERE id='form-a'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="authentication challenge time range"):
        db.execute(
            """INSERT INTO authentication_challenges
               (id,normalized_email,token_hash,purpose,provisioning_context,redirect_path,
                expires_at_ms,created_at_ms)
               VALUES('challenge','speaker@example.test',zeroblob(32),'sign_in',
                      'existing_user','/',1,1)"""
        )


def test_user_bearing_rows_cannot_reference_another_organization(
    db: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="submission owner scope mismatch"):
        db.execute(
            "UPDATE submissions SET submitter_user_id='user-b' WHERE id='submission-a'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="invitation actor scope mismatch"):
        db.execute(
            """INSERT INTO identity_invitations
               (id,organization_id,event_id,normalized_email,email,role,status,
                invited_by_user_id,expires_at_ms,created_at_ms,updated_at_ms)
               VALUES('invite','org-a','event-a','invitee@example.test','invitee@example.test',
                      'speaker','pending','user-b',20,1,1)"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="resource creator scope mismatch"):
        db.execute(
            """INSERT INTO event_resources
               (id,organization_id,event_id,title,slug,status,created_by_user_id,
                created_at_ms,updated_at_ms,published_at_ms)
               VALUES('resource','org-a','event-a','Guide','guide','published','user-b',1,1,1)"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="communication recipient scope mismatch"):
        db.execute(
            """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_user_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES('message','org-a','event-a','user-b','user-b@example.test','Hello','Body',
                      'cross-tenant','queued',1,1)"""
        )


def test_submission_decision_is_final_across_rounds(db: sqlite3.Connection) -> None:
    db.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES('decision-a','org-a','event-a','round-a','submission-a','accepted','',
                  'user-a',1,1)"""
    )
    db.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,name,rubric_json,status,
            created_at_ms,updated_at_ms,closed_at_ms)
           VALUES('round-a-2','org-a','event-a','Second','{}','closed',2,2,2)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO submission_decisions
               (id,organization_id,event_id,round_id,submission_id,decision,internal_reason,
                decided_by_user_id,decided_at_ms,updated_at_ms)
               VALUES('decision-a-2','org-a','event-a','round-a-2','submission-a','rejected','',
                      'user-a',2,2)"""
        )


def test_cfp_workspace_queries_use_covering_scope_indexes(db: sqlite3.Connection) -> None:
    form_plan = " ".join(
        str(value)
        for row in db.execute(
            """EXPLAIN QUERY PLAN SELECT id FROM call_for_speaker_forms
               WHERE organization_id=? AND event_id=? AND status='published'
               ORDER BY version DESC,published_at_ms DESC,id DESC LIMIT 1""",
            ("org-a", "event-a"),
        )
        for value in row
    )
    assert "idx_cfp_forms_event_published" in form_plan
    assert "USE TEMP B-TREE" not in form_plan

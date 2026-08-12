"""Round membership schema: what SQLite guarantees, and what it deliberately does not.

Explicit per-submission assignment needs round membership to exist independently of the
assignment relation -- otherwise a proposal with no reviewer yet, or a reviewer with no
proposals, cannot be represented at all, and the last removal silently drops them from the
round. These tests pin the resulting contract:

  Database invariant   every assignment references existing round-submission and
                       round-evaluator membership rows.
  Application invariant assignments may only be inserted or revived while BOTH memberships
                       are active. SQLite cannot express this; the round diff must.
  Historical invariant  removed memberships and revoked assignments stay readable for audit.
  ON DELETE RESTRICT    prevents destroying membership history that assignments reference.

The boundary tests exist to stop someone "simplifying" the diff on the assumption that the
schema already refuses these writes. It does not.
"""

import sqlite3
from pathlib import Path

import pytest

BASELINE = Path(__file__).resolve().parents[2] / "migrations_baseline" / "0001_baseline.sql"


def db():
    c = sqlite3.connect(":memory:")
    c.executescript(BASELINE.read_text())
    c.execute("PRAGMA foreign_keys=ON")
    x = c.execute
    x("INSERT INTO organizations VALUES('org','O','active',1,1,1,NULL)")
    x("""INSERT INTO users(id,email,normalized_email,status,version,authorization_version,
         created_at_ms,updated_at_ms,display_name)
         VALUES('adm','a@e.com','a@e.com','active',1,1,1,1,'Adm')""")
    x("""INSERT INTO users(id,email,normalized_email,status,version,authorization_version,
         created_at_ms,updated_at_ms,display_name)
         VALUES('sam','s@e.com','s@e.com','active',1,1,1,1,'Sam')""")
    x("""INSERT INTO organization_memberships(id,organization_id,user_id,role,status,version,
         created_at_ms,updated_at_ms) VALUES('om','org','sam','member','active',1,1,1)""")
    x("""INSERT INTO organization_memberships(id,organization_id,user_id,role,status,version,
         created_at_ms,updated_at_ms)
         VALUES('oma','org','adm','organization_admin','active',1,1,1)""")
    x("""INSERT INTO events(id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
         delivery_mode,description,status,version,created_at_ms,updated_at_ms)
         VALUES('ev','org','E',1,2,'UTC','P','hybrid','d','active',1,1,1)""")
    x("""INSERT INTO call_for_speaker_forms(id,organization_id,event_id,version,slug,welcome_text,
         schema_json,status,published_at_ms,created_at_ms,updated_at_ms,success_title,success_message,
         redirect_to_portal,confirmation_subject,confirmation_body)
         VALUES('form','org','ev',1,'devflow','w','{}','published',1,1,1,'t','m',0,'s','b')""")
    for sid in ("A", "B", "C"):
        x(
            """INSERT INTO submissions(
             id,organization_id,event_id,form_id,public_session_id,proposal_title,
             proposal_abstract,speaker_name,status,submitted_at_ms,created_at_ms,updated_at_ms,
             speaker_email,answers_json,version)
             VALUES(?,'org','ev','form',?,?,'a','S','submitted',1,1,1,'s@e.com','{}',1)""",
            (sid, "p" + sid, sid),
        )
    # the pre-existing assignment trigger requires an active reviewer role AND an accepted
    # evaluator invitation for the assignee -- without these it aborts before any FK is tested
    x("INSERT INTO user_roles VALUES('sam','reviewer','active',1,1,NULL,0)")
    x("""INSERT INTO identity_invitations(id,organization_id,event_id,normalized_email,email,role,
         status,invited_by_user_id,expires_at_ms,accepted_at_ms,created_at_ms,updated_at_ms,
         display_name,job_title,company,biography)
         VALUES('inv','org','ev','s@e.com','s@e.com','evaluator','accepted','adm',9,1,1,1,'Sam','','','')""")
    x("""INSERT INTO evaluation_rounds(id,organization_id,event_id,name,rubric_json,status,
         created_at_ms,updated_at_ms) VALUES('rnd','org','ev','R1','{}','draft',1,1)""")
    c.commit()
    return c


def add_membership(c, kind, key, status="active"):
    if kind == "sub":
        c.execute(
            """INSERT INTO evaluation_round_submissions
          (round_id,submission_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
          VALUES('rnd',?,'org','ev',?,1,1)""",
            (key, status),
        )
    else:
        c.execute(
            """INSERT INTO evaluation_round_evaluators
          (round_id,evaluator_user_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
          VALUES('rnd',?,'org','ev',?,1,1)""",
            (key, status),
        )


def add_assignment(c, sub, ev, status="assigned", aid=None):
    c.execute(
        """INSERT INTO evaluation_assignments
      (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,status,created_at_ms,updated_at_ms)
      VALUES(?,'org','ev','rnd',?,?,?,1,1)""",
        (aid or f"as-{sub}-{ev}", sub, ev, status),
    )


@pytest.fixture()
def connection():
    return db()


def test_baseline_applies_to_an_empty_database():
    fresh = sqlite3.connect(":memory:")
    fresh.executescript(BASELINE.read_text())
    assert fresh.execute("PRAGMA foreign_key_check").fetchall() == []


def test_baseline_is_a_fresh_install_script_not_a_reentrant_one():
    """Re-executing the raw SQL must fail loudly.

    This is NOT the repeat-migration proof -- see the skipped test below. What it pins is
    that the baseline is honest about being a fresh-install script: no CREATE TABLE IF NOT
    EXISTS quietly making a second execution look successful while skipping the object it
    was asked to create. Idempotency at this layer would hide a partially-applied schema.
    """
    fresh = sqlite3.connect(":memory:")
    fresh.executescript(BASELINE.read_text())
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        fresh.executescript(BASELINE.read_text())


@pytest.mark.skip(
    reason=(
        "Requires the D1 migration runner, which tracks applied migrations in its own "
        "d1_migrations table (the baseline is validated NOT to contain it). Raw sqlite3 "
        "cannot observe that bookkeeping, so this proof is a command-level check per "
        "docs/activity-pipeline.md 10.4:\n"
        "    docker compose run --rm --no-deps worker npm run worker:migrate\n"
        "    docker compose run --rm --no-deps worker npm run worker:migrate\n"
        "The first invocation must apply the ordered chain; the second must report no "
        "migrations to apply."
    )
)
def test_repeat_migration_reports_nothing_pending():
    """Placeholder that keeps the required proof visible in the suite rather than only in
    a runbook. Deliberately has no body: shelling out to wrangler from the test process
    would be unverifiable here and would fail for environment reasons rather than schema
    ones, which is worse than an explicit skip."""


def test_membership_tables_are_composite_foreign_key_parents(connection):
    submissions_pk = [
        r[1] for r in connection.execute("PRAGMA table_info(evaluation_round_submissions)") if r[5]
    ]
    evaluators_pk = [
        r[1] for r in connection.execute("PRAGMA table_info(evaluation_round_evaluators)") if r[5]
    ]
    assert submissions_pk == ["round_id", "submission_id"]
    assert evaluators_pk == ["round_id", "evaluator_user_id"]
    parents = {r[2] for r in connection.execute("PRAGMA foreign_key_list(evaluation_assignments)")}
    assert parents == {
        "evaluation_rounds",
        "evaluation_round_submissions",
        "evaluation_round_evaluators",
    }


def test_assignment_requires_both_memberships_to_exist(connection):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        add_assignment(connection, "A", "sam")
    add_membership(connection, "sub", "A")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        add_assignment(connection, "A", "sam")
    add_membership(connection, "ev", "sam")
    add_assignment(connection, "A", "sam")
    assert connection.execute("SELECT COUNT(*) FROM evaluation_assignments").fetchone()[0] == 1


def test_membership_history_survives_removal(connection):
    add_membership(connection, "sub", "A")
    add_membership(connection, "ev", "sam")
    add_assignment(connection, "A", "sam", status="revoked")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM evaluation_round_evaluators WHERE evaluator_user_id='sam'")
    connection.execute(
        "UPDATE evaluation_round_evaluators SET status='removed' WHERE evaluator_user_id='sam'"
    )
    assert (
        connection.execute(
            "SELECT status FROM evaluation_assignments WHERE id='as-A-sam'"
        ).fetchone()[0]
        == "revoked"
    )


def test_database_does_not_enforce_ACTIVE_membership(connection):
    """The boundary. Both writes below are wrong, and SQLite accepts both -- which is
    precisely why the application invariant cannot be dropped."""
    add_membership(connection, "sub", "A")
    add_membership(connection, "ev", "sam", status="removed")
    add_assignment(connection, "A", "sam")
    assert connection.execute("SELECT COUNT(*) FROM evaluation_assignments").fetchone()[0] == 1

    connection.execute("UPDATE evaluation_assignments SET status='revoked' WHERE id='as-A-sam'")
    connection.execute("UPDATE evaluation_assignments SET status='assigned' WHERE id='as-A-sam'")
    assert (
        connection.execute(
            "SELECT status FROM evaluation_assignments WHERE id='as-A-sam'"
        ).fetchone()[0]
        == "assigned"
    )

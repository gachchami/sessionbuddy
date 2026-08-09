import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from sessionbuddy.cfp.router import (
    _co_speaker_expiry,
    accept_co_speaker_invitation,
    decline_co_speaker_invitation,
    get_co_speaker_invitation,
)
from sessionbuddy.platform.auth import hash_token
from sessionbuddy.platform.db.types import utc_now_ms
from tests.speaker_operations.test_asset_boundary import AsyncSqlite

MIGRATIONS = sorted((Path(__file__).parents[2] / "migrations").glob("*.sql"))


def request_for(database: AsyncSqlite) -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/co-speaker-invitations/token/accept",
            "headers": [],
            "env": SimpleNamespace(
                DB=database,
                APP_ENV="local",
                PUBLIC_BASE_URL="http://127.0.0.1:8787",
            ),
        }
    )
    request.state.request_id = "co-speaker-request"
    request.state.timings = {}
    return request


@pytest.fixture
def invitation_database() -> tuple[sqlite3.Connection, AsyncSqlite]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    now = utc_now_ms()
    connection.execute(
        "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
        "VALUES('org','Organization','active',?,?)",
        (now, now),
    )
    connection.execute(
        """INSERT INTO users(id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES('owner','owner@example.test','owner@example.test','active',?,?)""",
        (now, now),
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('owner-member','org','owner','member','active',?,?)""",
        (now, now),
    )
    connection.execute(
        """INSERT INTO events
           (id,organization_id,name,starts_at_ms,ends_at_ms,time_zone,location,
            delivery_mode,description,status,created_at_ms,updated_at_ms)
           VALUES('event','org','Event',?,?, 'UTC','Online','virtual','Description',
                  'active',?,?)""",
        (now + 10_000_000, now + 20_000_000, now, now),
    )
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,
            status,published_at_ms,created_at_ms,updated_at_ms,closes_at_ms)
           VALUES('form','org','event',1,'event-cfp','Welcome','{}','published',?,?,?,?)""",
        (now, now, now, now + 7_000_000),
    )
    connection.execute(
        """INSERT INTO submissions
           (id,organization_id,event_id,form_id,public_session_id,
            proposal_title,proposal_abstract,speaker_name,speaker_email,submitter_user_id,
            status,submitted_at_ms,created_at_ms,updated_at_ms)
           VALUES('submission','org','event','form','public-session','Proposal','Abstract',
                  'Owner','owner@example.test','owner','submitted',?,?,?)""",
        (now, now, now),
    )
    yield connection, AsyncSqlite(connection)
    connection.close()


def seed_invitation(
    connection: sqlite3.Connection, token: str, *, contributor_id: str = "co-speaker"
) -> None:
    now = utc_now_ms()
    connection.execute(
        """INSERT INTO submission_contributors
           (id,organization_id,event_id,submission_id,display_name,email,normalized_email,
            role,created_at_ms,updated_at_ms,invitation_status,invitation_token_hash,
            invitation_expires_at_ms,invited_at_ms,invitation_version)
           VALUES(?,'org','event','submission','Co Speaker','co@example.test','co@example.test',
                  'co_speaker',?,?,'pending',?,?,?,1)""",
        (contributor_id, now, now, hash_token(token), now + 3_600_000, now),
    )


def test_co_speaker_expiry_is_capped_by_the_cfp_close() -> None:
    now = 1_000
    assert _co_speaker_expiry(now, None) == now + 7 * 86_400_000
    assert _co_speaker_expiry(now, now + 10_000) == now + 10_000


async def test_accept_is_single_use_and_creates_relationships(
    invitation_database,
) -> None:
    connection, database = invitation_database
    token = "accept-token-with-at-least-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token)
    request = request_for(database)

    detail = await get_co_speaker_invitation(token, request)
    assert detail.invitation_status == "pending"
    accepted = await accept_co_speaker_invitation(token, request)
    assert accepted.invitation_status == "accepted"
    row = connection.execute(
        """SELECT invitation_status,invitation_token_hash,user_id
           FROM submission_contributors WHERE id='co-speaker'"""
    ).fetchone()
    assert row["invitation_status"] == "accepted"
    assert row["invitation_token_hash"] is None
    assert row["user_id"] is not None
    assert connection.execute(
        "SELECT COUNT(*) FROM submission_speakers WHERE submission_id='submission' "
        "AND role='co_speaker'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT status FROM event_memberships WHERE event_id='event' AND user_id=? "
        "AND role='speaker'",
        (row["user_id"],),
    ).fetchone()[0] == "active"
    assert connection.execute(
        "SELECT action FROM audit_events WHERE target_id='co-speaker'"
    ).fetchone()[0] == "submission.co_speaker.accepted"

    with pytest.raises(HTTPException) as reused:
        await accept_co_speaker_invitation(token, request)
    assert reused.value.status_code == 404


async def test_decline_consumes_token_without_creating_an_account(
    invitation_database,
) -> None:
    connection, database = invitation_database
    token = "decline-token-with-at-least-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token)

    declined = await decline_co_speaker_invitation(token, request_for(database))
    assert declined.invitation_status == "declined"
    row = connection.execute(
        """SELECT invitation_status,invitation_token_hash,user_id
           FROM submission_contributors WHERE id='co-speaker'"""
    ).fetchone()
    assert tuple(row) == ("declined", None, None)
    assert connection.execute(
        "SELECT COUNT(*) FROM users WHERE normalized_email='co@example.test'"
    ).fetchone()[0] == 0


def test_owner_lifecycle_routes_preserve_contributors_and_never_target_primary() -> None:
    router = (
        Path(__file__).parents[2] / "src" / "sessionbuddy" / "cfp" / "router.py"
    ).read_text(encoding="utf-8")

    assert (
        '"/api/v1/forms/{slug}/submissions/{submission_id}/co-speakers/'
        '{co_speaker_id}/resend"'
    ) in router
    assert (
        '"/api/v1/forms/{slug}/submissions/{submission_id}/co-speakers/'
        '{co_speaker_id}"'
    ) in router
    reconcile = router.split("async def _reconcile_co_speakers", 1)[1].split(
        "async def _timed_first", 1
    )[0]
    assert "DELETE FROM submission_contributors" not in reconcile
    assert "invitation_status='removed'" in reconcile
    assert "invitation_status!='removed'" in reconcile
    assert "ON CONFLICT(submission_id,normalized_email) DO UPDATE" in reconcile
    assert "invitation_version=submission_contributors.invitation_version+1" in reconcile
    assert "ss.role='primary'" in reconcile
    assert "submission.co_speaker.invite" in reconcile
    assert "submission.co_speaker.remove" in reconcile

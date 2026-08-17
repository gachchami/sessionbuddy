import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from sessionbuddy.cfp import router as cfp_routes
from sessionbuddy.cfp.models import (
    AcceptedSubmissionParticipantsUpdate,
    CoSpeakerInput,
    SubmissionUpdate,
)
from sessionbuddy.cfp.router import (
    _co_speaker_expiry,
    _private_submission_by_id,
    _reconcile_co_speakers,
    accept_co_speaker_invitation,
    decline_co_speaker_invitation,
    get_co_speaker_invitation,
    remove_co_speaker,
    update_accepted_submission_participants,
    update_submission,
)
from sessionbuddy.platform.auth import hash_token
from sessionbuddy.platform.db.types import utc_now_ms
from sessionbuddy.speaker_operations.acceptance_tasks import (
    reconcile_accepted_submission_speakers,
)
from tests.schema import MIGRATIONS
from tests.speaker_operations.test_asset_boundary import AsyncSqlite


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
            delivery_mode,description,status,created_at_ms,updated_at_ms,created_by_user_id)
           VALUES('event','org','Event',?,?, 'UTC','Online','virtual','Description',
                  'active',?,?, 'owner')""",
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
    connection: sqlite3.Connection,
    token: str,
    *,
    contributor_id: str = "co-speaker",
    role: str = "co_speaker",
) -> None:
    now = utc_now_ms()
    connection.execute(
        """INSERT INTO submission_contributors
           (id,organization_id,event_id,submission_id,display_name,email,normalized_email,
            role,created_at_ms,updated_at_ms,invitation_status,invitation_token_hash,
            invitation_expires_at_ms,invited_at_ms,invitation_version)
           VALUES(?,'org','event','submission','Co Speaker','co@example.test','co@example.test',
                  ?,?,?,'pending',?,?,?,1)""",
        (contributor_id, role, now, now, hash_token(token), now + 3_600_000, now),
    )


def test_co_speaker_expiry_is_capped_by_the_cfp_close() -> None:
    now = 1_000
    assert _co_speaker_expiry(now, None) == now + 7 * 86_400_000
    assert _co_speaker_expiry(now, now + 10_000) == now + 10_000


async def test_accepted_speaker_can_manage_participants_without_reopening_proposal(
    invitation_database, monkeypatch
) -> None:
    connection, database = invitation_database
    now = utc_now_ms()
    connection.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,submission_id,decision,internal_reason,version,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES('decision','org','event','submission','accepted','Selected',1,
                  'owner',?,?)""",
        (now, now),
    )
    authenticated = SimpleNamespace(actor=SimpleNamespace(user_id="owner"))

    async def authenticate(_request):
        return authenticated

    async def permit(_request, *_args, **_kwargs):
        return authenticated

    async def no_rate_limit(_request, **_kwargs):
        return None

    monkeypatch.setattr(cfp_routes, "authenticate_request", authenticate)
    monkeypatch.setattr(cfp_routes, "require_permission", permit)
    monkeypatch.setattr(cfp_routes, "enforce_rate_limit", no_rate_limit)

    updated = await update_accepted_submission_participants(
        "event-cfp",
        "submission",
        AcceptedSubmissionParticipantsUpdate(
            version=1,
            co_speakers=[
                CoSpeakerInput(
                    display_name="Marcus Okafor",
                    email="marcus@example.test",
                    role="co_author",
                )
            ],
        ),
        request_for(database),
        "participant-update-submission-v2",
    )

    assert updated.status == "accepted"
    assert updated.version == 2
    assert updated.proposal_title == "Proposal"
    assert [(item.email, item.role) for item in updated.co_speakers] == [
        ("marcus@example.test", "co_author")
    ]
    assert connection.execute(
        "SELECT decision FROM submission_decisions WHERE id='decision'"
    ).fetchone()[0] == "accepted"
    correction_audit = connection.execute(
        """SELECT metadata_json FROM audit_events
           WHERE action='submission.participants.correct' AND target_id='submission'"""
    ).fetchone()
    assert correction_audit is not None
    assert '"participant_count":1' in correction_audit[0]

    with pytest.raises(HTTPException) as stale:
        await update_accepted_submission_participants(
            "event-cfp",
            "submission",
            AcceptedSubmissionParticipantsUpdate(
                version=1,
                co_speakers=[
                    CoSpeakerInput(
                        display_name="Stale Participant",
                        email="stale@example.test",
                        role="panelist",
                    )
                ],
            ),
            request_for(database),
            "participant-update-submission-stale",
        )
    assert stale.value.status_code == 409
    assert connection.execute(
        "SELECT version FROM submissions WHERE id='submission'"
    ).fetchone()[0] == 2
    assert connection.execute(
        """SELECT COUNT(*) FROM submission_contributors
           WHERE normalized_email='stale@example.test'"""
    ).fetchone()[0] == 0


async def test_legacy_remove_cannot_bypass_accepted_participant_reconciliation(
    invitation_database, monkeypatch
) -> None:
    connection, database = invitation_database
    now = utc_now_ms()
    seed_invitation(connection, "legacy-remove-token-with-at-least-thirty-two-characters")
    connection.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,submission_id,decision,internal_reason,version,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES('legacy-decision','org','event','submission','accepted','Selected',1,
                  'owner',?,?)""",
        (now, now),
    )
    authenticated = SimpleNamespace(actor=SimpleNamespace(user_id="owner"))

    async def authenticate(_request):
        return authenticated

    async def permit(_request, *_args, **_kwargs):
        return authenticated

    monkeypatch.setattr(cfp_routes, "authenticate_request", authenticate)
    monkeypatch.setattr(cfp_routes, "require_permission", permit)

    with pytest.raises(HTTPException) as conflict:
        await remove_co_speaker(
            "event-cfp", "submission", "co-speaker", request_for(database)
        )

    assert conflict.value.status_code == 409
    assert connection.execute(
        "SELECT invitation_status FROM submission_contributors WHERE id='co-speaker'"
    ).fetchone()[0] == "pending"


async def test_participant_role_is_preserved_when_invitation_is_accepted(
    invitation_database,
) -> None:
    connection, database = invitation_database
    token = "moderator-token-with-at-least-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token, contributor_id="moderator", role="moderator")

    accepted = await accept_co_speaker_invitation(token, request_for(database))

    assert accepted.role == "moderator"
    assert accepted.role_label == "Moderator"
    assert connection.execute(
        "SELECT role FROM submission_speakers WHERE submission_id='submission' AND role!='primary'"
    ).fetchone()[0] == "moderator"


async def test_accepted_participant_receives_missing_identity_onboarding(
    invitation_database,
) -> None:
    connection, database = invitation_database
    now = utc_now_ms()
    connection.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,submission_id,decision,internal_reason,version,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES('accepted-decision','org','event','submission','accepted','Selected',1,
                  'owner',?,?)""",
        (now, now),
    )
    token = "accepted-participant-token-with-at-least-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token, role="co_author")

    accepted = await accept_co_speaker_invitation(token, request_for(database))

    assert accepted.invitation_status == "accepted"
    speaker = connection.execute(
        """SELECT es.id,es.status,es.selection_status
           FROM event_speakers es
           JOIN submission_speakers ss ON ss.event_speaker_id=es.id
           WHERE ss.submission_id='submission' AND ss.role='co_author'"""
    ).fetchone()
    assert tuple(speaker[1:]) == ("onboarding", "accepted")
    tasks = connection.execute(
        """SELECT task_type,submission_id FROM speaker_tasks
           WHERE event_speaker_id=? ORDER BY task_type""",
        (speaker["id"],),
    ).fetchall()
    assert [tuple(task) for task in tasks] == [
        ("headshot", "submission"),
        ("profile", "submission"),
    ]

    await _reconcile_co_speakers(
        request_for(database),
        submission_id="submission",
        organization_id="org",
        event_id="event",
        invitation_deadline_ms=None,
        proposal_title="Proposal",
        primary_name="Owner",
        desired=[],
        actor_user_id="owner",
    )

    assert connection.execute(
        "SELECT COUNT(*) FROM submission_speakers WHERE event_speaker_id=?",
        (speaker["id"],),
    ).fetchone()[0] == 0
    assert tuple(connection.execute(
        "SELECT status,selection_status FROM event_speakers WHERE id=?",
        (speaker["id"],),
    ).fetchone()) == ("complete", "submitted")
    assert connection.execute(
        """SELECT COUNT(*) FROM speaker_tasks
           WHERE event_speaker_id=? AND state='waived'""",
        (speaker["id"],),
    ).fetchone()[0] == 2


async def test_complete_accepted_participant_is_not_forced_back_to_onboarding(
    invitation_database,
) -> None:
    connection, database = invitation_database
    now = utc_now_ms()
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,description,status,created_at_ms,updated_at_ms)
           VALUES('complete-user','co@example.test','co@example.test','Complete bio',
                  'active',?,?)""",
        (now, now),
    )
    connection.execute(
        """INSERT INTO user_headshots
           (user_id,object_key,content_type,byte_size,checksum_sha256,updated_at_ms)
           VALUES('complete-user','headshot/key','image/png',100,?,?)""",
        (b"x" * 32, now),
    )
    connection.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,submission_id,decision,internal_reason,version,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES('complete-decision','org','event','submission','accepted','Selected',1,
                  'owner',?,?)""",
        (now, now),
    )
    token = "complete-participant-token-with-at-least-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token)

    await accept_co_speaker_invitation(token, request_for(database))

    status = connection.execute(
        """SELECT es.status FROM event_speakers es
           JOIN people p ON p.id=es.person_id WHERE p.user_id='complete-user'"""
    ).fetchone()[0]
    assert status == "complete"
    assert connection.execute(
        "SELECT COUNT(*) FROM speaker_tasks WHERE event_speaker_id IN "
        "(SELECT es.id FROM event_speakers es JOIN people p ON p.id=es.person_id "
        "WHERE p.user_id='complete-user')"
    ).fetchone()[0] == 0


async def test_post_commit_reconciliation_closes_invitation_decision_race(
    invitation_database,
) -> None:
    connection, database = invitation_database
    token = "racing-participant-token-with-at-least-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token)
    await accept_co_speaker_invitation(token, request_for(database))
    now = utc_now_ms()
    connection.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,submission_id,decision,internal_reason,version,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES('racing-decision','org','event','submission','accepted','Selected',1,
                  'owner',?,?)""",
        (now, now),
    )

    async def execute(batch) -> None:
        await batch.execute()

    await reconcile_accepted_submission_speakers(
        database,
        organization_id="org",
        event_id="event",
        submission_id="submission",
        now=now,
        execute_batch=execute,
    )

    speaker = connection.execute(
        """SELECT es.id,es.status,es.selection_status FROM event_speakers es
           JOIN submission_speakers ss ON ss.event_speaker_id=es.id
           WHERE ss.submission_id='submission' AND ss.role!='primary'"""
    ).fetchone()
    assert tuple(speaker[1:]) == ("onboarding", "accepted")
    assert connection.execute(
        "SELECT COUNT(*) FROM speaker_tasks WHERE event_speaker_id=? AND state='open'",
        (speaker["id"],),
    ).fetchone()[0] == 2


async def test_accepted_participant_role_can_change_without_reinvitation(
    invitation_database,
) -> None:
    connection, database = invitation_database
    token = "editable-role-token-with-at-least-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token)
    await accept_co_speaker_invitation(token, request_for(database))
    queued: list[dict[str, object]] = []

    class Queue:
        async def send(self, message: dict[str, object]) -> None:
            queued.append(message)

    request = request_for(database)
    request.scope["env"].COMMUNICATION_QUEUE = Queue()
    await _reconcile_co_speakers(
        request,
        submission_id="submission",
        organization_id="org",
        event_id="event",
        invitation_deadline_ms=utc_now_ms() + 3_600_000,
        proposal_title="Proposal",
        primary_name="Owner",
        desired=[
            CoSpeakerInput(
                display_name="Co Speaker",
                email="co@example.test",
                role="moderator",
            )
        ],
        actor_user_id="owner",
    )

    contributor = connection.execute(
        "SELECT role,invitation_status FROM submission_contributors WHERE id='co-speaker'"
    ).fetchone()
    assert tuple(contributor) == ("moderator", "accepted")
    assert connection.execute(
        "SELECT role FROM submission_speakers WHERE submission_id='submission' AND role!='primary'"
    ).fetchone()[0] == "moderator"
    assert connection.execute("SELECT COUNT(*) FROM communication_messages").fetchone()[0] == 0
    assert queued == []
    audit = connection.execute(
        """SELECT metadata_json FROM audit_events
           WHERE action='submission.co_speaker.update' AND target_id='co-speaker'"""
    ).fetchone()
    assert audit is not None
    assert '"participant_role":"moderator"' in audit[0]


async def test_removal_preserves_independent_organizer_session_participation(
    invitation_database,
) -> None:
    connection, database = invitation_database
    now = utc_now_ms()
    connection.execute(
        """INSERT INTO submission_decisions
           (id,organization_id,event_id,submission_id,decision,internal_reason,version,
            decided_by_user_id,decided_at_ms,updated_at_ms)
           VALUES('shared-decision','org','event','submission','accepted','Selected',1,
                  'owner',?,?)""",
        (now, now),
    )
    token = "shared-session-participant-token-with-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token, contributor_id="shared-participant")
    await accept_co_speaker_invitation(token, request_for(database))
    speaker_id = connection.execute(
        """SELECT event_speaker_id FROM submission_speakers
           WHERE submission_id='submission' AND role!='primary'"""
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO accepted_sessions
           (id,organization_id,event_id,source_type,organizer_title,organizer_abstract,
            created_at_ms)
           VALUES('organizer-session','org','event','organizer_created',
                  'Organizer session','Session abstract',?)""",
        (now,),
    )
    connection.execute(
        """INSERT INTO accepted_session_participants
           (id,organization_id,event_id,accepted_session_id,event_speaker_id,
            display_name_snapshot,created_at_ms,updated_at_ms)
           VALUES('organizer-participant','org','event','organizer-session',?,
                  'Co Speaker',?,?)""",
        (speaker_id, now, now),
    )

    await _reconcile_co_speakers(
        request_for(database),
        submission_id="submission",
        organization_id="org",
        event_id="event",
        invitation_deadline_ms=None,
        proposal_title="Proposal",
        primary_name="Owner",
        desired=[],
        actor_user_id="owner",
    )

    assert connection.execute(
        """SELECT COUNT(*) FROM speaker_tasks
           WHERE event_speaker_id=? AND state='open'""",
        (speaker_id,),
    ).fetchone()[0] == 2
    assert tuple(connection.execute(
        "SELECT status,selection_status FROM event_speakers WHERE id=?",
        (speaker_id,),
    ).fetchone()) == ("onboarding", "accepted")
    assert connection.execute(
        """SELECT membership.status FROM event_memberships membership
           JOIN people person ON person.user_id=membership.user_id
           JOIN event_speakers speaker ON speaker.person_id=person.id
           WHERE speaker.id=? AND membership.event_id='event' AND membership.role='speaker'""",
        (speaker_id,),
    ).fetchone()[0] == "active"


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


async def test_accept_cannot_restore_revoked_organizer_authority(
    invitation_database,
) -> None:
    connection, database = invitation_database
    now = utc_now_ms()
    token = "former-admin-co-speaker-token-with-thirty-two-chars"  # noqa: S105
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES('former-admin','co@example.test','co@example.test','active',?,?)""",
        (now, now),
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,revoked_at_ms,created_at_ms,updated_at_ms)
           VALUES('former-org-admin','org','former-admin','organization_admin','revoked',?,?,?)""",
        (now, now, now),
    )
    seed_invitation(connection, token)

    accepted = await accept_co_speaker_invitation(token, request_for(database))

    assert accepted.invitation_status == "accepted"
    assert tuple(connection.execute(
        """SELECT role,status FROM organization_memberships
           WHERE organization_id='org' AND user_id='former-admin'"""
    ).fetchone()) == ("member", "active")
    memberships = connection.execute(
        """SELECT role,status FROM event_memberships
           WHERE organization_id='org' AND event_id='event' AND user_id='former-admin'
           ORDER BY role"""
    ).fetchall()
    assert [tuple(row) for row in memberships] == [("speaker", "active")]


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


async def test_removed_co_speaker_readd_uses_stable_identity_and_next_message_version(
    invitation_database,
) -> None:
    connection, database = invitation_database
    token = "removed-token-with-at-least-thirty-two-characters"  # noqa: S105
    seed_invitation(connection, token)
    connection.execute(
        """UPDATE submission_contributors SET invitation_status='removed',
                  invitation_token_hash=NULL,invitation_expires_at_ms=NULL
           WHERE id='co-speaker'"""
    )
    queued: list[dict[str, object]] = []

    class Queue:
        async def send(self, message: dict[str, object]) -> None:
            queued.append(message)

    request = request_for(database)
    request.scope["env"].COMMUNICATION_QUEUE = Queue()
    await _reconcile_co_speakers(
        request,
        submission_id="submission",
        organization_id="org",
        event_id="event",
        invitation_deadline_ms=utc_now_ms() + 3_600_000,
        proposal_title="Proposal",
        primary_name="Owner",
        desired=[CoSpeakerInput(display_name="Co Speaker", email="co@example.test")],
        actor_user_id="owner",
    )

    contributor = connection.execute(
        """SELECT id,invitation_status,invitation_version
           FROM submission_contributors WHERE normalized_email='co@example.test'"""
    ).fetchone()
    assert tuple(contributor) == ("co-speaker", "pending", 2)
    message = connection.execute(
        "SELECT id,deterministic_key FROM communication_messages"
    ).fetchone()
    assert message["deterministic_key"] == "co-speaker:co-speaker:v2"
    assert queued == [{"schema_version": 1, "message_id": message["id"]}]


class _FirstSendFailsQueue:
    """Fails the first publish only, so continuation past a failure is visible."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    async def send(self, message: dict[str, object]) -> None:
        self.attempts.append(str(message["message_id"]))
        if len(self.attempts) == 1:
            raise RuntimeError("synthetic queue outage")


async def test_co_speaker_save_succeeds_when_queue_dispatch_fails_after_commit(
    invitation_database,
) -> None:
    connection, database = invitation_database
    queue = _FirstSendFailsQueue()
    request = request_for(database)
    request.scope["env"].COMMUNICATION_QUEUE = queue

    await _reconcile_co_speakers(
        request,
        submission_id="submission",
        organization_id="org",
        event_id="event",
        invitation_deadline_ms=utc_now_ms() + 3_600_000,
        proposal_title="Proposal",
        primary_name="Owner",
        desired=[
            CoSpeakerInput(display_name="First Co-author", email="first@example.test"),
            CoSpeakerInput(display_name="Second Co-author", email="second@example.test"),
        ],
        actor_user_id="owner",
    )

    contributors = connection.execute(
        """SELECT normalized_email,invitation_status FROM submission_contributors
           ORDER BY normalized_email"""
    ).fetchall()
    assert [tuple(row) for row in contributors] == [
        ("first@example.test", "pending"),
        ("second@example.test", "pending"),
    ]
    messages = connection.execute(
        "SELECT recipient_email,status FROM communication_messages ORDER BY recipient_email"
    ).fetchall()
    assert [tuple(row) for row in messages] == [
        ("first@example.test", "queued"),
        ("second@example.test", "queued"),
    ]
    # One failed publish must not strand the remaining committed envelopes.
    assert len(queue.attempts) == 2
    # The absorbed failure still surfaces on the request's completion telemetry.
    assert request.state.degradations == ["communication_queue_publish_failed"]


async def test_update_submission_stays_committed_when_queue_dispatch_fails(
    invitation_database, monkeypatch
) -> None:
    """The full route regression: before the fix, a post-commit queue outage
    turned the committed save into a 500 whose stale-version retry hit 409."""
    connection, database = invitation_database
    authenticated = SimpleNamespace(actor=SimpleNamespace(user_id="owner"))

    async def authenticate(_request):
        return authenticated

    async def permit(_request, *_args, **_kwargs):
        return authenticated

    async def no_rate_limit(_request, **_kwargs):
        return None

    monkeypatch.setattr(cfp_routes, "authenticate_request", authenticate)
    monkeypatch.setattr(cfp_routes, "require_permission", permit)
    monkeypatch.setattr(cfp_routes, "enforce_rate_limit", no_rate_limit)

    class AlwaysFailingQueue:
        def __init__(self) -> None:
            self.attempts = 0

        async def send(self, _message: dict[str, object]) -> None:
            self.attempts += 1
            raise RuntimeError("synthetic queue outage")

    queue = AlwaysFailingQueue()
    body = SubmissionUpdate(
        version=1,
        speaker_name="Owner",
        speaker_email="owner@example.test",
        proposal_title="Proposal, revised",
        proposal_abstract="Abstract, revised",
        co_speakers=[
            CoSpeakerInput(display_name="Queued Co-author", email="queued@example.test")
        ],
    )

    request = request_for(database)
    request.scope["env"].COMMUNICATION_QUEUE = queue
    view = await update_submission("event-cfp", "submission", body, request)

    assert view.editable is True
    assert view.version == 2
    assert view.proposal_title == "Proposal, revised"
    assert [(item.email, item.invitation_status) for item in view.co_speakers] == [
        ("queued@example.test", "pending")
    ]
    assert queue.attempts == 1
    assert request.state.degradations == ["communication_queue_publish_failed"]
    assert connection.execute(
        "SELECT status FROM communication_messages WHERE recipient_email='queued@example.test'"
    ).fetchone()[0] == "queued"

    # A retry replaying the now-stale version is a real optimistic-lock
    # conflict — the only way this route may answer 409 for a saved edit.
    stale_retry = request_for(database)
    stale_retry.scope["env"].COMMUNICATION_QUEUE = queue
    with pytest.raises(HTTPException) as conflict:
        await update_submission("event-cfp", "submission", body, stale_retry)
    assert conflict.value.status_code == 409
    assert queue.attempts == 1
    assert connection.execute(
        "SELECT version FROM submissions WHERE id='submission'"
    ).fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM communication_messages"
    ).fetchone()[0] == 1


async def test_editable_submission_response_accepts_computed_co_speaker_label(
    invitation_database,
) -> None:
    connection, database = invitation_database
    seed_invitation(connection, "response-token-with-at-least-thirty-two-characters")

    submission = await _private_submission_by_id(
        database, "submission", editable=True
    )

    assert submission.editable is True
    assert submission.can_manage_participants is True
    assert submission.co_speakers[0].role_label == "Co-speaker"


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
    assert 'f"co-speaker:{contributor_id}:v{invitation_version}"' in reconcile
    assert "all_existing_by_email.get(normalized)" in reconcile
    assert "ss.role='primary'" in reconcile
    assert "submission.co_speaker.invite" in reconcile
    assert "submission.co_speaker.remove" in reconcile

    assert router.count('"cfp.co_speaker.email", limit=20, window_seconds=3_600') == 2
    assert "for _normalized_email in desired_emails" in router

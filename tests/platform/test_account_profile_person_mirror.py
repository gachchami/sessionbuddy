"""The account page must not erase person fields it never displayed.

`users` backs the account page. `people` backs the speaker profile form, the
organizer roster, and invitation import. The account-profile PATCH mirrors a few
fields onto the person record, and mirroring them unconditionally meant a speaker
saving their account page wiped a job title, company, or link list that had only
ever been set on the person record -- the save reported success and the values
were gone on the next load.
"""

import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response

from sessionbuddy.platform.auth import access
from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Persona, Role
from tests.schema import MIGRATIONS
from tests.speaker_operations.test_asset_boundary import AsyncSqlite

USER_ID = "speaker-user"
PERSON_ID = "person"
SPEAKER_LINKS = ["https://priya.dev", "https://github.com/priya"]


def request_for(database: AsyncSqlite) -> Request:
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/api/v1/account/profile",
            "headers": [],
            "env": SimpleNamespace(
                DB=database,
                CSRF_HMAC_KEY="c" * 32,
                PASSWORD_PEPPER="p" * 32,
                SESSION_HMAC_KEY="s" * 32,
            ),
            "state": {"request_id": "request"},
        }
    )


@pytest.fixture
def profile_database() -> tuple[sqlite3.Connection, AsyncSqlite]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    for migration in MIGRATIONS:
        connection.executescript(migration.read_text(encoding="utf-8"))
    connection.execute(
        "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
        "VALUES('org','Organization','active',1,1)"
    )
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,first_name,last_name,display_name,description,
            status,version,created_at_ms,updated_at_ms)
           VALUES(?,'speaker@example.test','speaker@example.test','Priya','Raman',
                  'Priya Raman','Speaks about distributed systems.','active',1,1,1)""",
        (USER_ID,),
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES('membership','org',?,'member','active',1,1)""",
        (USER_ID,),
    )
    connection.execute(
        """INSERT INTO user_roles
           (user_id,role,status,is_default,created_at_ms,updated_at_ms)
           VALUES(?,'speaker','active',1,1,1)""",
        (USER_ID,),
    )
    # The state an organizer (or the speaker's own profile page) leaves behind:
    # these fields exist only on the person record.
    connection.execute(
        """INSERT INTO people
           (id,organization_id,user_id,display_name,job_title,company,biography,location,
            links_json,version,created_at_ms,updated_at_ms)
           VALUES(?, 'org',?,'Priya Raman','Principal Engineer','Latticework Systems',
                  'Builds distributed systems.','Bengaluru',?,1,1,1)""",
        (PERSON_ID, USER_ID, json.dumps(SPEAKER_LINKS)),
    )
    connection.commit()
    return connection, AsyncSqlite(connection)


@pytest.fixture
def signed_in_speaker(monkeypatch) -> None:
    async def authenticated(request: Request) -> AuthenticatedContext:
        return AuthenticatedContext(
            Actor(
                user_id=USER_ID,
                active_persona=Persona.SPEAKER,
                organization_roles={"org": frozenset({Role.SPEAKER})},
            ),
            "session",
        )

    monkeypatch.setattr(access, "authenticate_request", authenticated)
    monkeypatch.setattr(access, "guard_mutation", lambda *args, **kwargs: None)


def person(connection: sqlite3.Connection) -> dict[str, object]:
    row = dict(
        connection.execute(
            "SELECT job_title,company,biography,location,links_json FROM people WHERE id=?",
            (PERSON_ID,),
        ).fetchone()
    )
    row["links_json"] = json.loads(str(row["links_json"]))
    return row


def account_update(**overrides) -> access.AccountProfileUpdate:
    payload = {
        "first_name": "Priya",
        "last_name": "Raman",
        "job_title": None,
        "company": None,
        "time_zone": "Asia/Kolkata",
        "description": "Speaks about distributed systems.",
        "website_url": None,
        "linkedin_url": None,
        "x_url": None,
        "public_profile_enabled": False,
        "version": 1,
    }
    payload.update(overrides)
    return access.AccountProfileUpdate(**payload)


async def _update_account_profile(body, request):
    return await access.update_account_profile(body, request, Response())


async def test_account_save_keeps_person_fields_the_account_page_never_showed(
    profile_database, signed_in_speaker
) -> None:
    connection, database = profile_database

    await _update_account_profile(account_update(), request_for(database))

    assert person(connection) == {
        "job_title": "Principal Engineer",
        "company": "Latticework Systems",
        "biography": "Builds distributed systems.",
        "location": "Bengaluru",
        "links_json": SPEAKER_LINKS,
    }


async def test_account_save_adds_its_own_link_without_dropping_the_others(
    profile_database, signed_in_speaker
) -> None:
    connection, database = profile_database

    await _update_account_profile(
        account_update(linkedin_url="https://www.linkedin.com/in/priya"),
        request_for(database),
    )

    assert person(connection)["links_json"] == [
        *SPEAKER_LINKS,
        "https://www.linkedin.com/in/priya",
    ]


async def test_account_save_still_propagates_a_real_edit(
    profile_database, signed_in_speaker
) -> None:
    connection, database = profile_database
    connection.execute(
        "UPDATE users SET job_title='Staff Engineer',company='Latticework Systems' WHERE id=?",
        (USER_ID,),
    )
    connection.commit()

    await _update_account_profile(
        account_update(job_title="Distinguished Engineer", company="Latticework Systems"),
        request_for(database),
    )

    assert person(connection)["job_title"] == "Distinguished Engineer"


async def test_account_save_still_propagates_an_intentional_clear(
    profile_database, signed_in_speaker
) -> None:
    connection, database = profile_database
    connection.execute("UPDATE users SET job_title='Staff Engineer' WHERE id=?", (USER_ID,))
    connection.commit()

    await _update_account_profile(account_update(), request_for(database))

    assert person(connection)["job_title"] is None


def person_name_and_version(connection: sqlite3.Connection) -> tuple[str, int]:
    row = connection.execute(
        "SELECT display_name,version FROM people WHERE id=?", (PERSON_ID,)
    ).fetchone()
    return str(row["display_name"]), int(row["version"])


async def test_account_save_keeps_a_roster_name_the_organizer_curated(
    profile_database, signed_in_speaker
) -> None:
    """display_name follows the same rule as the other mirrored fields.

    It was the one field still copied unconditionally, so an account save that never
    touched the name re-created the erasure this mirror exists to prevent -- on the field
    an organizer is most likely to have curated for the program.
    """
    connection, database = profile_database
    connection.execute(
        "UPDATE people SET display_name='Dr. Priya Raman' WHERE id=?", (PERSON_ID,)
    )
    connection.commit()

    await _update_account_profile(account_update(), request_for(database))

    assert person_name_and_version(connection)[0] == "Dr. Priya Raman"


async def test_an_actual_rename_still_reaches_the_person_record(
    profile_database, signed_in_speaker
) -> None:
    connection, database = profile_database
    connection.execute(
        "UPDATE people SET display_name='Dr. Priya Raman' WHERE id=?", (PERSON_ID,)
    )
    connection.commit()

    await _update_account_profile(
        account_update(last_name="Raman-Iyer"), request_for(database)
    )

    assert person_name_and_version(connection)[0] == "Priya Raman-Iyer"


async def test_the_link_this_save_carries_survives_the_ten_link_cap(
    profile_database, signed_in_speaker
) -> None:
    """Truncation trims the retained tail, never the link the user just typed.

    Before: retained links went first and merged[:10] cut from the end, so a person
    already carrying ten organizer-added links lost the new account link -- while the
    users row saved it and the page rendered it back as kept.
    """
    connection, database = profile_database
    organizer_links = [f"https://example.test/link-{index}" for index in range(10)]
    connection.execute(
        "UPDATE people SET links_json=? WHERE id=?",
        (json.dumps(organizer_links), PERSON_ID),
    )
    connection.commit()

    await _update_account_profile(
        account_update(linkedin_url="https://www.linkedin.com/in/priya"),
        request_for(database),
    )

    links = person(connection)["links_json"]
    assert "https://www.linkedin.com/in/priya" in links
    assert links == [*organizer_links[:9], "https://www.linkedin.com/in/priya"]


class InterleavingSqlite(AsyncSqlite):
    """Runs a rival write between the handler's reads and its batch.

    This is the schedule D1 actually allows: the mirror's links merge is computed from a
    pre-batch read, and the database serializes statements, not read-then-write pairs.
    """

    def __init__(self, connection: sqlite3.Connection, interleaved_sql: str, values) -> None:
        super().__init__(connection)
        self.interleaved = (interleaved_sql, values)

    async def batch(self, statements):
        if self.interleaved is not None:
            sql, values = self.interleaved
            self.interleaved = None
            self.connection.execute(sql, values)
            self.connection.commit()
        return await super().batch(statements)


async def test_password_rotation_reads_the_authorization_version_inside_the_batch(
    profile_database, signed_in_speaker
) -> None:
    """A rival authorization write before execution cannot stale the new session."""
    connection, _plain = profile_database
    starting_version = int(
        connection.execute(
            "SELECT authorization_version FROM users WHERE id=?", (USER_ID,)
        ).fetchone()[0]
    )
    database = InterleavingSqlite(
        connection,
        "UPDATE users SET authorization_version=authorization_version+1 WHERE id=?",
        (USER_ID,),
    )
    new_password = "a private local passphrase"  # noqa: S105 - synthetic test credential

    updated = await _update_account_profile(
        account_update(
            password=new_password,
            password_confirmation=new_password,
        ),
        request_for(database),
    )

    assert updated.csrf_token
    row = connection.execute(
        """SELECT s.authorization_version AS session_version,
                  u.authorization_version AS user_version
           FROM sessions s JOIN users u ON u.id=s.user_id
           WHERE s.user_id=? AND s.revoked_at_ms IS NULL""",
        (USER_ID,),
    ).fetchone()
    expected_version = starting_version + 2  # rival bump plus password rotation
    assert (row["session_version"], row["user_version"]) == (
        expected_version,
        expected_version,
    )


async def test_failed_post_commit_session_confirmation_records_degradation(
    profile_database, signed_in_speaker
) -> None:
    """A role revoked at execution time leaves an operator-visible signal."""
    connection, _plain = profile_database
    database = InterleavingSqlite(
        connection,
        """UPDATE user_roles
           SET status='revoked',revoked_at_ms=2,updated_at_ms=2 WHERE user_id=?""",
        (USER_ID,),
    )
    request = request_for(database)
    new_password = "a private local passphrase"  # noqa: S105 - synthetic test credential

    with pytest.raises(HTTPException) as failure:
        await _update_account_profile(
            account_update(
                password=new_password,
                password_confirmation=new_password,
            ),
            request,
        )

    assert failure.value.status_code == 409
    assert request.state.degradations == ["account_session_rotation_unconfirmed"]
    assert connection.execute(
        "SELECT COUNT(*) FROM password_credentials WHERE user_id=? AND status='active'",
        (USER_ID,),
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM sessions WHERE user_id=? AND revoked_at_ms IS NULL",
        (USER_ID,),
    ).fetchone()[0] == 0


async def test_a_concurrent_speaker_profile_save_is_not_clobbered_by_the_mirror(
    profile_database, signed_in_speaker
) -> None:
    """The person row moved after the mirror's read, so the mirror stands down.

    The speaker profile form and the organizer roster update `people` under optimistic
    concurrency (competition/router.py binds version on both paths). A mirror computed
    from a stale read must not outrank them: without its own version predicate it
    overwrote the rival's links merge AND bumped version, making the rival's next save
    fail its guard for something the account page never displayed.
    """
    connection, _plain = profile_database
    database = InterleavingSqlite(
        connection,
        "UPDATE people SET links_json=?,version=version+1,updated_at_ms=2 WHERE id=?",
        (json.dumps([*SPEAKER_LINKS, "https://talks.priya.dev"]), PERSON_ID),
    )
    request = request_for(database)

    await _update_account_profile(
        account_update(linkedin_url="https://www.linkedin.com/in/priya"), request
    )

    # The rival's write survives untouched; the account save itself still landed.
    assert person(connection)["links_json"] == [*SPEAKER_LINKS, "https://talks.priya.dev"]
    assert person_name_and_version(connection)[1] == 2
    user = connection.execute(
        "SELECT linkedin_url,version FROM users WHERE id=?", (USER_ID,)
    ).fetchone()
    assert (user["linkedin_url"], user["version"]) == ("https://www.linkedin.com/in/priya", 2)
    # Standing down is correct but must not be invisible: the zero-row mirror is
    # flagged on the request's completion record, where an operator can find it.
    assert request.state.degradations == ["account_person_mirror_skipped"]


async def test_a_mirror_that_lands_records_no_degradation(
    profile_database, signed_in_speaker
) -> None:
    connection, database = profile_database
    request = request_for(database)

    await _update_account_profile(
        account_update(linkedin_url="https://www.linkedin.com/in/priya"), request
    )

    assert person_name_and_version(connection)[1] == 2
    assert getattr(request.state, "degradations", []) == []


class ShapeDriftingSqlite(AsyncSqlite):
    """Executes the batch faithfully but reports results in an unrecognised shape.

    This is what a D1 result-format change would look like from the handler's
    side: the writes land, the row counts become unreadable.
    """

    async def batch(self, statements):
        results = await super().batch(statements)
        return [{"success": entry["success"]} for entry in results]


async def test_an_unreadable_batch_result_is_reported_not_presumed_successful(
    profile_database, signed_in_speaker
) -> None:
    """Shape drift must not mute the signal.

    A None row count cannot be settled by a follow-up read -- a rival write
    leaves the same bumped version the mirror would -- so the handler's honest
    answer is a distinct degradation code, not silence. If the provider's result
    shape ever drifts, every account save records it and the drift is caught the
    day it ships rather than the month the first lost mirror is noticed.
    """
    connection, plain = profile_database
    request = request_for(ShapeDriftingSqlite(plain.connection))

    await _update_account_profile(
        account_update(linkedin_url="https://www.linkedin.com/in/priya"), request
    )

    # The save itself was fine -- only the verification was blinded.
    assert person(connection)["links_json"] == [
        *SPEAKER_LINKS,
        "https://www.linkedin.com/in/priya",
    ]
    assert person_name_and_version(connection)[1] == 2
    assert request.state.degradations == ["account_person_mirror_unverified"]

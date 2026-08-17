"""The round version contract: one editor at a time wins, and losers leave no residue.

Three defects shared one root. The Start review button sent a cookie-authenticated
POST with no JSON content type, so the mutation guard refused it before round logic
ran -- rounds stayed Draft and every reviewer-facing step behind them never started.
Draft saves read the round, its memberships and its assignments with nothing tying
those reads to their writes, so concurrent editors merged into a matrix neither
requested and a save could race Open or Close past its status check. And a draft
save had no idempotency contract at all, so a retried request raced itself.

What is pinned:

* A raw bodyless POST to open is still a 403 from the mutation guard -- the browser
  defect -- while the corrected request opens the round.
* Every round mutation carries ``expected_version``; absent or stale tokens get an
  actionable 409 naming the reload, not a validation error or a silent merge.
* The compare-and-set plus write guard aborts the WHOLE batch when another writer
  won between this request's reads and its writes: no membership change, no
  assignment, no audit record, no queued mail, no idempotency row.
* Two interleaved opens produce exactly one transition, one audit record and one
  notification batch.
* A draft PUT replays its exact self under the same idempotency key even though
  the key now carries a stale version; a different payload under that key is
  refused.
"""

import asyncio
from types import SimpleNamespace

import pytest

from sessionbuddy.platform.db.d1 import PersistenceError, execute_batch
from tests.evaluation.test_draft_round_notification_timing import (
    SAM_USER_ID,
    A,
    B,
    _admin_user_id,
    _create_round,
    _key,
    _organizer,
    _round_body,
    _seed_reviewer,
    _seed_submissions,
)
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401 - pytest fixture
)


class _SQLiteStatement:
    def __init__(self, connection, sql: str) -> None:
        self.connection = connection
        self.sql = sql
        self.parameters: tuple = ()

    def bind(self, *values):
        self.parameters = values
        return self

    async def first(self, column: str | None = None):
        row = self.connection.execute(self.sql, self.parameters).fetchone()
        if row is None:
            return None
        if column is not None:
            return row[column]
        return dict(row)

    async def all(self):
        rows = [dict(row) for row in self.connection.execute(self.sql, self.parameters).fetchall()]
        return {"results": rows}


class SQLiteD1:
    def __init__(self, connection) -> None:
        self.connection = connection

    def prepare(self, sql: str) -> _SQLiteStatement:
        return _SQLiteStatement(self.connection, sql)

    async def batch(self, statements: list[_SQLiteStatement]):
        results = []
        try:
            self.connection.execute("BEGIN")
            for statement in statements:
                cursor = self.connection.execute(statement.sql, statement.parameters)
                entry: dict[str, object] = {"meta": {"changes": max(0, cursor.rowcount)}}
                if cursor.description is not None:
                    entry["results"] = [dict(row) for row in cursor.fetchall()]
                results.append(entry)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return results


class GatedRoundWriteD1(SQLiteD1):
    """Pause one round mutation after its reads but before its batch.

    The gate opens on the batch holding the endpoint's compare-and-set UPDATE,
    which is exactly where a real D1 deployment would interleave a competing
    request: every read has happened, nothing has been written yet.
    """

    def __init__(self, connection, cas_fragment: str, read_fragment: str) -> None:
        super().__init__(connection)
        self.read_seen = asyncio.Event()
        self.allow_batch = asyncio.Event()
        self.cas_fragment = cas_fragment
        self.read_fragment = read_fragment

    def prepare(self, sql: str) -> _SQLiteStatement:
        statement = super().prepare(sql)

        if self.read_fragment in sql:
            original_first = statement.first

            async def first(column: str | None = None):
                result = await original_first(column)
                self.read_seen.set()
                return result

            statement.first = first
        return statement

    async def batch(self, statements: list[_SQLiteStatement]):
        gated = any(self.cas_fragment in statement.sql for statement in statements)
        if gated:
            await self.allow_batch.wait()
        return await super().batch(statements)


def _version(connection, round_id: str) -> int:
    return connection.execute(
        "SELECT version FROM evaluation_rounds WHERE id=?", (round_id,)
    ).fetchone()[0]


def _mutation_headers(csrf: str, key: str) -> dict[str, str]:
    return {
        "origin": "https://test",
        "x-csrf-token": csrf,
        "idempotency-key": key,
    }


async def _setup_event(client, connection):
    csrf, organization_id, event_id = await _organizer(client, connection)
    _seed_submissions(connection, organization_id, event_id)
    _seed_reviewer(connection, organization_id, event_id, _admin_user_id(connection))
    return csrf, organization_id, event_id


DRAFT_CAS = "UPDATE evaluation_rounds SET name="
OPEN_CAS = "UPDATE evaluation_rounds SET status = 'open'"
DRAFT_READ = "review_closes_at_ms,version"
ROUND_READ = "SELECT id, organization_id, event_id, status"


def _racing_environment(environment, connection, cas_fragment: str, read_fragment: str):
    """A COPY of the ASGI environment whose DB pauses one mutation mid-flight.

    The copy is load-bearing. ``environment`` is a shared SimpleNamespace and
    every client injects it per request; swapping ``.DB`` on the original would
    gate the competing request too, parking both tasks forever. The CFP suite's
    gated tests use the same copy trick.
    """
    racing = SimpleNamespace(**vars(environment))
    racing.DB = GatedRoundWriteD1(connection, cas_fragment, read_fragment)
    return racing


async def test_a_bodyless_open_is_refused_by_the_mutation_guard_and_the_correct_request_opens(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The browser defect: a cookie-authenticated POST without a JSON media type.

    The old test helpers all sent ``json={}``, so the suite exercised the guard's
    happy path while the real client -- which sent neither a content type nor a
    body -- collected a bare 403 before authorization or round logic ever ran,
    leaving configured rounds stuck in Draft.
    """
    connection, queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _setup_event(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="draft"
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]

        # Exactly what the old browser code put on the wire: origin and CSRF
        # token present, no content type, no body.
        raw = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/open",
            headers={"origin": "https://test", "x-csrf-token": csrf},
        )
        assert raw.status_code == 403, raw.text
        assert connection.execute(
            "SELECT status FROM evaluation_rounds WHERE id=?", (round_id,)
        ).fetchone()[0] == "draft"

        opened = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/open",
            headers=_mutation_headers(csrf, _key("open")),
            json={"expected_version": _version(connection, round_id)},
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["status"] == "open"
        assert opened.json()["version"] == 2

    # The transition happened once, and opening -- not drafting -- is what told
    # the reviewer about the work.
    assert len(queue.messages) >= 1


async def test_missing_version_tokens_get_an_actionable_409_not_a_silent_write(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """An unbuilt client must be told to reload, not guessed a version."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _setup_event(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="draft"
        )
        round_id = created.json()["id"]
        body = {**_round_body(status="draft", submission_ids=[A]), "name": "Renamed"}

        missing = await client.put(
            f"/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft",
            headers=_mutation_headers(csrf, _key("no-version")),
            json=body,
        )
        assert missing.status_code == 409, missing.text
        assert missing.headers["x-conflict-type"] == "round-version"
        assert "Reload" in missing.json()["error"]["message"]

        no_token_open = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/open",
            headers=_mutation_headers(csrf, _key("no-version-open")),
            json={},
        )
        assert no_token_open.status_code == 409
        assert no_token_open.headers["x-conflict-type"] == "round-version"

        no_token_evaluator = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/evaluators",
            headers=_mutation_headers(csrf, _key("no-version-evaluator")),
            json={"evaluator_user_id": SAM_USER_ID, "submission_ids": [A]},
        )
        assert no_token_evaluator.status_code == 409

        no_token_additions = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/submissions",
            headers=_mutation_headers(csrf, _key("no-version-submissions")),
            json={"submission_ids": [B]},
        )
        assert no_token_additions.status_code == 409

        no_token_removal = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/evaluators/{SAM_USER_ID}/remove",
            headers=_mutation_headers(csrf, _key("no-version-remove")),
            json={},
        )
        assert no_token_removal.status_code == 409

        close_without_version = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/close",
            headers=_mutation_headers(csrf, _key("no-version-close")),
            json={"force": True, "reason": "Not without a version token."},
        )
        assert close_without_version.status_code == 409

    # Nothing moved: same name, same version, no audit trail for the refusals.
    row = connection.execute(
        "SELECT name,status,version FROM evaluation_rounds WHERE id=?", (round_id,)
    ).fetchone()
    assert tuple(row) == ("Initial review", "draft", 1)
    assert (
        connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] > 0
    )  # creation audited
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action LIKE 'evaluation_round.%' "
            "AND action != 'evaluation_round.create'"
        ).fetchone()[0]
        == 0
    )


async def test_a_stale_draft_save_loses_atomically_and_names_the_reload(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Two editors, one round: the loser's diff must never merge with the winner's."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _setup_event(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="draft"
        )
        round_id = created.json()["id"]
        stale_version = _version(connection, round_id)

        winner = await client.put(
            f"/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft",
            headers=_mutation_headers(csrf, _key("winner")),
            json={
                **_round_body(status="draft"),
                "name": "Winner's matrix",
                "assignments": [{"submission_id": A, "evaluator_user_id": SAM_USER_ID}],
                "expected_version": stale_version,
            },
        )
        assert winner.status_code == 200, winner.text
        assert winner.json()["version"] == stale_version + 1

        loser = await client.put(
            f"/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft",
            headers=_mutation_headers(csrf, _key("loser")),
            json={
                **_round_body(status="draft"),
                "name": "Loser's matrix",
                "assignments": [{"submission_id": B, "evaluator_user_id": SAM_USER_ID}],
                "expected_version": stale_version,
            },
        )
        assert loser.status_code == 409, loser.text
        assert loser.headers["x-conflict-type"] == "round-version"

    stored_name, live_pairs = connection.execute(
        """SELECT r.name,
                  (SELECT COUNT(*) FROM evaluation_assignments a
                    WHERE a.round_id=r.id AND a.status!='revoked')
             FROM evaluation_rounds r WHERE r.id=?""",
        (round_id,),
    ).fetchone()
    assert stored_name == "Winner's matrix"
    assert live_pairs == 1
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='evaluation_round.update'",
            (),
        ).fetchone()[0]
        == 1
    )
    # The losing request left no idempotency residue a replay could resurrect.
    assert (
        connection.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0]
        == 2  # create + winner's save
    )


async def test_the_write_guard_rolls_back_every_statement_after_a_lost_cas(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Exercise the guard itself, not the route's preliminary stale-version read."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup_event(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="guarded-draft"
        )
        round_id = created.json()["id"]

    db = SQLiteD1(connection)
    before_name, before_version = connection.execute(
        "SELECT name,version FROM evaluation_rounds WHERE id=?", (round_id,)
    ).fetchone()
    audit_before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    idempotency_before = connection.execute(
        "SELECT COUNT(*) FROM idempotency_records"
    ).fetchone()[0]
    statements = [
        db.prepare(
            "INSERT INTO idempotency_records "
            "(id,principal_key,route_key,idempotency_key_hash,request_fingerprint,state,"
            "created_at_ms,expires_at_ms) VALUES('lost-cas-key','organizer','test-route',"
            "X'01',X'02','in_progress',1900000000000,1900000001000)"
        ),
        db.prepare(
            "UPDATE evaluation_rounds SET name='must-not-land',version=version+1 "
            "WHERE id=?1 AND version=?2"
        ).bind(round_id, before_version + 99),
        db.prepare(
            "INSERT INTO evaluation_round_write_guards "
            "(id,round_id,applied_changes,created_at_ms) "
            "VALUES('lost-cas-guard',?1,changes(),1900000000000)"
        ).bind(round_id),
        db.prepare(
            "UPDATE evaluation_round_evaluators SET status='removed' WHERE round_id=?1"
        ).bind(round_id),
        db.prepare(
            "INSERT INTO audit_events "
            "(id,actor_type,action,target_type,result,correlation_id,metadata_json,occurred_at_ms) "
            "VALUES('lost-cas-audit','system','evaluation_round.update',"
            "'evaluation_round','succeeded','lost-cas','{}',1900000000000)"
        ),
    ]

    with pytest.raises(PersistenceError):
        await execute_batch(db, statements)

    assert tuple(
        connection.execute(
            "SELECT name,version FROM evaluation_rounds WHERE id=?", (round_id,)
        ).fetchone()
    ) == (before_name, before_version)
    assert connection.execute(
        "SELECT COUNT(*) FROM evaluation_round_write_guards WHERE id='lost-cas-guard'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM evaluation_round_evaluators "
        "WHERE round_id=? AND status='removed'",
        (round_id,),
    ).fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == audit_before
    assert connection.execute(
        "SELECT COUNT(*) FROM idempotency_records"
    ).fetchone()[0] == idempotency_before


async def test_two_concurrent_open_requests_emit_one_audit_and_notification(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Different keys may race, but only one open lands -- the loser gets the 409.

    The racing request is paused by its own D1 double after every read has run
    but before its compare-and-set batch executes, which is precisely where a
    real deployment interleaves. The winner commits through the unmodified
    environment; when the loser's batch finally runs, its UPDATE matches zero
    rows, the write guard aborts it, and the route reports the version conflict.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup_event(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="concurrent-open"
        )
        round_id = created.json()["id"]
        version = _version(connection, round_id)
        racing_environment = _racing_environment(environment, connection, OPEN_CAS, ROUND_READ)

        async with _client(racing_environment) as slower_click:
            for cookie in client.cookies.jar:
                slower_click.cookies.set(cookie.name, cookie.value, domain=cookie.domain)
            losing = asyncio.create_task(
                slower_click.post(
                    f"/api/v1/admin/evaluation-rounds/{round_id}/open",
                    headers=_mutation_headers(csrf, _key("open-loser")),
                    json={"expected_version": version},
                )
            )
            await asyncio.wait_for(racing_environment.DB.read_seen.wait(), 10)

            # The competing click completes while the loser sits paused in front
            # of its batch: reads saw a draft, the write meets an open round.
            winner = await client.post(
                f"/api/v1/admin/evaluation-rounds/{round_id}/open",
                headers=_mutation_headers(csrf, _key("open-winner")),
                json={"expected_version": version},
            )
            assert winner.status_code == 200, winner.text

            racing_environment.DB.allow_batch.set()
            refused = await asyncio.wait_for(losing, 10)

        assert refused.status_code == 409, refused.text
        assert refused.headers["x-conflict-type"] == "round-version"

    assert tuple(
        connection.execute(
            "SELECT status,version FROM evaluation_rounds WHERE id=?", (round_id,)
        ).fetchone()
    ) == ("open", version + 1)
    assert connection.execute(
        "SELECT COUNT(*) FROM audit_events WHERE action='evaluation_round.open' AND target_id=?",
        (round_id,),
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM communication_messages WHERE deterministic_key LIKE ?",
        (f"%{round_id}%:round-opened",),
    ).fetchone()[0] == 1
    # The loser's rolled-back batch left no idempotency residue either.
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM idempotency_records "
            "WHERE organization_id=(SELECT organization_id FROM evaluation_rounds WHERE id=?) "
            "AND event_id=(SELECT event_id FROM evaluation_rounds WHERE id=?) "
            "AND state='completed'",
            (round_id, round_id),
        ).fetchone()[0]
        == 2  # the round's create plus the winning open
    )


async def test_a_save_that_races_another_writer_aborts_inside_the_batch(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The write guard, not luck: a loser interleaved mid-batch rolls back whole.

    The losing save is paused by its own D1 double after every read has run but
    before its compare-and-set batch executes. The winner commits through the
    unmodified environment; when the loser's batch finally runs, the guard's
    CHECK (applied_changes = 1) fires and every statement after the lost UPDATE
    -- membership, assignments, audit, idempotency -- rolls back with it.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _setup_event(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="draft"
        )
        round_id = created.json()["id"]
        version = _version(connection, round_id)
        racing_environment = _racing_environment(
            environment, connection, DRAFT_CAS, DRAFT_READ
        )

        async with _client(racing_environment) as second_tab:
            for cookie in client.cookies.jar:
                second_tab.cookies.set(cookie.name, cookie.value, domain=cookie.domain)
            losing = asyncio.create_task(
                second_tab.put(
                    f"/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft",
                    headers=_mutation_headers(csrf, _key("interleaved-loser")),
                    json={
                        **_round_body(status="draft"),
                        "name": "Interleaved loser",
                        "expected_version": version,
                    },
                )
            )
            await asyncio.wait_for(racing_environment.DB.read_seen.wait(), 10)

            # The competing save lands while the loser sits paused in front of
            # its batch.
            winner = await client.put(
                f"/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft",
                headers=_mutation_headers(csrf, _key("interleaved-winner")),
                json={
                    **_round_body(status="draft"),
                    "name": "Interleaved winner",
                    "expected_version": version,
                },
            )
            assert winner.status_code == 200, winner.text

            racing_environment.DB.allow_batch.set()
            lost = await asyncio.wait_for(losing, 10)

        assert lost.status_code == 409, lost.text
        assert lost.headers["x-conflict-type"] == "round-version"

    name = connection.execute(
        "SELECT name FROM evaluation_rounds WHERE id=?", (round_id,)
    ).fetchone()[0]
    assert name == "Interleaved winner"
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='evaluation_round.update'"
        ).fetchone()[0]
        == 1
    )
    # Exactly one guard row survived -- the winner's. The loser's rolled back.
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM evaluation_round_write_guards g "
            "JOIN evaluation_rounds r ON r.id=g.round_id WHERE r.id=? AND g.applied_changes=1",
            (round_id,),
        ).fetchone()[0]
        == 1
    )
    # And no idempotency row for the loser's key: its begin rolled back too.
    assert (
        connection.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0]
        == 2  # the round's create plus the winning save
    )


async def test_a_draft_put_replays_itself_before_its_version_can_go_stale(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Retry semantics: exact replay wins over staleness; changed payloads do not."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _setup_event(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="draft"
        )
        round_id = created.json()["id"]
        endpoint = f"/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft"
        fetched = await client.get(endpoint)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["version"] == _version(connection, round_id)
        payload = {
            **_round_body(status="draft"),
            "name": "Retried save",
            "expected_version": _version(connection, round_id),
        }
        key = _key("retried-save")

        first = await client.put(endpoint, headers=_mutation_headers(csrf, key), json=payload)
        assert first.status_code == 200, first.text
        version_after_first = first.json()["version"]
        audits_after_first = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='evaluation_round.update'"
        ).fetchone()[0]

        # The same key and the same payload: the retry replays even though the
        # version it carries has just been spent.
        replay = await client.put(endpoint, headers=_mutation_headers(csrf, key), json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["version"] == version_after_first
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_events WHERE action='evaluation_round.update'"
            ).fetchone()[0]
            == audits_after_first
        )

        # Same key, different data: refuse rather than guess which save was meant.
        divergent = await client.put(
            endpoint,
            headers=_mutation_headers(csrf, key),
            json={**payload, "name": "Different save"},
        )
        assert divergent.status_code == 409, divergent.text


async def test_add_evaluator_assignment_replays_after_a_lost_response(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """A committed assignment retry returns its first result instead of a stale 409."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup_event(client, connection)
        body = {
            **_round_body(status="draft"),
            "assignments": [{"submission_id": A, "evaluator_user_id": SAM_USER_ID}],
        }
        created = await _create_round(client, csrf, event_id, body, label="retry-add-round")
        round_id = created.json()["id"]
        key = _key("retry-add-evaluator")
        payload = {
            "evaluator_user_id": SAM_USER_ID,
            "submission_ids": [B],
            "expected_version": _version(connection, round_id),
        }
        endpoint = f"/api/v1/admin/evaluation-rounds/{round_id}/evaluators"

        first = await client.post(
            endpoint, headers=_mutation_headers(csrf, key), json=payload
        )
        assert first.status_code == 200, first.text
        version_after_first = _version(connection, round_id)
        replay = await client.post(
            endpoint, headers=_mutation_headers(csrf, key), json=payload
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert _version(connection, round_id) == version_after_first
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE action='evaluation_round.evaluator.add' AND target_id=?",
            (round_id,),
        ).fetchone()[0] == 1


async def test_remove_evaluator_replays_without_repeating_side_effects(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """A lost remove response replays once; divergent reuse is refused."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup_event(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="retry-remove-round"
        )
        round_id = created.json()["id"]
        connection.execute(
            "UPDATE evaluation_assignments SET status='revoked' "
            "WHERE round_id=? AND evaluator_user_id=?",
            (round_id, SAM_USER_ID),
        )
        connection.commit()
        version = _version(connection, round_id)
        payload = {"expected_version": version}
        key = _key("retry-remove-evaluator")
        endpoint = (
            f"/api/v1/admin/evaluation-rounds/{round_id}/evaluators/"
            f"{SAM_USER_ID}/remove"
        )

        first = await client.post(
            endpoint, headers=_mutation_headers(csrf, key), json=payload
        )
        assert first.status_code == 200, first.text
        assert _version(connection, round_id) == version + 1
        replay = await client.post(
            endpoint, headers=_mutation_headers(csrf, key), json=payload
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert _version(connection, round_id) == version + 1
        assert connection.execute(
            "SELECT status FROM evaluation_round_evaluators "
            "WHERE round_id=? AND evaluator_user_id=?",
            (round_id, SAM_USER_ID),
        ).fetchone()[0] == "removed"
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE action='evaluation_round.evaluator.remove' AND target_id=?",
            (round_id,),
        ).fetchone()[0] == 1

        divergent = await client.post(
            endpoint,
            headers=_mutation_headers(csrf, key),
            json={"expected_version": version + 1},
        )
        assert divergent.status_code == 409, divergent.text

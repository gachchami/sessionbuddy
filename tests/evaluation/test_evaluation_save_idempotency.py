"""Evaluation saves return canonical state and replay as one semantic write."""

import asyncio
import hashlib

from tests.evaluation.test_round_selection_persistence import (
    SAM_EMAIL,
    SAM_USER_ID,
    A,
    B,
    _client,
    _create_round,
    _round_body,
    _setup,
    _sign_in,
    production_environment,  # noqa: F401 - pytest fixture
)
from tests.security.test_production_identity_flow import SQLiteD1


class SameBaseVersionSqlite(SQLiteD1):
    """Let two requests finish their reads before either write batch executes."""

    def __init__(self, connection) -> None:
        super().__init__(connection)
        self._arrived = 0
        self._both_arrived = asyncio.Event()
        self._first_finished = asyncio.Event()

    async def batch(self, statements):
        self._arrived += 1
        position = self._arrived
        if position == 1:
            await self._both_arrived.wait()
            try:
                return await super().batch(statements)
            finally:
                self._first_finished.set()
        if position == 2:
            self._both_arrived.set()
            await self._first_finished.wait()
        return await super().batch(statements)


async def test_final_evaluation_replays_after_close_and_returns_canonical_fields(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        organizer_csrf, _organization_id, event_id = await _setup(client, connection)
        created = await _create_round(
            client,
            organizer_csrf,
            event_id,
            {
                "name": "Canonical review",
                "rating_min": 1,
                "rating_max": 5,
                "recommendations": ["accept", "reject"],
                "criteria": [
                    {
                        "key": "quality",
                        "label": "Quality",
                        "response_type": "score",
                        "weight": 100,
                    },
                    {
                        "key": "verdict",
                        "label": "Verdict",
                        "response_type": "select",
                        "weight": None,
                        "options": ["accept", "reject"],
                        "purpose": "recommendation",
                    },
                    {
                        "key": "notes",
                        "label": "Notes",
                        "response_type": "text",
                        "weight": None,
                        "purpose": "comment",
                    },
                ],
                "blind_review": False,
                "submission_ids": [A],
                "evaluator_user_ids": ["dddddddd-3333-4333-8333-dddddddddddd"],
                "assignments": [
                    {
                        "submission_id": A,
                        "evaluator_user_id": "dddddddd-3333-4333-8333-dddddddddddd",
                    }
                ],
                "assignment_strategy": "all",
                "status": "open",
            },
            label="canonical-final",
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]
        assignment_id = connection.execute(
            "SELECT id FROM evaluation_assignments WHERE round_id=?", (round_id,)
        ).fetchone()[0]

        async with _client(environment) as anonymous:
            missing_identity = await anonymous.put(
                f"/api/v1/evaluator/assignments/{assignment_id}/evaluation",
                json={
                    "rating": 4,
                    "recommendation": "accept",
                    "internal_comment": "",
                    "criterion_responses": {},
                    "state": "final",
                },
            )
        assert missing_identity.status_code == 401, missing_identity.text

        reviewer = await _sign_in(client, connection, SAM_EMAIL)
        payload = {
            "rating": None,
            "recommendation": "reject",
            "internal_comment": "stale legacy fields",
            "criterion_responses": {
                "quality": 4,
                "verdict": "accept",
                "notes": "Canonical reviewer note.",
            },
            "state": "final",
        }
        headers = {
            "origin": "https://test",
            "x-csrf-token": reviewer["csrf_token"],
            "idempotency-key": "first-client-finalization-key",
        }
        saved = await client.put(
            f"/api/v1/evaluator/assignments/{assignment_id}/evaluation",
            headers=headers,
            json=payload,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["recommendation"] == "accept"
        assert saved.json()["internal_comment"] == "Canonical reviewer note."
        semantic_record = connection.execute(
            """SELECT state, response_resource_id FROM idempotency_records
               WHERE idempotency_key_hash=?""",
            (hashlib.sha256(f"evaluation-save:{assignment_id}:v1".encode()).digest(),),
        ).fetchone()
        assert tuple(semantic_record) == ("completed", saved.json()["id"])

        replay_without_csrf = await client.put(
            f"/api/v1/evaluator/assignments/{assignment_id}/evaluation",
            headers={"idempotency-key": "replay-without-mutation-guard"},
            json=payload,
        )
        assert replay_without_csrf.status_code == 403, replay_without_csrf.text
        replay_without_key = await client.put(
            f"/api/v1/evaluator/assignments/{assignment_id}/evaluation",
            headers={"origin": "https://test", "x-csrf-token": reviewer["csrf_token"]},
            json=payload,
        )
        assert replay_without_key.status_code == 400, replay_without_key.text

        connection.execute(
            "UPDATE evaluation_rounds SET status='closed', closed_at_ms=2000 WHERE id=?",
            (round_id,),
        )
        connection.commit()
        replayed = await client.put(
            f"/api/v1/evaluator/assignments/{assignment_id}/evaluation",
            headers={**headers, "idempotency-key": "different-client-retry-key"},
            json=payload,
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json() == saved.json()

        changed = await client.put(
            f"/api/v1/evaluator/assignments/{assignment_id}/evaluation",
            headers={**headers, "idempotency-key": "conflicting-client-final-key"},
            json={
                **payload,
                "criterion_responses": {
                    **payload["criterion_responses"],
                    "quality": 2,
                },
            },
        )
        assert changed.status_code == 409, changed.text
        assert "already saved with different answers" in changed.json()["error"]["message"]
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='evaluation.final'"
        ).fetchone()[0] == 1


async def test_sequential_drafts_receive_new_semantic_versions(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        organizer_csrf, _organization_id, event_id = await _setup(client, connection)
        created = await _create_round(
            client,
            organizer_csrf,
            event_id,
            _round_body(status="open", assignments=[A], submission_ids=[A]),
            label="sequential-drafts",
        )
        assert created.status_code == 201, created.text
        assignment_id = connection.execute(
            "SELECT id FROM evaluation_assignments WHERE round_id=?", (created.json()["id"],)
        ).fetchone()[0]
        reviewer = await _sign_in(client, connection, SAM_EMAIL)
        headers = {
            "origin": "https://test",
            "x-csrf-token": reviewer["csrf_token"],
            "idempotency-key": "draft-client-key-one",
        }
        body = {
            "rating": 4,
            "recommendation": "accept",
            "internal_comment": "First draft.",
            "criterion_responses": {},
            "state": "draft",
        }
        first = await client.put(
            f"/api/v1/evaluator/assignments/{assignment_id}/evaluation",
            headers=headers,
            json=body,
        )
        assert first.status_code == 200, first.text
        second = await client.put(
            f"/api/v1/evaluator/assignments/{assignment_id}/evaluation",
            headers={**headers, "idempotency-key": "draft-client-key-two"},
            json={**body, "internal_comment": "Second draft."},
        )
        assert second.status_code == 200, second.text
        assert (first.json()["version"], second.json()["version"]) == (1, 2)
        assert second.json()["internal_comment"] == "Second draft."
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='evaluation.draft'"
        ).fetchone()[0] == 2
        assert connection.execute(
            """SELECT COUNT(*) FROM idempotency_records
               WHERE principal_key=? AND route_key LIKE 'PUT /api/v1/evaluator/%'
                 AND state='completed'""",
            (SAM_USER_ID,),
        ).fetchone()[0] == 2


async def test_same_base_draft_race_replays_exact_payload_and_rejects_changed_payload(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        organizer_csrf, _organization_id, event_id = await _setup(client, connection)
        created = await _create_round(
            client,
            organizer_csrf,
            event_id,
            _round_body(status="open", assignments=[A, B]),
            label="same-base-races",
        )
        assert created.status_code == 201, created.text
        assignment_ids = [
            connection.execute(
                """SELECT id FROM evaluation_assignments
                   WHERE round_id=? AND submission_id=?""",
                (created.json()["id"], submission_id),
            ).fetchone()[0]
            for submission_id in (A, B)
        ]
        reviewer = await _sign_in(client, connection, SAM_EMAIL)
        headers = {
            "origin": "https://test",
            "x-csrf-token": reviewer["csrf_token"],
        }
        body = {
            "rating": 4,
            "recommendation": "accept",
            "internal_comment": "Concurrent draft.",
            "criterion_responses": {},
            "state": "draft",
        }

        environment.DB = SameBaseVersionSqlite(connection)
        exact = await asyncio.gather(
            client.put(
                f"/api/v1/evaluator/assignments/{assignment_ids[0]}/evaluation",
                headers={**headers, "idempotency-key": "exact-one".ljust(32, "0")},
                json=body,
            ),
            client.put(
                f"/api/v1/evaluator/assignments/{assignment_ids[0]}/evaluation",
                headers={**headers, "idempotency-key": "exact-two".ljust(32, "0")},
                json=body,
            ),
        )
        assert [response.status_code for response in exact] == [200, 200]
        assert exact[0].json() == exact[1].json()

        environment.DB = SameBaseVersionSqlite(connection)
        changed = await asyncio.gather(
            client.put(
                f"/api/v1/evaluator/assignments/{assignment_ids[1]}/evaluation",
                headers={**headers, "idempotency-key": "changed-one".ljust(32, "0")},
                json=body,
            ),
            client.put(
                f"/api/v1/evaluator/assignments/{assignment_ids[1]}/evaluation",
                headers={**headers, "idempotency-key": "changed-two".ljust(32, "0")},
                json={**body, "internal_comment": "Different concurrent draft."},
            ),
        )
        assert sorted(response.status_code for response in changed) == [200, 409]
        conflict = next(response for response in changed if response.status_code == 409)
        assert "already saved with different answers" in conflict.json()["error"]["message"]
        for assignment_id in assignment_ids:
            assert connection.execute(
                """SELECT COUNT(*) FROM audit_events audit
                   JOIN evaluations evaluation ON evaluation.id=audit.target_id
                   WHERE audit.action='evaluation.draft'
                     AND evaluation.assignment_id=?""",
                (assignment_id,),
            ).fetchone()[0] == 1

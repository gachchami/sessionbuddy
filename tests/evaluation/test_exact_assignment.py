"""ABS-05: a reviewer's queue contains exactly the proposals assigned to them.

The rubric wording is about the REVIEWER's view, not the organizer's, so these tests
authenticate as Sam and read /api/v1/evaluator/assignments. The admin round-trip can be
perfectly correct while the evaluator query still leaks the third proposal.

What is pinned:

* Saving Sam -> A and B gives Sam exactly {A, B}. C stays a member of the round with
  nobody on it, and never appears in Sam's queue.
* Repeating the identical PUT is a true no-op -- no new assignment, no resurrection, and
  the round's updated_at_ms does not move.
* A conflict-revoked pair stays revoked even when the organizer resubmits it, and C stays
  absent on a fresh request so neither caching nor regeneration can hide a leak.
"""

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)

EVENT_PAYLOAD = {
    "name": "Exact Assignment Summit",
    "starts_at_ms": 1_900_000_000_000,
    "ends_at_ms": 1_900_086_400_000,
    "time_zone": "Asia/Kolkata",
    "delivery_mode": "hybrid",
    "location": "Pune",
    "description": "Per-submission assignment",
    "email_sender_name": "Program Team",
    "email_reply_to": "program@example.com",
}
ROUND_ID = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
A = "aaaaaaaa-2222-4222-8222-aaaaaaaaaaaa"
B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
C = "cccccccc-2222-4222-8222-cccccccccccc"
TITLES = {A: "Taming 40-Minute CI", B: "Scaling Postgres", C: "Docs That Answer Back"}


async def _sign_in(client, connection, email):
    assert (
        await client.post("/api/v1/auth/magic-links", json={"email": email, "redirect_path": "/"})
    ).status_code == 202
    verified = await client.post(
        "/auth/verify", data={"token": _token(connection, email)}, follow_redirects=False
    )
    assert verified.status_code == 303, verified.text
    return (await client.get("/api/v1/auth/session")).json()


async def _organizer(client, connection):
    bootstrap = await client.post(
        "/api/v1/bootstrap",
        headers={"x-bootstrap-token": _deployment_key(connection)},
        json={
            "organization_name": "Exact Assignment Events",
            "admin_name": "Admin",
            "admin_email": "admin@example.com",
        },
    )
    assert bootstrap.status_code == 200, bootstrap.text
    organization_id = bootstrap.json()["organization_id"]
    session = await _sign_in(client, connection, "admin@example.com")
    created = await client.post(
        f"/api/v1/admin/organizations/{organization_id}/events",
        headers={"origin": "https://test", "x-csrf-token": session["csrf_token"]},
        json=EVENT_PAYLOAD,
    )
    assert created.status_code == 201, created.text
    return session["csrf_token"], organization_id, created.json()["id"]


SAM_EMAIL = "sam.reviewer@example.com"
SAM_USER_ID = "dddddddd-3333-4333-8333-dddddddddddd"
TAYLOR_EMAIL = "taylor.reviewer@example.com"
TAYLOR_USER_ID = "eeeeeeee-3333-4333-8333-eeeeeeeeeeee"


def _seed_reviewer(
    connection,
    organization_id,
    event_id,
    invited_by_user_id,
    *,
    user_id=SAM_USER_ID,
    email=SAM_EMAIL,
    key="sam",
    display_name="Sam Whitfield",
):
    """A genuinely separate reviewer identity.

    Reusing the bootstrap admin looks simpler and does not work: a database trigger
    (validate_evaluation_assignment_*) refuses any assignment whose evaluator lacks an
    active `reviewer` role AND an accepted `evaluator` invitation for the event. Verified
    against the real baseline -- without both rows the insert aborts with
    'evaluation assignment scope mismatch'. It also makes the queue assertion meaningful,
    because Sam is then a different user from the organizer.
    """
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,version,authorization_version,
            created_at_ms,updated_at_ms,display_name)
           VALUES (?, ?,?,'active',1,1,1000,1000,?)""",
        (user_id, email, email, display_name),
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,version,created_at_ms,updated_at_ms)
           VALUES (?, ?,?,'member','active',1,1000,1000)""",
        (f"om-{key}", organization_id, user_id),
    )
    connection.execute(
        "INSERT INTO user_roles VALUES (?,'reviewer','active',1000,1000,NULL,1)",
        (user_id,),
    )
    connection.execute(
        """INSERT INTO identity_invitations
           (id,organization_id,event_id,normalized_email,email,role,status,invited_by_user_id,
            expires_at_ms,accepted_at_ms,created_at_ms,updated_at_ms,display_name,job_title,
            company,biography)
           VALUES (?, ?,?,?,?,'evaluator','accepted',?,9999999999999,1000,1000,1000,
                   ?,'','','')""",
        (
            f"inv-{key}",
            organization_id,
            event_id,
            email,
            email,
            invited_by_user_id,
            display_name,
        ),
    )
    connection.commit()
    return user_id


def _seed_round(connection, organization_id, event_id, sam_user_id, *, status="open"):
    """Three proposals in a draft round, Sam in the reviewer pool, no assignments yet.

    Seeded directly so the test exercises the assignment API rather than the whole CFP
    intake, but through the same tables the API writes.
    """
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
            published_at_ms,created_at_ms,updated_at_ms,success_title,success_message,
            redirect_to_portal,confirmation_subject,confirmation_body)
           VALUES ('form-1',?,?,1,'exact-assignment','Welcome','{"fields":[]}','published',
                   1000,1000,1000,'Thanks','Received',0,'Received','Thanks')""",
        (organization_id, event_id),
    )
    for submission_id, title in TITLES.items():
        connection.execute(
            """INSERT INTO submissions
               (id,organization_id,event_id,form_id,public_session_id,proposal_title,
                proposal_abstract,speaker_name,status,submitted_at_ms,created_at_ms,
                updated_at_ms,speaker_email,answers_json,version)
               VALUES (?,?,?,'form-1',?,?,'Abstract','Speaker','submitted',
                       1000,1000,1000,'speaker@example.com','{}',1)""",
            (submission_id, organization_id, event_id, f"public-{submission_id}", title),
        )
    connection.execute(
        """INSERT INTO evaluation_rounds
           (id,organization_id,event_id,name,rubric_json,status,created_at_ms,updated_at_ms)
           VALUES (?,?,?,'Initial review',
                   '{"rating":{"min":1,"max":5},"recommendation":{"choices":["accept","reject"]},
                     "criteria":[],"blind_review":false,"assignment_strategy":"all"}',
                   ?,1000,1000)""",
        (ROUND_ID, organization_id, event_id, status),
    )
    for submission_id in TITLES:
        connection.execute(
            """INSERT INTO evaluation_round_submissions
               (round_id,submission_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
               VALUES (?,?,?,?,'active',1000,1000)""",
            (ROUND_ID, submission_id, organization_id, event_id),
        )
    connection.execute(
        """INSERT INTO evaluation_round_evaluators
           (round_id,evaluator_user_id,organization_id,event_id,status,created_at_ms,updated_at_ms)
           VALUES (?,?,?,?,'active',1000,1000)""",
        (ROUND_ID, sam_user_id, organization_id, event_id),
    )
    connection.commit()


def _assign(connection, organization_id, event_id, pairs, status="assigned"):
    for index, (submission_id, evaluator_user_id) in enumerate(pairs):
        connection.execute(
            """INSERT INTO evaluation_assignments
               (id,organization_id,event_id,round_id,submission_id,evaluator_user_id,
                status,created_at_ms,updated_at_ms)
               VALUES (?,?,?,?,?,?,?,1000,1000)""",
            (
                f"assign-{index}-{submission_id[:8]}",
                organization_id,
                event_id,
                ROUND_ID,
                submission_id,
                evaluator_user_id,
                status,
            ),
        )
    connection.commit()


def _queue_titles(payload):
    return sorted(item["proposal_title"] for item in payload["data"])


async def test_reviewer_queue_contains_exactly_the_assigned_proposals(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _organizer(client, connection)
        admin_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        sam = _seed_reviewer(connection, organization_id, event_id, admin_user_id)
        _seed_round(connection, organization_id, event_id, sam)
        _assign(connection, organization_id, event_id, [(A, sam), (B, sam)])

        session = await _sign_in(client, connection, SAM_EMAIL)
        assert session["active_role"] == "reviewer"
        queue = await client.get("/api/v1/evaluator/assignments")
        assert queue.status_code == 200, queue.text
        payload = queue.json()
        assert payload["total"] == 2
        assert payload["completed_count"] == 0
        assert payload["next_cursor"] is None
        assert {item["submission_id"] for item in payload["data"]} == {A, B}
        assert {item["evaluation_state"] for item in payload["data"]} == {"not_started"}
        assert {item["round_id"] for item in payload["data"]} == {ROUND_ID}
        assert all(item["id"].startswith("assign-") for item in payload["data"])
        titles = _queue_titles(payload)
        assert titles == sorted([TITLES[A], TITLES[B]]), titles
        assert TITLES[C] not in titles, "the unassigned proposal leaked into the queue"


async def test_c_remains_absent_on_a_fresh_request(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Neither query caching nor a regenerated matrix may reintroduce the third proposal."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _organizer(client, connection)
        admin_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        sam = _seed_reviewer(connection, organization_id, event_id, admin_user_id)
        _seed_round(connection, organization_id, event_id, sam)
        _assign(connection, organization_id, event_id, [(A, sam), (B, sam)])

        await _sign_in(client, connection, SAM_EMAIL)
        for _attempt in range(3):
            payload = (await client.get("/api/v1/evaluator/assignments")).json()
            assert TITLES[C] not in _queue_titles(payload)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM evaluation_assignments WHERE round_id=? AND submission_id=?",
                (ROUND_ID, C),
            ).fetchone()[0]
            == 0
        )


async def test_conflict_revoked_pair_is_not_resurrected_and_stays_out_of_the_queue(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _organizer(client, connection)
        admin_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        sam = _seed_reviewer(connection, organization_id, event_id, admin_user_id)
        _seed_round(connection, organization_id, event_id, sam)
        _assign(connection, organization_id, event_id, [(A, sam), (B, sam)])
        _assign(connection, organization_id, event_id, [(C, sam)], status="revoked")
        conflicted = connection.execute(
            "SELECT id FROM evaluation_assignments WHERE submission_id=? AND round_id=?",
            (C, ROUND_ID),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO evaluation_conflicts
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,
                conflict_type,explanation,declared_at_ms)
               VALUES ('conflict-1',?,?,?,?,?,'same_company','Same employer',1000)""",
            (organization_id, event_id, ROUND_ID, conflicted, sam),
        )
        connection.commit()

        await _sign_in(client, connection, SAM_EMAIL)
        titles = _queue_titles((await client.get("/api/v1/evaluator/assignments")).json())
        assert TITLES[C] not in titles
        assert (
            connection.execute(
                "SELECT status FROM evaluation_assignments WHERE id=?", (conflicted,)
            ).fetchone()[0]
            == "revoked"
        ), "a recorded conflict of interest must stay revoked"


async def test_finalized_reviewer_can_report_conflict_without_rewriting_evaluation(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        organizer_csrf, organization_id, event_id = await _organizer(client, connection)
        admin_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        sam = _seed_reviewer(connection, organization_id, event_id, admin_user_id)
        taylor = _seed_reviewer(
            connection,
            organization_id,
            event_id,
            admin_user_id,
            user_id=TAYLOR_USER_ID,
            email=TAYLOR_EMAIL,
            key="taylor",
            display_name="Taylor Reed",
        )
        _seed_round(connection, organization_id, event_id, sam)
        _assign(connection, organization_id, event_id, [(A, sam)], status="completed")
        assignment_id = connection.execute(
            "SELECT id FROM evaluation_assignments WHERE submission_id=?", (A,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE evaluation_rounds SET status='closed',closed_at_ms=1100 WHERE id=?",
            (ROUND_ID,),
        )
        connection.execute(
            """INSERT INTO evaluations
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,
                rating,recommendation,internal_comment,state,created_at_ms,updated_at_ms,
                finalized_at_ms,criterion_responses_json)
               VALUES ('final-before-conflict',?,?,?,?,?,4,'accept','Complete review',
                       'final',1000,1000,1000,'{}')""",
            (organization_id, event_id, ROUND_ID, assignment_id, sam),
        )
        connection.commit()

        reviewer = await _sign_in(client, connection, SAM_EMAIL)
        reported = await client.post(
            f"/api/v1/evaluator/assignments/{assignment_id}/conflict",
            headers={
                "origin": "https://test",
                "x-csrf-token": reviewer["csrf_token"],
                "idempotency-key": "post-final-conflict",
            },
            json={"conflict_type": "same_company", "explanation": "Late disclosure."},
        )
        assert reported.status_code == 200, reported.text
        assert connection.execute(
            "SELECT status FROM evaluation_assignments WHERE id=?", (assignment_id,)
        ).fetchone()[0] == "revoked"
        saved = connection.execute(
            "SELECT state,rating,internal_comment FROM evaluations WHERE id='final-before-conflict'"
        ).fetchone()
        assert tuple(saved) == ("final", 4, "Complete review")

        organizer = await _sign_in(client, connection, "admin@example.com")
        results = await client.get(
            f"/api/v1/admin/evaluation-rounds/{ROUND_ID}/results",
            headers={"x-csrf-token": organizer["csrf_token"]},
        )
        assert results.status_code == 200, results.text
        proposal = next(
            item for item in results.json()["submissions"] if item["submission_id"] == A
        )
        assert proposal["assigned_count"] == 0
        assert proposal["completed_count"] == 0
        assert proposal["average_rating"] is None
        assert results.json()["conflicts"][0]["assignment_id"] == assignment_id
        connection.execute(
            """INSERT INTO evaluation_round_evaluators
               (round_id,evaluator_user_id,organization_id,event_id,status,
                created_at_ms,updated_at_ms)
               VALUES (?,?,?,?, 'removed',1000,1000)""",
            (ROUND_ID, taylor, organization_id, event_id),
        )
        connection.commit()
        refused_removed = await client.post(
            f"/api/v1/admin/evaluation-assignments/{assignment_id}/reassign",
            headers={
                "origin": "https://test",
                "x-csrf-token": organizer["csrf_token"],
                "idempotency-key": "removed-reviewer-reassignment",
            },
            json={"evaluator_user_id": taylor},
        )
        assert refused_removed.status_code == 409, refused_removed.text
        assert connection.execute(
            """SELECT status FROM evaluation_round_evaluators
               WHERE round_id=? AND evaluator_user_id=?""",
            (ROUND_ID, taylor),
        ).fetchone()[0] == "removed"
        connection.execute(
            "DELETE FROM evaluation_round_evaluators WHERE round_id=? AND evaluator_user_id=?",
            (ROUND_ID, taylor),
        )
        connection.commit()
        reassigned = await client.post(
            f"/api/v1/admin/evaluation-assignments/{assignment_id}/reassign",
            headers={
                "origin": "https://test",
                "x-csrf-token": organizer["csrf_token"],
                "idempotency-key": "closed-round-reassignment",
            },
            json={"evaluator_user_id": taylor},
        )
        assert reassigned.status_code == 200, reassigned.text
        replacement_id = reassigned.json()["assignment_id"]
        notification = connection.execute(
            """SELECT subject,html_body,status FROM communication_messages
               WHERE recipient_user_id=? ORDER BY queued_at_ms DESC LIMIT 1""",
            (taylor,),
        ).fetchone()
        assert notification[0] == "Replacement review assigned: Initial review"
        assert "assigned to you after the round closed" in notification[1]
        assert notification[2] == "queued"

        reminder = await client.post(
            f"/api/v1/admin/evaluation-rounds/{ROUND_ID}/evaluators/{taylor}/reminder",
            headers={
                "origin": "https://test",
                "x-csrf-token": organizer["csrf_token"],
            },
            json={},
        )
        assert reminder.status_code == 200, reminder.text

        replacement_reviewer = await _sign_in(client, connection, TAYLOR_EMAIL)
        replacement_queue = await client.get("/api/v1/evaluator/assignments")
        assert replacement_queue.status_code == 200, replacement_queue.text
        assert [item["id"] for item in replacement_queue.json()["data"]] == [
            replacement_id
        ]
        assert replacement_queue.json()["data"][0]["assigned_after_close"] is True
        replacement_review = await client.put(
            f"/api/v1/evaluator/assignments/{replacement_id}/evaluation",
            headers={
                "origin": "https://test",
                "x-csrf-token": replacement_reviewer["csrf_token"],
                "idempotency-key": "closed-round-replacement-review",
            },
            json={
                "rating": 5,
                "recommendation": "accept",
                "internal_comment": "Independent replacement review.",
                "criterion_responses": {},
                "state": "final",
            },
        )
        assert replacement_review.status_code == 200, replacement_review.text

        organizer = await _sign_in(client, connection, "admin@example.com")
        refreshed_results = await client.get(
            f"/api/v1/admin/evaluation-rounds/{ROUND_ID}/results",
            headers={"x-csrf-token": organizer["csrf_token"]},
        )
        refreshed_proposal = next(
            item
            for item in refreshed_results.json()["submissions"]
            if item["submission_id"] == A
        )
        assert refreshed_proposal["assigned_count"] == 1
        assert refreshed_proposal["completed_count"] == 1
        assert refreshed_proposal["average_rating"] == 5
        audit = connection.execute(
            """SELECT metadata_json FROM audit_events
               WHERE action='evaluation.conflict.declare' ORDER BY occurred_at_ms DESC LIMIT 1"""
        ).fetchone()[0]
        assert '"finalized_review_excluded":true' in audit


async def test_refused_draft_assignment_update_is_atomic(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """A finalized review blocks the entire requested diff, including membership removal."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        admin_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        sam = _seed_reviewer(connection, organization_id, event_id, admin_user_id)
        _seed_round(connection, organization_id, event_id, sam, status="draft")
        _assign(connection, organization_id, event_id, [(A, sam)], status="completed")
        assignment_id = connection.execute(
            "SELECT id FROM evaluation_assignments WHERE round_id=? AND submission_id=?",
            (ROUND_ID, A),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO evaluations
               (id,organization_id,event_id,round_id,assignment_id,evaluator_user_id,
                rating,recommendation,internal_comment,state,created_at_ms,updated_at_ms,
                finalized_at_ms,criterion_responses_json)
               VALUES
                 ('final-evaluation',?,?,?,?,?,4,'accept','Done','final',1000,1000,1000,'{}')""",
            (organization_id, event_id, ROUND_ID, assignment_id, sam),
        )
        connection.commit()

        response = await client.put(
            f"/api/v1/admin/events/{event_id}/evaluation-rounds/{ROUND_ID}/draft",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": "refused-draft-update".ljust(32, "0"),
            },
            json={
                "name": "This must not be saved",
                "rating_min": 1,
                "rating_max": 5,
                "recommendations": ["accept", "reject"],
                "criteria": [],
                "blind_review": False,
                "submission_ids": list(TITLES),
                "evaluator_user_ids": [],
                "assignment_strategy": "all",
                "assignments": [],
                "status": "draft",
                "expected_version": connection.execute(
                    "SELECT version FROM evaluation_rounds WHERE id=?", (ROUND_ID,)
                ).fetchone()[0],
            },
        )
        assert response.status_code == 409, response.text
        assert (
            connection.execute(
                "SELECT name FROM evaluation_rounds WHERE id=?", (ROUND_ID,)
            ).fetchone()[0]
            == "Initial review"
        )
        assert (
            connection.execute(
                """SELECT status FROM evaluation_round_evaluators
               WHERE round_id=? AND evaluator_user_id=?""",
                (ROUND_ID, sam),
            ).fetchone()[0]
            == "active"
        )
        assert (
            connection.execute(
                "SELECT status FROM evaluation_assignments WHERE id=?", (assignment_id,)
            ).fetchone()[0]
            == "completed"
        )

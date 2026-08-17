"""A round reports the proposals and reviewers it actually holds, or refuses to be saved.

The organizer report was "the reviewers and proposals I selected did not persist; the
round had zero assignments". Both halves came from one payload shape. `assignments` is
stored verbatim -- an explicit list is never regenerated from `assignment_strategy`, which
is what lets a hand-tailored matrix survive a save -- so an empty list means "nobody
reviews anything". A draft never reaches the coverage rule that would refuse that, so the
round saved: membership rows written, no assignment rows. Every surface that reads the
round back then counted reviewers and listed proposals from `evaluation_assignments`, so
the round came back as "0 assignments · 0 reviewers" with no proposals, and the selection
looked lost rather than unassigned.

What is pinned:

* An empty matrix alongside proposals AND reviewers is refused, on create and on a draft
  save, before anything is written. `null` remains the way to ask for generation.
* A round holding a reviewer with nothing assigned yet, or a proposal nobody reviews yet,
  reports that reviewer and that proposal -- it is a legal drafting state, not an empty
  round.
* A revoked assignment stops being counted. A removed reviewer must not keep inflating the
  number the organizer is shown.
"""

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)

EVENT_PAYLOAD = {
    "name": "Selection Persistence Summit",
    "starts_at_ms": 1_900_000_000_000,
    "ends_at_ms": 1_900_086_400_000,
    "time_zone": "Asia/Kolkata",
    "delivery_mode": "hybrid",
    "location": "Pune",
    "description": "Round selection persistence",
    "email_sender_name": "Program Team",
    "email_reply_to": "program@example.com",
}
A = "aaaaaaaa-2222-4222-8222-aaaaaaaaaaaa"
B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
TITLES = {A: "Taming 40-Minute CI", B: "Scaling Postgres"}
SAM_EMAIL = "sam.reviewer@example.com"
SAM_USER_ID = "dddddddd-3333-4333-8333-dddddddddddd"


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
            "organization_name": "Selection Persistence Events",
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


def _admin_user_id(connection):
    return connection.execute(
        "SELECT id FROM users WHERE normalized_email='admin@example.com'"
    ).fetchone()[0]


def _seed_reviewer(connection, organization_id, event_id, invited_by_user_id):
    """Sam: the reviewer role plus an accepted invitation, which eligibility requires."""
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,version,authorization_version,
            created_at_ms,updated_at_ms,display_name)
           VALUES (?,?,?,'active',1,1,1000,1000,'Sam Whitfield')""",
        (SAM_USER_ID, SAM_EMAIL, SAM_EMAIL),
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,version,created_at_ms,updated_at_ms)
           VALUES ('om-sam',?,?,'member','active',1,1000,1000)""",
        (organization_id, SAM_USER_ID),
    )
    connection.execute(
        "INSERT INTO user_roles VALUES (?,'reviewer','active',1000,1000,NULL,1)",
        (SAM_USER_ID,),
    )
    connection.execute(
        """INSERT INTO identity_invitations
           (id,organization_id,event_id,normalized_email,email,role,status,invited_by_user_id,
            expires_at_ms,accepted_at_ms,created_at_ms,updated_at_ms,display_name,job_title,
            company,biography)
           VALUES ('inv-sam',?,?,?,?,'evaluator','accepted',?,9999999999999,1000,1000,1000,
                   'Sam Whitfield','','','')""",
        (organization_id, event_id, SAM_EMAIL, SAM_EMAIL, invited_by_user_id),
    )
    connection.commit()
    return SAM_USER_ID


def _seed_submissions(connection, organization_id, event_id):
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
            published_at_ms,created_at_ms,updated_at_ms,success_title,success_message,
            redirect_to_portal,confirmation_subject,confirmation_body)
           VALUES ('form-1',?,?,1,'selection-persistence','Welcome','{"fields":[]}','published',
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
    connection.commit()


def _round_body(*, status="draft", assignments, submission_ids=(A, B)):
    body = {
        "name": "Initial review",
        "rating_min": 1,
        "rating_max": 5,
        "recommendations": ["accept", "reject"],
        "criteria": [],
        "blind_review": False,
        "submission_ids": list(submission_ids),
        "evaluator_user_ids": [SAM_USER_ID],
        "assignment_strategy": "all",
        "status": status,
    }
    if assignments is not None:
        body["assignments"] = [
            {"submission_id": submission_id, "evaluator_user_id": SAM_USER_ID}
            for submission_id in assignments
        ]
    else:
        # Explicitly present and null: "generate from the strategy", the one way to ask
        # for a matrix rather than supply one.
        body["assignments"] = None
    return body


def _key(label):
    return f"idempotency-{label}".ljust(32, "0")


def _round_version(connection, round_id):
    """The round's current optimistic-concurrency token, read from the store."""
    return connection.execute(
        "SELECT version FROM evaluation_rounds WHERE id=?", (round_id,)
    ).fetchone()[0]


async def _create_round(client, csrf, event_id, body, *, label):
    return await client.post(
        f"/api/v1/admin/events/{event_id}/evaluation-rounds",
        headers={"origin": "https://test", "x-csrf-token": csrf, "idempotency-key": _key(label)},
        json=body,
    )


async def _update_draft(client, csrf, connection, event_id, round_id, body):
    """PUT the draft with the version token and idempotency key it requires."""
    return await client.put(
        f"/api/v1/admin/events/{event_id}/evaluation-rounds/{round_id}/draft",
        headers={
            "origin": "https://test",
            "x-csrf-token": csrf,
            "idempotency-key": _key("draft-update"),
        },
        json={**body, "expected_version": _round_version(connection, round_id)},
    )


async def _setup(client, connection):
    csrf, organization_id, event_id = await _organizer(client, connection)
    _seed_submissions(connection, organization_id, event_id)
    _seed_reviewer(connection, organization_id, event_id, _admin_user_id(connection))
    return csrf, organization_id, event_id


async def test_an_empty_matrix_is_refused_instead_of_saved_as_an_unreviewable_round(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup(client, connection)

        refused = await _create_round(
            client, csrf, event_id, _round_body(assignments=[]), label="empty"
        )

        assert refused.status_code == 422, refused.text
        assert "assignments" in refused.json()["error"]["message"]
    # Refused before the write, not half-written: no round, no membership, no idempotency
    # record that a retry could replay into existence.
    assert connection.execute("SELECT COUNT(*) FROM evaluation_rounds").fetchone()[0] == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM evaluation_round_submissions").fetchone()[0] == 0
    )
    assert (
        connection.execute("SELECT COUNT(*) FROM evaluation_round_evaluators").fetchone()[0] == 0
    )


async def test_an_empty_matrix_cannot_erase_the_assignments_a_draft_already_has(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The draft save is the same model, so it is refused at the same point.

    This is the path the organizer reaches from "Edit draft", where a wrong save costs
    work that already exists rather than work that was never written.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(assignments=[A, B]), label="draft"
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]
        assert created.json()["assignment_count"] == 2

        refused = await _update_draft(
            client, csrf, connection, event_id, round_id, _round_body(assignments=[])
        )

        assert refused.status_code == 422, refused.text
        assert (
            connection.execute(
                """SELECT COUNT(*) FROM evaluation_assignments
                   WHERE round_id=? AND status!='revoked'""",
                (round_id,),
            ).fetchone()[0]
            == 2
        )


async def test_null_assignments_still_generate_the_matrix_from_the_strategy(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The escape hatch the refusal names has to work, or the message is a dead end."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup(client, connection)

        created = await _create_round(
            client, csrf, event_id, _round_body(assignments=None), label="generated"
        )

        assert created.status_code == 201, created.text
        assert created.json()["assignment_count"] == 2
        assert created.json()["evaluator_count"] == 1


async def test_a_round_reports_the_reviewers_and_proposals_it_holds_not_only_the_assigned(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """A proposal nobody reviews yet is still in the round, and so is its reviewer pool.

    Counting reviewers from `evaluation_assignments` made a legal drafting state -- and,
    before the empty-matrix refusal above, a mis-saved one -- read as a round with nothing
    in it, which is how "my selection did not persist" was reported for data that was
    sitting in the membership tables the whole time.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _setup(client, connection)
        created = await _create_round(
            client, csrf, event_id, _round_body(assignments=[A]), label="partial"
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]

        listed = await client.get(f"/api/v1/admin/events/{event_id}/evaluation-rounds")
        assert listed.status_code == 200, listed.text
        (round_view,) = listed.json()["data"]
        assert round_view["assignment_count"] == 1
        assert round_view["evaluator_count"] == 1
        # B has no reviewer yet. It is still a proposal in this round.
        assert {item["submission_id"] for item in round_view["proposals"]} == {A, B}
        assert {item["proposal_title"] for item in round_view["proposals"]} == set(TITLES.values())

        # Revoking the only assignment leaves a reviewer in the pool with nothing to do.
        connection.execute(
            "UPDATE evaluation_assignments SET status='revoked' WHERE round_id=?", (round_id,)
        )
        connection.commit()

        revoked = await client.get(f"/api/v1/admin/events/{event_id}/evaluation-rounds")
        (after,) = revoked.json()["data"]
        # The revoked pair stops counting -- it is audit history, not work in progress --
        # while the membership it references is still the round's.
        assert after["assignment_count"] == 0
        assert after["evaluator_count"] == 1
        assert {item["submission_id"] for item in after["proposals"]} == {A, B}

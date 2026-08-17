"""A draft round is silent; opening it is what tells the reviewers.

`open_evaluation_round` calls a draft round "invisible to reviewers", and the reviewer
queue keeps that promise: `/api/v1/evaluator/assignments` joins
`evaluation_rounds ... AND r.status = 'open'`, so a draft assignment is neither listed
nor counted. The mail did not keep it. Every write that creates assignments -- round
creation, adding a reviewer, adding proposals -- queued "You have been assigned N
proposals to review", including while the round was a draft, and the link in that mail
goes to /reviews, which shows the reviewer nothing. Opening the round, the moment the
work actually appears, sent nothing at all. So the one notification a reviewer received
arrived when there was nothing to do, and none arrived when there was.

What is pinned:

* A draft round queues no assignment mail, however its assignments got there.
* Opening the round queues exactly one message per reviewer, counting every assignment
  the round holds, whenever it was added.
* An open round still notifies at the moment of the change, as it always did, and is not
  re-announced by a redundant open.
* The notification arrives with the queue: the same request that makes the assignments
  visible on /reviews is the one that mails about them.
"""

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)

EVENT_PAYLOAD = {
    "name": "Draft Silence Summit",
    "starts_at_ms": 1_900_000_000_000,
    "ends_at_ms": 1_900_086_400_000,
    "time_zone": "Asia/Kolkata",
    "delivery_mode": "hybrid",
    "location": "Pune",
    "description": "Notification timing",
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
            "organization_name": "Draft Silence Events",
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
    """Sam: a separate identity with the reviewer role and an accepted invitation.

    Both rows are required -- validate_evaluation_assignment_* refuses an assignment
    whose evaluator lacks either -- and being a different user from the organizer is what
    makes the mailbox assertions meaningful.
    """
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
           VALUES ('form-1',?,?,1,'draft-silence','Welcome','{"fields":[]}','published',
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


def _round_body(*, status, submission_ids=(A, B), evaluator_user_ids=(SAM_USER_ID,)):
    return {
        "name": "Initial review",
        "rating_min": 1,
        "rating_max": 5,
        "recommendations": ["accept", "reject"],
        "criteria": [],
        "blind_review": False,
        "submission_ids": list(submission_ids),
        "evaluator_user_ids": list(evaluator_user_ids),
        "assignment_strategy": "all",
        "assignments": [
            {"submission_id": submission_id, "evaluator_user_id": evaluator_user_id}
            for submission_id in submission_ids
            for evaluator_user_id in evaluator_user_ids
        ],
        "status": status,
    }


def _key(label):
    return f"idempotency-{label}".ljust(32, "0")


def _assignment_mail(connection):
    """Every queued assignment notification, in queue order.

    Keyed on the deterministic key rather than the subject: the dedup suffix is part of
    what changed, and reading it here is what proves an opened round cannot mail twice.
    """
    return connection.execute(
        """SELECT recipient_email,subject,html_body,deterministic_key
             FROM communication_messages
            WHERE deterministic_key LIKE 'evaluation-assignment:%'
            ORDER BY queued_at_ms,id"""
    ).fetchall()


async def _create_round(client, csrf, event_id, body, *, label):
    return await client.post(
        f"/api/v1/admin/events/{event_id}/evaluation-rounds",
        headers={"origin": "https://test", "x-csrf-token": csrf, "idempotency-key": _key(label)},
        json=body,
    )


def _round_version(connection, round_id):
    """The round's current optimistic-concurrency token, read from the store."""
    return connection.execute(
        "SELECT version FROM evaluation_rounds WHERE id=?", (round_id,)
    ).fetchone()[0]


async def _open_round(client, csrf, connection, round_id, *, label):
    return await client.post(
        f"/api/v1/admin/evaluation-rounds/{round_id}/open",
        headers={"origin": "https://test", "x-csrf-token": csrf, "idempotency-key": _key(label)},
        json={"expected_version": _round_version(connection, round_id)},
    )


async def _reviewer_queue(connection, environment):
    async with _client(environment) as reviewer:
        await _sign_in(reviewer, connection, SAM_EMAIL)
        queued = await reviewer.get("/api/v1/evaluator/assignments")
        assert queued.status_code == 200, queued.text
        return queued.json()


async def test_a_draft_round_queues_no_mail_about_work_nobody_can_see(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_submissions(connection, organization_id, event_id)
        _seed_reviewer(connection, organization_id, event_id, _admin_user_id(connection))

        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="draft"
        )
        assert created.status_code == 201, created.text
        # The assignments exist -- this is a fully configured draft, not an empty one.
        assert created.json()["assignment_count"] == 2

    # Nothing was said, because there is nothing to see.
    assert _assignment_mail(connection) == []
    queue = await _reviewer_queue(connection, environment)
    assert queue["data"] == []
    assert queue["total"] == 0


async def test_opening_the_round_is_what_notifies_its_reviewers(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_submissions(connection, organization_id, event_id)
        _seed_reviewer(connection, organization_id, event_id, _admin_user_id(connection))
        created = await _create_round(
            client, csrf, event_id, _round_body(status="draft"), label="draft"
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]

        opened = await _open_round(client, csrf, connection, round_id, label="open")
        assert opened.status_code == 200, opened.text

    mail = _assignment_mail(connection)
    assert len(mail) == 1
    recipient, subject, body, deterministic_key = mail[0]
    assert recipient == SAM_EMAIL
    assert subject == "New review assignments: Initial review"
    # Counted from the round, so a draft that accumulated its assignments over several
    # edits still announces all of them once.
    assert "assigned 2 proposals" in body
    # Keyed to the round, not the clock: a round leaves draft exactly once, and the
    # UNIQUE (organization_id,event_id,deterministic_key) index is what makes a repeat
    # impossible rather than merely unlikely.
    assert deterministic_key.endswith(":round-opened")

    # The mail and the queue now agree, which is the whole point of moving it here.
    queue = await _reviewer_queue(connection, environment)
    assert queue["total"] == 2
    assert sorted(item["proposal_title"] for item in queue["data"]) == sorted(TITLES.values())


async def test_an_open_round_still_notifies_when_the_assignments_are_made(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The immediate notification is correct when the round is already open.

    Only the draft case moved. A round created open puts its assignments on /reviews in
    the same request, so the mail belongs with the creation, and opening an already-open
    round -- which returns the round unchanged -- must not announce it a second time.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_submissions(connection, organization_id, event_id)
        _seed_reviewer(connection, organization_id, event_id, _admin_user_id(connection))

        created = await _create_round(
            client, csrf, event_id, _round_body(status="open"), label="open-round"
        )
        assert created.status_code == 201, created.text
        mail = _assignment_mail(connection)
        assert len(mail) == 1
        assert mail[0][0] == SAM_EMAIL
        assert "assigned 2 proposals" in mail[0][2]

        replayed = await _open_round(client, csrf, connection, created.json()["id"], label="reopen")
        assert replayed.status_code == 200, replayed.text

    # Still one: an open round is not re-announced by asking to open it again.
    assert len(_assignment_mail(connection)) == 1


async def test_a_reviewer_added_to_a_draft_hears_about_it_when_the_round_opens(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Assignments added after the draft was created are on the same schedule.

    add_round_evaluator and add_round_submissions both accept a draft round, so both can
    write invisible assignments. The count in the opening mail comes from the round's
    assignments rather than from any one request, so an assignment added this way is
    announced exactly once, at open, along with the rest.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_submissions(connection, organization_id, event_id)
        _seed_reviewer(connection, organization_id, event_id, _admin_user_id(connection))
        # A draft holding both proposals but only one of them assigned to Sam.
        created = await _create_round(
            client,
            csrf,
            event_id,
            {
                **_round_body(status="draft"),
                "assignments": [{"submission_id": A, "evaluator_user_id": SAM_USER_ID}],
            },
            label="draft",
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]

        widened = await client.post(
            f"/api/v1/admin/evaluation-rounds/{round_id}/evaluators",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": _key("widen"),
            },
            json={
                "evaluator_user_id": SAM_USER_ID,
                "submission_ids": [A, B],
                "expected_version": _round_version(connection, round_id),
            },
        )
        assert widened.status_code == 200, widened.text
        # Adding to a draft is as silent as creating one.
        assert _assignment_mail(connection) == []

        opened = await _open_round(client, csrf, connection, round_id, label="open")
        assert opened.status_code == 200, opened.text

    mail = _assignment_mail(connection)
    assert len(mail) == 1
    # Both proposals, in one message, rather than one message per edit.
    assert "assigned 2 proposals" in mail[0][2]


async def test_incremental_open_round_assignments_share_one_hourly_notice(
    production_environment,  # noqa: F811 - pytest fixture
    monkeypatch,
) -> None:
    """Proposal-by-proposal assignment cannot become proposal-by-proposal email spam.

    The first click queues an immediate, count-free notice. Further clicks for the same
    reviewer and round in the hour converge on the same outbox row, while both assignment
    mutations still commit.
    """
    # Both mutations intentionally belong to one fixed clock-hour bucket. Without a
    # frozen clock, the test itself could cross an hour boundary between POSTs and
    # correctly observe two production windows as a false failure.
    monkeypatch.setattr(
        "sessionbuddy.evaluation.router.utc_now_ms", lambda: 1_900_000_123_000
    )
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_submissions(connection, organization_id, event_id)
        _seed_reviewer(connection, organization_id, event_id, _admin_user_id(connection))
        created = await _create_round(
            client,
            csrf,
            event_id,
            _round_body(status="open"),
            label="open-incremental",
        )
        assert created.status_code == 201, created.text
        round_id = created.json()["id"]

        # Isolate notifications produced by the incremental control, then make two real
        # changes: revive A and B. Both proposals are already round members.
        connection.execute("DELETE FROM communication_messages")
        connection.execute(
            """UPDATE evaluation_assignments SET status='revoked'
               WHERE round_id=?""",
            (round_id,),
        )
        connection.commit()
        for label, submission_id in (("revive-a", A), ("add-b", B)):
            changed = await client.post(
                f"/api/v1/admin/evaluation-rounds/{round_id}/evaluators",
                headers={
                    "origin": "https://test",
                    "x-csrf-token": csrf,
                    "idempotency-key": _key(label),
                },
                json={
                    "evaluator_user_id": SAM_USER_ID,
                    "submission_ids": [submission_id],
                    "expected_version": _round_version(connection, round_id),
                },
            )
            assert changed.status_code == 200, (label, changed.text)
            assert changed.json()["assignment_count"] == 1

    mail = _assignment_mail(connection)
    assert len(mail) == 1
    assert "New review work was added to your queue" in mail[0][2]
    assert "assigned 1 proposal" not in mail[0][2]
    assert ":assignment-window:" in mail[0][3]
    assert connection.execute(
        """SELECT COUNT(*) FROM evaluation_assignments
           WHERE round_id=? AND evaluator_user_id=? AND status='assigned'""",
        (round_id, SAM_USER_ID),
    ).fetchone()[0] == 2

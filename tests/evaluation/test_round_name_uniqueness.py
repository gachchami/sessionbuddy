"""A round name identifies a round to the humans using it, so it must not repeat.

The bug these pin: creating a second round whose name differed from a live one only by
capitalization was allowed. The organizer got two rounds -- "Round 1" and "round 1" --
carrying the same proposals and the same reviewers, of which only one was ever opened.
The other sat in the history as an orphan draft, and every assignment count in the console
had to be read against a name that appeared twice.

What is pinned:

* Case and stray whitespace do not make a new name. "round 1" and "Round  1" both lose to
  an existing "Round 1", and nothing is written when they do.
* An idempotent replay of the create still returns its own round rather than colliding
  with it -- the guard sits after the replay check, not before.
* Saving a draft without touching its name is not a self-collision, but renaming it onto a
  sibling's name is refused and leaves the stored name alone.
* The scope is rounds still in play. A closed round keeps its name in the history and
  frees it for reuse, and a different event is a different namespace.
"""

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)

EVENT_PAYLOAD = {
    "name": "Round Naming Summit",
    "starts_at_ms": 1_900_000_000_000,
    "ends_at_ms": 1_900_086_400_000,
    "time_zone": "Asia/Kolkata",
    "delivery_mode": "hybrid",
    "location": "Pune",
    "description": "Duplicate round names",
    "email_sender_name": "Program Team",
    "email_reply_to": "program@example.com",
}
SUBMISSION_ID = "aaaaaaaa-2222-4222-8222-aaaaaaaaaaaa"
REVIEWER_EMAIL = "nia.reviewer@example.com"
REVIEWER_USER_ID = "dddddddd-3333-4333-8333-dddddddddddd"


async def _sign_in(client, connection, email):
    assert (
        await client.post("/api/v1/auth/magic-links", json={"email": email, "redirect_path": "/"})
    ).status_code == 202
    verified = await client.post(
        "/auth/verify", data={"token": _token(connection, email)}, follow_redirects=False
    )
    assert verified.status_code == 303, verified.text
    return (await client.get("/api/v1/auth/session")).json()


async def _create_event(client, csrf, organization_id, **overrides):
    created = await client.post(
        f"/api/v1/admin/organizations/{organization_id}/events",
        headers={"origin": "https://test", "x-csrf-token": csrf},
        json={**EVENT_PAYLOAD, **overrides},
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _organizer(client, connection):
    bootstrap = await client.post(
        "/api/v1/bootstrap",
        headers={"x-bootstrap-token": _deployment_key(connection)},
        json={
            "organization_name": "Round Naming Events",
            "admin_name": "Admin",
            "admin_email": "admin@example.com",
        },
    )
    assert bootstrap.status_code == 200, bootstrap.text
    organization_id = bootstrap.json()["organization_id"]
    session = await _sign_in(client, connection, "admin@example.com")
    csrf = session["csrf_token"]
    return csrf, organization_id, await _create_event(client, csrf, organization_id)


def _seed_reviewable_event(connection, organization_id, event_id, invited_by_user_id):
    """One submitted proposal and one reviewer the assignment triggers will accept.

    The database refuses an assignment whose evaluator lacks both an active `reviewer` role
    and an accepted `evaluator` invitation for the event, so both rows are real. Seeded
    directly rather than through CFP intake -- these tests are about the round's name, not
    about how a proposal arrives.
    """
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
            published_at_ms,created_at_ms,updated_at_ms,success_title,success_message,
            redirect_to_portal,confirmation_subject,confirmation_body)
           VALUES ('form-1',?,?,1,'round-naming','Welcome','{"fields":[]}','published',
                   1000,1000,1000,'Thanks','Received',0,'Received','Thanks')""",
        (organization_id, event_id),
    )
    connection.execute(
        """INSERT INTO submissions
           (id,organization_id,event_id,form_id,public_session_id,proposal_title,
            proposal_abstract,speaker_name,status,submitted_at_ms,created_at_ms,
            updated_at_ms,speaker_email,answers_json,version)
           VALUES (?,?,?,'form-1','public-1','Taming 40-Minute CI','Abstract','Speaker',
                   'submitted',1000,1000,1000,'speaker@example.com','{}',1)""",
        (SUBMISSION_ID, organization_id, event_id),
    )
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,version,authorization_version,
            created_at_ms,updated_at_ms,display_name)
           VALUES (?,?,?,'active',1,1,1000,1000,'Nia Okafor')""",
        (REVIEWER_USER_ID, REVIEWER_EMAIL, REVIEWER_EMAIL),
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,version,created_at_ms,updated_at_ms)
           VALUES ('om-nia',?,?,'member','active',1,1000,1000)""",
        (organization_id, REVIEWER_USER_ID),
    )
    connection.execute(
        "INSERT INTO user_roles VALUES (?,'reviewer','active',1000,1000,NULL,1)",
        (REVIEWER_USER_ID,),
    )
    connection.execute(
        """INSERT INTO identity_invitations
           (id,organization_id,event_id,normalized_email,email,role,status,invited_by_user_id,
            expires_at_ms,accepted_at_ms,created_at_ms,updated_at_ms,display_name,job_title,
            company,biography)
           VALUES ('inv-nia',?,?,?,?,'evaluator','accepted',?,9999999999999,1000,1000,1000,
                   'Nia Okafor','','','')""",
        (organization_id, event_id, REVIEWER_EMAIL, REVIEWER_EMAIL, invited_by_user_id),
    )
    connection.commit()


def _admin_user_id(connection):
    return connection.execute(
        "SELECT id FROM users WHERE normalized_email='admin@example.com'"
    ).fetchone()[0]


def _round_body(name, *, staffed=True):
    """A draft round the organizer would recognise: one proposal, one reviewer, one pair."""
    return {
        "name": name,
        "rating_min": 1,
        "rating_max": 5,
        "recommendations": ["accept", "reject"],
        "criteria": [],
        "blind_review": False,
        "submission_ids": [SUBMISSION_ID] if staffed else [],
        "evaluator_user_ids": [REVIEWER_USER_ID] if staffed else [],
        "assignment_strategy": "all",
        "assignments": (
            [{"submission_id": SUBMISSION_ID, "evaluator_user_id": REVIEWER_USER_ID}]
            if staffed
            else []
        ),
        "status": "draft",
    }


def _key(label):
    # _key() in the router rejects anything outside 16..255 characters. Every request that
    # is not deliberately a replay gets its own key: reusing one would make the router
    # answer 409 from the idempotency fingerprint check, which is a different guard and
    # would let a broken name check pass these tests.
    return f"idempotency-{label}".ljust(32, "0")


async def _create_round(client, csrf, event_id, name, *, key, staffed=True):
    return await client.post(
        f"/api/v1/admin/events/{event_id}/evaluation-rounds",
        headers={
            "origin": "https://test",
            "x-csrf-token": csrf,
            "idempotency-key": _key(key),
        },
        json=_round_body(name, staffed=staffed),
    )


def _names(connection, event_id):
    return [
        row[0]
        for row in connection.execute(
            "SELECT name FROM evaluation_rounds WHERE event_id=? ORDER BY created_at_ms, name",
            (event_id,),
        ).fetchall()
    ]


async def test_a_live_round_name_cannot_be_retaken_by_changing_case_or_spacing(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_reviewable_event(connection, organization_id, event_id, _admin_user_id(connection))

        first = await _create_round(client, csrf, event_id, "Round 1", key="first-round")
        assert first.status_code == 201, first.text

        for index, duplicate in enumerate(("round 1", "ROUND 1", "Round  1", "  Round 1  ")):
            response = await _create_round(
                client, csrf, event_id, duplicate, key=f"duplicate-{index}"
            )
            assert response.status_code == 409, f"{duplicate!r}: {response.text}"
            assert "Round 1" in response.json()["error"]["message"]

        # The refusal is total. A partially written round is exactly the orphan this fixes.
        assert _names(connection, event_id) == ["Round 1"]
        # The write path maintains the key uq_evaluation_rounds_live_name compares, so the
        # database would have refused the second insert even if both guards had read "free".
        assert (
            connection.execute(
                "SELECT name_key FROM evaluation_rounds WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            == "round 1"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM evaluation_assignments WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            == 1
        )


async def test_replaying_the_create_returns_its_own_round_instead_of_conflicting(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The guard must sit after the idempotency replay, or every retry becomes a 409."""
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_reviewable_event(connection, organization_id, event_id, _admin_user_id(connection))

        first = await _create_round(client, csrf, event_id, "Round 1", key="replayed-round")
        assert first.status_code == 201, first.text
        replay = await _create_round(client, csrf, event_id, "Round 1", key="replayed-round")
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == first.json()["id"]
        assert _names(connection, event_id) == ["Round 1"]


async def test_a_draft_keeps_its_own_name_but_cannot_take_a_siblings(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_reviewable_event(connection, organization_id, event_id, _admin_user_id(connection))

        screening = await _create_round(client, csrf, event_id, "Screening", key="screening")
        assert screening.status_code == 201, screening.text
        second_look = await _create_round(
            client, csrf, event_id, "Second look", key="second-look", staffed=False
        )
        assert second_look.status_code == 201, second_look.text
        second_look_id = second_look.json()["id"]

        headers = {"origin": "https://test", "x-csrf-token": csrf}
        endpoint = f"/api/v1/admin/events/{event_id}/evaluation-rounds/{second_look_id}/draft"

        def draft_headers(label):
            return {
                **headers,
                "idempotency-key": _key(label),
            }

        def version():
            return connection.execute(
                "SELECT version FROM evaluation_rounds WHERE id=?", (second_look_id,)
            ).fetchone()[0]

        unchanged = await client.put(
            endpoint,
            headers=draft_headers("draft-unchanged"),
            json={**_round_body("Second look", staffed=False), "expected_version": version()},
        )
        assert unchanged.status_code == 200, unchanged.text

        collision = await client.put(
            endpoint,
            headers=draft_headers("draft-collision"),
            json={**_round_body("  screening ", staffed=False), "expected_version": version()},
        )
        assert collision.status_code == 409, collision.text
        # tuple(): the fixture connection uses sqlite3.Row, and a Row never
        # equals a tuple -- the untupled comparison was False even on matching
        # values, which is how this assertion failed the release gate while the
        # endpoint itself behaved.
        assert tuple(
            connection.execute(
                "SELECT name,name_key FROM evaluation_rounds WHERE id=?", (second_look_id,)
            ).fetchone()
        ) == ("Second look", "second look")

        renamed = await client.put(
            endpoint,
            headers=draft_headers("draft-rename"),
            json={**_round_body("Second  Look", staffed=False), "expected_version": version()},
        )
        assert renamed.status_code == 200, renamed.text
        assert tuple(
            connection.execute(
                "SELECT name,name_key FROM evaluation_rounds WHERE id=?", (second_look_id,)
            ).fetchone()
        ) == ("Second  Look", "second look"), "a rename must rewrite the key it is indexed on"


async def test_an_open_round_blocks_on_being_open_before_it_blocks_on_the_name(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Renaming does not clear the one-open-round rule, so that rule answers first.

    Reporting the name instead would send the organizer away to rename and bring them back
    to the same wall. Once they save the second round as a draft -- the advice that 409
    actually gives -- the name guard is what they meet.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_reviewable_event(connection, organization_id, event_id, _admin_user_id(connection))

        opened = await client.post(
            f"/api/v1/admin/events/{event_id}/evaluation-rounds",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": _key("opened-round"),
            },
            json={**_round_body("Round 1"), "status": "open"},
        )
        assert opened.status_code == 201, opened.text

        still_open = await client.post(
            f"/api/v1/admin/events/{event_id}/evaluation-rounds",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": _key("second-open-round"),
            },
            json={**_round_body("round 1"), "status": "open"},
        )
        assert still_open.status_code == 409, still_open.text
        assert "already open" in still_open.json()["error"]["message"]

        as_draft = await _create_round(client, csrf, event_id, "round 1", key="shadow-draft")
        assert as_draft.status_code == 409, as_draft.text
        assert "Round 1" in as_draft.json()["error"]["message"]
        assert _names(connection, event_id) == ["Round 1"]


async def test_a_constraint_refusal_that_is_not_a_name_conflict_keeps_its_own_answer(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The retry after a 409 must not invent a rename message for an unrelated conflict.

    The divergence between name and name_key below is constructed: in real data the guard
    is the stricter of the two layers, so only a genuine concurrent insert reaches the
    index with the guard reporting the name free -- and that cannot be staged from a single
    test client. Forcing the divergence isolates the branch, and pins that when the re-run
    guard finds nothing, the caller gets the ordinary conflict envelope rather than
    instructions to rename a round that does not exist.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_reviewable_event(connection, organization_id, event_id, _admin_user_id(connection))
        connection.execute(
            """INSERT INTO evaluation_rounds
               (id,organization_id,event_id,name,name_key,rubric_json,status,
                created_at_ms,updated_at_ms)
               VALUES('r-divergent',?,?,'Zeta','alpha','{}','draft',1000,1000)""",
            (organization_id, event_id),
        )
        connection.commit()

        refused = await _create_round(client, csrf, event_id, "Alpha", key="unrelated-conflict")
        assert refused.status_code == 409, refused.text
        assert "already exists for this event" not in refused.json()["error"]["message"]
        assert _names(connection, event_id) == ["Zeta"]


async def test_a_closed_round_frees_its_name_and_other_events_never_held_it(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_reviewable_event(connection, organization_id, event_id, _admin_user_id(connection))

        first = await _create_round(client, csrf, event_id, "Screening", key="screening")
        assert first.status_code == 201, first.text

        other_event_id = await _create_event(
            client, csrf, organization_id, name="Round Naming Winter"
        )
        elsewhere = await _create_round(
            client, csrf, other_event_id, "Screening", key="other-event", staffed=False
        )
        assert elsewhere.status_code == 201, elsewhere.text

        connection.execute(
            "UPDATE evaluation_rounds SET status='closed', closed_at_ms=2000 WHERE id=?",
            (first.json()["id"],),
        )
        connection.commit()

        reused = await _create_round(client, csrf, event_id, "Screening", key="reused")
        assert reused.status_code == 201, reused.text
        assert _names(connection, event_id) == ["Screening", "Screening"]

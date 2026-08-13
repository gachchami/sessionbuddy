"""A reviewer who was invited but has not accepted is visibly pending, not missing.

Eligibility requires `identity_invitations.status='accepted'`, and only the invited
person can set it -- acceptance happens when they consume their own access link. That
prerequisite is deliberate. What was not deliberate is that every organizer surface
reported an unaccepted invitee exactly like a stranger: the lookup returned an empty
list, the round endpoints raised a bare 400, and the round form answered "Invite them
as a Reviewer first" to an organizer who had already invited them -- sending them back
to re-invite, which only reissues the same pending row and leaves the round as blocked.

What is pinned:

* The evaluators lookup distinguishes the two cases: an outstanding invitation comes
  back under `pending`, a genuine stranger comes back with `pending` empty.
* An expired invitation is reported as expired, because that one needs a fresh
  invitation rather than a resend.
* The pending entry carries state only. This route is gated by SUBMISSION_MANAGE, which
  an event grant of `edit` satisfies, while the invitation roster needs
  RESOURCE_ACCESS_MANAGE; since the lookup is keyed by a guessable email, a name or an
  invitation id here would be a probe-by-email around that stricter gate.
* Creating a round with an unaccepted reviewer still fails -- the gate is intact -- but
  the 400 now names who is being waited on and says what makes them assignable.
"""

from tests.security.test_production_identity_flow import (
    _client,
    _deployment_key,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)

EVENT_PAYLOAD = {
    "name": "Pending Reviewer Summit",
    "starts_at_ms": 1_900_000_000_000,
    "ends_at_ms": 1_900_086_400_000,
    "time_zone": "Asia/Kolkata",
    "delivery_mode": "hybrid",
    "location": "Pune",
    "description": "Reviewer eligibility messaging",
    "email_sender_name": "Program Team",
    "email_reply_to": "program@example.com",
}
SUBMISSION_ID = "aaaaaaaa-2222-4222-8222-aaaaaaaaaaaa"
REVIEWER_EMAIL = "nia.reviewer@example.com"
REVIEWER_USER_ID = "dddddddd-3333-4333-8333-dddddddddddd"
NEVER_EXPIRES = 9_999_999_999_999
ALREADY_EXPIRED = 2_000


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
            "organization_name": "Pending Reviewer Events",
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


def _seed_submission(connection, organization_id, event_id):
    connection.execute(
        """INSERT INTO call_for_speaker_forms
           (id,organization_id,event_id,version,slug,welcome_text,schema_json,status,
            published_at_ms,created_at_ms,updated_at_ms,success_title,success_message,
            redirect_to_portal,confirmation_subject,confirmation_body)
           VALUES ('form-1',?,?,1,'pending-reviewer','Welcome','{"fields":[]}','published',
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
    connection.commit()


def _seed_unaccepted_reviewer(
    connection, organization_id, event_id, invited_by_user_id, *, expires_at_ms=NEVER_EXPIRES
):
    """Nia has the global reviewer role and an invitation she has not accepted.

    This is the state the organizer actually lands in: the account exists and can sign
    in, so "no such person" is the wrong answer, but the per-event invitation is still
    `pending`, so "eligible" is the wrong answer too.
    """
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
           VALUES ('inv-nia',?,?,?,?,'evaluator','pending',?,?,NULL,1000,1000,
                   'Nia Okafor','','','')""",
        (
            organization_id,
            event_id,
            REVIEWER_EMAIL,
            REVIEWER_EMAIL,
            invited_by_user_id,
            expires_at_ms,
        ),
    )
    connection.commit()


def _round_body(name):
    return {
        "name": name,
        "rating_min": 1,
        "rating_max": 5,
        "recommendations": ["accept", "reject"],
        "criteria": [],
        "blind_review": False,
        "submission_ids": [SUBMISSION_ID],
        "evaluator_user_ids": [REVIEWER_USER_ID],
        "assignment_strategy": "all",
        "assignments": [{"submission_id": SUBMISSION_ID, "evaluator_user_id": REVIEWER_USER_ID}],
        "status": "draft",
    }


def _key(label):
    return f"idempotency-{label}".ljust(32, "0")


async def test_lookup_reports_an_outstanding_invitation_instead_of_nothing(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_unaccepted_reviewer(connection, organization_id, event_id, _admin_user_id(connection))

        found = await client.get(
            f"/api/v1/admin/events/{event_id}/evaluators?email={REVIEWER_EMAIL}"
        )
        assert found.status_code == 200, found.text
        payload = found.json()
        # Still not eligible -- the acceptance gate is untouched.
        assert payload["data"] == []
        # But no longer indistinguishable from a stranger.
        assert len(payload["pending"]) == 1
        pending = payload["pending"][0]
        assert pending["email"] == REVIEWER_EMAIL
        assert pending["expired"] is False


async def test_lookup_leaves_pending_empty_for_someone_never_invited(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, _organization_id, event_id = await _organizer(client, connection)

        found = await client.get(
            f"/api/v1/admin/events/{event_id}/evaluators?email=stranger@example.com"
        )
        assert found.status_code == 200, found.text
        payload = found.json()
        assert payload["data"] == []
        # The one case where "invite them first" is the right advice.
        assert payload["pending"] == []


async def test_lookup_marks_an_expired_invitation_as_expired(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_unaccepted_reviewer(
            connection,
            organization_id,
            event_id,
            _admin_user_id(connection),
            expires_at_ms=ALREADY_EXPIRED,
        )

        found = await client.get(
            f"/api/v1/admin/events/{event_id}/evaluators?email={REVIEWER_EMAIL}"
        )
        assert found.status_code == 200, found.text
        payload = found.json()
        assert payload["data"] == []
        # Waiting is pointless here; this invitation has to be sent again.
        assert payload["pending"][0]["expired"] is True


async def test_creating_a_round_with_an_unaccepted_reviewer_explains_itself(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_submission(connection, organization_id, event_id)
        _seed_unaccepted_reviewer(connection, organization_id, event_id, _admin_user_id(connection))

        refused = await client.post(
            f"/api/v1/admin/events/{event_id}/evaluation-rounds",
            headers={
                "origin": "https://test",
                "x-csrf-token": csrf,
                "idempotency-key": _key("pending-reviewer"),
            },
            json=_round_body("Initial review"),
        )
        # The gate still holds.
        assert refused.status_code == 400, refused.text
        # SessionBuddy wraps every error as {"error":{"code","message"},"request_id"};
        # http_error() copies a non-empty string detail into `message` for any sub-500
        # status, so the explanation arrives there rather than under a `detail` key.
        body = refused.json()
        assert body["error"]["code"] == "invalid_request"
        message = body["error"]["message"]
        # And it now says who, and what would change it.
        assert "Nia Okafor" in message
        assert "accept" in message.lower()
        # No round was created on the way out.
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM evaluation_rounds WHERE event_id=?", (event_id,)
            ).fetchone()[0]
            == 0
        )


async def test_resend_refuses_an_expired_invitation(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """Why the search result offers a fresh invitation, not "Send again", when expired.

    `resend_invitation` requires `status='pending' AND expires_at_ms>now`. Offering a
    resend for an expired invitation therefore produces a 404 on click, which is the
    one thing an organizer chasing an unaccepted reviewer least needs. If this ever
    starts succeeding, the expired branch in access_admin.js can be collapsed back
    into the ordinary resend.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_unaccepted_reviewer(
            connection,
            organization_id,
            event_id,
            _admin_user_id(connection),
            expires_at_ms=ALREADY_EXPIRED,
        )

        resent = await client.post(
            f"/api/v1/admin/events/{event_id}/invitations/inv-nia/resend",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={},
        )
        assert resent.status_code == 404, resent.text


async def test_lookup_discloses_no_roster_identity_with_the_pending_state(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    """The lookup answers "is this address assignable", never "who is behind it".

    SUBMISSION_MANAGE reaches this route and is satisfied by an event grant of `edit`;
    GET /admin/events/{event_id}/invitations needs RESOURCE_ACCESS_MANAGE, which needs
    `manage`. Because the key here is an email an `edit` collaborator can simply guess,
    every field added to the pending entry is a roster fact leaking around that gate.
    The Reviewers page, which does hold `manage`, reads the name and invitation id from
    the roster instead.
    """
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _organizer(client, connection)
        _seed_unaccepted_reviewer(connection, organization_id, event_id, _admin_user_id(connection))

        found = await client.get(
            f"/api/v1/admin/events/{event_id}/evaluators?email={REVIEWER_EMAIL}"
        )
        assert found.status_code == 200, found.text
        pending = found.json()["pending"][0]
        assert set(pending) == {"email", "expired"}
        # The invited person's name and the invitation id are roster facts, not lookup ones.
        assert "Nia Okafor" not in found.text
        assert "inv-nia" not in found.text

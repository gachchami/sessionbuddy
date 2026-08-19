"""Behavioral organization invitation regressions through the ASGI/D1 harness.

These exercise persistence and authorization, not the separate real-browser UAT gate.
"""

# Imported fixture is registered by pytest and injected by name.
# ruff: noqa: F811
import pytest

from sessionbuddy.platform.auth import organization_invitation_identity, organization_invitations
from tests.security.test_organization_admin_invitations import (
    email_token,
    headers,
    invite,
    verify_new,
)
from tests.security.test_organizer_workflow import _bootstrap_admin
from tests.security.test_production_identity_flow import (
    _client,
    _insert_password_speaker,
    production_environment,  # noqa: F401
)


async def test_roleless_password_sign_in_preserves_invitation_without_granting_access(
    production_environment,
):
    connection, _, env = production_environment
    email = "returning-admin@example.com"
    password = "returning synthetic invitation password"  # noqa: S105
    _insert_password_speaker(connection, user_id="returning", email=email, password=password)
    connection.execute("DELETE FROM user_roles WHERE user_id='returning'")
    connection.commit()
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org, email)
        destination = f"/organization-admin-invitations?invitation={row['id']}"
        signed_in = await recipient.post(
            "/api/v1/auth/password/sign-in",
            json={"email": email, "password": password, "redirect_path": destination},
        )
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["redirect_path"] == destination
        session = (await recipient.get("/api/v1/auth/session")).json()
        assert session["user_id"] == "returning"
        assert session["organization_access"] == []
        assert session["account_roles"] == []
        protected = f"/api/v1/admin/organizations/{org}/events"
        # The event list rejects a non-organizer persona before tenant lookup.
        assert (await recipient.get(protected)).status_code == 403
        assert (
            connection.execute(
                "SELECT status FROM organization_admin_invitations WHERE id=?", (row["id"],)
            ).fetchone()[0]
            == "pending"
        )
        accepted = await recipient.post(
            f"/api/v1/account/organization-invitations/{row['id']}/accept",
            headers=headers(session["csrf_token"]),
            json={},
        )
        assert accepted.status_code == 200, accepted.text
        assert (await recipient.get(protected)).status_code == 200
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM resource_access_grants WHERE user_id='returning' "
                "AND permission='manage' AND revoked_at_ms IS NULL"
            ).fetchone()[0]
            == 1
        )


async def test_wrong_account_cannot_accept_another_recipient_invitation(production_environment):
    connection, _, env = production_environment
    async with _client(env) as owner:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        resolved = await owner.post(
            "/api/v1/organization-admin-invitations/resolve",
            json={"token": email_token(connection, row["email"])},
        )
        assert resolved.status_code == 200
        assert resolved.json() == {"identity_matches": False}
        response = await owner.post(
            f"/api/v1/account/organization-invitations/{row['id']}/accept",
            headers=headers(csrf),
            json={},
        )
        assert response.status_code == 404, response.text
        assert connection.execute(
            "SELECT status,accepted_by_user_id FROM organization_admin_invitations WHERE id=?",
            (row["id"],),
        ).fetchone()[:] == ("pending", None)


async def test_suspended_recipient_verification_does_not_consume_or_create_session(
    production_environment,
):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        session = await verify_new(recipient, connection, row["email"])
        await recipient.post(
            "/api/v1/organization-admin-invitations/verification",
            json={"token": email_token(connection, row["email"])},
        )
        verification = email_token(connection, row["email"], True)
        connection.execute("UPDATE users SET status='suspended' WHERE id=?", (session["user_id"],))
        connection.commit()
        before = connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        response = await recipient.post("/auth/verify", data={"token": verification})
        assert response.status_code == 404, response.text
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM authentication_challenges WHERE "
                "organization_admin_invitation_id=? AND consumed_at_ms IS NULL",
                (row["id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM resource_access_grants WHERE user_id=?", (session["user_id"],)
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize("offset,expected", [(-1, 200), (0, 404)])
async def test_verification_fifteen_minute_boundary_is_exclusive(
    production_environment,
    monkeypatch,
    offset,
    expected,
):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        requested = await recipient.post(
            "/api/v1/organization-admin-invitations/verification",
            json={"token": email_token(connection, row["email"])},
        )
        assert requested.status_code == 202
        challenge = connection.execute(
            "SELECT created_at_ms,expires_at_ms FROM authentication_challenges WHERE "
            "organization_admin_invitation_id=?",
            (row["id"],),
        ).fetchone()
        assert challenge[1] - challenge[0] == 15 * 60 * 1000
        monkeypatch.setattr(
            organization_invitation_identity, "utc_now_ms", lambda: challenge[1] + offset
        )
        response = await recipient.post(
            "/auth/verify", data={"token": email_token(connection, row["email"], True)}
        )
        assert response.status_code == expected, response.text
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM users WHERE normalized_email=?", (row["email"],)
            ).fetchone()[0]
            == 0
        )


async def test_invitation_seventy_two_hour_boundary_refuses_verification_and_resend(
    production_environment,
    monkeypatch,
):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        created = connection.execute(
            "SELECT created_at_ms FROM organization_admin_invitations WHERE id=?", (row["id"],)
        ).fetchone()[0]
        assert row["expires_at_ms"] - created == 72 * 60 * 60 * 1000
        token = email_token(connection, row["email"])
        monkeypatch.setattr(
            organization_invitations, "utc_now_ms", lambda: row["expires_at_ms"] - 1
        )
        before = await recipient.post(
            "/api/v1/organization-admin-invitations/resolve", json={"token": token}
        )
        assert before.json()["status"] == "pending"
        monkeypatch.setattr(organization_invitations, "utc_now_ms", lambda: row["expires_at_ms"])
        expired = await recipient.post(
            "/api/v1/organization-admin-invitations/resolve", json={"token": token}
        )
        assert expired.json()["status"] == "expired"
        assert (
            await recipient.post(
                "/api/v1/organization-admin-invitations/verification", json={"token": token}
            )
        ).status_code == 409
        assert (
            await owner.post(
                f"/api/v1/admin/organizations/{org}/admin-invitations/{row['id']}/resend",
                headers=headers(csrf),
                json={},
            )
        ).status_code == 409
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM authentication_challenges "
                "WHERE organization_admin_invitation_id=?",
                (row["id"],),
            ).fetchone()[0]
            == 0
        )


async def test_duplicate_create_with_distinct_keys_queues_one_invitation_email(
    production_environment,
):
    connection, _, env = production_environment
    async with _client(env) as owner:
        csrf, org = await _bootstrap_admin(owner, connection)
        first = await invite(owner, csrf, org)
        second = await invite(owner, csrf, org)
        assert first["id"] == second["id"]
        assert (
            connection.execute("SELECT COUNT(*) FROM organization_admin_invitations").fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM communication_messages "
                "WHERE deterministic_key LIKE 'organization-admin-invitation:%'"
            ).fetchone()[0]
            == 1
        )


async def test_decline_is_terminal_consumes_live_verification_and_never_grants_access(
    production_environment,
):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        session = await verify_new(recipient, connection, row["email"])
        token = email_token(connection, row["email"])
        assert (
            await recipient.post(
                "/api/v1/organization-admin-invitations/verification", json={"token": token}
            )
        ).status_code == 202
        verification = email_token(connection, row["email"], True)
        declined = await recipient.post(
            f"/api/v1/account/organization-invitations/{row['id']}/decline",
            headers=headers(session["csrf_token"]),
            json={},
        )
        assert declined.status_code == 200, declined.text
        assert declined.json()["status"] == "declined"
        assert (
            await recipient.post(
                f"/api/v1/account/organization-invitations/{row['id']}/accept",
                headers=headers(session["csrf_token"]),
                json={},
            )
        ).status_code == 409
        assert (await recipient.post("/auth/verify", data={"token": verification})).status_code in (
            404,
            409,
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM authentication_challenges "
                "WHERE organization_admin_invitation_id=? AND consumed_at_ms IS NULL",
                (row["id"],),
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM resource_access_grants WHERE user_id=?", (session["user_id"],)
            ).fetchone()[0]
            == 0
        )
        assert (await recipient.get(f"/api/v1/admin/organizations/{org}/events")).status_code == 403

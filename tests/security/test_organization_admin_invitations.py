# Fixture injection shadows its imported registration; SQL identifiers are a fixed test tuple.
# ruff: noqa: F811, S608
import re
import sqlite3
import uuid

import pytest

from sessionbuddy.platform.auth.organization_grants import append_organization_manage_grant
from sessionbuddy.platform.db.commands import CommandBatch
from tests.security.test_organizer_workflow import _bootstrap_admin, _mutation
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)


def headers(csrf):
    return {**_mutation(csrf), "Idempotency-Key": str(uuid.uuid4())}


async def test_concurrent_grant_does_not_double_bump_authorization(
    production_environment, monkeypatch
):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        session = await verify_new(recipient, connection, row["email"])
        uid = session["user_id"]
        before = connection.execute(
            "SELECT authorization_version FROM users WHERE id=?", (uid,)
        ).fetchone()[0]
        original = env.DB.batch
        raced = []

        async def interleave(statements):
            if not raced and any("accepted_by_user_id=CASE" in s.sql for s in statements):
                raced.append(True)
                batch = CommandBatch(env.DB)
                append_organization_manage_grant(
                    batch,
                    env.DB,
                    organization_id=org,
                    user_id=uid,
                    granted_by_user_id=connection.execute(
                        "SELECT invited_by_user_id FROM organization_admin_invitations WHERE id=?",
                        (row["id"],),
                    ).fetchone()[0],
                    now=row["expires_at_ms"] - 259200000,
                )
                await batch.execute()
            return await original(statements)

        monkeypatch.setattr(env.DB, "batch", interleave)
        result = await recipient.post(
            f"/api/v1/account/organization-invitations/{row['id']}/accept",
            headers=headers(session["csrf_token"]),
            json={},
        )
        assert result.status_code == 200, result.text
        assert result.json()["already_admin"] is True
        assert raced
        assert (
            connection.execute(
                "SELECT authorization_version FROM users WHERE id=?", (uid,)
            ).fetchone()[0]
            == before + 1
        )


async def test_invalid_registration_password_preserves_challenge(production_environment):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        await recipient.post(
            "/api/v1/organization-admin-invitations/verification",
            json={"token": email_token(connection, row["email"])},
        )
        result = await recipient.post(
            "/auth/verify",
            data={
                "token": email_token(connection, row["email"], True),
                "first_name": "New",
                "last_name": "Admin",
                "password": "short",
                "password_confirmation": "short",
            },
        )
        assert result.status_code == 422
        assert (
            connection.execute(
                "SELECT consumed_at_ms FROM authentication_challenges "
                "WHERE organization_admin_invitation_id=?",
                (row["id"],),
            ).fetchone()[0]
            is None
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM users WHERE normalized_email=?", (row["email"],)
            ).fetchone()[0]
            == 0
        )


def email_token(connection, email, verify=False):
    prefix = "organization-admin-invitation-verify:" if verify else "organization-admin-invitation:"
    row = connection.execute(
        """SELECT html_body FROM communication_messages
        WHERE recipient_email=? AND deterministic_key LIKE ? ORDER BY rowid DESC LIMIT 1""",
        (email, prefix + "%"),
    ).fetchone()
    return re.search(r"#token=([^\"<]+)", row[0])[1]


async def invite(owner, csrf, org, email="new-admin@example.com"):
    result = await owner.post(
        f"/api/v1/admin/organizations/{org}/admin-invitations",
        headers=headers(csrf),
        json={"email": email},
    )
    assert result.status_code == 200, result.text
    assert "token" not in result.json()
    return result.json()


async def verify_new(client, connection, email):
    token = email_token(connection, email)
    requested = await client.post(
        "/api/v1/organization-admin-invitations/verification", json={"token": token}
    )
    assert requested.status_code == 202, requested.text
    verify = email_token(connection, email, True)
    preview = await client.post("/auth/verify", data={"token": verify})
    assert preview.status_code == 200, preview.text
    assert "Create your account" in preview.text
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM users WHERE normalized_email=?", (email,)
        ).fetchone()[0]
        == 0
    )
    response = await client.post(
        "/auth/verify",
        data={
            "token": verify,
            "first_name": "New",
            "last_name": "Admin",
            "password": "A fresh unique passphrase 938!",
            "password_confirmation": "A fresh unique passphrase 938!",
        },
    )
    assert response.status_code == 303, response.text
    session = await client.get("/api/v1/auth/session")
    assert session.status_code == 200, session.text
    return session.json()


async def test_new_identity_has_no_access_until_explicit_accept(production_environment):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        token = email_token(connection, row["email"])
        resolved = await recipient.post(
            "/api/v1/organization-admin-invitations/resolve", json={"token": token}
        )
        assert resolved.status_code == 200, resolved.text
        assert "email" not in resolved.json()
        session = await verify_new(recipient, connection, row["email"])
        assert session["organization_access"] == []
        assert session["account_roles"] == []
        uid = session["user_id"]
        for table in ("user_roles", "organization_memberships", "resource_access_grants"):
            assert (
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (uid,)
                ).fetchone()[0]
                == 0
            )
        pending = await recipient.get("/api/v1/account/invitations")
        assert pending.json()["pending_organization_invitations"][0]["id"] == row["id"]
        accepted = await recipient.post(
            f"/api/v1/account/organization-invitations/{row['id']}/accept",
            headers=headers(session["csrf_token"]),
            json={},
        )
        assert accepted.status_code == 200, accepted.text
        current = (await recipient.get("/api/v1/auth/session")).json()
        assert current["active_role"] == "organizer"
        assert current["organization_access"][0]["organization_id"] == org
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


async def test_resend_rotates_both_tokens_but_never_extends_expiry(production_environment):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as guest:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        old = email_token(connection, row["email"])
        for _ in range(2):
            assert (
                await guest.post(
                    "/api/v1/organization-admin-invitations/verification", json={"token": old}
                )
            ).status_code == 202
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM authentication_challenges WHERE "
                "organization_admin_invitation_id=?",
                (row["id"],),
            ).fetchone()[0]
            == 1
        )
        verification = email_token(connection, row["email"], True)
        resent = await owner.post(
            f"/api/v1/admin/organizations/{org}/admin-invitations/{row['id']}/resend",
            headers=headers(csrf),
            json={},
        )
        assert resent.status_code == 200, resent.text
        assert resent.json()["expires_at_ms"] == row["expires_at_ms"]
        assert email_token(connection, row["email"]) != old
        assert (
            await guest.post("/api/v1/organization-admin-invitations/resolve", json={"token": old})
        ).status_code == 404
        assert (await guest.post("/auth/verify", data={"token": verification})).status_code == 404


async def test_reissue_is_atomic_and_replaces_same_recipient(production_environment):
    connection, _, env = production_environment
    async with _client(env) as owner:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        result = await owner.post(
            f"/api/v1/admin/organizations/{org}/admin-invitations/{row['id']}/reissue",
            headers=headers(csrf),
            json={},
        )
        assert result.status_code == 200, result.text
        assert result.json()["id"] != row["id"]
        old = connection.execute(
            "SELECT status,superseded_by_id FROM organization_admin_invitations WHERE id=?",
            (row["id"],),
        ).fetchone()
        assert tuple(old) == ("revoked", result.json()["id"])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


async def test_registration_failure_rolls_back_challenge_and_user(
    production_environment, monkeypatch
):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        assert (
            await recipient.post(
                "/api/v1/organization-admin-invitations/verification",
                json={"token": email_token(connection, row["email"])},
            )
        ).status_code == 202
        raw = email_token(connection, row["email"], True)
        original = env.DB.batch
        reached = []

        async def fail_batch(statements):
            if any("INSERT INTO password_credentials" in s.sql for s in statements):
                reached.append(True)
                statements.insert(-1, env.DB.prepare("INSERT INTO users(id) VALUES(NULL)"))
            return await original(statements)

        monkeypatch.setattr(env.DB, "batch", fail_batch)
        response = await recipient.post(
            "/auth/verify",
            data={
                "token": raw,
                "first_name": "New",
                "last_name": "Admin",
                "password": "A fresh unique passphrase 938!",
                "password_confirmation": "A fresh unique passphrase 938!",
            },
        )
        assert response.status_code == 409
        assert reached
        assert (
            connection.execute(
                "SELECT consumed_at_ms FROM authentication_challenges WHERE "
                "organization_admin_invitation_id=?",
                (row["id"],),
            ).fetchone()[0]
            is None
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM users WHERE normalized_email=?", (row["email"],)
            ).fetchone()[0]
            == 0
        )


async def test_concurrent_verification_loser_is_202_without_duplicate_email(
    production_environment, monkeypatch
):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as first, _client(env) as second:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        token = email_token(connection, row["email"])
        original = env.DB.batch
        raced = []

        async def interleave(statements):
            if not raced and any(
                "INSERT INTO authentication_challenges" in s.sql
                and "organization_admin_invitation_id" in s.sql
                for s in statements
            ):
                raced.append(True)
                winner = await second.post(
                    "/api/v1/organization-admin-invitations/verification", json={"token": token}
                )
                assert winner.status_code == 202, winner.text
            return await original(statements)

        monkeypatch.setattr(env.DB, "batch", interleave)
        loser = await first.post(
            "/api/v1/organization-admin-invitations/verification", json={"token": token}
        )
        assert loser.status_code == 202, loser.text
        assert raced
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM authentication_challenges WHERE "
                "organization_admin_invitation_id=?",
                (row["id"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM communication_messages WHERE deterministic_key LIKE ?",
                (f"organization-admin-invitation-verify:{row['id']}:%",),
            ).fetchone()[0]
            == 1
        )


async def test_inviter_authority_lost_during_acceptance_is_checked_inside_batch(
    production_environment, monkeypatch
):
    connection, _, env = production_environment
    async with _client(env) as owner, _client(env) as recipient:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        session = await verify_new(recipient, connection, row["email"])
        original = env.DB.batch
        raced = []

        async def interleave(statements):
            if not raced and any("accepted_by_user_id=CASE" in s.sql for s in statements):
                raced.append(True)
                connection.execute(
                    "UPDATE users SET status='suspended' WHERE id=(SELECT "
                    "invited_by_user_id FROM organization_admin_invitations WHERE id=?)",
                    (row["id"],),
                )
                connection.commit()
            return await original(statements)

        monkeypatch.setattr(env.DB, "batch", interleave)
        result = await recipient.post(
            f"/api/v1/account/organization-invitations/{row['id']}/accept",
            headers=headers(session["csrf_token"]),
            json={},
        )
        assert result.status_code == 409, result.text
        assert result.json()["error"]["metadata"]["cause"] == "inviter_authority_lost"
        assert raced
        assert (
            connection.execute(
                "SELECT status FROM organization_admin_invitations WHERE id=?", (row["id"],)
            ).fetchone()[0]
            == "pending"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM resource_access_grants WHERE user_id=?", (session["user_id"],)
            ).fetchone()[0]
            == 0
        )


async def test_invitation_expiry_immutable_and_cross_scope_challenge_rejected(
    production_environment,
):
    connection, _, env = production_environment
    async with _client(env) as owner:
        csrf, org = await _bootstrap_admin(owner, connection)
        row = await invite(owner, csrf, org)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE organization_admin_invitations SET "
                "expires_at_ms=expires_at_ms+1 WHERE id=?",
                (row["id"],),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO authentication_challenges
             (id,normalized_email,token_hash,purpose,provisioning_context,redirect_path,expires_at_ms,created_at_ms,organization_id,organization_admin_invitation_id)
             VALUES('bad','other@example.com',X'1234','verify_email','invitation','/account',2,1,?,?)""",
                (org, row["id"]),
            )
        connection.rollback()

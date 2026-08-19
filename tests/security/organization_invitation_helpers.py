# Test-only passwords for isolated disposable fixtures.
# ruff: noqa: S106
"""Test fixtures that obtain administrator access through consent, never direct grants."""

import re
import uuid


def mutation(csrf):
    return {"origin": "https://test", "x-csrf-token": csrf, "Idempotency-Key": str(uuid.uuid4())}


async def accept_organization_invitation(client, connection, email):
    row = connection.execute(
        """SELECT id FROM organization_admin_invitations
      WHERE normalized_email=? AND status='pending' ORDER BY created_at_ms DESC LIMIT 1""",
        (email,),
    ).fetchone()
    assert row
    message = connection.execute(
        """SELECT html_body FROM communication_messages
      WHERE deterministic_key LIKE ? ORDER BY rowid DESC LIMIT 1""",
        (f"organization-admin-invitation:{row[0]}:%",),
    ).fetchone()
    raw = re.search(r"#token=([^\"<]+)", message[0])[1]
    sent = await client.post(
        "/api/v1/organization-admin-invitations/verification", json={"token": raw}
    )
    assert sent.status_code == 202, sent.text
    message = connection.execute(
        """SELECT html_body FROM communication_messages
      WHERE deterministic_key LIKE ? ORDER BY rowid DESC LIMIT 1""",
        (f"organization-admin-invitation-verify:{row[0]}:%",),
    ).fetchone()
    token = re.search(r"#token=([^\"<]+)", message[0])[1]
    first = await client.post("/auth/verify", data={"token": token})
    assert first.status_code == 200, first.text
    data = {"token": token, "organization_confirm": "yes"}
    if (
        connection.execute("SELECT id FROM users WHERE normalized_email=?", (email,)).fetchone()
        is None
    ):
        data.update(
            first_name="Invited",
            last_name="Admin",
            password="Unique invitation fixture passphrase!",
            password_confirmation="Unique invitation fixture passphrase!",
        )
    verified = await client.post("/auth/verify", data=data)
    assert verified.status_code == 303, verified.text
    session = (await client.get("/api/v1/auth/session")).json()
    accepted = await client.post(
        f"/api/v1/account/organization-invitations/{row[0]}/accept",
        headers=mutation(session["csrf_token"]),
        json={},
    )
    assert accepted.status_code == 200, accepted.text
    return (await client.get("/api/v1/auth/session")).json()


async def invite_and_accept(owner, connection, environment, org, csrf, email):
    from tests.security.test_production_identity_flow import _client

    invited = await owner.post(
        f"/api/v1/admin/organizations/{org}/admin-invitations",
        headers=mutation(csrf),
        json={"email": email},
    )
    assert invited.status_code == 200, invited.text
    async with _client(environment) as recipient:
        await accept_organization_invitation(recipient, connection, email)
    return await owner.get(f"/api/v1/admin/organizations/{org}/access-grants")

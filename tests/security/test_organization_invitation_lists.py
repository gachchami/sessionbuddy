"""List delivery semantics and bounded D1 calls, using the real SQL adapter."""

# Fixture registration and fixed test SQL identifiers.
# ruff: noqa: F811
import json
import uuid

import pytest

from sessionbuddy.platform.auth.organization_invitations import DELIVERIES, LIFETIME_MS
from sessionbuddy.platform.db.types import utc_now_ms
from tests.security.test_organizer_workflow import _bootstrap_admin
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)


def seed_org(connection, owner, now):
    org = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO organizations(id,name,status,created_at_ms,updated_at_ms) "
        "VALUES(?,'Other organization','active',?,?)",
        (org, now, now),
    )
    connection.execute(
        "INSERT INTO organization_memberships(id,organization_id,user_id,role,status,"
        "created_at_ms,updated_at_ms) VALUES(?,?,?,'organization_admin','active',?,?)",
        (str(uuid.uuid4()), org, owner, now, now),
    )
    connection.execute(
        "INSERT INTO owned_resources(id,resource_type,created_by_user_id,owner_user_id,"
        "created_at_ms,updated_at_ms) VALUES(?,'organization',?,?,?,?)",
        (org, owner, owner, now, now),
    )
    return org


def seed_invitation(connection, org, owner, now, email):
    iid = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO organization_admin_invitations(id,organization_id,email,normalized_email,"
        "invited_by_user_id,token_hash,expires_at_ms,created_at_ms,updated_at_ms) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (iid, org, email, email, owner, uuid.uuid4().bytes, now + LIFETIME_MS, now, now),
    )
    return iid


def seed_message(
    connection,
    org,
    iid,
    now,
    *,
    status="delivered",
    error=None,
    prefix="organization-admin-invitation",
    mid=None,
):
    mid = mid or str(uuid.uuid4())
    connection.execute(
        "INSERT INTO communication_messages(id,organization_id,recipient_email,subject,"
        "html_body,deterministic_key,status,last_error_code,queued_at_ms,updated_at_ms) "
        "VALUES(?,?,'fixture@example.com','Fixture','Fixture',?,?,?,?,?)",
        (mid, org, f"{prefix}:{iid}:{mid}", status, error, now, now),
    )


@pytest.mark.parametrize("account", [False, True], ids=["admin-list", "account-list"])
async def test_list_delivery_calls_stay_constant_at_page_limit(production_environment, account):
    connection, _, env = production_environment
    async with _client(env) as client:
        _, org = await _bootstrap_admin(client, connection)
        owner = connection.execute(
            "SELECT id FROM users WHERE normalized_email='root@example.com'"
        ).fetchone()[0]
        now = utc_now_ms()
        url = (
            "/api/v1/account/invitations"
            if account
            else f"/api/v1/admin/organizations/{org}/admin-invitations"
        )
        key = "pending_organization_invitations" if account else "data"
        first = seed_invitation(connection, org, owner, now, "root@example.com")
        seed_message(connection, org, first, now)
        connection.commit()
        env.DB.prepare_count = 0
        small = await client.get(url)
        assert small.status_code == 200, small.text
        assert len(small.json()[key]) == 1
        small_calls = env.DB.prepare_count
        for index in range(1, 105):
            target = seed_org(connection, owner, now) if account else org
            email = "root@example.com" if account else f"recipient-{index}@example.com"
            iid = seed_invitation(connection, target, owner, now + index, email)
            seed_message(connection, target, iid, now + index)
        unrelated = None
        if account:
            unrelated = seed_invitation(
                connection, org, owner, now + 1000, "someone-else@example.com"
            )
            seed_message(connection, org, unrelated, now + 1000)
        connection.commit()
        env.DB.prepare_count = 0
        large = await client.get(url)
        assert large.status_code == 200, large.text
        assert len(large.json()[key]) == 100
        assert env.DB.prepare_count == small_calls
        assert all(row["delivery"]["status"] == "delivered" for row in large.json()[key])
        assert first not in {row["id"] for row in large.json()[key]}
        assert unrelated not in {row["id"] for row in large.json()[key]}


async def test_delivery_is_latest_exact_invitation_and_tenant_scoped(production_environment):
    connection, _, env = production_environment
    async with _client(env) as client:
        _, org = await _bootstrap_admin(client, connection)
        owner = connection.execute(
            "SELECT id FROM users WHERE normalized_email='root@example.com'"
        ).fetchone()[0]
        now = utc_now_ms()
        iid = seed_invitation(connection, org, owner, now, "one@example.com")
        missing = seed_invitation(connection, org, owner, now, "two@example.com")
        other_org = seed_org(connection, owner, now)
        seed_invitation(connection, other_org, owner, now, "hidden@example.com")
        seed_message(connection, org, iid, now, status="delivered")
        seed_message(connection, org, iid, now + 1, status="queued", mid="a")
        seed_message(
            connection, org, iid, now + 1, status="failed", error="private-provider-error", mid="z"
        )
        seed_message(connection, other_org, iid, now + 100)
        seed_message(connection, org, iid, now + 100, prefix="organization-admin-invitation-verify")
        seed_message(connection, org, iid + "-different", now + 100)
        connection.commit()
        result = await client.get(f"/api/v1/admin/organizations/{org}/admin-invitations")
        assert result.status_code == 200, result.text
        rows = {row["id"]: row for row in result.json()["data"]}
        assert set(rows) == {iid, missing}
        assert rows[iid]["delivery"] == {
            "status": "failed",
            "reason": "unknown",
            "updated_at_ms": now + 1,
        }
        assert rows[missing]["delivery"] == {
            "status": "none",
            "reason": None,
            "updated_at_ms": None,
        }
        assert "private-provider-error" not in result.text
        assert "token_hash" not in result.text


async def test_delivery_query_plan_bounds_message_lookup_by_tenant_and_prefix(
    production_environment,
):
    connection, _, env = production_environment
    async with _client(env) as client:
        _, org = await _bootstrap_admin(client, connection)
        owner = connection.execute(
            "SELECT id FROM users WHERE normalized_email='root@example.com'"
        ).fetchone()[0]
        now = utc_now_ms()
        iid = seed_invitation(connection, org, owner, now, "one@example.com")
        for index in range(5000):
            seed_message(connection, org, f"unrelated-{index}", now)
        seed_message(connection, org, iid, now)
        connection.commit()
        parameters = (json.dumps([{"id": iid, "organization_id": org}]),)
        plan = [
            row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + DELIVERIES, parameters)
        ]
        assert any(
            "SEARCH message USING INDEX" in detail
            and "organization_id=?" in detail
            and "deterministic_key>?" in detail
            and "deterministic_key<?" in detail
            for detail in plan
        ), plan
        rows = connection.execute(DELIVERIES, parameters).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "delivered"

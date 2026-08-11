import sqlite3
import time

import pytest

from sessionbuddy.platform.activity import (
    dispatch_pending_activities,
    distribute_activity,
)
from sessionbuddy.platform.db.commands import ActivityRecord, AuditEvent, CommandBatch
from tests.security.test_organizer_workflow import _bootstrap_admin
from tests.security.test_production_identity_flow import (
    CapturingQueue,
    _client,
    production_environment,  # noqa: F401
)


async def test_audited_mutation_is_dispatched_and_idempotently_projected_by_role(
    production_environment,  # noqa: F811
) -> None:
    connection, _communication_queue, environment = production_environment
    async with _client(environment) as owner:
        _csrf, organization_id = await _bootstrap_admin(owner, connection)
    owner_user_id = connection.execute(
        "SELECT owner_user_id FROM owned_resources WHERE id=?", (organization_id,)
    ).fetchone()[0]
    now = int(time.time() * 1000)
    for role in ("reviewer", "speaker"):
        connection.execute(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES(?,?,'active',?,?,0)""",
            (owner_user_id, role, now, now),
        )
    connection.commit()
    batch = CommandBatch(environment.DB)
    batch.activity(
        ActivityRecord(
            id="A101",
            actor_type="user",
            actor_id=owner_user_id,
            operation="update",
            resource_type="organization",
            resource_id=organization_id,
            organization_id=organization_id,
            occurred_at_ms=now,
        )
    )
    await batch.execute()
    assert connection.execute(
        "SELECT status FROM activity_status WHERE activity_id='A101'"
    ).fetchone()[0] == "UNPROCESSED"

    queue = CapturingQueue()
    dispatched = await dispatch_pending_activities(environment.DB, queue, now, limit=100)
    assert dispatched.published >= 1
    assert dispatched.failed == 0
    assert queue.messages

    target = next(
        message
        for message in queue.messages
        if message["activity_id"] == "A101"
    )
    first = await distribute_activity(environment.DB, target, now + 1)
    replay = await distribute_activity(environment.DB, target, now + 2)
    assert first.acknowledged and first.projected
    assert replay.acknowledged and not replay.projected
    activity_id = target["activity_id"]
    assert connection.execute(
        "SELECT COUNT(*) FROM organization_activity WHERE activity_id=?",
        (activity_id,),
    ).fetchone()[0] == 1
    for table in ("organizer_activity", "reviewer_activity", "speaker_activity"):
        assert connection.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE activity_id=?',  # noqa: S608
            (activity_id,),
        ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT processed_at_ms FROM activity_status WHERE activity_id=?", (activity_id,)
    ).fetchone()[0] == now + 1
    assert connection.execute(
        "SELECT status FROM activity_status WHERE activity_id=?", (activity_id,)
    ).fetchone()[0] == "PROCESSED"


async def test_activity_projector_acks_malformed_and_retries_missing_activity(
    production_environment,  # noqa: F811
) -> None:
    _connection, _queue, environment = production_environment
    malformed = await distribute_activity(environment.DB, {"activity_id": "x"}, 1)
    missing = await distribute_activity(
        environment.DB, {"schema_version": 1, "activity_id": "missing"}, 1
    )
    assert malformed.acknowledged and malformed.reason == "malformed_envelope"
    assert not missing.acknowledged and missing.reason == "activity_not_found"


async def test_only_successful_audited_crud_is_recorded_atomically(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        _csrf, organization_id = await _bootstrap_admin(owner, connection)
    actor_id = connection.execute(
        "SELECT owner_user_id FROM owned_resources WHERE id=?", (organization_id,)
    ).fetchone()[0]
    before = connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    batch = CommandBatch(environment.DB)
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=actor_id,
            action="organization.update",
            target_type="organization",
            target_id=organization_id,
            result="failed",
            reason_code="conflict",
            correlation_id="request-failed",
            organization_id=organization_id,
            occurred_at_ms=100,
        )
    )
    await batch.execute()
    assert connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == before

    batch = CommandBatch(environment.DB)
    batch.audit(
        AuditEvent(
            actor_type="user",
            actor_user_id=actor_id,
            action="organization.update",
            target_type="organization",
            target_id=organization_id,
            result="succeeded",
            correlation_id="request-success",
            organization_id=organization_id,
            occurred_at_ms=101,
        )
    )
    await batch.execute()
    activity = connection.execute(
        """SELECT a.operation,a.resource_type,a.resource_id,s.status
           FROM activities a JOIN activity_status s ON s.activity_id=a.id
           JOIN activity_entities entity ON entity.public_id=a.resource_id
           WHERE a.operation='update' AND a.resource_type='organization'
             AND entity.internal_id=?
           ORDER BY a.occurred_at_ms DESC LIMIT 1""",
        (organization_id,),
    ).fetchone()
    assert activity[0] == "update"
    assert activity[1] == "organization"
    assert activity[2].startswith("X") and activity[2][1:].isdigit()
    assert activity[3] == "UNPROCESSED"


async def test_distribution_rows_and_processed_marker_are_one_transaction(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        _csrf, organization_id = await _bootstrap_admin(owner, connection)
    actor_id = connection.execute(
        "SELECT owner_user_id FROM owned_resources WHERE id=?", (organization_id,)
    ).fetchone()[0]
    now = int(time.time() * 1000)
    connection.execute(
        """INSERT INTO user_roles
           (user_id,role,status,created_at_ms,updated_at_ms,is_default)
           VALUES(?, 'speaker','active',?,?,0)""",
        (actor_id, now, now),
    )
    connection.commit()
    batch = CommandBatch(environment.DB)
    batch.activity(
        ActivityRecord(
            id="A102",
            actor_type="user",
            actor_id=actor_id,
            operation="update",
            resource_type="organization",
            resource_id=organization_id,
            organization_id=organization_id,
            occurred_at_ms=now,
        )
    )
    await batch.execute()
    connection.execute("DROP TABLE speaker_activity")
    connection.commit()

    result = await distribute_activity(
        environment.DB,
        {"schema_version": 1, "activity_id": "A102"},
        now + 1,
    )
    assert not result.acknowledged and result.reason == "distribution_failed"
    assert connection.execute(
        "SELECT COUNT(*) FROM organization_activity WHERE activity_id='A102'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT status FROM activity_status WHERE activity_id='A102'"
    ).fetchone()[0] == "UNPROCESSED"


async def test_activity_uses_typed_public_references(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as owner:
        _csrf, organization_id = await _bootstrap_admin(owner, connection)
    actor_id = connection.execute(
        "SELECT owner_user_id FROM owned_resources WHERE id=?", (organization_id,)
    ).fetchone()[0]
    batch = CommandBatch(environment.DB)
    batch.activity(
        ActivityRecord(
            actor_type="user",
            actor_id=actor_id,
            operation="create",
            resource_type="submission",
            resource_id="internal-proposal-id",
            organization_id=organization_id,
            occurred_at_ms=200,
        )
    )
    await batch.execute()
    row = connection.execute(
        """SELECT id,actor_id,resource_type,resource_id FROM activities
           WHERE occurred_at_ms=200"""
    ).fetchone()
    assert row[0].startswith("A") and row[0][1:].isdigit()
    assert row[1].startswith("U") and row[1][1:].isdigit()
    assert row[2] == "proposal"
    assert row[3].startswith("P") and row[3][1:].isdigit()


async def test_distribution_guard_rejects_a_lost_claim(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, _environment = production_environment
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO activity_distribution_guards(activity_id,claim_token)
               VALUES('missing',(
                 SELECT claim_token FROM activity_status
                 WHERE activity_id='missing' AND claim_token='lost'
               ))"""
        )


async def test_organization_activity_endpoint_is_tenant_scoped_and_safe(
    production_environment,  # noqa: F811
) -> None:
    connection, _communication_queue, environment = production_environment
    async with _client(environment) as owner:
        _csrf, organization_id = await _bootstrap_admin(owner, connection)
        queue = CapturingQueue()
        now = int(time.time() * 1000)
        await dispatch_pending_activities(environment.DB, queue, now, limit=100)
        for message in queue.messages:
            await distribute_activity(environment.DB, message, now + 1)
        response = await owner.get(
            f"/api/v1/admin/organizations/{organization_id}/activities"
        )
        assert response.status_code == 200
        activities = response.json()["data"]
        assert activities
        assert set(activities[0]) == {
            "activity_id",
            "actor_id",
            "actor_name",
            "operation",
            "resource_type",
            "resource_id",
            "subject_name",
            "event_id",
            "occurred_at_ms",
        }
        organization_activity = next(
            item for item in activities if item["resource_type"] == "organization"
        )
        assert organization_activity["subject_name"] == "Summit Events"
        assert "metadata" not in response.text
        assert "email" not in response.text
        assert (
            await owner.get("/api/v1/admin/organizations/foreign/activities")
        ).status_code == 404

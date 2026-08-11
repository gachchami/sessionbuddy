"""Durable activity dispatch and atomic feed distribution."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from sessionbuddy.platform.db.commands import CommandBatch
from sessionbuddy.platform.db.d1 import D1Database, result_rows, row_mapping
from sessionbuddy.platform.db.types import new_id

_CLAIM_TTL_MS = 5 * 60 * 1000
_QUEUE_TTL_MS = 60 * 1000
_MAX_ATTEMPTS = 5


@dataclass(frozen=True, slots=True)
class ActivityDispatchResult:
    claimed: int = 0
    published: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class ActivityProjectionResult:
    acknowledged: bool
    projected: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ActivityContext:
    activity_id: str
    actor_id: str | None
    organization_id: str | None
    event_id: str | None
    occurred_at_ms: int
    actor_roles: frozenset[str]


class ActivityProcessor(Protocol):
    def applies(self, context: ActivityContext) -> bool: ...

    def add(
        self, batch: CommandBatch, db: D1Database, context: ActivityContext
    ) -> None: ...


class OrganizationActivity:
    """Build the organization feed row when the activity is organization scoped."""

    @staticmethod
    def applies(context: ActivityContext) -> bool:
        return context.organization_id is not None

    @staticmethod
    def add(batch: CommandBatch, db: D1Database, context: ActivityContext) -> None:
        batch.add_statement(
            db.prepare(
                """INSERT OR IGNORE INTO organization_activity
                   (organization_id,activity_id,occurred_at_ms) VALUES(?1,?2,?3)"""
            ).bind(context.organization_id, context.activity_id, context.occurred_at_ms)
        )


class EventActivity:
    """Build the event feed row when the activity is event scoped."""

    @staticmethod
    def applies(context: ActivityContext) -> bool:
        return context.organization_id is not None and context.event_id is not None

    @staticmethod
    def add(batch: CommandBatch, db: D1Database, context: ActivityContext) -> None:
        batch.add_statement(
            db.prepare(
                """INSERT OR IGNORE INTO event_activity
                   (organization_id,event_id,activity_id,occurred_at_ms)
                   VALUES(?1,?2,?3,?4)"""
            ).bind(
                context.organization_id,
                context.event_id,
                context.activity_id,
                context.occurred_at_ms,
            )
        )


class ActorRoleActivity:
    """Build an actor feed row for one active persona."""

    def __init__(self, role: str) -> None:
        if role not in {"organizer", "reviewer", "speaker"}:
            raise ValueError("unsupported activity role")
        self.role = role

    def applies(self, context: ActivityContext) -> bool:
        return context.actor_id is not None and self.role in context.actor_roles

    def add(self, batch: CommandBatch, db: D1Database, context: ActivityContext) -> None:
        table = {
            "organizer": "organizer_activity",
            "reviewer": "reviewer_activity",
            "speaker": "speaker_activity",
        }[self.role]
        batch.add_statement(
            db.prepare(
                f"""INSERT OR IGNORE INTO {table}
                    (user_id,activity_id,occurred_at_ms) VALUES(?1,?2,?3)"""  # noqa: S608
            ).bind(context.actor_id, context.activity_id, context.occurred_at_ms)
        )


_PROCESSORS: tuple[ActivityProcessor, ...] = (
    OrganizationActivity(),
    EventActivity(),
    ActorRoleActivity("organizer"),
    ActorRoleActivity("reviewer"),
    ActorRoleActivity("speaker"),
)


def activity_envelope(activity_id: str) -> dict[str, object]:
    return {"schema_version": 1, "activity_id": activity_id}


def parse_activity_envelope(value: object) -> str | None:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "activity_id"}:
        return None
    if value.get("schema_version") != 1:
        return None
    activity_id = value.get("activity_id")
    if not isinstance(activity_id, str) or not activity_id or len(activity_id) > 100:
        return None
    return activity_id


async def dispatch_pending_activities(
    db: D1Database, queue, now_ms: int, *, limit: int = 100
) -> ActivityDispatchResult:
    """Publish unprocessed activity IDs; distribution state remains in D1."""
    if not 1 <= limit <= 100:
        raise ValueError("activity dispatch limit must be between 1 and 100")
    candidates = result_rows(
        await db.prepare(
            """SELECT activity_id FROM activity_status
               WHERE status='UNPROCESSED'
                 AND (queued_at_ms IS NULL OR queued_at_ms<?1)
               ORDER BY updated_at_ms,activity_id LIMIT ?2"""
        ).bind(now_ms - _QUEUE_TTL_MS, limit).all()
    )
    published = failed = 0
    for candidate in candidates:
        activity_id = str(candidate["activity_id"])
        try:
            await queue.send(activity_envelope(activity_id))
        except Exception as error:  # noqa: BLE001 - provider boundary
            failed += 1
            await db.prepare(
                """UPDATE activity_status SET last_error_code=?1,updated_at_ms=?2
                   WHERE activity_id=?3 AND status='UNPROCESSED'"""
            ).bind(type(error).__name__, now_ms, activity_id).run()
            continue
        await db.prepare(
            """UPDATE activity_status SET queued_at_ms=?1,last_error_code=NULL,
                 updated_at_ms=?1 WHERE activity_id=?2 AND status='UNPROCESSED'"""
        ).bind(now_ms, activity_id).run()
        published += 1
    return ActivityDispatchResult(
        claimed=len(candidates), published=published, failed=failed
    )


async def distribute_activity(
    db: D1Database, envelope: object, now_ms: int
) -> ActivityProjectionResult:
    """Invoke all applicable builders and commit projections plus marker atomically."""
    activity_id = parse_activity_envelope(envelope)
    if activity_id is None:
        return ActivityProjectionResult(acknowledged=True, reason="malformed_envelope")

    claim_token = new_id()
    await db.prepare(
        """UPDATE activity_status SET status='PROCESSING',claim_token=?1,
             claimed_at_ms=?2,attempt_count=attempt_count+1,updated_at_ms=?2
           WHERE activity_id=?3 AND (
             status='UNPROCESSED' OR
             (status='PROCESSING' AND claimed_at_ms<?4)
           )"""
    ).bind(claim_token, now_ms, activity_id, now_ms - _CLAIM_TTL_MS).run()
    claimed = await db.prepare(
        """SELECT status,attempt_count FROM activity_status
           WHERE activity_id=?1 AND claim_token=?2"""
    ).bind(activity_id, claim_token).first()
    if claimed is None:
        status = await db.prepare(
            "SELECT status FROM activity_status WHERE activity_id=?1"
        ).bind(activity_id).first("status")
        if status == "PROCESSED":
            return ActivityProjectionResult(acknowledged=True, projected=False)
        return ActivityProjectionResult(
            acknowledged=False,
            reason="activity_not_found" if status is None else "activity_claimed",
        )

    row = row_mapping(
        await db.prepare(
            """SELECT a.id,actor_entity.internal_id AS actor_internal_id,
                      a.occurred_at_ms,
                      r.organization_id,r.event_id
               FROM activities a
               LEFT JOIN activity_routing r ON r.activity_id=a.id
               LEFT JOIN activity_entities actor_entity
                 ON actor_entity.public_id=a.actor_id
               WHERE a.id=?1 LIMIT 1"""
        ).bind(activity_id).first()
    )
    if row is None:
        await _release_claim(db, activity_id, claim_token, now_ms, "activity_not_found")
        return ActivityProjectionResult(acknowledged=False, reason="activity_not_found")

    actor_id = row.get("actor_internal_id")
    roles: frozenset[str] = frozenset()
    if isinstance(actor_id, str) and actor_id:
        roles = frozenset(
            str(role["role"])
            for role in result_rows(
                await db.prepare(
                    """SELECT role FROM user_roles WHERE user_id=?1 AND status='active'
                       AND role IN ('organizer','reviewer','speaker')"""
                ).bind(actor_id).all()
            )
        )
    context = ActivityContext(
        activity_id=activity_id,
        actor_id=actor_id if isinstance(actor_id, str) and actor_id else None,
        organization_id=(
            str(row["organization_id"]) if row.get("organization_id") else None
        ),
        event_id=str(row["event_id"]) if row.get("event_id") else None,
        occurred_at_ms=int(row["occurred_at_ms"]),
        actor_roles=roles,
    )

    batch = CommandBatch(db)
    batch.add_statement(
        db.prepare(
            """INSERT OR REPLACE INTO activity_distribution_guards
               (activity_id,claim_token)
               VALUES(?1,(
                 SELECT claim_token FROM activity_status
                 WHERE activity_id=?1 AND status='PROCESSING' AND claim_token=?2
               ))"""
        ).bind(activity_id, claim_token)
    )
    for processor in _PROCESSORS:
        if processor.applies(context):
            processor.add(batch, db, context)
    batch.add_statement(
        db.prepare(
            """UPDATE activity_status SET status='PROCESSED',claim_token=NULL,
                 claimed_at_ms=NULL,processed_at_ms=?1,last_error_code=NULL,
                 updated_at_ms=?1 WHERE activity_id=?2 AND status='PROCESSING'
                 AND claim_token=?3"""
        ).bind(now_ms, activity_id, claim_token)
    )
    batch.add_statement(
        db.prepare(
            """DELETE FROM activity_distribution_guards
               WHERE activity_id=?1 AND claim_token=?2"""
        ).bind(activity_id, claim_token)
    )
    try:
        await batch.execute()
    except Exception as error:  # noqa: BLE001 - transaction boundary
        await _release_claim(
            db, activity_id, claim_token, now_ms, type(error).__name__
        )
        return ActivityProjectionResult(acknowledged=False, reason="distribution_failed")
    return ActivityProjectionResult(acknowledged=True, projected=True)


async def _release_claim(
    db: D1Database,
    activity_id: str,
    claim_token: str,
    now_ms: int,
    error_code: str,
) -> None:
    await db.prepare(
        """UPDATE activity_status SET
             status=CASE WHEN attempt_count>=?1 THEN 'FAILED' ELSE 'UNPROCESSED' END,
             claim_token=NULL,claimed_at_ms=NULL,queued_at_ms=NULL,
             last_error_code=?2,updated_at_ms=?3
           WHERE activity_id=?4 AND claim_token=?5"""
    ).bind(_MAX_ATTEMPTS, error_code, now_ms, activity_id, claim_token).run()


def activity_log(event: str, **fields: object) -> str:
    return json.dumps({"event": event, **fields}, separators=(",", ":"), sort_keys=True)

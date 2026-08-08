"""Production-shaped Queue delivery and Workflow runtime boundaries."""

import json
from base64 import b64encode
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from sessionbuddy.platform.db.d1 import (
    D1Database,
    execute_batch,
    row_mapping,
    to_python,
)
from sessionbuddy.platform.db.types import new_id

from .rendering import render_template


@dataclass(frozen=True, slots=True)
class DeliveryEnvelope:
    schema_version: Literal[1]
    message_id: str

    @classmethod
    def parse(cls, raw: str | bytes | Mapping[str, object] | object) -> "DeliveryEnvelope":
        value = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else to_python(raw)
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "message_id"}:
            raise ValueError("invalid delivery envelope")
        if value["schema_version"] != 1 or not isinstance(value["message_id"], str):
            raise ValueError("invalid delivery envelope")
        if not 1 <= len(value["message_id"]) <= 100:
            raise ValueError("invalid delivery envelope")
        return cls(1, value["message_id"])


@dataclass(frozen=True, slots=True)
class ReminderEnvelope:
    schema_version: Literal[1]
    schedule_id: str
    schedule_version: int

    @classmethod
    def parse(cls, value: Mapping[str, object] | object) -> "ReminderEnvelope":
        converted = to_python(value)
        required = {"schema_version", "schedule_id", "schedule_version"}
        if not isinstance(converted, Mapping) or set(converted) != required:
            raise ValueError("invalid reminder envelope")
        if (
            converted["schema_version"] != 1
            or not isinstance(converted["schedule_id"], str)
            or not isinstance(converted["schedule_version"], int)
            or int(converted["schedule_version"]) < 1
        ):
            raise ValueError("invalid reminder envelope")
        return cls(1, converted["schedule_id"], int(converted["schedule_version"]))


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    message_id: str
    organization_id: str
    event_id: str
    recipient_email: str
    subject: str
    html_body: str
    deterministic_key: str
    attempt_number: int
    calendar_ics: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderResult:
    delivered: bool
    provider_message_id: str | None = None
    retryable: bool = False
    error_code: Literal["provider_rejected", "provider_unavailable", "invalid_response"] | None = (
        None
    )


class DeliveryProvider(Protocol):
    async def send(self, claim: DeliveryClaim) -> ProviderResult: ...


class DeliveryRepository(Protocol):
    async def claim(self, message_id: str, now_ms: int) -> DeliveryClaim | None: ...
    async def delivered(self, claim: DeliveryClaim, provider_id: str, now_ms: int) -> None: ...
    async def failed(
        self, claim: DeliveryClaim, code: str, retryable: bool, now_ms: int
    ) -> None: ...


class D1DeliveryRepository:
    def __init__(self, db: D1Database) -> None:
        self.db = db

    async def claim(self, message_id: str, now_ms: int) -> DeliveryClaim | None:
        row = row_mapping(
            await self.db.prepare(
                """SELECT cm.id,cm.organization_id,cm.event_id,cm.recipient_email,
                          cm.subject,cm.html_body,cm.deterministic_key,cm.attempt_count,
                          cm.status,civ.ics_content
                   FROM communication_messages cm
                   LEFT JOIN calendar_invitation_versions civ
                     ON civ.organization_id=cm.organization_id AND civ.event_id=cm.event_id
                    AND civ.communication_message_id=cm.id
                   WHERE cm.id=?1 LIMIT 1"""
            )
            .bind(message_id)
            .first()
        )
        if row is None or row["status"] not in {"queued", "failed"}:
            return None
        attempt = int(row["attempt_count"]) + 1
        try:
            result = await execute_batch(
                self.db,
                [
                    self.db.prepare(
                        """UPDATE communication_messages SET status='sending',attempt_count=?1,
                              last_error_code=NULL,updated_at_ms=?2
                       WHERE id=?3 AND attempt_count=?4 AND status IN ('queued','failed')"""
                    ).bind(attempt, now_ms, message_id, attempt - 1),
                    self.db.prepare(
                        """INSERT INTO communication_delivery_attempts
                       (id,organization_id,event_id,message_id,attempt_number,status,started_at_ms)
                       SELECT ?1,organization_id,event_id,id,?2,'started',?3
                       FROM communication_messages WHERE id=?4 AND status='sending'
                         AND attempt_count=?2"""
                    ).bind(new_id(), attempt, now_ms, message_id),
                ],
            )
        except Exception:
            return None
        results = to_python(result)
        if isinstance(results, list) and results:
            first = to_python(results[0])
            meta = first.get("meta", first) if isinstance(first, dict) else {}
            if isinstance(meta, dict) and int(meta.get("changes", 1)) == 0:
                return None
        return DeliveryClaim(
            message_id=str(row["id"]),
            organization_id=str(row["organization_id"]),
            event_id=str(row["event_id"]),
            recipient_email=str(row["recipient_email"]),
            subject=str(row["subject"]),
            html_body=str(row["html_body"]),
            deterministic_key=str(row["deterministic_key"]),
            attempt_number=attempt,
            calendar_ics=str(row["ics_content"]) if row.get("ics_content") is not None else None,
        )

    async def delivered(self, claim: DeliveryClaim, provider_id: str, now_ms: int) -> None:
        await execute_batch(
            self.db,
            [
                self.db.prepare(
                    """UPDATE communication_messages SET status='delivered',provider_message_id=?1,
                          delivered_at_ms=?2,updated_at_ms=?2
                   WHERE id=?3 AND status='sending' AND attempt_count=?4"""
                ).bind(provider_id, now_ms, claim.message_id, claim.attempt_number),
                self.db.prepare(
                    """UPDATE communication_delivery_attempts SET status='delivered',
                          provider_message_id=?1,completed_at_ms=?2
                   WHERE message_id=?3 AND attempt_number=?4 AND status='started'"""
                ).bind(provider_id, now_ms, claim.message_id, claim.attempt_number),
            ],
        )

    async def failed(self, claim: DeliveryClaim, code: str, retryable: bool, now_ms: int) -> None:
        attempt_state = "retryable_failure" if retryable else "permanent_failure"
        await execute_batch(
            self.db,
            [
                self.db.prepare(
                    """UPDATE communication_messages SET status='failed',last_error_code=?1,
                          updated_at_ms=?2 WHERE id=?3 AND status='sending' AND attempt_count=?4"""
                ).bind(code, now_ms, claim.message_id, claim.attempt_number),
                self.db.prepare(
                    """UPDATE communication_delivery_attempts SET status=?1,error_code=?2,
                          completed_at_ms=?3 WHERE message_id=?4 AND attempt_number=?5
                          AND status='started'"""
                ).bind(attempt_state, code, now_ms, claim.message_id, claim.attempt_number),
            ],
        )


class ResendProvider:
    def __init__(self, api_key: str, from_address: str, *, fetcher=None) -> None:
        if not api_key or not from_address:
            raise ValueError("Resend configuration is required")
        self.api_key, self.from_address, self.fetcher = api_key, from_address, fetcher

    async def send(self, claim: DeliveryClaim) -> ProviderResult:
        fetcher = self.fetcher
        if fetcher is None:
            from workers import fetch

            fetcher = fetch
        try:
            payload: dict[str, object] = {
                "from": self.from_address,
                "to": [claim.recipient_email],
                "subject": claim.subject,
                "html": claim.html_body,
            }
            if claim.calendar_ics is not None:
                payload["attachments"] = [
                    {
                        "filename": "session.ics",
                        "content": b64encode(claim.calendar_ics.encode()).decode(),
                    }
                ]
            response = await fetcher(
                "https://api.resend.com/emails",
                method="POST",
                headers={
                    "authorization": f"Bearer {self.api_key}",
                    "content-type": "application/json",
                    "idempotency-key": claim.deterministic_key,
                },
                body=json.dumps(payload, separators=(",", ":")),
            )
        except Exception:
            return ProviderResult(False, retryable=True, error_code="provider_unavailable")
        if 200 <= int(response.status) < 300:
            try:
                payload = to_python(await response.json())
                provider_id = str(payload["id"])
            except Exception:
                return ProviderResult(False, retryable=True, error_code="invalid_response")
            return ProviderResult(True, provider_message_id=provider_id)
        retryable = int(response.status) == 429 or int(response.status) >= 500
        return ProviderResult(
            False,
            retryable=retryable,
            error_code="provider_unavailable" if retryable else "provider_rejected",
        )


async def consume_delivery(
    raw: str | bytes | Mapping[str, object] | object,
    repository: DeliveryRepository,
    provider: DeliveryProvider,
    now_ms: int,
) -> bool:
    try:
        envelope = DeliveryEnvelope.parse(raw)
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return True
    claim = await repository.claim(envelope.message_id, now_ms)
    if claim is None:
        return True
    result = await provider.send(claim)
    if result.delivered and result.provider_message_id:
        await repository.delivered(claim, result.provider_message_id, now_ms)
        return True
    code = result.error_code or "invalid_response"
    await repository.failed(claim, code, result.retryable, now_ms)
    return not result.retryable


async def consume_reminder(
    db: D1Database,
    envelope: ReminderEnvelope | Mapping[str, object] | object,
    now_ms: int,
) -> str | None:
    """Atomically materialize one due schedule version into one queued message."""
    parsed = (
        envelope if isinstance(envelope, ReminderEnvelope) else ReminderEnvelope.parse(envelope)
    )
    row = row_mapping(
        await db.prepare(
            """SELECT rs.id,rs.organization_id,rs.event_id,rs.schedule_version,
                      rs.recipient_user_id,rs.send_at_ms,u.email,
                      COALESCE(p.display_name,u.email) AS display_name,e.name AS event_name,
                      st.title AS task_title,st.due_at_ms,ct.id AS template_id,
                      ct.subject_template,ct.html_template
               FROM reminder_schedules rs
               JOIN users u ON u.id=rs.recipient_user_id
               JOIN events e ON e.organization_id=rs.organization_id AND e.id=rs.event_id
               JOIN speaker_tasks st ON st.organization_id=rs.organization_id
                AND st.event_id=rs.event_id AND st.id=rs.task_id
               JOIN event_speakers es ON es.organization_id=st.organization_id
                AND es.event_id=st.event_id AND es.id=st.event_speaker_id
               JOIN people p ON p.organization_id=es.organization_id AND p.id=es.person_id
               JOIN communication_templates ct ON ct.organization_id=rs.organization_id
                AND ct.event_id=rs.event_id AND ct.id=rs.template_id
               WHERE rs.id=?1 AND rs.schedule_version=?2 AND rs.state='scheduled'
                 AND rs.send_at_ms<=?3 LIMIT 1"""
        )
        .bind(parsed.schedule_id, parsed.schedule_version, now_ms)
        .first()
    )
    if row is None:
        return None
    deadline = ""
    if row["due_at_ms"] is not None:
        deadline = datetime.fromtimestamp(int(row["due_at_ms"]) / 1000, UTC).isoformat()
    values = {
        "event.name": str(row["event_name"]),
        "speaker.name": str(row["display_name"]),
        "task.title": str(row["task_title"]),
        "task.deadline": deadline,
    }
    subject = render_template(str(row["subject_template"]), values)
    html_body = render_template(str(row["html_template"]), values)
    message_id, outbox_id = new_id(), new_id()
    deterministic = f"reminder:{parsed.schedule_id}:v{parsed.schedule_version}"
    result = await execute_batch(
        db,
        [
            db.prepare(
                """INSERT INTO communication_messages
                   (id,organization_id,event_id,template_id,recipient_user_id,recipient_email,
                    subject,html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
                   SELECT ?1,organization_id,event_id,template_id,recipient_user_id,?2,
                          ?3,?4,?5,'queued',?6,?6 FROM reminder_schedules
                   WHERE id=?7 AND schedule_version=?8 AND state='scheduled' AND send_at_ms<=?6"""
            ).bind(
                message_id,
                row["email"],
                subject,
                html_body,
                deterministic,
                now_ms,
                parsed.schedule_id,
                parsed.schedule_version,
            ),
            db.prepare(
                """UPDATE reminder_schedules SET state='dispatched',dispatched_at_ms=?1,
                          updated_at_ms=?1 WHERE id=?2 AND schedule_version=?3
                          AND state='scheduled' AND send_at_ms<=?1"""
            ).bind(now_ms, parsed.schedule_id, parsed.schedule_version),
            db.prepare(
                """INSERT INTO outbox_messages
                   (id,organization_id,event_id,topic,payload_version,aggregate_type,
                    aggregate_id,deduplication_key,payload_json,available_at_ms,created_at_ms)
                   SELECT ?1,organization_id,event_id,'communication.delivery.requested',1,
                          'communication_message',id,deterministic_key,?2,?3,?3
                   FROM communication_messages WHERE id=?4"""
            ).bind(
                outbox_id,
                json.dumps({"schema_version": 1, "message_id": message_id}, separators=(",", ":")),
                now_ms,
                message_id,
            ),
        ],
    )
    converted = to_python(result)
    if isinstance(converted, list) and converted:
        first = to_python(converted[0])
        meta = first.get("meta", first) if isinstance(first, dict) else {}
        if isinstance(meta, dict) and int(meta.get("changes", 1)) == 0:
            return None
    return message_id


try:
    from workers import WorkflowEntrypoint as _WorkflowEntrypoint
except Exception:

    class _WorkflowEntrypoint:
        pass


class ReminderWorkflow(_WorkflowEntrypoint):
    async def run(self, event, step):
        converted_event = to_python(event)
        if not isinstance(converted_event, Mapping) or "payload" not in converted_event:
            raise ValueError("invalid reminder workflow event")
        payload = to_python(converted_event["payload"])
        required = {"schema_version", "schedule_id", "schedule_version", "send_at_ms"}
        if (
            not isinstance(payload, dict)
            or set(payload) != required
            or payload["schema_version"] != 1
        ):
            raise ValueError("invalid reminder workflow payload")
        timestamp = datetime.fromtimestamp(int(payload["send_at_ms"]) / 1000, UTC)
        await step.sleep_until("wait-until-reminder", timestamp)
        await self.env.COMMUNICATION_QUEUE.send(
            {
                "schema_version": 1,
                "schedule_id": str(payload["schedule_id"]),
                "schedule_version": int(payload["schedule_version"]),
            }
        )

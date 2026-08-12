"""Tenant-scoped D1 communications service used by HTTP and local development."""

import hashlib
from html import escape

from fastapi import HTTPException, Request

from sessionbuddy.platform.authorization import Actor, ResourceContext
from sessionbuddy.platform.db.commands import (
    AuditEvent,
    CommandBatch,
    IdempotencyRecord,
)
from sessionbuddy.platform.db.d1 import PersistenceError, result_rows, row_mapping
from sessionbuddy.platform.db.types import new_id, utc_now_ms
from sessionbuddy.platform.signed_cursors import decode_signed_cursor, encode_signed_cursor

from .models import (
    CommunicationStatus,
    CommunicationStatusList,
    DispatchResponse,
    ManualSendRequest,
    ManualSendResponse,
    RecipientPreview,
    RecipientPreviewRequest,
    RecipientPreviewResponse,
    ReminderQueuedResponse,
    SpeakerMessagePreviewRequest,
    SpeakerMessageSendRequest,
)
from .rendering import render_template, validate_template


def _status_cursor(
    request: Request,
    value: str | None,
    *,
    organization_id: str,
    event_id: str,
) -> tuple[int, str] | None:
    decoded = decode_signed_cursor(
        request,
        value,
        scope={"event": event_id, "organization": organization_id},
        position_fields={"id", "ts"},
    )
    if decoded is None:
        return None
    timestamp, row_id = decoded["ts"], decoded["id"]
    if type(timestamp) is not int or not isinstance(row_id, str) or not 1 <= len(row_id) <= 100:
        raise HTTPException(status_code=400, detail="Invalid or expired cursor")
    return timestamp, row_id


def _status_next_cursor(
    request: Request,
    *,
    organization_id: str,
    event_id: str,
    timestamp: int,
    row_id: str,
) -> str:
    return encode_signed_cursor(
        request,
        scope={"event": event_id, "organization": organization_id},
        position={"id": row_id, "ts": timestamp},
    )


class D1CommunicationsService:
    def __init__(self, request: Request) -> None:
        self.request = request
        self.db = getattr(request.scope.get("env"), "DB", None)
        if self.db is None:
            raise HTTPException(status_code=503)
        self.actor: Actor | None = None
        self.organization_id: str | None = None

    async def _publish_delivery_requests(self, message_ids: list[str]) -> None:
        environment = self.request.scope.get("env")
        queue = getattr(environment, "COMMUNICATION_QUEUE", None)
        if queue is None:
            # Lightweight unit/local harnesses may intentionally omit a queue.
            # A real local Worker binds the same queue consumer used in deployed
            # environments so messages reach Mailpit instead of staying queued.
            if getattr(environment, "APP_ENV", "production") == "local":
                return
            raise HTTPException(status_code=503)
        try:
            for message_id in message_ids:
                await queue.send({"schema_version": 1, "message_id": message_id})
        except Exception as exc:
            # The committed queued message is recoverable by the scheduled dispatcher.
            raise HTTPException(status_code=503) from exc

    async def context_for_event(self, actor: Actor, event_id: str) -> ResourceContext:
        self.actor = actor
        row = row_mapping(
            await self.db.prepare(
                "SELECT organization_id FROM events WHERE id=?1 AND status!='archived' LIMIT 1"
            )
            .bind(event_id)
            .first()
        )
        self.organization_id = str(row["organization_id"]) if row else None
        return ResourceContext(
            str(row["organization_id"]) if row else "missing",
            event_id,
            resource_exists=row is not None,
        )

    async def _template(self, event_id: str, template_id: str) -> dict[str, object]:
        if self.organization_id is None:
            raise HTTPException(status_code=404)
        row = row_mapping(
            await self.db.prepare(
                """SELECT t.id,t.organization_id,t.subject_template,t.html_template,
                          e.name AS event_name
               FROM communication_templates t JOIN events e
                 ON e.organization_id=t.organization_id AND e.id=t.event_id
                   WHERE t.organization_id=?1 AND t.event_id=?2 AND t.id=?3 LIMIT 1"""
            )
            .bind(self.organization_id, event_id, template_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404)
        return row

    async def preview(
        self, event_id: str, body: RecipientPreviewRequest
    ) -> RecipientPreviewResponse:
        template = await self._template(event_id, body.template_id)
        recipients: list[RecipientPreview] = []
        for user_id in dict.fromkeys(body.recipient_user_ids):
            row = row_mapping(
                await self.db.prepare(
                    """SELECT u.id,u.email,COALESCE(p.display_name,u.email) AS display_name
                   FROM users u JOIN organization_memberships om ON om.user_id=u.id
                    AND om.organization_id=?1 AND om.status='active'
                   LEFT JOIN people p ON p.organization_id=om.organization_id AND p.user_id=u.id
                   WHERE u.id=?2 AND u.status='active' LIMIT 1"""
                )
                .bind(template["organization_id"], user_id)
                .first()
            )
            if row is None:
                raise HTTPException(status_code=404)
            values = {
                "event.name": str(template["event_name"]),
                "speaker.name": str(row["display_name"]),
            }
            recipients.append(
                RecipientPreview(
                    recipient_user_id=str(row["id"]),
                    display_name=str(row["display_name"]),
                    email=str(row["email"]),
                    subject=render_template(str(template["subject_template"]), values),
                    html_body=render_template(str(template["html_template"]), values),
                )
            )
        return RecipientPreviewResponse(recipients=recipients)

    async def queue_manual_send(
        self, event_id: str, body: ManualSendRequest, idempotency_key: str
    ) -> ManualSendResponse:
        preview = await self.preview(event_id, body)
        template = await self._template(event_id, body.template_id)
        now = utc_now_ms()
        fingerprint = hashlib.sha256(body.model_dump_json().encode()).digest()
        record = IdempotencyRecord(
            principal_key=self.actor.user_id if self.actor else "missing-actor",
            route_key="communications.send",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            organization_id=str(template["organization_id"]),
            event_id=event_id,
            expires_at_ms=now + 86_400_000,
        )
        replay = row_mapping(
            await self.db.prepare(
                """SELECT request_fingerprint,state FROM idempotency_records
               WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                 AND event_id=?4 LIMIT 1"""
            )
            .bind(record.principal_key, record.route_key, record.key_hash, event_id)
            .first()
        )
        if replay is not None:
            if (
                bytes(replay["request_fingerprint"]) != fingerprint
                or replay["state"] != "completed"
            ):
                raise HTTPException(status_code=409)
            ids: list[str] = []
            for recipient in preview.recipients:
                deterministic_key = hashlib.sha256(
                    f"manual:{event_id}:{body.template_id}:{recipient.recipient_user_id}:{idempotency_key}".encode()
                ).hexdigest()
                existing = (
                    await self.db.prepare(
                        """SELECT id FROM communication_messages
                           WHERE organization_id=?1 AND event_id=?2
                             AND deterministic_key=?3"""
                    )
                    .bind(template["organization_id"], event_id, deterministic_key)
                    .first("id")
                )
                if existing is not None:
                    ids.append(str(existing))
            try:
                await self._publish_delivery_requests(ids)
            except HTTPException:
                pass
            return ManualSendResponse(message_ids=ids)
        batch, ids = CommandBatch(self.db), []
        batch.begin_idempotency(record, now)
        for recipient in preview.recipients:
            message_id = new_id()
            ids.append(message_id)
            deterministic_key = hashlib.sha256(
                f"manual:{event_id}:{body.template_id}:{recipient.recipient_user_id}:{idempotency_key}".encode()
            ).hexdigest()
            batch.add_statement(
                self.db.prepare(
                    """INSERT INTO communication_messages
                   (id,organization_id,event_id,template_id,recipient_user_id,recipient_email,
                    subject,html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
                   VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,'queued',?10,?10)"""
                ).bind(
                    message_id,
                    template["organization_id"],
                    event_id,
                    body.template_id,
                    recipient.recipient_user_id,
                    recipient.email,
                    recipient.subject,
                    recipient.html_body,
                    deterministic_key,
                    now,
                )
            )
        batch.audit(
            AuditEvent(
                organization_id=str(template["organization_id"]),
                event_id=event_id,
                actor_type="user",
                actor_user_id=self.actor.user_id if self.actor else None,
                action="communication.manual.queue",
                target_type="communication_batch",
                result="succeeded",
                correlation_id=self.request.state.request_id,
                occurred_at_ms=now,
                metadata={"recipient_count": len(ids)},
            )
        )
        batch.complete_idempotency(
            record,
            status=202,
            resource_type="communication_batch",
            resource_id=record.id,
            completed_at_ms=now,
        )
        try:
            await batch.execute()
        except PersistenceError as exc:
            raise HTTPException(status_code=409) from exc
        try:
            await self._publish_delivery_requests(ids)
        except HTTPException:
            # The messages are already committed. A
            # transient Queue publish failure must not tell the caller that the
            # send failed; the scheduled dispatcher can safely retry it.
            pass
        return ManualSendResponse(message_ids=ids)

    async def preview_speaker_message(
        self, event_id: str, body: SpeakerMessagePreviewRequest
    ) -> RecipientPreviewResponse:
        if self.organization_id is None:
            raise HTTPException(status_code=404)
        recipients: list[RecipientPreview] = []
        for recipient_target_id in body.event_speaker_ids:
            row = row_mapping(
                await self.db.prepare(
                    """SELECT u.id AS recipient_user_id,es.id AS recipient_target_id,
                              'active' AS recipient_state,u.email,p.display_name,
                              e.name AS event_name,
                              -- Prefer the ACCEPTED submission; fall back to newest.
                              -- queue_speaker_message renders what it sends from this same
                              -- query, so a newest-wins title here is not a preview artefact:
                              -- it is the text mailed to the speaker.
                              COALESCE((SELECT ac.organizer_title
                                FROM accepted_sessions ac
                                JOIN accepted_session_participants participant
                                  ON participant.accepted_session_id=ac.id
                                WHERE ac.organization_id=es.organization_id
                                  AND ac.event_id=es.event_id
                                  AND participant.event_speaker_id=es.id
                                  AND ac.source_type='organizer_created'
                                ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),
                               (SELECT s.proposal_title
                                FROM submission_speakers ss JOIN submissions s
                                  ON s.organization_id=ss.organization_id
                                 AND s.event_id=ss.event_id AND s.id=ss.submission_id
                                JOIN accepted_sessions ac
                                  ON ac.organization_id=s.organization_id
                                 AND ac.event_id=s.event_id AND ac.submission_id=s.id
                                WHERE ss.organization_id=es.organization_id
                                  AND ss.event_id=es.event_id
                                  AND ss.event_speaker_id=es.id
                                ORDER BY ac.created_at_ms DESC,ac.id DESC LIMIT 1),
                               (SELECT s.proposal_title
                                FROM submission_speakers ss JOIN submissions s
                                  ON s.organization_id=ss.organization_id
                                 AND s.event_id=ss.event_id AND s.id=ss.submission_id
                                WHERE ss.organization_id=es.organization_id
                                  AND ss.event_id=es.event_id
                                  AND ss.event_speaker_id=es.id
                                ORDER BY s.submitted_at_ms DESC,s.id DESC LIMIT 1),'')
                                AS proposal_title
                       FROM event_speakers es
                       JOIN people p ON p.organization_id=es.organization_id AND p.id=es.person_id
                       JOIN users u ON u.id=p.user_id AND u.status='active'
                       JOIN event_memberships em ON em.organization_id=es.organization_id
                         AND em.event_id=es.event_id AND em.user_id=u.id
                         AND em.role='speaker' AND em.status='active'
                       JOIN events e ON e.organization_id=es.organization_id AND e.id=es.event_id
                       WHERE es.id=?1 AND es.organization_id=?2 AND es.event_id=?3
                         AND es.status!='withdrawn' LIMIT 1"""
                )
                .bind(recipient_target_id, self.organization_id, event_id)
                .first()
            )
            if row is None:
                row = row_mapping(
                    await self.db.prepare(
                        """SELECT NULL AS recipient_user_id,i.id AS recipient_target_id,
                                  'invited' AS recipient_state,i.email AS email,
                                  i.display_name,e.name AS event_name,'' AS proposal_title
                           FROM identity_invitations i
                           JOIN events e ON e.organization_id=i.organization_id
                             AND e.id=i.event_id
                           WHERE i.id=?1 AND i.organization_id=?2 AND i.event_id=?3
                             AND i.role='speaker' AND i.status='pending'
                             AND i.expires_at_ms>?4 LIMIT 1"""
                    )
                    .bind(
                        recipient_target_id,
                        self.organization_id,
                        event_id,
                        utc_now_ms(),
                    )
                    .first()
                )
            if row is None:
                raise HTTPException(status_code=404)
            display_name = str(row["display_name"])
            public_base = str(getattr(self.request.scope.get("env"), "PUBLIC_BASE_URL", "")).rstrip(
                "/"
            )
            values = {
                "event.name": str(row["event_name"]),
                "speaker.name": display_name,
                "speaker.first_name": display_name.split(maxsplit=1)[0],
            }
            if row["recipient_state"] == "active":
                values.update(
                    {
                        "submission.title": str(row["proposal_title"]),
                        "portal.link": (
                            f"{public_base}/speaker" if public_base else "/speaker"
                        ),
                    }
                )
            try:
                required = validate_template(body.subject) | validate_template(body.body_text)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            missing = sorted(required - values.keys())
            if missing:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{display_name} cannot receive this template; missing "
                        f"{', '.join(missing)}. Choose an invitation-safe template "
                        "or remove this recipient."
                    ),
                )
            try:
                rendered_subject = render_template(body.subject, values)
                rendered_body = render_template(body.body_text, values)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            recipients.append(
                RecipientPreview(
                    recipient_user_id=(
                        str(row["recipient_user_id"])
                        if row["recipient_user_id"] is not None
                        else None
                    ),
                    recipient_target_id=str(row["recipient_target_id"]),
                    recipient_state=str(row["recipient_state"]),
                    display_name=display_name,
                    email=str(row["email"]),
                    subject=rendered_subject,
                    html_body=f"<p>{rendered_body.replace(chr(10), '<br>')}</p>",
                )
            )
        return RecipientPreviewResponse(recipients=recipients)

    async def queue_speaker_message(
        self, event_id: str, body: SpeakerMessageSendRequest, idempotency_key: str
    ) -> ManualSendResponse:
        preview = await self.preview_speaker_message(event_id, body)
        if self.organization_id is None:
            raise HTTPException(status_code=404)
        now = utc_now_ms()
        fingerprint = hashlib.sha256(body.model_dump_json().encode()).digest()
        record = IdempotencyRecord(
            principal_key=self.actor.user_id if self.actor else "missing-actor",
            route_key="communications.speakers.send",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            organization_id=self.organization_id,
            event_id=event_id,
            expires_at_ms=now + 86_400_000,
        )
        replay = row_mapping(
            await self.db.prepare(
                """SELECT request_fingerprint,state FROM idempotency_records
                   WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                     AND event_id=?4 LIMIT 1"""
            )
            .bind(record.principal_key, record.route_key, record.key_hash, event_id)
            .first()
        )
        if replay is not None:
            if (
                bytes(replay["request_fingerprint"]) != fingerprint
                or replay["state"] != "completed"
            ):
                raise HTTPException(status_code=409)
            ids = []
            for recipient in preview.recipients:
                existing = await (
                    self.db.prepare(
                        """SELECT id FROM communication_messages
                           WHERE organization_id=?1 AND event_id=?2
                             AND deterministic_key=?3 LIMIT 1"""
                    )
                    .bind(
                        self.organization_id,
                        event_id,
                        f"speaker-bulk:{idempotency_key}:{recipient.recipient_target_id}",
                    )
                    .first("id")
                )
                if existing is not None:
                    ids.append(str(existing))
            try:
                await self._publish_delivery_requests(ids)
            except HTTPException:
                pass
            return ManualSendResponse(message_ids=ids)
        batch, ids = CommandBatch(self.db), []
        batch.begin_idempotency(record, now)
        for recipient in preview.recipients:
            message_id = new_id()
            ids.append(message_id)
            deterministic_key = f"speaker-bulk:{idempotency_key}:{recipient.recipient_target_id}"
            batch.add_statement(
                self.db.prepare(
                    """INSERT INTO communication_messages
                       (id,organization_id,event_id,recipient_user_id,recipient_email,subject,
                        html_body,deterministic_key,status,queued_at_ms,updated_at_ms)
                       VALUES(?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9)"""
                ).bind(
                    message_id,
                    self.organization_id,
                    event_id,
                    recipient.recipient_user_id,
                    recipient.email,
                    recipient.subject,
                    recipient.html_body,
                    deterministic_key,
                    now,
                )
            )
        batch.audit(
            AuditEvent(
                organization_id=self.organization_id,
                event_id=event_id,
                actor_type="user",
                actor_user_id=self.actor.user_id if self.actor else None,
                action="communication.speakers.queue",
                target_type="communication_batch",
                result="succeeded",
                correlation_id=self.request.state.request_id,
                occurred_at_ms=now,
                metadata={"recipient_count": len(ids)},
            )
        )
        batch.complete_idempotency(
            record,
            status=202,
            resource_type="communication_batch",
            resource_id=record.id,
            completed_at_ms=now,
        )
        try:
            await batch.execute()
        except PersistenceError as exc:
            raise HTTPException(status_code=409) from exc
        try:
            await self._publish_delivery_requests(ids)
        except HTTPException:
            # D1 already owns durable delivery state at this point.
            pass
        return ManualSendResponse(message_ids=ids)

    async def queue_task_reminder(
        self, event_id: str, task_id: str, idempotency_key: str
    ) -> ReminderQueuedResponse:
        row = row_mapping(
            await self.db.prepare(
                """SELECT st.organization_id,st.id,st.title,e.name AS event_name,u.id AS user_id,
                      u.email,COALESCE(p.display_name,u.email) AS display_name
               FROM speaker_tasks st JOIN event_speakers es ON es.organization_id=st.organization_id
                AND es.event_id=st.event_id AND es.id=st.event_speaker_id
               JOIN people p ON p.organization_id=es.organization_id AND p.id=es.person_id
               JOIN users u ON u.id=p.user_id JOIN events e ON e.organization_id=st.organization_id
                AND e.id=st.event_id
                   WHERE st.organization_id=?1 AND st.event_id=?2 AND st.id=?3
                     AND st.state='open' LIMIT 1"""
            )
            .bind(self.organization_id, event_id, task_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404)
        now, message_id = utc_now_ms(), new_id()
        deterministic = hashlib.sha256(f"task:{task_id}:{idempotency_key}".encode()).hexdigest()
        fingerprint = hashlib.sha256(f"task-reminder:{event_id}:{task_id}".encode()).digest()
        record = IdempotencyRecord(
            principal_key=self.actor.user_id if self.actor else "missing-actor",
            route_key="speaker-tasks.reminders",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            organization_id=str(row["organization_id"]),
            event_id=event_id,
            expires_at_ms=now + 86_400_000,
        )
        replay = row_mapping(
            await self.db.prepare(
                """SELECT request_fingerprint,state FROM idempotency_records
                   WHERE principal_key=?1 AND route_key=?2 AND idempotency_key_hash=?3
                     AND event_id=?4 LIMIT 1"""
            )
            .bind(record.principal_key, record.route_key, record.key_hash, event_id)
            .first()
        )
        existing = (
            await self.db.prepare(
                """SELECT id FROM communication_messages WHERE organization_id=?1
               AND event_id=?2 AND deterministic_key=?3 LIMIT 1"""
            )
            .bind(row["organization_id"], event_id, deterministic)
            .first("id")
        )
        if replay is not None:
            if (
                bytes(replay["request_fingerprint"]) != fingerprint
                or replay["state"] != "completed"
                or existing is None
            ):
                raise HTTPException(status_code=409)
            await self._publish_delivery_requests([str(existing)])
            return ReminderQueuedResponse(message_id=str(existing))
        subject = f"Reminder: {escape(str(row['title']))}"
        html_body = (
            f"<p>Hello {escape(str(row['display_name']))},</p>"
            f"<p>{escape(str(row['title']))} is still due for "
            f"{escape(str(row['event_name']))}.</p>"
        )
        batch = CommandBatch(self.db)
        batch.begin_idempotency(record, now)
        batch.add_statement(
            self.db.prepare(
                """INSERT INTO communication_messages
               (id,organization_id,event_id,recipient_user_id,recipient_email,subject,html_body,
                deterministic_key,status,queued_at_ms,updated_at_ms)
               VALUES (?1,?2,?3,?4,?5,?6,?7,?8,'queued',?9,?9)"""
            ).bind(
                message_id,
                row["organization_id"],
                event_id,
                row["user_id"],
                row["email"],
                subject,
                html_body,
                deterministic,
                now,
            )
        )
        batch.audit(
            AuditEvent(
                organization_id=str(row["organization_id"]),
                event_id=event_id,
                actor_type="user",
                actor_user_id=self.actor.user_id if self.actor else None,
                action="communication.task_reminder.queue",
                target_type="speaker_task",
                target_id=task_id,
                result="succeeded",
                correlation_id=self.request.state.request_id,
                occurred_at_ms=now,
            )
        )
        batch.complete_idempotency(
            record,
            status=202,
            resource_type="communication_message",
            resource_id=message_id,
            completed_at_ms=now,
        )
        try:
            await batch.execute()
        except PersistenceError as exc:
            raise HTTPException(status_code=409) from exc
        await self._publish_delivery_requests([message_id])
        return ReminderQueuedResponse(message_id=message_id)

    async def statuses(
        self, event_id: str, *, cursor: str | None = None, limit: int = 25
    ) -> CommunicationStatusList:
        if self.organization_id is None:
            raise HTTPException(status_code=404)
        position = _status_cursor(
            self.request,
            cursor,
            organization_id=self.organization_id,
            event_id=event_id,
        )
        before_ms, before_id = position if position is not None else (None, None)
        if before_ms is None:
            statement = self.db.prepare(
                """SELECT id,recipient_email,subject,status,attempt_count,
                          provider_message_id,last_error_code,updated_at_ms
                   FROM communication_messages WHERE organization_id=?1 AND event_id=?2
                   ORDER BY updated_at_ms DESC,id DESC LIMIT ?3"""
            ).bind(self.organization_id, event_id, limit + 1)
        else:
            statement = self.db.prepare(
                """SELECT id,recipient_email,subject,status,attempt_count,
                          provider_message_id,last_error_code,updated_at_ms
                   FROM communication_messages WHERE organization_id=?1 AND event_id=?2
                     AND (updated_at_ms<?3 OR (updated_at_ms=?3 AND id<?4))
                   ORDER BY updated_at_ms DESC,id DESC LIMIT ?5"""
            ).bind(self.organization_id, event_id, before_ms, before_id, limit + 1)
        rows = result_rows(await statement.all())
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page:
            last = page[-1]
            next_cursor = _status_next_cursor(
                self.request,
                organization_id=self.organization_id,
                event_id=event_id,
                timestamp=int(last["updated_at_ms"]),
                row_id=str(last["id"]),
            )
        return CommunicationStatusList(
            data=[CommunicationStatus(**row) for row in page], next_cursor=next_cursor
        )

    async def dispatch_local(self, event_id: str) -> DispatchResponse:
        if getattr(self.request.scope.get("env"), "APP_ENV", "production") != "local":
            raise HTTPException(status_code=404)
        if self.organization_id is None:
            raise HTTPException(status_code=404)
        rows = result_rows(
            await self.db.prepare(
                """SELECT id,organization_id FROM communication_messages
                   WHERE organization_id=?1 AND event_id=?2 AND status='queued'
                   ORDER BY queued_at_ms,id LIMIT 100"""
            )
            .bind(self.organization_id, event_id)
            .all()
        )
        now = utc_now_ms()
        statements = []
        for row in rows:
            provider_id = f"local-{row['id']}"
            statements.extend(
                [
                    self.db.prepare(
                        """UPDATE communication_messages SET status='delivered',attempt_count=1,
                       provider_message_id=?1,delivered_at_ms=?2,updated_at_ms=?2
                       WHERE id=?3 AND organization_id=?4 AND event_id=?5 AND status='queued'"""
                    ).bind(provider_id, now, row["id"], row["organization_id"], event_id),
                    self.db.prepare(
                        """INSERT INTO communication_delivery_attempts
                       (id,organization_id,event_id,message_id,attempt_number,status,
                        provider_message_id,started_at_ms,completed_at_ms)
                       VALUES (?1,?2,?3,?4,1,'delivered',?5,?6,?6)"""
                    ).bind(new_id(), row["organization_id"], event_id, row["id"], provider_id, now),
                ]
            )
        if statements:
            await self.db.batch(statements)
        return DispatchResponse(delivered=len(rows))


def communications_service(request: Request) -> D1CommunicationsService:
    return D1CommunicationsService(request)

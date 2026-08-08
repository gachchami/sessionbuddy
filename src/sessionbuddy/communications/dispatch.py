from dataclasses import dataclass, replace
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    id: str
    organization_id: str
    event_id: str
    task_id: str
    recipient_user_id: str
    send_at_ms: int
    schedule_version: int
    state: str = "scheduled"
    dispatched_at_ms: int | None = None

    @property
    def deterministic_key(self) -> str:
        return f"reminder:{self.id}:v{self.schedule_version}"


class MessageQueue(Protocol):
    async def enqueue(self, *, deterministic_key: str, schedule_id: str) -> None: ...


class ReminderStore(Protocol):
    async def due(self, now_ms: int, limit: int) -> list[ReminderSchedule]: ...
    async def mark_dispatched(self, schedule_id: str, version: int, now_ms: int) -> bool: ...


class LocalReminderDispatcher:
    def __init__(self, store: ReminderStore, queue: MessageQueue) -> None:
        self._store, self._queue = store, queue

    async def dispatch_due(self, now_ms: int, *, limit: int = 100) -> int:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        dispatched = 0
        for schedule in sorted(
            await self._store.due(now_ms, limit), key=lambda row: (row.send_at_ms, row.id)
        ):
            if schedule.state != "scheduled" or schedule.send_at_ms > now_ms:
                continue
            if await self._store.mark_dispatched(schedule.id, schedule.schedule_version, now_ms):
                await self._queue.enqueue(
                    deterministic_key=schedule.deterministic_key, schedule_id=schedule.id
                )
                dispatched += 1
        return dispatched


def recompute(schedule: ReminderSchedule, send_at_ms: int) -> ReminderSchedule:
    if send_at_ms < 0:
        raise ValueError("send time must be nonnegative")
    return replace(
        schedule,
        send_at_ms=send_at_ms,
        schedule_version=schedule.schedule_version + 1,
        state="scheduled",
        dispatched_at_ms=None,
    )


def cancel(schedule: ReminderSchedule) -> ReminderSchedule:
    return replace(schedule, state="cancelled")

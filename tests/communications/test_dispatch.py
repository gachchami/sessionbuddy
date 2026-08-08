from dataclasses import replace

import pytest

from sessionbuddy.communications.dispatch import (
    LocalReminderDispatcher,
    ReminderSchedule,
    cancel,
    recompute,
)


class Store:
    def __init__(self, rows):
        self.rows = {row.id: row for row in rows}

    async def due(self, now_ms: int, limit: int):
        return [row for row in self.rows.values() if row.send_at_ms <= now_ms][:limit]

    async def mark_dispatched(self, schedule_id: str, version: int, now_ms: int) -> bool:
        row = self.rows[schedule_id]
        if row.state != "scheduled" or row.schedule_version != version:
            return False
        self.rows[schedule_id] = replace(row, state="dispatched", dispatched_at_ms=now_ms)
        return True


class Queue:
    def __init__(self):
        self.items = []

    async def enqueue(self, **item):
        self.items.append(item)


def schedule(id: str, at: int = 100) -> ReminderSchedule:
    return ReminderSchedule(id, "org", "event", "task", "user", at, 1)


@pytest.mark.asyncio
async def test_local_dispatch_is_ordered_bounded_and_exactly_once_per_version() -> None:
    store, queue = Store([schedule("b"), schedule("a")]), Queue()
    dispatcher = LocalReminderDispatcher(store, queue)
    assert await dispatcher.dispatch_due(100) == 2
    assert await dispatcher.dispatch_due(100) == 0
    assert queue.items == [
        {"deterministic_key": "reminder:a:v1", "schedule_id": "a"},
        {"deterministic_key": "reminder:b:v1", "schedule_id": "b"},
    ]


@pytest.mark.asyncio
async def test_cancelled_never_dispatches_and_recompute_changes_key() -> None:
    original = schedule("a")
    changed = recompute(original, 200)
    assert changed.schedule_version == 2 and changed.deterministic_key.endswith(":v2")
    store, queue = Store([cancel(changed)]), Queue()
    assert await LocalReminderDispatcher(store, queue).dispatch_due(300) == 0
    assert queue.items == []


@pytest.mark.asyncio
async def test_dispatch_limit_is_bounded() -> None:
    with pytest.raises(ValueError):
        await LocalReminderDispatcher(Store([]), Queue()).dispatch_due(0, limit=101)

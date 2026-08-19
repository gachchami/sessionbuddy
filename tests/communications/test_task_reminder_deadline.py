"""Queued task reminders carry the event-local deadline, not only a task name."""

import json
import sys
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfoNotFoundError

import pytest

import sessionbuddy.communications.d1 as communications
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)
from tests.speaker_operations.test_speaker_attribution import (
    _admin,
    _seed_speaker_with_two_submissions,
)


@pytest.mark.parametrize("deadline", ["2026-09-17T06:59:00+00:00", None])
async def test_task_reminder_persists_event_local_deadline_or_explicit_no_deadline(
    production_environment, deadline,  # noqa: F811
):
    connection, _, environment = production_environment
    async with _client(environment) as client:
        csrf, org_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, org_id, event_id, link_user=True)
        due_ms = int(datetime.fromisoformat(deadline).timestamp() * 1000) if deadline else None
        connection.execute(
            "UPDATE events SET time_zone='America/Los_Angeles' WHERE id=?", (event_id,),
        )
        connection.execute(
            "UPDATE speaker_tasks SET title='Upload Final Headshot (print quality)',"
            "due_at_ms=? WHERE id='task-speaker-1'", (due_ms,),
        )
        connection.commit()
        response = await client.post(
            f"/api/v1/admin/events/{event_id}/speaker-tasks/task-speaker-1/reminders",
            headers={"origin": "https://test", "x-csrf-token": csrf,
                     "idempotency-key": "deadline-evidence"}, json={},
        )
        assert response.status_code == 202, response.text
        row = connection.execute(
            "SELECT html_body FROM communication_messages WHERE id=?",
            (response.json()["message_id"],),
        ).fetchone()
        assert "Upload Final Headshot (print quality)" in row[0]
        if deadline:
            assert "Sep 16, 2026 at 23:59 (America/Los_Angeles)" in row[0]
        else:
            assert "No deadline set." in row[0]


def test_deadline_formatter_delegates_to_worker_intl_without_python_tzdata(monkeypatch):
    def missing_zone(_name):
        raise ZoneInfoNotFoundError("Worker has no Python timezone database")

    def formatter(locale, options):
        assert locale == "en-US"
        assert options["timeZone"] == "America/Los_Angeles"
        assert options["hourCycle"] == "h23"

        def parts(timestamp):
            assert timestamp == 1789628340000
            return [SimpleNamespace(type=k, value=v) for k, v in (
                ("month", "Sep"), ("day", "16"), ("year", "2026"),
                ("hour", "23"), ("minute", "59"),
            )]

        return SimpleNamespace(formatToParts=parts)

    monkeypatch.setattr(communications, "ZoneInfo", missing_zone)
    monkeypatch.setitem(sys.modules, "js", SimpleNamespace(
        Intl=SimpleNamespace(DateTimeFormat=SimpleNamespace(new=formatter)),
        Date=SimpleNamespace(new=lambda timestamp: timestamp),
        JSON=SimpleNamespace(parse=json.loads),
    ))
    assert communications._task_deadline(1789628340000, "America/Los_Angeles") == (
        "Due: Sep 16, 2026 at 23:59 (America/Los_Angeles)."
    )

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sessionbuddy.platform.auth.http import AuthenticatedContext
from sessionbuddy.platform.authorization import Actor, Persona
from sessionbuddy.speaker_operations import router
from sessionbuddy.speaker_operations.models import SpeakerTaskResponseCreate


class ReplayStatement:
    def __init__(self, database: "ReplayDatabase", query: str) -> None:
        self.database = database
        self.query = query
        self.parameters: tuple[object, ...] = ()

    def bind(self, *values: object) -> "ReplayStatement":
        self.parameters = values
        return self

    async def first(self):
        if "FROM idempotency_records" in self.query:
            return self.database.replay
        if "FROM speaker_tasks" in self.query:
            self.database.task_lookup = self.parameters
            task_id, organization_id, event_id, event_speaker_id = self.parameters
            row = self.database.tasks.get(str(task_id))
            if row is None:
                return None
            if (
                row["organization_id"] != organization_id
                or row["event_id"] != event_id
                or row["event_speaker_id"] != event_speaker_id
            ):
                return None
            return row
        raise AssertionError(f"Unexpected query: {self.query}")


class ReplayDatabase:
    def __init__(self, replay: dict[str, object], tasks: dict[str, dict[str, object]]) -> None:
        self.replay = replay
        self.tasks = tasks
        self.task_lookup: tuple[object, ...] | None = None

    def prepare(self, query: str) -> ReplayStatement:
        return ReplayStatement(self, query)


def request_for(database: ReplayDatabase) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/speaker/tasks/victim-task/response",
            "headers": [],
            "env": SimpleNamespace(DB=database),
        }
    )


async def speaker_context(*_args, **_kwargs):
    return (
        AuthenticatedContext(
            Actor(user_id="attacker", active_persona=Persona.SPEAKER),
            "session-attacker",
        ),
        {
            "organization_id": "attacker-org",
            "event_id": "attacker-event",
            "event_speaker_id": "attacker-speaker",
        },
    )


async def allow_permission(*_args, **_kwargs) -> None:
    return None


async def test_task_response_replay_uses_recorded_resource_not_attacker_path(
    monkeypatch,
) -> None:
    body = SpeakerTaskResponseCreate(answers={"private": "same body"}, version=1)
    database = ReplayDatabase(
        replay={
            "request_fingerprint": router._fingerprint(body),
            "response_resource_id": "attacker-task",
        },
        tasks={
            "attacker-task": {
                "id": "attacker-task",
                "organization_id": "attacker-org",
                "event_id": "attacker-event",
                "event_speaker_id": "attacker-speaker",
                "state": "completed",
                "response_json": '{"private":"attacker answer"}',
                "version": 2,
            },
            "victim-task": {
                "id": "victim-task",
                "organization_id": "victim-org",
                "event_id": "victim-event",
                "event_speaker_id": "victim-speaker",
                "state": "completed",
                "response_json": '{"private":"victim answer"}',
                "version": 2,
            },
        },
    )
    monkeypatch.setattr(router, "_speaker_row", speaker_context)
    monkeypatch.setattr(router, "require_permission", allow_permission)

    response = await router.complete_custom_speaker_task(
        "victim-task",
        request_for(database),
        body,
        "replay-key-0123456789abcdef0123456789",
    )

    assert response.id == "attacker-task"
    assert response.response == {"private": "attacker answer"}
    assert database.task_lookup == (
        "attacker-task",
        "attacker-org",
        "attacker-event",
        "attacker-speaker",
    )


async def test_task_response_replay_rejects_recorded_resource_outside_speaker_scope(
    monkeypatch,
) -> None:
    body = SpeakerTaskResponseCreate(answers={"private": "same body"}, version=1)
    database = ReplayDatabase(
        replay={
            "request_fingerprint": router._fingerprint(body),
            "response_resource_id": "victim-task",
        },
        tasks={
            "victim-task": {
                "id": "victim-task",
                "organization_id": "victim-org",
                "event_id": "victim-event",
                "event_speaker_id": "victim-speaker",
                "state": "completed",
                "response_json": '{"private":"victim answer"}',
                "version": 2,
            }
        },
    )
    monkeypatch.setattr(router, "_speaker_row", speaker_context)
    monkeypatch.setattr(router, "require_permission", allow_permission)

    with pytest.raises(HTTPException) as denied:
        await router.complete_custom_speaker_task(
            "victim-task",
            request_for(database),
            body,
            "replay-key-0123456789abcdef0123456789",
        )

    assert denied.value.status_code == 409
    assert database.task_lookup == (
        "victim-task",
        "attacker-org",
        "attacker-event",
        "attacker-speaker",
    )

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from sessionbuddy.cfp.models import FormRoutingRule
from sessionbuddy.cfp.router import (
    _validate_form_routing_tracks,
    _validate_routed_track,
)
from sessionbuddy.scheduling import router as scheduling_router
from tests.speaker_operations.test_asset_boundary import AsyncSqlite


@pytest.fixture
def track_database() -> AsyncSqlite:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE event_tracks (
             id TEXT PRIMARY KEY,
             organization_id TEXT NOT NULL,
             event_id TEXT NOT NULL,
             name TEXT NOT NULL,
             status TEXT NOT NULL,
             version INTEGER NOT NULL
           )"""
    )
    connection.executemany(
        "INSERT INTO event_tracks VALUES (?,?,?,?,?,?)",
        [
            ("track-a", "org-a", "event-a", "AI", "active", 1),
            ("track-b", "org-a", "event-a", "Web", "archived", 2),
            ("track-c", "org-b", "event-b", "Security", "active", 1),
        ],
    )
    return AsyncSqlite(connection)


def rule(*, track: str | None = None) -> FormRoutingRule:
    return FormRoutingRule(
        source_key="topic",
        operator="equals",
        value="yes",
        track=track,
        category=None if track else "General",
    )


async def test_form_routing_accepts_only_active_event_tracks(track_database) -> None:
    await _validate_form_routing_tracks(
        track_database,
        organization_id="org-a",
        event_id="event-a",
        routing_rules=(rule(track="AI"),),
    )

    for unavailable in ("Web", "Security", "Invented"):
        with pytest.raises(HTTPException) as error:
            await _validate_form_routing_tracks(
                track_database,
                organization_id="org-a",
                event_id="event-a",
                routing_rules=(rule(track=unavailable),),
            )
        assert error.value.status_code == 422


async def test_category_and_review_queue_routing_do_not_require_tracks(track_database) -> None:
    await _validate_form_routing_tracks(
        track_database,
        organization_id="org-a",
        event_id="event-a",
        routing_rules=(rule(),),
    )


async def test_submission_track_is_validated_and_canonicalized(track_database) -> None:
    routing = {"category": None, "track": "ai", "review_queue": "Review A"}
    await _validate_routed_track(
        track_database,
        organization_id="org-a",
        event_id="event-a",
        routing=routing,
    )
    assert routing == {"category": None, "track": "AI", "review_queue": "Review A"}

    routing["track"] = "Web"
    with pytest.raises(HTTPException) as error:
        await _validate_routed_track(
            track_database,
            organization_id="org-a",
            event_id="event-a",
            routing=routing,
        )
    assert error.value.status_code == 422


async def test_track_catalog_lists_only_active_tracks_for_the_event(
    track_database, monkeypatch
) -> None:
    async def allow_event_scope(request, event_id, permission, *, mutation):
        assert event_id == "event-a"
        assert not mutation
        return {"id": event_id, "organization_id": "org-a"}, object()

    monkeypatch.setattr(scheduling_router, "_event_scope", allow_event_scope)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/events/event-a/agenda/tracks",
            "headers": [],
            "env": SimpleNamespace(DB=track_database),
        }
    )
    response = await scheduling_router.list_event_tracks("event-a", request)
    assert response.model_dump() == {
        "event_id": "event-a",
        "data": [{"id": "track-a", "name": "AI", "status": "active", "version": 1}],
    }

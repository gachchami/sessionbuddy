import pytest

from tests.agenda.test_session_content_history import EVENT_PAYLOAD, _admin
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)


async def _published_tracks(client, connection):
    csrf, organization_id, event_id = await _admin(client, connection)
    headers = {"origin": "https://test", "x-csrf-token": csrf}
    setup = await client.post(
        f"/api/v1/admin/events/{event_id}/agenda/setup",
        headers={**headers, "idempotency-key": "track-filter-setup"},
        json={"room_names": ["Room"], "track_names": ["Platform", "Other"]},
    )
    assert setup.status_code == 201, setup.text
    tracks = {item["name"]: item["id"] for item in setup.json()["tracks"]}
    for index, track in enumerate(["Platform", "Other"]):
        connection.execute(
            "INSERT INTO people(id,organization_id,display_name,created_at_ms,updated_at_ms) "
            "VALUES (?,?,?,1,1)",
            (f"person-{index}", organization_id, f"Speaker {index}"),
        )
        connection.execute(
            "INSERT INTO event_speakers(id,organization_id,event_id,person_id,status,"
            "selection_status,accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms) "
            "VALUES (?,?,?,?,'onboarding','accepted',1,1,1,1)",
            (f"speaker-{index}", organization_id, event_id, f"person-{index}"),
        )
        connection.commit()
        created = await client.post(
            f"/api/v1/admin/events/{event_id}/sessions",
            headers={**headers, "idempotency-key": f"filter-session-{index}"},
            json={
                "title": f"Talk {index}",
                "abstract": "Public talk",
                "participant_ids": [f"speaker-{index}"],
            },
        )
        assert created.status_code == 201, created.text
        session_id = next(
            x["session_id"]
            for x in created.json()["unscheduled_sessions"]
            if x["title"] == f"Talk {index}"
        )
        placed = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/items",
            headers={**headers, "idempotency-key": f"track-filter-place-{index}"},
            json={
                "session_id": session_id,
                "room_id": setup.json()["rooms"][0]["id"],
                "track_id": tracks[track],
                "start_at_ms": 1_900_000_000_000 + index * 3_600_000,
                "end_at_ms": 1_900_001_800_000 + index * 3_600_000,
            },
        )
        assert placed.status_code == 201, placed.text
    agenda = (await client.get(f"/api/v1/admin/events/{event_id}/agenda")).json()
    published = await client.post(
        f"/api/v1/admin/events/{event_id}/agenda/publish",
        headers={**headers, "idempotency-key": "track-filter-publish"},
        json={
            "revision_id": agenda["revision"]["id"],
            "version": agenda["revision"]["version"],
            "approve_draft_sessions": True,
        },
    )
    assert published.status_code == 200, published.text
    other = await client.post(
        f"/api/v1/admin/organizations/{organization_id}/events",
        headers=headers,
        json={**EVENT_PAYLOAD, "name": "Other event"},
    )
    assert other.status_code == 201, other.text
    other_setup = await client.post(
        f"/api/v1/admin/events/{other.json()['id']}/agenda/setup",
        headers={**headers, "idempotency-key": "foreign-track-setup"},
        json={"room_names": ["Other room"], "track_names": ["Platform"]},
    )
    assert other_setup.status_code == 201, other_setup.text
    tracks["Foreign"] = other_setup.json()["tracks"][0]["id"]
    client.cookies.clear()
    return event_id, tracks


@pytest.mark.parametrize("suffix", ["schedule", "schedule.xml", "schedule.ics", "speakers"])
async def test_public_track_filter_scopes_outputs_and_fails_closed(production_environment, suffix):  # noqa: F811
    connection, _, environment = production_environment
    async with _client(environment) as client:
        event_id, tracks = await _published_tracks(client, connection)
        url = f"/api/v1/public/events/{event_id}/{suffix}"
        selected = await client.get(url, params={"track_id": tracks["Platform"]})
        assert selected.status_code == 200, selected.text
        assert selected.headers["cache-control"] == "no-store"
        assert "Talk 0" in selected.text and "Talk 1" not in selected.text
        connection.execute(
            "UPDATE event_tracks SET name='Renamed' WHERE id=?", (tracks["Platform"],)
        )
        connection.commit()
        renamed = await client.get(url, params={"track_id": tracks["Platform"]})
        assert "Talk 0" in renamed.text and "Talk 1" not in renamed.text
        for params in (
            {"track_id": "missing"},
            {"track_id": tracks["Foreign"]},
            {"track": "Platform"},
            {"track_id": "", "track": "Other"},
        ):
            empty = await client.get(url, params=params)
            assert empty.status_code == 200, empty.text
            assert "Talk 0" not in empty.text and "Talk 1" not in empty.text
            if suffix in {"schedule", "speakers"}:
                assert empty.json()["track_filter"]["matched"] is False
        legacy = await client.get(url, params={"track": "Renamed"})
        assert "Talk 0" in legacy.text and "Talk 1" not in legacy.text
        unfiltered = await client.get(url)
        assert "Talk 0" in unfiltered.text and "Talk 1" in unfiltered.text
        duplicate = await client.get(url, params=[("track_id", tracks["Platform"])] * 2)
        assert "Talk 0" not in duplicate.text and "Talk 1" not in duplicate.text
        connection.execute(
            "UPDATE event_tracks SET status='archived' WHERE id=?", (tracks["Platform"],)
        )
        connection.commit()
        archived = await client.get(url, params={"track_id": tracks["Platform"]})
        assert "Talk 0" not in archived.text and "Talk 1" not in archived.text

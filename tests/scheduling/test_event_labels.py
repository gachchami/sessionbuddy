from tests.agenda.test_session_content_history import _admin, _seed_accepted_session
from tests.schema import BASELINE
from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401 - pytest fixture
)


async def test_auto_schedule_replay_keeps_its_established_wire_shape(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, _organization_id, event_id = await _admin(client, connection)
        headers = {"origin": "https://test", "x-csrf-token": csrf}
        assert (
            await client.post(
                f"/api/v1/admin/events/{event_id}/agenda/setup",
                headers={**headers, "idempotency-key": "typed-agenda-setup"},
                json={"room_names": ["Main stage"], "track_names": []},
            )
        ).status_code == 201
        request = {
            "headers": {**headers, "idempotency-key": "typed-auto-schedule"},
            "json": {"session_minutes": 45, "gap_minutes": 15, "room_ids": []},
        }
        first = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/auto-schedule", **request
        )
        replay = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/auto-schedule", **request
        )

    assert first.status_code == 200
    assert first.json()["auto_schedule"] == {
        "scheduled_count": 0,
        "remaining_count": 0,
    }
    assert replay.status_code == 200
    assert "auto_schedule" not in replay.json()
    assert set(replay.json()) == set(first.json()) - {"auto_schedule"}


async def test_event_labels_are_owned_assignable_and_public(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        headers = {"origin": "https://test", "x-csrf-token": csrf}
        labels_url = f"/api/v1/admin/events/{event_id}/labels"

        created = await client.post(
            labels_url,
            headers=headers,
            json={"name": "  Beginner  ", "color": "#19724b"},
        )
        assert created.status_code == 201, created.text
        label = created.json()
        assert label == {
            "id": label["id"],
            "name": "Beginner",
            "color": "#19724B",
            "status": "active",
            "version": 1,
            "can_manage": True,
        }
        actor_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        ownership = connection.execute(
            """SELECT resource_type,created_by_user_id,owner_user_id,status
               FROM owned_resources WHERE id=?""",
            (label["id"],),
        ).fetchone()
        assert tuple(ownership) == ("label", actor_user_id, actor_user_id, "active")

        duplicate = await client.post(
            labels_url,
            headers=headers,
            json={"name": "beginner", "color": "#000000"},
        )
        assert duplicate.status_code == 409

        edited = await client.patch(
            f"{labels_url}/{label['id']}",
            headers=headers,
            json={
                "name": "Beginner friendly",
                "color": "#2563eb",
                "status": "active",
                "version": 1,
            },
        )
        assert edited.status_code == 200, edited.text
        label = edited.json()
        assert (label["name"], label["color"], label["version"]) == (
            "Beginner friendly",
            "#2563EB",
            2,
        )

        session_id = _seed_accepted_session(connection, organization_id, event_id)
        connection.execute(
            """UPDATE submissions SET routed_track='General'
               WHERE id=(SELECT submission_id FROM accepted_sessions WHERE id=?)""",
            (session_id,),
        )
        setup = await client.post(
            f"/api/v1/admin/events/{event_id}/agenda/setup",
            headers={**headers, "idempotency-key": "label-agenda-setup-2026"},
            json={"room_names": ["Main stage"], "track_names": ["General"]},
        )
        assert setup.status_code == 201, setup.text
        assert set(setup.json()) == {
            "event",
            "revision",
            "published_revision",
            "items",
            "unscheduled_sessions",
            "rooms",
            "tracks",
            "labels",
            "archived_rooms",
            "archived_tracks",
            "archived_labels",
            "can_manage_resource_lifecycle",
            "session_participants",
        }
        assigned = await client.put(
            f"/api/v1/admin/events/{event_id}/sessions/{session_id}/labels",
            headers=headers,
            json={"label_ids": [label["id"]], "version": 1},
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["version"] == 2
        assert [item["name"] for item in assigned.json()["labels"]] == ["Beginner friendly"]

        stale = await client.put(
            f"/api/v1/admin/events/{event_id}/sessions/{session_id}/labels",
            headers=headers,
            json={"label_ids": [], "version": 1},
        )
        assert stale.status_code == 409
        foreign = await client.put(
            f"/api/v1/admin/events/{event_id}/sessions/{session_id}/labels",
            headers=headers,
            json={"label_ids": ["label-from-another-event"], "version": 2},
        )
        assert foreign.status_code == 422

        agenda = await client.get(f"/api/v1/admin/events/{event_id}/agenda")
        assert agenda.status_code == 200
        assert agenda.json()["labels"] == [label]
        assert agenda.json()["unscheduled_sessions"][0]["label_ids"] == [label["id"]]
        assert agenda.json()["unscheduled_sessions"][0]["track_name"] == "General"
        assert agenda.json()["unscheduled_sessions"][0]["speaker_names"] == "Priya Raman"
        assert set(agenda.json()["unscheduled_sessions"][0]) == {
            "session_id",
            "source_type",
            "title",
            "abstract",
            "content_status",
            "content_version",
            "label_version",
            "labels",
            "label_ids",
            "track_id",
            "track_name",
            "speaker_names",
            "participants",
        }

        room_id = setup.json()["rooms"][0]["id"]
        revision_id = setup.json()["revision"]["id"]
        connection.execute(
            "UPDATE accepted_sessions SET content_status='approved' WHERE id=?",
            (session_id,),
        )
        connection.execute(
            """INSERT INTO agenda_items
               (id,organization_id,event_id,revision_id,accepted_session_id,room_id,
                track_id,event_date,event_time_zone,starts_at_ms,ends_at_ms,version,
                created_at_ms,updated_at_ms)
               VALUES ('item-1',?,?,?, ?,?,NULL,'2030-03-20','Asia/Kolkata',
                       1900000000000,1900003600000,1,1000,1000)""",
            (organization_id, event_id, revision_id, session_id, room_id),
        )
        connection.execute(
            """UPDATE schedule_revisions SET status='published',published_at_ms=1000
               WHERE id=?""",
            (revision_id,),
        )
        connection.commit()
        public = await client.get(f"/api/v1/public/events/{event_id}/schedule")
        assert public.status_code == 200, public.text
        assert set(public.json()) == {"event", "revision", "items"}
        assert set(public.json()["items"][0]) == {
            "id",
            "session_id",
            "title",
            "description",
            "start_at_ms",
            "end_at_ms",
            "room_name",
            "track_name",
                "speaker_names",
                "speaker_details",
                "format_name",
                "labels",
            "label_ids",
        }
        assert public.json()["items"][0]["labels"] == [
            {"id": label["id"], "name": "Beginner friendly", "color": "#2563EB"}
        ]
        # This test publishes by directly changing the fixture revision rather
        # than using the publish workflow, which would create the next draft.
        connection.execute(
            "UPDATE schedule_revisions SET status='draft',published_at_ms=NULL WHERE id=?",
            (revision_id,),
        )
        connection.commit()

        in_use = await client.patch(
            f"{labels_url}/{label['id']}",
            headers=headers,
            json={
                "name": label["name"],
                "color": label["color"],
                "status": "archived",
                "version": label["version"],
            },
        )
        assert in_use.status_code == 409
        removed = await client.put(
            f"/api/v1/admin/events/{event_id}/sessions/{session_id}/labels",
            headers=headers,
            json={"label_ids": [], "version": 2},
        )
        assert removed.status_code == 200
        archived = await client.patch(
            f"{labels_url}/{label['id']}",
            headers=headers,
            json={
                "name": label["name"],
                "color": label["color"],
                "status": "archived",
                "version": label["version"],
            },
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["status"] == "archived"
        assert (await client.get(labels_url)).json()["data"] == []
        assert (
            connection.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()[0]
            == "active"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM owned_resources WHERE id=?", (event_id,)
        ).fetchone()[0] == 0
        session_after_archive = await client.get("/api/v1/auth/session")
        assert session_after_archive.status_code == 200, session_after_archive.text
        session_payload = session_after_archive.json()
        assert not any(
            access["event_id"] == event_id for access in session_payload["event_access"]
        )
        assert any(
            access["organization_id"] == organization_id
            for access in session_payload["organization_access"]
        )
        archived_agenda = await client.get(f"/api/v1/admin/events/{event_id}/agenda")
        assert archived_agenda.status_code == 200, archived_agenda.text
        archived_label = archived_agenda.json()["archived_labels"][0]
        assert archived_label["id"] == label["id"]
        restored = await client.patch(
            f"{labels_url}/{label['id']}",
            headers=headers,
            json={
                "name": label["name"],
                "color": label["color"],
                "status": "active",
                "version": archived_label["version"],
            },
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["status"] == "active"
        assert (await client.get(labels_url)).json()["data"][0]["id"] == label["id"]
        assert (
            connection.execute(
                "SELECT status FROM owned_resources WHERE id=?", (label["id"],)
            ).fetchone()[0]
            == "active"
        )
        query_plan = " ".join(
            str(row[3])
            for row in connection.execute(
                """EXPLAIN QUERY PLAN SELECT id,name,color,status,version
                   FROM event_labels WHERE organization_id=? AND event_id=?
                     AND status='active' ORDER BY lower(name),id""",
                (organization_id, event_id),
            ).fetchall()
        )
        assert "idx_event_labels_event_status" in query_plan
        actions = {
            row[0]
            for row in connection.execute(
                "SELECT action FROM audit_events WHERE target_id IN (?,?)",
                (label["id"], session_id),
            ).fetchall()
        }
        assert {"label.create", "label.update", "label.archive"}.issubset(actions)
        assert "agenda.session_labels.update" in actions


def test_source_wiring_label_schema_rejects_cross_event_assignments() -> None:
    schema = BASELINE.read_text(encoding="utf-8")
    assert "FOREIGN KEY (organization_id,event_id,accepted_session_id)" in schema
    assert "REFERENCES accepted_sessions(organization_id,event_id,id)" in schema
    assert "FOREIGN KEY (organization_id,event_id,label_id)" in schema
    assert "REFERENCES event_labels(organization_id,event_id,id)" in schema
    assert "CHECK(applied_changes=1)" in schema

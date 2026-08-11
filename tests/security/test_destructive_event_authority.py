"""Exact-manage boundaries for destructive event operations."""

import sqlite3
import time

from tests.agenda.test_session_content_history import EVENT_PAYLOAD, _admin
from tests.security.test_production_identity_flow import (
    _client,
    _token,
    production_environment,  # noqa: F401 - pytest fixture
)


def _headers(csrf_token: str) -> dict[str, str]:
    return {"origin": "https://test", "x-csrf-token": csrf_token}


def _seed_user(
    connection: sqlite3.Connection, user_id: str, email: str, event_id: str
) -> None:
    now = int(time.time() * 1000)
    organization_id = connection.execute(
        "SELECT organization_id FROM events WHERE id=?", (event_id,)
    ).fetchone()[0]
    connection.execute(
        """INSERT INTO users
           (id,email,normalized_email,status,created_at_ms,updated_at_ms)
           VALUES(?,?,?,'active',?,?)""",
        (user_id, email, email, now, now),
    )
    connection.execute(
        """INSERT INTO organization_memberships
           (id,organization_id,user_id,role,status,created_at_ms,updated_at_ms)
           VALUES(?,?,?,'member','active',?,?)""",
        (f"membership-{user_id}", organization_id, user_id, now, now),
    )
    connection.commit()


async def _grant_and_sign_in(
    root,
    principal,
    connection: sqlite3.Connection,
    event_id: str,
    root_csrf: str,
    *,
    user_id: str,
    email: str,
    permission: str,
) -> str:
    _seed_user(connection, user_id, email, event_id)
    granted = await root.post(
        f"/api/v1/admin/events/{event_id}/access-grants",
        headers=_headers(root_csrf),
        json={"email": email, "permission": permission},
    )
    assert granted.status_code == 201, granted.text
    requested = await principal.post(
        "/api/v1/auth/magic-links",
        json={"email": email, "redirect_path": f"/admin/events/{event_id}"},
    )
    assert requested.status_code == 202
    verified = await principal.post(
        f"/auth/verify?token={_token(connection, email)}", follow_redirects=False
    )
    assert verified.status_code == 303
    return (await principal.get("/api/v1/auth/session")).json()["csrf_token"]


async def test_only_exact_manage_can_archive_and_recover_event_access(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with (
        _client(environment) as root,
        _client(environment) as editor,
        _client(environment) as manager,
    ):
        root_csrf, _organization_id, event_id = await _admin(root, connection)
        editor_csrf = await _grant_and_sign_in(
            root,
            editor,
            connection,
            event_id,
            root_csrf,
            user_id="event-editor",
            email="event-editor@example.com",
            permission="edit",
        )
        manager_csrf = await _grant_and_sign_in(
            root,
            manager,
            connection,
            event_id,
            root_csrf,
            user_id="event-manager",
            email="event-manager@example.com",
            permission="manage",
        )

        ordinary_edit = await editor.patch(
            f"/api/v1/admin/events/{event_id}",
            headers=_headers(editor_csrf),
            json={**EVENT_PAYLOAD, "name": "Editor-renamed event", "version": 1},
        )
        assert ordinary_edit.status_code == 200, ordinary_edit.text

        denied_archive = await editor.patch(
            f"/api/v1/admin/events/{event_id}",
            headers=_headers(editor_csrf),
            json={**EVENT_PAYLOAD, "status": "archived", "version": 2},
        )
        assert denied_archive.status_code == 404
        stored_event = connection.execute(
            "SELECT status,version FROM events WHERE id=?", (event_id,)
        ).fetchone()
        assert tuple(stored_event) == ("active", 2)

        archived = await manager.patch(
            f"/api/v1/admin/events/{event_id}",
            headers=_headers(manager_csrf),
            json={**EVENT_PAYLOAD, "status": "archived", "version": 2},
        )
        assert archived.status_code == 200, archived.text
        denied_restore = await editor.patch(
            f"/api/v1/admin/events/{event_id}",
            headers=_headers(editor_csrf),
            json={**EVENT_PAYLOAD, "status": "active", "version": 3},
        )
        assert denied_restore.status_code == 404
        grants = await manager.get(f"/api/v1/admin/events/{event_id}/access-grants")
        assert grants.status_code == 200, grants.text
        revoked = await manager.delete(
            f"/api/v1/admin/events/{event_id}/access-grants/event-editor",
            headers={**_headers(manager_csrf), "content-type": "application/json"},
        )
        assert revoked.status_code == 204, revoked.text
        restored = await manager.patch(
            f"/api/v1/admin/events/{event_id}",
            headers=_headers(manager_csrf),
            json={**EVENT_PAYLOAD, "status": "active", "version": 3},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["status"] == "active"


async def test_agenda_editors_can_add_resources_but_cannot_archive_them(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root, _client(environment) as editor:
        root_csrf, _organization_id, event_id = await _admin(root, connection)
        editor_csrf = await _grant_and_sign_in(
            root,
            editor,
            connection,
            event_id,
            root_csrf,
            user_id="agenda-editor",
            email="agenda-editor@example.com",
            permission="edit",
        )
        agenda = await root.post(
            f"/api/v1/admin/events/{event_id}/agenda/setup",
            headers={**_headers(root_csrf), "idempotency-key": "destructive-agenda-setup"},
            json={"room_names": ["Main", "Overflow"], "track_names": ["General"]},
        )
        assert agenda.status_code == 201, agenda.text
        added = await editor.post(
            f"/api/v1/admin/events/{event_id}/agenda/rooms",
            headers=_headers(editor_csrf),
            json={"name": "Green room"},
        )
        assert added.status_code == 200, added.text

        room = next(item for item in added.json()["rooms"] if item["name"] == "Overflow")
        track = added.json()["tracks"][0]
        denied_room = await editor.patch(
            f"/api/v1/admin/events/{event_id}/agenda/rooms/{room['id']}",
            headers=_headers(editor_csrf),
            json={"status": "archived", "version": room["version"]},
        )
        denied_track = await editor.patch(
            f"/api/v1/admin/events/{event_id}/agenda/tracks/{track['id']}",
            headers=_headers(editor_csrf),
            json={"status": "archived", "version": track["version"]},
        )
        assert (denied_room.status_code, denied_track.status_code) == (404, 404)

        archived_room = await root.patch(
            f"/api/v1/admin/events/{event_id}/agenda/rooms/{room['id']}",
            headers=_headers(root_csrf),
            json={"status": "archived", "version": room["version"]},
        )
        archived_track = await root.patch(
            f"/api/v1/admin/events/{event_id}/agenda/tracks/{track['id']}",
            headers=_headers(root_csrf),
            json={"status": "archived", "version": track["version"]},
        )
        assert (archived_room.status_code, archived_track.status_code) == (200, 200)


async def test_event_manager_can_archive_orphaned_label_at_cap(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with (
        _client(environment) as root,
        _client(environment) as editor,
        _client(environment) as manager,
    ):
        root_csrf, organization_id, event_id = await _admin(root, connection)
        editor_csrf = await _grant_and_sign_in(
            root,
            editor,
            connection,
            event_id,
            root_csrf,
            user_id="label-editor",
            email="label-editor@example.com",
            permission="edit",
        )
        manager_csrf = await _grant_and_sign_in(
            root,
            manager,
            connection,
            event_id,
            root_csrf,
            user_id="label-manager",
            email="label-manager@example.com",
            permission="manage",
        )
        labels_url = f"/api/v1/admin/events/{event_id}/labels"
        created = await editor.post(
            labels_url,
            headers=_headers(editor_csrf),
            json={"name": "Editor label", "color": "#123ABC"},
        )
        assert created.status_code == 201, created.text
        label = created.json()
        manager_cannot_rename = await manager.patch(
            f"{labels_url}/{label['id']}",
            headers=_headers(manager_csrf),
            json={
                "name": "Manager rewrite",
                "color": label["color"],
                "status": "active",
                "version": label["version"],
            },
        )
        assert manager_cannot_rename.status_code == 404
        listed = await manager.get(labels_url)
        assert listed.status_code == 200
        assert listed.json()["data"][0]["can_manage"] is True

        rows = [
            (
                f"cap-label-{index:03d}",
                "label-editor",
                "label-editor",
                "active",
                1,
                1000,
                1000,
                f"Cap label {index:03d}",
                "#ABCDEF",
            )
            for index in range(99)
        ]
        connection.executemany(
            """INSERT INTO owned_resources
               (id,resource_type,created_by_user_id,owner_user_id,status,version,
                created_at_ms,updated_at_ms)
               VALUES(?,'label',?,?,?,?,?,?)""",
            [row[:7] for row in rows],
        )
        connection.executemany(
            """INSERT INTO event_labels
               (id,organization_id,event_id,name,color,status,version,created_at_ms,updated_at_ms)
               VALUES(?,?,?,?,?,'active',1,1000,1000)""",
            [(row[0], organization_id, event_id, row[7], row[8]) for row in rows],
        )
        connection.commit()
        capped = await manager.post(
            labels_url,
            headers=_headers(manager_csrf),
            json={"name": "Replacement", "color": "#654321"},
        )
        assert capped.status_code == 409

        revoked = await root.delete(
            f"/api/v1/admin/events/{event_id}/access-grants/label-editor",
            headers={**_headers(root_csrf), "content-type": "application/json"},
        )
        assert revoked.status_code == 204
        denied = await editor.patch(
            f"{labels_url}/{label['id']}",
            headers=_headers(editor_csrf),
            json={
                "name": label["name"],
                "color": label["color"],
                "status": "archived",
                "version": label["version"],
            },
        )
        # Revocation increments the authorization version, so the already-open
        # editor session is invalid before it can touch the orphaned label.
        assert denied.status_code == 401
        archived = await manager.patch(
            f"{labels_url}/{label['id']}",
            headers=_headers(manager_csrf),
            json={
                "name": label["name"],
                "color": label["color"],
                "status": "archived",
                "version": label["version"],
            },
        )
        assert archived.status_code == 200, archived.text
        replacement = await manager.post(
            labels_url,
            headers=_headers(manager_csrf),
            json={"name": "Replacement", "color": "#654321"},
        )
        assert replacement.status_code == 201, replacement.text
        assert connection.execute(
            "SELECT COUNT(*) FROM event_labels WHERE event_id=? AND status='active'",
            (event_id,),
        ).fetchone()[0] == 100

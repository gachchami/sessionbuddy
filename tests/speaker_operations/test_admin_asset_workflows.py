import io
import sqlite3
import zipfile

from tests.security.test_production_identity_flow import (
    _client,
    _token,
    production_environment,  # noqa: F401
)
from tests.speaker_operations.test_speaker_attribution import (
    _admin,
    _seed_speaker_with_two_submissions,
)


class _Bucket:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.reads: list[str] = []

    async def get(self, key: str):
        self.reads.append(key)
        return self.objects.get(key)


def _asset(
    connection: sqlite3.Connection,
    organization_id: str,
    event_id: str,
    asset_id: str,
    version_id: str,
    kind: str,
    *,
    filename: str = "file.pdf",
    byte_size: int = 1,
    event_speaker_id: str = "speaker-1",
) -> str:
    object_key = f"private/{organization_id}/{event_id}/{version_id}"
    connection.execute(
        """INSERT INTO speaker_assets
           (id,organization_id,event_id,event_speaker_id,submission_id,kind,
            created_at_ms,updated_at_ms)
           VALUES (?,?,?,?, 'submission-accepted',?,1000,1000)""",
        (asset_id, organization_id, event_id, event_speaker_id, kind),
    )
    connection.execute(
        """INSERT INTO speaker_asset_versions
           (id,organization_id,event_id,event_speaker_id,asset_id,generation,
            object_key,original_filename,content_type,byte_size,checksum_sha256,
            scan_state,is_current,created_at_ms,uploaded_at_ms,scan_started_at_ms,
            scanned_at_ms,version_comment)
           VALUES (?,?,?,?,?,1,?,?,'application/pdf',?,?,'clean',1,
                   1000,1100,1200,1300,'Initial upload')""",
        (
            version_id,
            organization_id,
            event_id,
            event_speaker_id,
            asset_id,
            object_key,
            filename,
            byte_size,
            bytes(32),
        ),
    )
    connection.commit()
    return object_key


async def test_speaker_asset_http_boundary_and_shared_comment_replay(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        _csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id, link_user=True)
        connection.execute(
            """INSERT INTO user_roles
               (user_id,role,status,created_at_ms,updated_at_ms,is_default)
               VALUES('user-person-1','speaker','active',1,1,1)"""
        )
        _asset(connection, organization_id, event_id, "asset-own", "version-own", "slides")
        connection.execute(
            """INSERT INTO people
               (id,organization_id,display_name,created_at_ms,updated_at_ms)
               VALUES('person-2',?,'Other Speaker',1,1)""",
            (organization_id,),
        )
        connection.execute(
            """INSERT INTO event_speakers
               (id,organization_id,event_id,person_id,status,selection_status,
                accepted_at_ms,last_activity_at_ms,created_at_ms,updated_at_ms)
               VALUES('speaker-2',?,?,'person-2','onboarding','accepted',1,1,1,1)""",
            (organization_id, event_id),
        )
        _asset(
            connection,
            organization_id,
            event_id,
            "asset-other",
            "version-other",
            "slides",
            event_speaker_id="speaker-2",
        )
        admin_user_id = connection.execute(
            "SELECT id FROM users WHERE normalized_email='admin@example.com'"
        ).fetchone()[0]
        for comment_id, text, visibility in (
            ("comment-internal", "Organizer secret", "internal"),
            ("comment-shared", "Please update this", "shared"),
        ):
            connection.execute(
                """INSERT INTO speaker_asset_comments
                   (id,organization_id,event_id,asset_id,version_id,author_user_id,
                    body_text,visibility,created_at_ms)
                   VALUES(?,?,?,?,?,?,?, ?,1)""",
                (
                    comment_id,
                    organization_id,
                    event_id,
                    "asset-own",
                    "version-own",
                    admin_user_id,
                    text,
                    visibility,
                ),
            )
        connection.commit()

        assert (
            await client.post(
                "/api/v1/auth/magic-links",
                json={"email": "priya@example.com", "redirect_path": "/speaker"},
            )
        ).status_code == 202
        assert (
            await client.post(
                "/auth/verify",
                data={"token": _token(connection, "priya@example.com")},
                follow_redirects=False,
            )
        ).status_code == 303
        session = (await client.get("/api/v1/auth/session")).json()
        own = await client.get(f"/api/v1/speaker/events/{event_id}/assets/asset-own")
        assert own.status_code == 200, own.text
        assert [comment["body_text"] for comment in own.json()["comments"]] == [
            "Please update this"
        ]
        assert (
            await client.get(f"/api/v1/speaker/events/{event_id}/assets/asset-other")
        ).status_code == 404

        url = f"/api/v1/speaker/events/{event_id}/assets/asset-own/versions/version-own/comments"
        headers = {
            "origin": "https://test",
            "x-csrf-token": session["csrf_token"],
            "idempotency-key": "speaker-comment-replay-0001",
        }
        payload = {
            "body_text": "Updated now",
            "parent_comment_id": "comment-shared",
        }
        first = await client.post(url, headers=headers, json=payload)
        replay = await client.post(url, headers=headers, json=payload)
        assert first.status_code == replay.status_code == 201
        assert replay.json() == first.json()
        assert first.json()["visibility"] == "shared"


async def test_asset_detail_comment_and_idempotent_replay(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        _asset(connection, organization_id, event_id, "asset-a", "version-a", "slides")

        detail = await client.get(f"/api/v1/admin/events/{event_id}/assets/asset-a")
        assert detail.status_code == 200, detail.text
        assert detail.json()["versions"][0]["id"] == "version-a"

        url = f"/api/v1/admin/events/{event_id}/assets/asset-a/versions/version-a/comments"
        headers = {
            "origin": "https://test",
            "x-csrf-token": csrf,
            "idempotency-key": "comment-replay-key-0001",
        }
        first = await client.post(url, headers=headers, json={"body_text": "Please revise."})
        replay = await client.post(url, headers=headers, json={"body_text": "Please revise."})
        assert first.status_code == replay.status_code == 201
        assert replay.json() == first.json()
        assert connection.execute("SELECT COUNT(*) FROM speaker_asset_comments").fetchone()[0] == 1

        _asset(
            connection,
            organization_id,
            event_id,
            "asset-b",
            "version-b",
            "supporting_document",
        )
        second_resource = await client.post(
            f"/api/v1/admin/events/{event_id}/assets/asset-b/versions/version-b/comments",
            headers=headers,
            json={"body_text": "Please revise."},
        )
        assert second_resource.status_code == 201, second_resource.text
        assert second_resource.json()["id"] != first.json()["id"]


async def test_shared_reply_cannot_reference_an_internal_comment(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        _asset(connection, organization_id, event_id, "asset-a", "version-a", "slides")
        url = f"/api/v1/admin/events/{event_id}/assets/asset-a/versions/version-a/comments"
        base_headers = {"origin": "https://test", "x-csrf-token": csrf}

        internal = await client.post(
            url,
            headers={**base_headers, "idempotency-key": "internal-comment-key-0001"},
            json={"body_text": "Organizer-only context", "visibility": "internal"},
        )
        assert internal.status_code == 201, internal.text
        rejected = await client.post(
            url,
            headers={**base_headers, "idempotency-key": "shared-reply-key-000001"},
            json={
                "body_text": "This must not become an orphaned public reply",
                "parent_comment_id": internal.json()["id"],
                "visibility": "shared",
            },
        )
        assert rejected.status_code == 422, rejected.text
        assert connection.execute("SELECT COUNT(*) FROM speaker_asset_comments").fetchone()[0] == 1


async def test_deliverables_export_rejects_unavailable_and_large_assets(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        key = _asset(
            connection,
            organization_id,
            event_id,
            "asset-large",
            "version-large",
            "slides",
            byte_size=26 * 1024 * 1024,
        )
        environment.ASSETS = _Bucket({key: b"unused"})
        headers = {"origin": "https://test", "x-csrf-token": csrf}
        url = f"/api/v1/admin/events/{event_id}/deliverables/export"

        unavailable = await client.post(url, headers=headers, json={"asset_ids": ["missing"]})
        assert unavailable.status_code == 422
        assert "missing" in unavailable.json()["error"]["message"]

        too_large = await client.post(url, headers=headers, json={"asset_ids": ["asset-large"]})
        assert too_large.status_code == 413
        assert "25 MB" in too_large.json()["error"]["message"]


async def test_deliverables_export_uses_stored_zip_and_suffixes_collisions(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        first_key = _asset(connection, organization_id, event_id, "asset-a", "version-a", "slides")
        second_key = _asset(
            connection,
            organization_id,
            event_id,
            "asset-b",
            "version-b",
            "supporting_document",
        )
        environment.ASSETS = _Bucket({first_key: b"first", second_key: b"second"})

        response = await client.post(
            f"/api/v1/admin/events/{event_id}/deliverables/export",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={"asset_ids": ["asset-a", "asset-b"]},
        )
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.compression == zipfile.ZIP_STORED
            assert archive.namelist() == ["Priya Raman/file.pdf", "Priya Raman/2-file.pdf"]
            assert {archive.read(name) for name in archive.namelist()} == {b"first", b"second"}


async def test_deliverables_export_reads_only_selected_current_clean_version(
    production_environment,  # noqa: F811
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as client:
        csrf, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        previous_bytes = b"%PDF-1.4 superseded slides version one"
        current_bytes = b"%PDF-1.4 current slides version two with revised content"
        unselected_bytes = b"%PDF-1.4 unselected supporting document"
        previous_key = _asset(
            connection,
            organization_id,
            event_id,
            "asset-selected",
            "version-one",
            "slides",
            filename="slides.pdf",
            byte_size=len(previous_bytes),
        )
        connection.execute(
            "UPDATE speaker_asset_versions SET is_current=0,scan_state='superseded' "
            "WHERE id='version-one'"
        )
        current_key = f"private/{organization_id}/{event_id}/version-two"
        connection.execute(
            """INSERT INTO speaker_asset_versions
               (id,organization_id,event_id,event_speaker_id,asset_id,generation,
                object_key,original_filename,content_type,byte_size,checksum_sha256,
                scan_state,is_current,created_at_ms,uploaded_at_ms,scan_started_at_ms,
                scanned_at_ms,version_comment)
               VALUES ('version-two',?,?,'speaker-1','asset-selected',2,?,
                       'slides.pdf','application/pdf',?,?,'clean',1,
                       2000,2100,2200,2300,'Revised slides')""",
            (organization_id, event_id, current_key, len(current_bytes), bytes(32)),
        )
        connection.commit()
        unselected_key = _asset(
            connection,
            organization_id,
            event_id,
            "asset-unselected",
            "version-unselected",
            "supporting_document",
            byte_size=len(unselected_bytes),
        )
        bucket = _Bucket(
            {
                previous_key: previous_bytes,
                current_key: current_bytes,
                unselected_key: unselected_bytes,
            }
        )
        environment.ASSETS = bucket

        response = await client.post(
            f"/api/v1/admin/events/{event_id}/deliverables/export",
            headers={"origin": "https://test", "x-csrf-token": csrf},
            json={"asset_ids": ["asset-selected"]},
        )

        assert response.status_code == 200, response.text
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.namelist() == ["Priya Raman/slides.pdf"]
            assert archive.read("Priya Raman/slides.pdf") == current_bytes
        assert bucket.reads == [current_key]

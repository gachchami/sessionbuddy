"""Bulk speaker imports classify every row and are safe to retry."""

import sessionbuddy.platform.auth.access as access_module
from tests.security.test_organizer_workflow import (
    EVENT_PAYLOAD,
    _bootstrap_admin,
    _client,
    _mutation,
    production_environment,  # noqa: F401 - pytest fixture
)


def _row(
    row_number: int,
    email: str,
    name: str,
    *,
    company: str = "",
    disposition: str = "import",
) -> dict[str, object]:
    return {
        "row_number": row_number,
        "email": email,
        "display_name": name,
        "job_title": "",
        "company": company,
        "biography": "",
        "disposition": disposition,
    }


async def _event(root, csrf: str, organization_id: str) -> str:
    created = await root.post(
        f"/api/v1/admin/organizations/{organization_id}/events",
        headers=_mutation(csrf),
        json=EVENT_PAYLOAD,
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def test_bulk_speaker_import_previews_every_row_without_mutation(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event_id = await _event(root, csrf, organization_id)
        invited = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={
                "email": "existing@example.com",
                "display_name": "Existing Speaker",
                "role": "speaker",
                "expires_in_days": 14,
            },
        )
        assert invited.status_code == 201, invited.text
        before = connection.execute(
            "SELECT COUNT(*) FROM identity_invitations WHERE event_id=?", (event_id,)
        ).fetchone()[0]

        rows = [
            _row(2, "clean@example.com", "Clean Speaker"),
            _row(3, "clean@example.com", "Clean Speaker"),
            _row(4, "conflict@example.com", "First Identity", company="One"),
            _row(5, "conflict@example.com", "Second Identity", company="Two"),
            _row(6, "different@example.com", "Existing Speaker"),
            _row(7, "existing@example.com", "Existing Speaker"),
            _row(8, "not-an-email", "Malformed"),
            _row(9, "missing-name@example.com", ""),
        ]
        response = await root.post(
            f"/api/v1/admin/events/{event_id}/speaker-invitations/import",
            headers=_mutation(csrf),
            json={"mode": "preview", "rows": rows},
        )

        assert response.status_code == 200, response.text
        outcomes = {item["row_number"]: item for item in response.json()["data"]}
        assert outcomes[2]["outcome"] == "ready"
        assert outcomes[3]["outcome"] == "skipped_duplicate"
        assert outcomes[4]["outcome"] == "needs_resolution"
        assert outcomes[4]["allowed_dispositions"] == ["import", "skip"]
        assert outcomes[5]["outcome"] == "needs_resolution"
        assert outcomes[6]["outcome"] == "needs_resolution"
        assert outcomes[6]["allowed_dispositions"] == ["separate_person", "skip"]
        assert outcomes[7]["outcome"] == "skipped_existing_invitation"
        assert outcomes[8]["outcome"] == "rejected"
        assert outcomes[9]["outcome"] == "rejected"
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_invitations WHERE event_id=?", (event_id,)
        ).fetchone()[0] == before


async def test_bulk_speaker_import_continues_past_bad_rows_and_retry_sends_no_email(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event_id = await _event(root, csrf, organization_id)
        rows = [
            _row(2, "first@example.com", "First Speaker"),
            _row(3, "bad", "Bad Speaker"),
            _row(4, "second@example.com", "Second Speaker"),
        ]
        url = f"/api/v1/admin/events/{event_id}/speaker-invitations/import"
        headers = {
            **_mutation(csrf),
            "idempotency-key": "speaker-csv-import-retry-2026-08-21",
        }

        created = await root.post(
            url, headers=headers, json={"mode": "execute", "rows": rows}
        )
        assert created.status_code == 200, created.text
        assert [item["outcome"] for item in created.json()["data"]] == [
            "created",
            "rejected",
            "created",
        ]
        assert created.json()["created_count"] == 2
        messages_before_retry = connection.execute(
            "SELECT COUNT(*) FROM communication_messages WHERE event_id=?", (event_id,)
        ).fetchone()[0]
        challenges_before_retry = connection.execute(
            "SELECT COUNT(*) FROM authentication_challenges WHERE event_id=?", (event_id,)
        ).fetchone()[0]

        replayed = await root.post(
            url, headers=headers, json={"mode": "execute", "rows": rows}
        )
        assert replayed.status_code == 200, replayed.text
        assert [item["outcome"] for item in replayed.json()["data"]] == [
            "created",
            "rejected",
            "created",
        ]
        assert all(
            "no new email" in item["reason"]
            for item in replayed.json()["data"]
            if item["outcome"] == "created"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM communication_messages WHERE event_id=?", (event_id,)
        ).fetchone()[0] == messages_before_retry
        assert connection.execute(
            "SELECT COUNT(*) FROM authentication_challenges WHERE event_id=?", (event_id,)
        ).fetchone()[0] == challenges_before_retry
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_invitations WHERE event_id=?", (event_id,)
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM speaker_tasks WHERE event_id=?", (event_id,)
        ).fetchone()[0] == 6
        assert connection.execute(
            """SELECT COUNT(*) FROM speaker_tasks
               WHERE event_id=? AND task_type IN ('headshot','slides')
                 AND json_extract(form_schema_json,'$.upload.enabled')=1
                 AND json_array_length(form_schema_json,'$.upload.allowed_content_types')>0
                 AND json_extract(form_schema_json,'$.upload.max_file_bytes')>0""",
            (event_id,),
        ).fetchone()[0] == 4
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_id=? AND action='identity.invitation.bulk.create'",
            (event_id,),
        ).fetchone()[0] == 2


async def test_bulk_speaker_import_requires_explicit_divergent_email_disposition(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event_id = await _event(root, csrf, organization_id)
        url = f"/api/v1/admin/events/{event_id}/speaker-invitations/import"
        headers = {
            **_mutation(csrf),
            "idempotency-key": "speaker-csv-conflict-choice-2026-08-21",
        }
        unresolved = [
            _row(2, "shared@example.com", "First Name", company="One"),
            _row(3, "shared@example.com", "Second Name", company="Two"),
        ]
        blocked = await root.post(
            url, headers=headers, json={"mode": "execute", "rows": unresolved}
        )
        assert blocked.status_code == 200
        assert {item["outcome"] for item in blocked.json()["data"]} == {
            "needs_resolution"
        }

        skipped_all = await root.post(
            url,
            headers=headers,
            json={
                "mode": "execute",
                "rows": [
                    {**unresolved[0], "disposition": "skip"},
                    {**unresolved[1], "disposition": "skip"},
                ],
            },
        )
        assert skipped_all.status_code == 200, skipped_all.text
        assert [item["outcome"] for item in skipped_all.json()["data"]] == [
            "skipped",
            "skipped",
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM identity_invitations WHERE event_id=?",
            (event_id,),
        ).fetchone()[0] == 0

        resolved = [
            unresolved[0],
            {**unresolved[1], "disposition": "skip"},
        ]
        imported = await root.post(
            url, headers=headers, json={"mode": "execute", "rows": resolved}
        )
        assert imported.status_code == 200, imported.text
        assert [item["outcome"] for item in imported.json()["data"]] == [
            "created",
            "skipped",
        ]
        saved = connection.execute(
            "SELECT display_name,company FROM identity_invitations WHERE event_id=?",
            (event_id,),
        ).fetchone()
        assert tuple(saved) == ("First Name", "One")


async def test_bulk_speaker_import_identical_duplicate_uses_non_skip_disposition(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event_id = await _event(root, csrf, organization_id)
        first = _row(
            2,
            "duplicate@example.com",
            "Duplicate Speaker",
            disposition="skip",
        )
        second = _row(3, "duplicate@example.com", "Duplicate Speaker")

        preview = await root.post(
            f"/api/v1/admin/events/{event_id}/speaker-invitations/import",
            headers=_mutation(csrf),
            json={"mode": "preview", "rows": [first, second]},
        )

        assert preview.status_code == 200, preview.text
        assert [item["outcome"] for item in preview.json()["data"]] == [
            "skipped_duplicate",
            "ready",
        ]


async def test_bulk_speaker_import_rejects_oversized_row_without_blocking_clean_row(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event_id = await _event(root, csrf, organization_id)
        response = await root.post(
            f"/api/v1/admin/events/{event_id}/speaker-invitations/import",
            headers=_mutation(csrf),
            json={
                "mode": "preview",
                "rows": [
                    _row(2, "oversized@example.com", "x" * 201),
                    _row(3, "clean@example.com", "Clean Speaker"),
                ],
            },
        )

        assert response.status_code == 200, response.text
        assert [item["outcome"] for item in response.json()["data"]] == [
            "rejected",
            "ready",
        ]
        assert response.json()["rejected_count"] == 1


async def test_bulk_speaker_import_allows_500_rows_with_blank_line_position_gap(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event_id = await _event(root, csrf, organization_id)
        rows = [
            _row(line, f"speaker-{line}@example.com", f"Speaker {line}")
            for line in range(2, 501)
        ]
        # A blank physical CSV line before the final record makes its source
        # line 503 even though the file still contains exactly 500 data rows.
        rows.append(_row(503, "speaker-503@example.com", "Speaker 503"))

        response = await root.post(
            f"/api/v1/admin/events/{event_id}/speaker-invitations/import",
            headers=_mutation(csrf),
            json={"mode": "preview", "rows": rows},
        )

        assert response.status_code == 200, response.text
        assert len(response.json()["data"]) == 500
        assert response.json()["data"][-1]["row_number"] == 503
        assert response.json()["data"][-1]["outcome"] == "ready"


async def test_bulk_speaker_import_preview_matches_revoked_invitation_execution(
    production_environment,  # noqa: F811 - pytest fixture
) -> None:
    connection, _queue, environment = production_environment
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event_id = await _event(root, csrf, organization_id)
        invited = await root.post(
            f"/api/v1/admin/events/{event_id}/invitations",
            headers=_mutation(csrf),
            json={
                "email": "revoked@example.com",
                "display_name": "Revoked Speaker",
                "role": "speaker",
                "expires_in_days": 14,
            },
        )
        assert invited.status_code == 201, invited.text
        connection.execute(
            "UPDATE identity_invitations SET status='revoked',revoked_at_ms=updated_at_ms "
            "WHERE id=?",
            (invited.json()["id"],),
        )
        connection.commit()

        preview = await root.post(
            f"/api/v1/admin/events/{event_id}/speaker-invitations/import",
            headers=_mutation(csrf),
            json={
                "mode": "preview",
                "rows": [_row(2, "revoked@example.com", "Revoked Speaker")],
            },
        )

        assert preview.status_code == 200, preview.text
        result = preview.json()["data"][0]
        assert result["outcome"] == "skipped_existing_invitation"
        assert "revoked invitation" in result["reason"]


async def test_bulk_speaker_import_rolls_back_row_when_delivery_persistence_fails(
    production_environment,  # noqa: F811 - pytest fixture
    monkeypatch,
) -> None:
    connection, _queue, environment = production_environment
    original = access_module._append_invitation_link

    async def append_broken_delivery(batch, request, **kwargs):
        result = await original(batch, request, **kwargs)
        batch.add_statement(
            request.scope["env"].DB.prepare(
                "INSERT INTO deliberately_missing_delivery_table(value) VALUES(1)"
            )
        )
        return result

    monkeypatch.setattr(
        access_module, "_append_invitation_link", append_broken_delivery
    )
    async with _client(environment) as root:
        csrf, organization_id = await _bootstrap_admin(root, connection)
        event_id = await _event(root, csrf, organization_id)
        response = await root.post(
            f"/api/v1/admin/events/{event_id}/speaker-invitations/import",
            headers={
                **_mutation(csrf),
                "idempotency-key": "speaker-csv-atomic-delivery-2026-08-21",
            },
            json={
                "mode": "execute",
                "rows": [_row(2, "atomic@example.com", "Atomic Speaker")],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"][0]["outcome"] == "failed"
        assert response.json()["failed_count"] == 1
        for table in (
            "identity_invitations",
            "speaker_tasks",
            "authentication_challenges",
            "communication_messages",
            "idempotency_records",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE event_id=?",  # noqa: S608
                (event_id,),
            ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_id=? AND action='identity.invitation.bulk.create'",
            (event_id,),
        ).fetchone()[0] == 0

import importlib

from tests.security.test_production_identity_flow import (
    _client,
    production_environment,  # noqa: F401
)
from tests.speaker_operations.test_admin_asset_workflows import _asset
from tests.speaker_operations.test_speaker_attribution import (
    _admin,
    _seed_speaker_with_two_submissions,
)


async def test_admin_asset_list_and_detail_show_only_stored_associations(
    production_environment,  # noqa: F811
):
    connection, _, environment = production_environment
    async with _client(environment) as client:
        _, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        for asset_id, kind in [
            ("linked", "slides"),
            ("general", "headshot"),
            ("task-linked", "supporting_document"),
        ]:
            _asset(connection, organization_id, event_id, asset_id, f"version-{asset_id}", kind)
        connection.execute("UPDATE speaker_assets SET submission_id=NULL WHERE id='general'")
        connection.execute(
            "UPDATE speaker_tasks SET submission_id='submission-accepted' WHERE id='task-speaker-1'"
        )
        connection.execute(
            "UPDATE speaker_assets SET submission_id=NULL,task_id='task-speaker-1' "
            "WHERE id='task-linked'"
        )
        connection.commit()
        url = f"/api/v1/admin/events/{event_id}/assets"
        listed = await client.get(url)
        assert listed.status_code == 200, listed.text
        for item in listed.json()["data"]:
            detail = await client.get(f"{url}/{item['id']}")
            assert detail.status_code == 200, detail.text
            for value in [item, detail.json()]:
                if item["id"] == "general":
                    assert value["submission_id"] is None
                    assert value["proposal_title"] is None
                    assert value["task_title"] is None
                else:
                    assert value["submission_id"] == "submission-accepted"
                    assert value["proposal_title"] == "Accepted talk"
                if item["id"] == "task-linked":
                    assert value["task_id"] == "task-speaker-1"
                    assert value["task_title"] == "Complete bio and profile"


async def test_admin_asset_association_query_plans_use_indexed_scoped_lookups(
    production_environment,  # noqa: F811
    monkeypatch,
):
    connection, _, environment = production_environment
    asset_router = importlib.import_module("sessionbuddy.speaker_operations.router")
    resolve_sql = asset_router._admin_asset_association_sql
    statements = []

    def capture_sql(sql):
        resolved = resolve_sql(sql)
        statements.append(resolved)
        return resolved

    monkeypatch.setattr(asset_router, "_admin_asset_association_sql", capture_sql)
    async with _client(environment) as client:
        _, organization_id, event_id = await _admin(client, connection)
        _seed_speaker_with_two_submissions(connection, organization_id, event_id)
        _asset(connection, organization_id, event_id, "linked", "version-linked", "slides")
        url = f"/api/v1/admin/events/{event_id}/assets"
        assert (await client.get(url)).status_code == 200
        assert (await client.get(f"{url}/linked")).status_code == 200

    assert len(statements) == 2, "Both HTTP readers must reach the shared association joins"
    for sql in statements:
        params = (
            (organization_id, event_id, "linked") if "?3" in sql else (organization_id, event_id)
        )
        plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params)]
        for alias in ("asset_task", "proposal"):
            lookup = [step for step in plan if f"SEARCH {alias} USING INDEX " in step]
            assert lookup, (alias, plan)
            assert all("organization_id=?" in step and "event_id=?" in step for step in lookup)
            assert not any(step.startswith(f"SCAN {alias}") for step in plan), plan
        # Only planner metadata is emitted: never rows, fixture identities or grants.
        print(
            "; ".join(
                step for step in plan if "SEARCH asset_task " in step or "SEARCH proposal " in step
            )
        )

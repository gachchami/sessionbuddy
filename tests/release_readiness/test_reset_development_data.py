import json
import re
from pathlib import Path

import pytest

from scripts import reset_development_data as reset_module
from scripts.reset_development_data import (
    ResetError,
    assert_canonical_baseline_layout,
    assert_local_workers_stopped,
    d1_target_arguments,
    deletion_order,
    drop_local_schema_sql,
    replace_database_id,
)


def test_reset_accepts_canonical_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "0001_baseline.sql"
    baseline.write_text("CREATE TABLE example(id TEXT);", encoding="utf-8")

    assert_canonical_baseline_layout(baseline)


def test_reset_accepts_an_ordered_incremental_migration(tmp_path: Path) -> None:
    baseline = tmp_path / "0001_baseline.sql"
    baseline.write_text("CREATE TABLE example(id TEXT);", encoding="utf-8")
    (tmp_path / "0002_incremental.sql").write_text(
        "ALTER TABLE example ADD COLUMN name TEXT;", encoding="utf-8"
    )

    assert_canonical_baseline_layout(baseline)


def test_reset_refuses_duplicate_migration_sequence(tmp_path: Path) -> None:
    baseline = tmp_path / "0001_baseline.sql"
    baseline.write_text("CREATE TABLE example(id TEXT);", encoding="utf-8")
    (tmp_path / "0002_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0002_second.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(ResetError, match="unique ordered names"):
        assert_canonical_baseline_layout(baseline)


def test_reset_refuses_a_missing_canonical_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "0001_baseline.sql"

    with pytest.raises(ResetError, match="found: none"):
        assert_canonical_baseline_layout(baseline)


def test_reset_target_arguments_keep_local_and_remote_explicit() -> None:
    config = Path("target.private.jsonc")
    assert d1_target_arguments(config, local=True) == [
        "--local",
        "--config",
        "target.private.jsonc",
    ]
    assert d1_target_arguments(config, local=False) == [
        "--remote",
        "--config",
        "target.private.jsonc",
    ]


def test_local_reset_refuses_a_running_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reachable:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        reset_module.socket,
        "create_connection",
        lambda *args, **kwargs: Reachable(),
    )

    with pytest.raises(ResetError, match="stop local worker"):
        assert_local_workers_stopped()


def test_local_recreation_drops_migration_ledger_but_not_cloudflare_state() -> None:
    schema = {
        "_cf_KV": "CREATE TABLE _cf_KV(key TEXT)",
        "d1_migrations": "CREATE TABLE d1_migrations(name TEXT)",
        "organizations": "CREATE TABLE organizations(id TEXT PRIMARY KEY)",
        "events": "CREATE TABLE events(organization_id TEXT REFERENCES organizations(id))",
    }

    sql = drop_local_schema_sql(schema)

    assert 'DROP TABLE IF EXISTS "events"' in sql
    assert 'DROP TABLE IF EXISTS "organizations"' in sql
    assert 'DROP TABLE IF EXISTS "d1_migrations"' not in sql
    assert "DELETE FROM d1_migrations" in sql
    assert 'DROP TABLE IF EXISTS "_cf_KV"' not in sql
    assert sql.index('DROP TABLE IF EXISTS "events"') < sql.index(
        'DROP TABLE IF EXISTS "organizations"'
    )


def test_source_wiring_remote_recreation_updates_only_the_exact_old_database_id(
    tmp_path: Path,
) -> None:
    config = tmp_path / "wrangler.jsonc"
    old_id = "11111111-1111-4111-8111-111111111111"
    new_id = "22222222-2222-4222-8222-222222222222"
    config.write_text(f'{{"database_id":"{old_id}"}}', encoding="utf-8")

    replace_database_id(config, old_id, new_id)

    assert old_id not in config.read_text(encoding="utf-8")
    assert new_id in config.read_text(encoding="utf-8")


def test_remote_recreation_rejects_ambiguous_database_ids(tmp_path: Path) -> None:
    config = tmp_path / "wrangler.jsonc"
    old_id = "11111111-1111-4111-8111-111111111111"
    config.write_text(f'{{"first":"{old_id}","second":"{old_id}"}}', encoding="utf-8")

    with pytest.raises(ResetError, match="exactly one"):
        replace_database_id(config, old_id, "22222222-2222-4222-8222-222222222222")


def test_remote_recreation_rebinds_both_workers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old_id = "11111111-1111-4111-8111-111111111111"
    new_id = "22222222-2222-4222-8222-222222222222"
    configs = (
        tmp_path / "main.jsonc",
        tmp_path / "activity.jsonc",
        tmp_path / "target.local",
    )
    for config in configs[:2]:
        config.write_text(
            json.dumps(
                {
                    "d1_databases": [
                        {
                            "binding": "DB",
                            "database_name": "sessionbuddy-development-clean",
                            "database_id": old_id,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    configs[2].write_text(
        f"D1_DATABASE_NAME=sessionbuddy-development-clean\nD1_DATABASE_ID={old_id}\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], *, timeout: int = 300) -> str:
        del timeout
        calls.append(arguments)
        if arguments[:3] == ["d1", "list", "--json"]:
            return json.dumps([{"name": "sessionbuddy-development-clean", "uuid": new_id}])
        return ""

    monkeypatch.setattr(reset_module, "run_checked", fake_run)

    assert (
        reset_module.recreate_remote_database(
            "sessionbuddy-development-clean", old_id, location="apac", configs=configs
        )
        == new_id
    )
    assert calls[:2] == [
        [
            "d1",
            "delete",
            "sessionbuddy-development-clean",
            "--skip-confirmation",
            "--config",
            str(configs[0]),
        ],
        [
            "d1",
            "create",
            "sessionbuddy-development-clean",
            "--location",
            "apac",
            "--config",
            str(configs[0]),
        ],
    ]
    assert all(new_id in config.read_text(encoding="utf-8") for config in configs)


def test_remote_recreation_validates_every_identity_before_deleting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old_id = "11111111-1111-4111-8111-111111111111"
    configs = (tmp_path / "main.jsonc", tmp_path / "activity.jsonc", tmp_path / "target.local")
    for config in configs[:2]:
        config.write_text(
            json.dumps(
                {
                    "d1_databases": [
                        {
                            "binding": "DB",
                            "database_name": "selected-database",
                            "database_id": old_id,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    configs[2].write_text(
        "D1_DATABASE_NAME=selected-database\nD1_DATABASE_ID=stale-id\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        reset_module,
        "run_checked",
        lambda arguments, timeout=300: calls.append(arguments) or "",
    )

    with pytest.raises(ResetError, match="before reset"):
        reset_module.recreate_remote_database(
            "selected-database", old_id, location="apac", configs=configs
        )

    assert calls == []


def test_remote_recreation_rejects_a_stale_manifest_database_name_before_deleting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old_id = "11111111-1111-4111-8111-111111111111"
    configs = (tmp_path / "main.jsonc", tmp_path / "activity.jsonc", tmp_path / "target.local")
    for config in configs[:2]:
        config.write_text(
            json.dumps(
                {
                    "d1_databases": [
                        {
                            "binding": "DB",
                            "database_name": "selected-database",
                            "database_id": old_id,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    configs[2].write_text(
        f"D1_DATABASE_NAME=other-database\nD1_DATABASE_ID={old_id}\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        reset_module,
        "run_checked",
        lambda arguments, timeout=300: calls.append(arguments) or "",
    )

    with pytest.raises(ResetError, match="does not identify"):
        reset_module.recreate_remote_database(
            "selected-database", old_id, location="apac", configs=configs
        )

    assert calls == []


def test_bootstrap_bundle_restores_complete_credential_with_explicit_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = {
        "organizations": {"id": "'org-1'"},
        "users": {"id": "'user-1'"},
        "password_credentials": {
            "user_id": "'user-1'",
            "verifier_phc": "'$argon2id$fixture-salt-and-verifier'",
            "pepper_version": "2",
            "status": "'active'",
        },
        "organization_memberships": {"id": "'membership-1'"},
        "user_roles": {"user_id": "'user-1'"},
        "instance_setup": {"singleton_key": "'primary'"},
    }

    def fake_execute(environment_name: str, sql: str, *, local: bool = False) -> list[dict]:
        del environment_name, local
        for table, row in required.items():
            if sql.startswith(f'PRAGMA table_info("{table}")'):
                return [{"success": True, "results": [{"name": key} for key in row]}]
            if f'FROM "{table}" ' in sql:
                return [{"success": True, "results": [row]}]
        for table in (
            "people",
            "user_headshots",
            "owned_resources",
            "resource_access_grants",
        ):
            if sql.startswith(f'PRAGMA table_info("{table}")'):
                return [{"success": True, "results": [{"name": "id"}]}]
            if f'FROM "{table}" ' in sql:
                return [{"success": True, "results": []}]
        raise AssertionError(sql)

    monkeypatch.setattr(reset_module, "execute_sql", fake_execute)

    sql = reset_module.bootstrap_insert_sql("dev", local=False)

    assert (
        'INSERT INTO "password_credentials" '
        '("user_id","verifier_phc","pepper_version","status") VALUES '
        "('user-1','$argon2id$fixture-salt-and-verifier',2,'active');"
    ) in sql
    assert "BEGIN TRANSACTION" not in sql


def test_retained_admin_set_scopes_every_identity_predicate() -> None:
    dana = "c2c12e5e-2cea-5d9a-bf7f-dc5dc012e2b4"
    jordan = "c948a37b-5c65-419d-8a2a-244c22279777"

    predicates = dict(reset_module.retained_admin_predicates((dana, jordan)))

    for table in (
        "organizations",
        "users",
        "password_credentials",
        "organization_memberships",
        "user_roles",
        "people",
        "user_headshots",
        "owned_resources",
        "resource_access_grants",
    ):
        assert dana in predicates[table]
        assert jordan in predicates[table]
    assert predicates["instance_setup"] == "singleton_key='primary'"


def test_retained_admin_set_must_be_unique_canonical_uuids() -> None:
    dana = "c2c12e5e-2cea-5d9a-bf7f-dc5dc012e2b4"

    with pytest.raises(ResetError, match="non-empty and unique"):
        reset_module.retained_admin_predicates((dana, dana))
    with pytest.raises(ResetError, match="canonical UUIDs"):
        reset_module.retained_admin_predicates(("not-a-user-id",))


def test_bootstrap_shape_accepts_only_the_explicit_two_admin_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dana = "c2c12e5e-2cea-5d9a-bf7f-dc5dc012e2b4"
    jordan = "c948a37b-5c65-419d-8a2a-244c22279777"
    observed_sql = ""

    def fake_execute(environment_name: str, sql: str, *, local: bool = False) -> list[dict]:
        nonlocal observed_sql
        del environment_name, local
        observed_sql = sql
        return [
            {
                "success": True,
                "results": [
                    {
                        "organizations": 1,
                        "bootstrap_users": 2,
                        "active_admin_memberships": 2,
                        "active_organizer_roles": 2,
                        "bootstrap_password_credentials": 2,
                        "manageable_organizers": 2,
                        "completed_setups": 1,
                        "bootstrapped_at_ms": 123,
                    }
                ],
            }
        ]

    monkeypatch.setattr(reset_module, "execute_sql", fake_execute)

    reset_module.assert_bootstrap_shape("dev", local=False, retained_admin_user_ids=(dana, jordan))

    assert observed_sql.count(dana) == 5
    assert observed_sql.count(jordan) == 5


def test_bootstrap_shape_rejects_retained_admin_without_workspace_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dana = "c2c12e5e-2cea-5d9a-bf7f-dc5dc012e2b4"
    jordan = "c948a37b-5c65-419d-8a2a-244c22279777"

    def fake_execute(environment_name: str, sql: str, *, local: bool = False) -> list[dict]:
        del environment_name, sql, local
        return [
            {
                "success": True,
                "results": [
                    {
                        "organizations": 1,
                        "bootstrap_users": 2,
                        "active_admin_memberships": 2,
                        "active_organizer_roles": 2,
                        "bootstrap_password_credentials": 2,
                        "manageable_organizers": 1,
                        "completed_setups": 1,
                        "bootstrapped_at_ms": 123,
                    }
                ],
            }
        ]

    monkeypatch.setattr(reset_module, "execute_sql", fake_execute)

    with pytest.raises(ResetError, match="manageable_organizers=1"):
        reset_module.assert_bootstrap_shape(
            "dev", retained_admin_user_ids=(dana, jordan)
        )


def test_deletion_order_removes_children_before_parents() -> None:
    schema = {
        "events": "CREATE TABLE events(id TEXT PRIMARY KEY)",
        "forms": "CREATE TABLE forms(event_id TEXT REFERENCES events(id))",
        "answers": "CREATE TABLE answers(form_id TEXT REFERENCES forms(id))",
        "users": "CREATE TABLE users(id TEXT PRIMARY KEY)",
    }

    order = deletion_order(schema, {"users"})

    assert order.index("answers") < order.index("forms") < order.index("events")


def test_deletion_order_never_touches_cloudflare_or_migration_tables() -> None:
    schema = {
        "_cf_KV": "CREATE TABLE _cf_KV(key TEXT)",
        "d1_migrations": "CREATE TABLE d1_migrations(name TEXT)",
        "events": "CREATE TABLE events(id TEXT PRIMARY KEY)",
    }

    assert deletion_order(schema, set()) == ["events"]


def test_deletion_order_rejects_cycles() -> None:
    schema = {
        "first": "CREATE TABLE first(second_id TEXT REFERENCES second(id))",
        "second": "CREATE TABLE second(first_id TEXT REFERENCES first(id))",
    }

    with pytest.raises(ResetError, match="cyclic"):
        deletion_order(schema, set())


def test_repository_wrangler_templates_share_only_the_local_database() -> None:
    root = Path(__file__).resolve().parents[2]
    main = (root / "wrangler.jsonc").read_text(encoding="utf-8")
    activity = (root / "wrangler.activity.jsonc").read_text(encoding="utf-8")

    database_id_pattern = r'"database_id": "([0-9a-f-]{36})"'
    main_ids = re.findall(database_id_pattern, main)
    activity_ids = re.findall(database_id_pattern, activity)

    assert main_ids[-1] == activity_ids[-1] == "00000000-0000-0000-0000-000000000000"

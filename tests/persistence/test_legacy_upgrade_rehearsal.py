import sqlite3

import pytest

from scripts import rehearse_legacy_upgrade as upgrade


@pytest.fixture
def databases(monkeypatch):
    source = sqlite3.connect(":memory:")
    target = sqlite3.connect(":memory:")
    for db, latest in [(source, False), (target, True)]:
        db.execute("CREATE TABLE users(id TEXT PRIMARY KEY, created_at_ms INTEGER)")
        db.execute("CREATE TABLE credentials(user_id TEXT, verifier BLOB)")
        for name in sorted(upgrade.REBUILD):
            if name == "owned_resources":
                columns = "id TEXT, resource_type TEXT"
            elif name == "resource_access_grants":
                columns = "id TEXT, permission TEXT"
            else:
                columns = "id TEXT"
            if latest:
                columns += ", CHECK(id IS NOT NULL)"
            db.execute(f"CREATE TABLE {upgrade.quote(name)} ({columns})")
        db.execute("CREATE TABLE d1_migrations(id INTEGER, name TEXT)")
        db.execute("INSERT INTO d1_migrations VALUES(1,'original.sql')")
        db.commit()
    for number in range(4):
        source.execute("INSERT INTO users VALUES (?,?)", (str(number), 1000 + number))
        source.execute("INSERT INTO credentials VALUES (?,?)", (str(number), b"test-verifier"))
    source.execute("INSERT INTO owned_resources VALUES('org','organization')")
    source.execute("INSERT INTO resource_access_grants VALUES('grant','manage')")
    source.commit()
    monkeypatch.setattr(upgrade, "LEGACY_SCHEMA", upgrade.schema_hash(source))
    yield source, target
    source.close()
    target.close()


def test_rehearsal_preserves_all_rows_credentials_timestamps_and_ledger(databases):
    source, target = databases
    before = upgrade.data_snapshot(source)
    report = upgrade.rehearse(source, target)
    assert upgrade.data_snapshot(source) == before
    assert upgrade.schema_contract(source) == upgrade.schema_contract(target)
    assert source.execute("SELECT * FROM d1_migrations").fetchall() == [(1, "original.sql")]
    assert report["users_preserved"] == 4
    assert report["deployment_ready"] is False


def test_unknown_source_refuses_before_modification(databases):
    source, target = databases
    source.execute("CREATE TABLE unexpected(id TEXT)")
    before = upgrade.schema_contract(source), upgrade.data_snapshot(source)
    with pytest.raises(ValueError, match="unsupported legacy schema"):
        upgrade.rehearse(source, target)
    assert (upgrade.schema_contract(source), upgrade.data_snapshot(source)) == before


@pytest.mark.parametrize("table", sorted(upgrade.EMPTY_REQUIRED))
def test_nonempty_operational_table_refuses_before_modification(databases, table):
    source, target = databases
    source.execute(f"INSERT INTO {upgrade.quote(table)} VALUES ('retained')")  # noqa: S608 - fixed table allowlist
    source.commit()
    before = upgrade.schema_contract(source), upgrade.data_snapshot(source)
    with pytest.raises(ValueError, match="must be empty"):
        upgrade.rehearse(source, target)
    assert (upgrade.schema_contract(source), upgrade.data_snapshot(source)) == before


def test_target_constraint_failure_rolls_back_entire_rebuild(databases):
    source, target = databases
    target.execute("DROP TABLE resource_access_grants")
    target.execute("CREATE TABLE resource_access_grants(id TEXT CHECK(id='other'),permission TEXT)")
    before = upgrade.schema_contract(source), upgrade.data_snapshot(source)
    with pytest.raises(sqlite3.IntegrityError):
        upgrade.rehearse(source, target)
    assert (upgrade.schema_contract(source), upgrade.data_snapshot(source)) == before


def test_rehearsal_does_not_silently_reapply_to_upgraded_database(databases):
    source, target = databases
    upgrade.rehearse(source, target)
    before = upgrade.schema_contract(source), upgrade.data_snapshot(source)
    with pytest.raises(ValueError, match="unsupported legacy schema"):
        upgrade.rehearse(source, target)
    assert (upgrade.schema_contract(source), upgrade.data_snapshot(source)) == before


def test_sql_comparison_preserves_literals_but_ignores_comments():
    assert upgrade.normalized("SELECT '-- keep spaces' -- ignore\n;") == "SELECT'-- keep spaces'"
    assert upgrade.normalized("SELECT 'a b'") != upgrade.normalized("SELECT 'ab'")

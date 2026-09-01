"""Rehearse a narrowly scoped pre-rebase upgrade without contacting Cloudflare.

This is not a migration runner. It emits no production SQL and never changes
Wrangler's migration ledger. A deployment still needs an approved, runner-owned
transition and old/new runtime compatibility checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

LEGACY_SCHEMA = "90d4644ca2498d7285c52b71ca088dca70995a3891442f2e78cbdff3a7923ede"
REBUILD = frozenset({
    "events", "evaluation_rounds", "event_memberships", "identity_invitations",
    "owned_resources", "resource_access_grants", "speaker_asset_versions",
    "speaker_tasks",
})
EMPTY_REQUIRED = REBUILD - {"owned_resources", "resource_access_grants"}


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def objects(db: sqlite3.Connection) -> dict[tuple[str, str], str]:
    return {
        (kind, name): sql
        for kind, name, sql in db.execute(
            "SELECT type,name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL ORDER BY type,name"
        )
        if not name.startswith(("sqlite_", "_cf_")) and name != "d1_migrations"
    }


def schema_hash(db: sqlite3.Connection) -> str:
    rows = [(kind, name, " ".join(sql.split()))
            for (kind, name), sql in objects(db).items()]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def normalized(sql: str) -> str:
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|--[^\n]*|/\*[\s\S]*?\*/|\S", sql)
    return "".join(t for t in tokens if not t.startswith(("--", "/*"))).rstrip(";")


def schema_contract(db: sqlite3.Connection) -> dict:
    return {key: normalized(sql) for key, sql in objects(db).items()}


def data_snapshot(db: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Keep credential material in memory; never include it in report output."""
    return {
        name: sorted(db.execute(f"SELECT * FROM {quote(name)}").fetchall(), key=repr)  # noqa: S608 - quoted schema identifier
        for (kind, name) in objects(db) if kind == "table"
    }


def require_integrity(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("backup integrity check failed")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("backup foreign-key check failed")


def rehearsal_sql(source: sqlite3.Connection, target: sqlite3.Connection) -> list[str]:
    """Plan only the known, empty-operational-data shape; refuse other upgrades."""
    require_integrity(source)
    if schema_hash(source) != LEGACY_SCHEMA:
        raise ValueError("unsupported legacy schema; refusing to guess an upgrade")
    for name in sorted(EMPTY_REQUIRED):
        if source.execute(f"SELECT COUNT(*) FROM {quote(name)}").fetchone()[0]:  # noqa: S608 - fixed allowlist
            raise ValueError(f"{name} must be empty for this rehearsal")
    if source.execute(
        "SELECT COUNT(*) FROM owned_resources WHERE resource_type!='organization'"
    ).fetchone()[0]:
        raise ValueError("non-organization ownership requires a different upgrade")
    if source.execute(
        "SELECT COUNT(*) FROM resource_access_grants WHERE permission!='manage'"
    ).fetchone()[0]:
        raise ValueError("retired grants require a different upgrade")

    old, new = objects(source), objects(target)
    changed = {name for (kind, name), sql in new.items()
               if kind == "table" and (kind, name) in old
               and normalized(sql) != normalized(old[kind, name])}
    if changed != REBUILD or set(old) - set(new):
        raise ValueError("target schema drifted beyond the reviewed table set")
    # Deferred FK validation does not disable CASCADE/SET NULL actions. Reject
    # any rebuild that could silently invoke those actions, even on empty rows.
    for kind, name in old:
        if kind == "table":
            for fk in source.execute(f"PRAGMA foreign_key_list({quote(name)})"):
                if fk[2] in REBUILD and fk[6] not in {"RESTRICT", "NO ACTION"}:
                    raise ValueError("rebuild could invoke a foreign-key delete action")

    statements = ["PRAGMA defer_foreign_keys=ON"]
    # Save only the two populated tables, with explicit columns when restoring.
    for name in sorted(REBUILD - EMPTY_REQUIRED):
        statements.append(
            f"CREATE TABLE {quote('_upgrade_' + name)} AS SELECT * FROM {quote(name)}"  # noqa: S608 - fixed allowlist
        )
    # Reinstall every trigger/index from the target so references are validated
    # only after all rebuilt tables exist. This happens only in a local transaction.
    for kind, name in old:
        if kind in {"trigger", "index"}:
            statements.append(f"DROP {kind.upper()} {quote(name)}")
    for name in sorted(REBUILD):
        statements.append(f"DROP TABLE {quote(name)}")
    for (kind, name), sql in new.items():
        if kind == "table" and (name in REBUILD or (kind, name) not in old):
            statements.append(sql)
    for name in sorted(REBUILD - EMPTY_REQUIRED):
        columns = ",".join(quote(r[1]) for r in source.execute(
            f"PRAGMA table_info({quote(name)})"
        ))
        statements.append(
            f"INSERT INTO {quote(name)} ({columns}) SELECT {columns} "  # noqa: S608 - quoted schema identifiers
            f"FROM {quote('_upgrade_' + name)}"
        )
        statements.append(f"DROP TABLE {quote('_upgrade_' + name)}")
    statements.extend(sql for (kind, _), sql in new.items()
                      if kind in {"index", "trigger", "view"})
    return statements


def rehearse(source: sqlite3.Connection, target: sqlite3.Connection) -> dict:
    """Rollback on any failure; prove all pre-existing rows and ledger survive."""
    before = data_snapshot(source)
    ledger = source.execute("SELECT * FROM d1_migrations ORDER BY id").fetchall()
    statements = rehearsal_sql(source, target)
    source.execute("PRAGMA foreign_keys=ON")
    source.execute("BEGIN")
    try:
        for statement in statements:
            source.execute(statement)
        require_integrity(source)
        if schema_contract(source) != schema_contract(target):
            raise ValueError("upgraded schema differs from canonical target")
        after = data_snapshot(source)
        if any(after.get(name) != rows for name, rows in before.items()):
            raise ValueError("upgrade changed existing data")
        if ledger != source.execute("SELECT * FROM d1_migrations ORDER BY id").fetchall():
            raise ValueError("upgrade changed the migration ledger")
        source.commit()
    except Exception:
        source.rollback()
        raise
    return {"status": "local-rehearsal-passed", "statements": len(statements),
            "existing_rows_unchanged": True, "migration_ledger_unchanged": True,
            "users_preserved": len(before["users"]),
            "target_schema_sha256": schema_hash(target),
            "remote_mutations": 0, "deployment_ready": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--migrations", type=Path, default=Path("migrations_baseline"))
    args = parser.parse_args()
    with sqlite3.connect(":memory:") as source, sqlite3.connect(":memory:") as target:
        source.executescript(args.backup.read_text())
        for path in sorted(args.migrations.glob("*.sql")):
            target.executescript(path.read_text())
        print(json.dumps(rehearse(source, target), sort_keys=True))


if __name__ == "__main__":
    main()

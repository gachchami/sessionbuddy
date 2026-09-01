"""Recreate a development D1 and restore only its bootstrap administrator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    from scripts.cloudflare_preflight import load_environment
    from scripts.render_private_cloudflare_config import load_manifest
except ModuleNotFoundError:  # Direct execution from scripts/.
    from cloudflare_preflight import load_environment
    from render_private_cloudflare_config import load_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_BASELINE = PROJECT_ROOT / "migrations_baseline" / "0001_baseline.sql"
MIGRATION_NAME = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql$")
NPX = shutil.which("npx") or "/usr/local/bin/npx"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
USER_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
REFERENCE = re.compile(r"\bREFERENCES\s+[\"`\[]?([A-Za-z_][A-Za-z0-9_]*)", re.I)

# These rows are the minimum viable bootstrap identity bundle. Tables with
# conditional retention are handled separately below.
PRESERVED_TABLES = {
    "instance_setup",
    "organization_memberships",
    "organizations",
    "password_credentials",
    "user_headshots",
    "user_roles",
    "users",
}
CONDITIONAL_TABLES = {"owned_resources", "people", "resource_access_grants"}
SYSTEM_TABLES = {"_cf_KV", "_cf_METADATA", "d1_migrations", "sqlite_sequence"}
LOCAL_SYSTEM_TABLES = SYSTEM_TABLES
BOOTSTRAP_TABLES = (
    (
        "organizations",
        "id IN (SELECT organization_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active')",
    ),
    (
        "users",
        "id IN (SELECT user_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active')",
    ),
    (
        "password_credentials",
        "status='active' AND user_id IN (SELECT user_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active')",
    ),
    (
        "organization_memberships",
        "role='organization_admin' AND status='active'",
    ),
    (
        "user_roles",
        "role='organizer' AND status='active' AND user_id IN "
        "(SELECT user_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active')",
    ),
    (
        "people",
        "user_id IN (SELECT user_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active')",
    ),
    (
        "user_headshots",
        "user_id IN (SELECT user_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active')",
    ),
    (
        "owned_resources",
        "resource_type='organization' AND owner_user_id IN "
        "(SELECT user_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active')",
    ),
    (
        "resource_access_grants",
        "permission='manage' AND status='active' AND user_id IN "
        "(SELECT user_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active')",
    ),
    ("instance_setup", "singleton_key='primary'"),
)


def retained_admin_predicates(user_ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    if not user_ids or len(set(user_ids)) != len(user_ids):
        raise ResetError("retained administrator user IDs must be non-empty and unique")
    invalid = [user_id for user_id in user_ids if USER_ID.fullmatch(user_id) is None]
    if invalid:
        raise ResetError("retained administrator user IDs must be canonical UUIDs")
    selected = ",".join(f"'{user_id}'" for user_id in user_ids)
    return (
        (
            "organizations",
            "id IN (SELECT organization_id FROM organization_memberships "  # noqa: S608
            f"WHERE role='organization_admin' AND status='active' "
            f"AND user_id IN ({selected}))",  # noqa: S608 - UUIDs validated above.
        ),
        ("users", f"id IN ({selected})"),
        (
            "password_credentials",
            f"status='active' AND user_id IN ({selected})",
        ),
        (
            "organization_memberships",
            f"role='organization_admin' AND status='active' AND user_id IN ({selected})",
        ),
        (
            "user_roles",
            f"role='organizer' AND status='active' AND user_id IN ({selected})",
        ),
        ("people", f"user_id IN ({selected})"),
        ("user_headshots", f"user_id IN ({selected})"),
        (
            "owned_resources",
            f"resource_type='organization' AND owner_user_id IN ({selected})",
        ),
        (
            "resource_access_grants",
            f"permission='manage' AND status='active' AND user_id IN ({selected})",
        ),
        ("instance_setup", "singleton_key='primary'"),
    )


class ResetError(RuntimeError):
    """An operator-safe reset failure."""


def assert_canonical_baseline_layout(baseline: Path = CANONICAL_BASELINE) -> None:
    migration_files = sorted(path.name for path in baseline.parent.glob("*.sql"))
    invalid = [name for name in migration_files if MIGRATION_NAME.fullmatch(name) is None]
    prefixes = [name.split("_", 1)[0] for name in migration_files]
    if (
        not baseline.is_file()
        or not migration_files
        or migration_files[0] != baseline.name
        or invalid
        or len(prefixes) != len(set(prefixes))
    ):
        found = ", ".join(migration_files) or "none"
        raise ResetError(
            "refusing reset: migrations must start with the canonical baseline and "
            f"use unique ordered names; found: {found}"
        )


def quote_identifier(value: str) -> str:
    if IDENTIFIER.fullmatch(value) is None:
        raise ResetError(f"D1 returned an unsafe table name: {value!r}")
    return f'"{value}"'


def run_wrangler(arguments: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "CI": "1", "NO_COLOR": "1"}
    return subprocess.run(  # noqa: S603 - fixed executable and validated arguments
        [NPX, "wrangler", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def d1_target_arguments(config: Path, *, local: bool) -> list[str]:
    target = "--local" if local else "--remote"
    return [target, "--config", str(config)]


def execute_sql(config: Path, sql: str, *, local: bool = False) -> list[dict]:
    result = run_wrangler(
        [
            "d1",
            "execute",
            "DB",
            *d1_target_arguments(config, local=local),
            "--command",
            sql,
            "--json",
        ]
    )
    if result.returncode != 0:
        detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise ResetError(detail or "Wrangler D1 execution failed without diagnostic output")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ResetError("Wrangler returned an invalid D1 response") from error
    if not isinstance(payload, list) or any(not item.get("success") for item in payload):
        raise ResetError("D1 did not report success for every reset statement")
    return payload


def first_results(payload: list[dict]) -> list[dict]:
    try:
        results = payload[0]["results"]
    except (IndexError, KeyError, TypeError) as error:
        raise ResetError("D1 returned an unexpected response shape") from error
    if not isinstance(results, list):
        raise ResetError("D1 returned an unexpected result set")
    return results


def run_checked(arguments: list[str], *, timeout: int = 300) -> str:
    result = run_wrangler(arguments, timeout=timeout)
    if result.returncode != 0:
        detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise ResetError(detail or "Wrangler command failed without diagnostic output")
    return result.stdout


def table_columns(environment_name: str, table: str, *, local: bool) -> list[str]:
    rows = first_results(
        execute_sql(
            environment_name,
            f"PRAGMA table_info({quote_identifier(table)})",  # noqa: S608
            local=local,
        )
    )
    columns = [str(row.get("name", "")) for row in rows]
    if not columns or any(IDENTIFIER.fullmatch(column) is None for column in columns):
        raise ResetError(f"could not read safe columns for bootstrap table {table}")
    return columns


def bootstrap_insert_sql(
    environment_name: str, *, local: bool, retained_admin_user_ids: tuple[str, ...] = ()
) -> str:
    statements: list[str] = []
    tables = (
        retained_admin_predicates(retained_admin_user_ids)
        if retained_admin_user_ids
        else BOOTSTRAP_TABLES
    )
    expected_count = len(retained_admin_user_ids) if retained_admin_user_ids else 1
    for table, where in tables:
        columns = table_columns(environment_name, table, local=local)
        projections = ",".join(
            f"quote({quote_identifier(column)}) AS {quote_identifier(column)}" for column in columns
        )
        rows = first_results(
            execute_sql(
                environment_name,
                f"SELECT {projections} FROM {quote_identifier(table)} "  # noqa: S608
                f"WHERE {where} ORDER BY rowid",
                local=local,
            )
        )
        required_count = 1 if table in {"organizations", "instance_setup"} else expected_count
        if (
            table
            in {
                "organizations",
                "users",
                "password_credentials",
                "organization_memberships",
                "user_roles",
                "instance_setup",
            }
            and len(rows) != required_count
        ):
            raise ResetError(
                f"bootstrap export expected exactly {required_count} {table} rows; "
                f"found {len(rows)}"
            )
        column_sql = ",".join(quote_identifier(column) for column in columns)
        for row in rows:
            values: list[str] = []
            for column in columns:
                value = row.get(column)
                if not isinstance(value, str) or not value:
                    raise ResetError(f"bootstrap export could not quote {table}.{column} safely")
                values.append(value)
            statements.append(
                f"INSERT INTO {quote_identifier(table)} ({column_sql}) "  # noqa: S608
                f"VALUES ({','.join(values)});"
            )
    return "\n".join(statements) + "\n"


def write_bootstrap_bundle(
    environment_name: str,
    database_name: str,
    *,
    local: bool,
    retained_admin_user_ids: tuple[str, ...] = (),
) -> tuple[Path, str]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = "local" if local else "remote"
    destination = (
        PROJECT_ROOT / ".local" / "backups" / f"{database_name}-{target}-bootstrap-{stamp}.sql"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = bootstrap_insert_sql(
        environment_name,
        local=local,
        retained_admin_user_ids=retained_admin_user_ids,
    )
    destination.write_text(content, encoding="utf-8")
    destination.chmod(0o600)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if "password_credentials" not in content or "instance_setup" not in content:
        raise ResetError("bootstrap bundle failed its integrity check")
    return destination, digest


def deletion_order(
    schema: dict[str, str],
    preserved: set[str],
    *,
    system_tables: set[str] = SYSTEM_TABLES,
) -> list[str]:
    targets = set(schema) - preserved - system_tables
    dependencies = {
        table: {name for name in REFERENCE.findall(sql) if name in targets and name != table}
        for table, sql in schema.items()
        if table in targets
    }
    state: dict[str, int] = {}
    parent_first: list[str] = []

    def visit(table: str) -> None:
        marker = state.get(table, 0)
        if marker == 1:
            raise ResetError(f"Cannot safely order cyclic table dependency at {table}")
        if marker == 2:
            return
        state[table] = 1
        for parent in sorted(dependencies[table]):
            visit(parent)
        state[table] = 2
        parent_first.append(table)

    for table in sorted(targets):
        visit(table)
    return list(reversed(parent_first))


def validate_configuration(config: Path, *, local: bool = False) -> str:
    environment, variables = load_environment(config)
    allowed = {"local", "development"} if local else {"development"}
    if variables.get("APP_ENV") not in allowed:
        target = "local" if local else "remote"
        expected = " or ".join(sorted(allowed))
        raise ResetError(f"{target} reset requires APP_ENV to be {expected}")
    databases = [
        item for item in environment.get("d1_databases", []) if item.get("binding") == "DB"
    ]
    if len(databases) != 1 or not databases[0].get("database_name"):
        raise ResetError("the selected environment must have exactly one DB binding")
    return str(databases[0]["database_name"])


def assert_bootstrap_shape(
    environment_name: str,
    *,
    local: bool = False,
    retained_admin_user_ids: tuple[str, ...] = (),
) -> dict:
    selected = ""
    expected_count = 1
    if retained_admin_user_ids:
        retained_admin_predicates(retained_admin_user_ids)
        selected = ",".join(f"'{user_id}'" for user_id in retained_admin_user_ids)
        expected_count = len(retained_admin_user_ids)
    user_filter = f" AND user_id IN ({selected})" if selected else ""
    authority_user_ids = selected or (
        "SELECT user_id FROM organization_memberships "
        "WHERE role='organization_admin' AND status='active'"
    )
    sql = f"""
SELECT
  (SELECT COUNT(*) FROM organizations) AS organizations,
  (SELECT COUNT(DISTINCT user_id) FROM organization_memberships
    WHERE role='organization_admin' AND status='active'{user_filter}) AS bootstrap_users,
  (SELECT COUNT(*) FROM organization_memberships
    WHERE role='organization_admin' AND status='active'{user_filter}) AS active_admin_memberships,
  (SELECT COUNT(*) FROM user_roles
    WHERE role='organizer' AND status='active'{user_filter}) AS active_organizer_roles,
  (SELECT COUNT(*) FROM password_credentials
    WHERE status='active'{user_filter}) AS bootstrap_password_credentials,
  (SELECT COUNT(*) FROM users retained_user
    WHERE retained_user.id IN ({authority_user_ids})
      AND EXISTS (
        SELECT 1 FROM owned_resources resources
        LEFT JOIN resource_access_grants grants
          ON grants.resource_id=resources.id AND grants.user_id=retained_user.id
         AND grants.status='active' AND grants.permission='manage'
        WHERE resources.resource_type='organization'
          AND resources.status='active'
          AND (resources.owner_user_id=retained_user.id OR grants.id IS NOT NULL)
      )) AS manageable_organizers,
  (SELECT COUNT(*) FROM instance_setup WHERE singleton_key='primary') AS completed_setups,
  (SELECT completed_at_ms FROM instance_setup WHERE singleton_key='primary') AS bootstrapped_at_ms
""".strip()  # noqa: S608 - retained UUIDs are validated before interpolation.
    rows = first_results(execute_sql(environment_name, sql, local=local))
    if len(rows) != 1:
        raise ResetError("could not read the bootstrap identity shape")
    shape = rows[0]
    expected_one = ("organizations", "completed_setups")
    expected_selected = (
        "bootstrap_users",
        "active_admin_memberships",
        "active_organizer_roles",
        "bootstrap_password_credentials",
        "manageable_organizers",
    )
    invalid = [name for name in expected_one if shape.get(name) != 1]
    invalid.extend(name for name in expected_selected if shape.get(name) != expected_count)
    if invalid or not isinstance(shape.get("bootstrapped_at_ms"), int):
        fields = (*expected_one, *expected_selected, "bootstrapped_at_ms")
        detail = ", ".join(f"{name}={shape.get(name)!r}" for name in fields)
        raise ResetError(
            f"refusing reset: expected one complete bootstrap identity; found {detail}"
        )
    return shape


def read_schema(environment_name: str, *, local: bool = False) -> dict[str, str]:
    rows = first_results(
        execute_sql(
            environment_name,
            "SELECT name,sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            local=local,
        )
    )
    schema = {str(row["name"]): str(row["sql"] or "") for row in rows}
    required = PRESERVED_TABLES | CONDITIONAL_TABLES | {"events"}
    missing = sorted(required - set(schema))
    if missing:
        raise ResetError(f"database schema is missing required tables: {', '.join(missing)}")
    return schema


def export_backup(environment_name: str, database_name: str, *, local: bool = False) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = "local" if local else "remote"
    backup = (
        PROJECT_ROOT / ".local" / "backups" / f"{database_name}-{target}-before-reset-{stamp}.sql"
    )
    backup.parent.mkdir(parents=True, exist_ok=True)
    result = run_wrangler(
        [
            "d1",
            "export",
            "DB",
            *d1_target_arguments(environment_name, local=local),
            "--output",
            str(backup),
        ],
        timeout=300,
    )
    if result.returncode != 0 or not backup.is_file() or backup.stat().st_size == 0:
        raise ResetError((result.stderr or result.stdout or "D1 backup was not created").strip())
    backup.chmod(0o600)
    if "CREATE TABLE" not in backup.read_text(encoding="utf-8", errors="replace"):
        raise ResetError("D1 backup does not contain a schema")
    return backup


def drop_local_schema_sql(schema: dict[str, str]) -> str:
    targets = set(schema) - LOCAL_SYSTEM_TABLES
    order = deletion_order(schema, set(), system_tables=LOCAL_SYSTEM_TABLES)
    if set(order) != targets:
        missing = sorted(targets - set(order))
        raise ResetError(f"local recreation could not order tables: {', '.join(missing)}")
    statements = ["PRAGMA foreign_keys=OFF"]
    statements.extend(f"DROP TABLE IF EXISTS {quote_identifier(table)}" for table in order)
    # Workerd protects Wrangler's migration ledger from DROP TABLE with
    # SQLITE_AUTH. Emptying the ledger is sufficient: the subsequent migration
    # apply recreates every application table and records the canonical
    # baseline exactly as it would for a new local D1.
    statements.append("DELETE FROM d1_migrations")
    statements.append("PRAGMA foreign_keys=ON")
    return ";\n".join(statements) + ";"


def apply_baseline(environment_name: str, *, local: bool) -> None:
    arguments = [
        "d1",
        "migrations",
        "apply",
        "DB",
        *d1_target_arguments(environment_name, local=local),
    ]
    run_checked(arguments, timeout=300)
    repeat = run_checked(arguments, timeout=300)
    if "No migrations to apply" not in repeat:
        raise ResetError("repeat baseline apply was not a confirmed no-op")


def restore_bootstrap(environment_name: str, bootstrap: Path, *, local: bool) -> None:
    run_checked(
        [
            "d1",
            "execute",
            "DB",
            *d1_target_arguments(environment_name, local=local),
            "--file",
            str(bootstrap),
        ],
        timeout=300,
    )


def replace_database_id(path: Path, old_id: str, new_id: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(old_id) != 1:
        raise ResetError(
            f"expected exactly one development database id in {path.name}; "
            f"found {source.count(old_id)}"
        )
    path.write_text(source.replace(old_id, new_id), encoding="utf-8")


def replace_database_ids(paths: tuple[Path, ...], old_id: str, new_id: str) -> None:
    sources = {path: path.read_text(encoding="utf-8") for path in paths}
    invalid = [path.name for path, source in sources.items() if source.count(old_id) != 1]
    if invalid:
        raise ResetError("expected exactly one development database id in: " + ", ".join(invalid))
    for path, source in sources.items():
        path.write_text(source.replace(old_id, new_id), encoding="utf-8")


def validate_remote_rebind_sources(
    configs: tuple[Path, ...], old_id: str, database_name: str
) -> None:
    sources = {path: path.read_text(encoding="utf-8") for path in configs}
    invalid = [path.name for path, source in sources.items() if source.count(old_id) != 1]
    if invalid:
        raise ResetError(
            "expected exactly one selected database id before reset in: " + ", ".join(invalid)
        )
    for config in configs[:2]:
        environment, _ = load_environment(config)
        bindings = [
            item for item in environment.get("d1_databases", []) if item.get("binding") == "DB"
        ]
        expected = {"database_name": database_name, "database_id": old_id}
        mismatched = len(bindings) != 1 or any(
            bindings[0].get(key) != value for key, value in expected.items()
        )
        if mismatched:
            raise ResetError(f"{config.name} does not identify the selected D1 database")
    manifest = load_manifest(configs[-1])
    if (
        manifest.get("D1_DATABASE_NAME") != database_name
        or manifest.get("D1_DATABASE_ID") != old_id
    ):
        raise ResetError(f"{configs[-1].name} does not identify the selected D1 database")


def configured_database_id(config: Path) -> str:
    environment, _ = load_environment(config)
    bindings = [item for item in environment.get("d1_databases", []) if item.get("binding") == "DB"]
    if len(bindings) != 1 or not bindings[0].get("database_id"):
        raise ResetError("the selected environment must have one exact DB id")
    return str(bindings[0]["database_id"])


def assert_local_workers_stopped() -> None:
    endpoints = (
        ("127.0.0.1", 8787),
        ("127.0.0.1", 8788),
        ("worker", 8787),
        ("activity-worker", 8788),
    )
    running: list[str] = []
    for host, port in endpoints:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                running.append(f"{host}:{port}")
        except OSError:
            continue
    if running:
        raise ResetError(
            "stop local worker and activity-worker before reset; reachable: " + ", ".join(running)
        )


def recreate_remote_database(
    database_name: str,
    old_id: str,
    *,
    location: str,
    configs: tuple[Path, ...],
) -> str:
    validate_remote_rebind_sources(configs, old_id, database_name)
    selected_config = configs[0]
    run_checked(
        [
            "d1",
            "delete",
            database_name,
            "--skip-confirmation",
            "--config",
            str(selected_config),
        ],
        timeout=300,
    )
    run_checked(
        [
            "d1",
            "create",
            database_name,
            "--location",
            location,
            "--config",
            str(selected_config),
        ],
        timeout=300,
    )
    payload = json.loads(
        run_checked(["d1", "list", "--json", "--config", str(selected_config)], timeout=180)
    )
    matches = [item for item in payload if item.get("name") == database_name]
    if len(matches) != 1 or not matches[0].get("uuid"):
        raise ResetError("could not resolve the recreated D1 database id")
    new_id = str(matches[0]["uuid"])
    if new_id == old_id:
        raise ResetError("recreated D1 unexpectedly retained its previous id")
    replace_database_ids(configs, old_id, new_id)
    return new_id


def deploy_workers(main_config: Path, activity_config: Path) -> None:
    commands = (
        ["uv", "run", "pywrangler", "deploy", "--config", str(main_config)],
        [
            "uv",
            "run",
            "pywrangler",
            "deploy",
            "--config",
            str(activity_config),
        ],
    )
    environment = {**os.environ, "CI": "1", "NO_COLOR": "1"}
    for command in commands:
        result = subprocess.run(  # noqa: S603 - fixed internal commands
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            detail = "\n".join(
                part.strip() for part in (result.stdout, result.stderr) if part.strip()
            )
            raise ResetError(detail or f"deployment failed: {' '.join(command)}")


def verify_fresh_reset(
    environment_name: str,
    bootstrapped_at_ms: int,
    *,
    local: bool,
    retained_admin_user_ids: tuple[str, ...] = (),
) -> None:
    schema = read_schema(environment_name, local=local)
    bootstrap_names = {table for table, _ in BOOTSTRAP_TABLES}
    operational = sorted(set(schema) - bootstrap_names - SYSTEM_TABLES)
    rows: list[dict] = []
    for offset in range(0, len(operational), 5):
        batch = operational[offset : offset + 5]
        unions = " UNION ALL ".join(
            f"SELECT '{table}' AS table_name,COUNT(*) AS row_count "  # noqa: S608
            f"FROM {quote_identifier(table)}"
            for table in batch
        )
        rows.extend(first_results(execute_sql(environment_name, unions, local=local)))
    leftovers = {str(row["table_name"]): int(row["row_count"]) for row in rows if row["row_count"]}
    if leftovers:
        raise ResetError(f"fresh reset retained operational rows: {leftovers}")
    shape = assert_bootstrap_shape(
        environment_name,
        local=local,
        retained_admin_user_ids=retained_admin_user_ids,
    )
    if shape["bootstrapped_at_ms"] != bootstrapped_at_ms:
        raise ResetError("restored bootstrap completion timestamp changed")
    totals = first_results(
        execute_sql(
            environment_name,
            "SELECT "
            "(SELECT COUNT(*) FROM organizations) AS organizations,"
            "(SELECT COUNT(*) FROM users) AS users,"
            "(SELECT COUNT(*) FROM organization_memberships) AS memberships,"
            "(SELECT COUNT(*) FROM user_roles) AS roles,"
            "(SELECT COUNT(*) FROM password_credentials) AS credentials",
            local=local,
        )
    )
    expected_count = len(retained_admin_user_ids) if retained_admin_user_ids else 1
    if (
        len(totals) != 1
        or totals[0].get("organizations") != 1
        or any(
            totals[0].get(name) != expected_count
            for name in ("users", "memberships", "roles", "credentials")
        )
    ):
        raise ResetError(f"fresh reset did not restore an exact bootstrap identity: {totals}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--activity-config", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--local",
        action="store_true",
        help="reset Wrangler's local D1 state instead of the remote development D1",
    )
    parser.add_argument(
        "--location",
        choices=("weur", "eeur", "apac", "oc", "wnam", "enam"),
        default="apac",
        help="primary location hint when recreating the remote D1",
    )
    parser.add_argument(
        "--confirm",
        help="must exactly match the configured D1 database name",
    )
    parser.add_argument(
        "--retain-admin-user-id",
        action="append",
        default=[],
        help="active administrator UUID to preserve; repeat to retain an explicit set",
    )
    arguments = parser.parse_args()
    retained_admin_user_ids = tuple(arguments.retain_admin_user_id)
    if arguments.local:
        main_config = arguments.config or PROJECT_ROOT / "wrangler.jsonc"
        activity_config = arguments.activity_config or PROJECT_ROOT / "wrangler.activity.jsonc"
    else:
        if (
            arguments.config is None
            or arguments.activity_config is None
            or arguments.manifest is None
        ):
            parser.error("remote reset requires --config, --activity-config, and --manifest")
        main_config = arguments.config
        activity_config = arguments.activity_config
    try:
        # Fail before reading or mutating D1 unless the immutable baseline is
        # followed only by a well-formed ordered migration ledger.
        assert_canonical_baseline_layout()
        database_name = validate_configuration(main_config, local=arguments.local)
        if arguments.confirm != database_name:
            raise ResetError(f"pass --confirm {database_name} to authorize the destructive reset")
        shape = assert_bootstrap_shape(
            main_config,
            local=arguments.local,
            retained_admin_user_ids=retained_admin_user_ids,
        )
        schema = read_schema(main_config, local=arguments.local)
        if arguments.local:
            assert_local_workers_stopped()
        backup = export_backup(main_config, database_name, local=arguments.local)
        backup_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        bootstrap, bootstrap_digest = write_bootstrap_bundle(
            main_config,
            database_name,
            local=arguments.local,
            retained_admin_user_ids=retained_admin_user_ids,
        )
        if arguments.local:
            execute_sql(
                main_config,
                drop_local_schema_sql(schema),
                local=True,
            )
        else:
            old_id = configured_database_id(main_config)
            recreate_remote_database(
                database_name,
                old_id,
                location=arguments.location,
                configs=(main_config, activity_config, arguments.manifest),
            )
        apply_baseline(main_config, local=arguments.local)
        execute_sql(
            main_config,
            "DELETE FROM instance_setup_credentials",
            local=arguments.local,
        )
        restore_bootstrap(main_config, bootstrap, local=arguments.local)
        verify_fresh_reset(
            main_config,
            int(shape["bootstrapped_at_ms"]),
            local=arguments.local,
            retained_admin_user_ids=retained_admin_user_ids,
        )
        if not arguments.local:
            deploy_workers(main_config, activity_config)
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        ResetError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"Development reset failed: {error}", file=sys.stderr)
        return 1
    target = "local" if arguments.local else "remote"
    print(
        f"Development data reset completed for {target} D1; "
        f"bootstrap identity preserved for {database_name}."
    )
    print(f"Backup: {backup}")
    print(f"Backup SHA-256: {backup_digest}")
    print(f"Bootstrap bundle: {bootstrap}")
    print(f"Bootstrap SHA-256: {bootstrap_digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

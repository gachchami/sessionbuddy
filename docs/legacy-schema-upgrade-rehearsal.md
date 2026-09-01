# Legacy schema upgrade rehearsal

Purpose: establish whether a retained, pre-rebase database can reach the current
canonical schema without changing existing rows.

Status: in-progress. Authority: diagnostic procedure only, not deployment
approval or a replacement migration chain. Repository migration and release
requirements still apply.

## Scope

`scripts/rehearse_legacy_upgrade.py` accepts a private SQL backup with the known
pre-rebase six-migration schema. It restores the backup into SQLite memory and
compares it with the current canonical baseline and incrementals. It refuses
unknown schemas, populated affected operational tables, retired ownership/grant
types, or potentially cascading rebuilds. It never contacts Cloudflare, emits
executable migration SQL, or alters remote migration bookkeeping.

Run with the checked-in container toolchain:

```sh
docker compose run --rm --no-deps worker uv run python \
  scripts/rehearse_legacy_upgrade.py --backup PATH_TO_PRIVATE_SQL_BACKUP
docker compose run --rm --no-deps worker uv run pytest -q \
  tests/persistence/test_legacy_upgrade_rehearsal.py
```

Keep backups under the runbook-assigned ignored `.local/backups/`, mode 0600,
and record their SHA-256 separately. Never commit backup rows or credentials.
Cloudflare SQL export warns that queries may be unavailable during export;
do not promise zero downtime for the backup operation itself.

The rehearsal checks backup integrity and foreign keys, applies the candidate
rebuild in one local SQLite transaction, verifies the exact target schema,
compares **every existing row** (including user IDs, timestamps, credentials,
ownership, sessions and audit history), and verifies the original migration
ledger is unchanged. A failed rebuild rolls back locally. Successful output
explicitly says `deployment_ready: false`.

## Remaining deployment requirements

Local SQLite transaction success does not prove D1 migration-runner behavior.
Do not send the local transaction's BEGIN/COMMIT to Wrangler. Before deployment:

1. Provide a reviewed runner-owned legacy upgrade chain without deleting or
   falsifying existing migration bookkeeping; canonical fresh installs and
   already-rebased environments must remain unaffected.
2. Rehearse it through the local D1 runner, including failure rollback and a
   repeated no-op application. Verify old/new runtime compatibility.
3. Choose an explicitly approved write-maintenance window or implement a tested
   expand/contract rollout. Old code uses the removed speaker task destination
   column; a one-step rebuild cannot be advertised as zero-downtime compatible.
4. Pass the full release gate for the exact code being deployed. Do not include
   unrelated working-tree modifications in a committed-main deployment.
5. Take an approved fresh backup, record the original Worker versions and
   exact database identity, freeze writes as agreed, and revalidate source
   schema and row assumptions immediately before migration.
6. Verify schema, foreign keys, all retained rows, identity credentials and
   timestamps, deployment bindings, health, and authenticated journeys afterward.

Recovery requires the retained backup and a rehearsed schema-compatible restore
or roll-forward. Rolling old code back alone is not safe after contract changes.
Do not reset the database, recreate users, or discard original creation times.

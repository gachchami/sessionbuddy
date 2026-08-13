# Database release smoke workflow

This workflow uses synthetic data and an isolated SQLite file with the same
migrations, constraints, triggers, and query planner used by D1. It never targets
a remote binding and refuses to overwrite an existing database.

Run the release checks in the pinned project container:

```bash
docker compose run --rm --no-deps worker \
  uv run pytest tests/persistence/test_large_seed_release.py -q
```

The suite applies every migration from empty storage, creates the full release
envelope (10,000 submissions, 2,000 speakers, 50,000 tasks, and 2,000 agenda
items), checks integrity and foreign keys, and asserts intended indexes with
`EXPLAIN QUERY PLAN`. It does not run latency benchmarks.

Create a disposable database for manual inspection:

```bash
docker compose run --rm --no-deps worker \
  uv run python scripts/seed_large.py --database /tmp/sessionbuddy-large.sqlite3
```

Run a quick backup/restore smoke, or add `--large` for the release envelope:

```bash
docker compose run --rm --no-deps worker \
  uv run python scripts/release_db_smoke.py
```

The restore check uses distinct source, backup, and restored files. It verifies
`PRAGMA integrity_check`, `PRAGMA foreign_key_check`, the schema digest, and core
table row counts after restoration. Temporary artifacts are removed on success.

For a production schema change, run this local gate first, apply migrations to a
disposable preview D1 database, and repeat the black-box application smoke there.
Production recovery uses D1 Time Travel or the approved export mechanism; record
the recovery point, elapsed recovery time, restored row counts, application smoke
result, operator, and date. Use roll-forward migrations and do not restore into or
seed an active production database.

## Baseline rebase notes

When `speaker_asset_versions.version_comment` is made properly nullable during a
planned baseline rebase, remove both parts of its temporary sentinel contract:

- the released `DEFAULT 'Legacy upload'` and non-empty constraint; and
- `_version_comment()` in `speaker_operations/router.py`, which currently maps
  that sentinel to an absent API value.

After the rebase, new note-less versions should store `NULL` directly and the
API should expose the nullable column without sentinel translation. Include the
helper removal in the rebase equivalence review so this compatibility
indirection does not survive after it has lost its purpose.

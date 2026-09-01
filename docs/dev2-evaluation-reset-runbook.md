# Dev2 evaluation reset runbook

Purpose: recreate the disposable Development 2 database from the canonical
baseline while retaining the approved organizer identities and reseeding the
demo personas used by the evaluation kit.

Lifecycle: implemented.

Authority: `scripts/reset_development_data.py`, the Dev2 Wrangler private
configuration, the canonical migrations, and the checked-in reset tests. If
this runbook and those executable sources disagree, stop and update this
runbook after verifying the current implementation.

## Scope and safety boundary

This procedure is only for the explicitly disposable Development 2 database.
It must never be pointed at production, staging, or a database whose data must
be retained. The reset deletes the selected D1 database after exporting a
backup, creates a replacement, applies migrations, restores the selected
administrator identities, reseeds the demo users, deploys the two Dev2
Workers, and validates the result.

Do not edit a released migration or manually delete D1 migration bookkeeping.
Do not continue if the database name, environment, retained administrator IDs,
or private configuration files are uncertain.

## Required final fixture

- One organization.
- Dana and Jordan remain usable organizers for that organization.
- Demo speaker and demo reviewer accounts exist with the credentials in the
  ignored demo credential file used by `evalconfig.json`.
- Marcus may exist only as the unaccepted invitation fixture required by its
  scenario; it is not a replacement for a demo login persona.
- Zero operational events before an evaluation begins.
- No saved `.auth` state is required when `forceCredentialLogin` is enabled.

## Preflight

1. Confirm the current checkout and Dev2 deployment are the intended versions.
2. Confirm Wrangler authentication and the Dev2 database binding.
3. Resolve Dana and Jordan's current user UUIDs from Dev2. Do not copy IDs from
   an old runbook or terminal transcript.
4. Verify both users have active organizer roles, active administrator
   memberships, active password credentials, and either ownership or an active
   `manage` grant for the retained organization.
5. Confirm the ignored demo credential file contains the same organizer,
   speaker, and reviewer credentials referenced by `evalconfig.json`.

The reset command requires the exact configured database name as an explicit
confirmation and one `--retain-admin-user-id` argument per retained organizer:

```sh
docker compose run --rm --no-deps worker uv run python \
  scripts/reset_development_data.py \
  --config wrangler.private.jsonc \
  --activity-config wrangler.activity.private.jsonc \
  --confirm sessionbuddy-development-2 \
  --retain-admin-user-id DANA_CURRENT_USER_UUID \
  --retain-admin-user-id JORDAN_CURRENT_USER_UUID
```

Use the actual private configuration filenames selected for Dev2 if they
differ. Never substitute a production configuration.

## Mandatory validation

The reset is complete only when all of these hold:

1. The backup path and SHA-256 are recorded under `.local/backups/`.
2. Canonical migrations apply successfully and a second application reports
   no migrations to apply.
3. `PRAGMA foreign_key_check` returns no rows.
4. Exactly one organization and zero events remain.
5. Dana and Jordan both resolve to usable organizer workspaces.
6. Password sign-in succeeds for the demo organizer, demo speaker, and demo
   reviewer using the ignored credential source.
7. The main Worker and activity Worker health checks return HTTP 200.
8. The deployed Worker bindings reference the new D1 database ID.
9. The eval configuration preflight passes with the same target origin and
   persona credentials.

If any validation fails, stop. Preserve the backup and the old database ID;
do not start a paid evaluation against a partially reset environment.

## Starting from scratch

Clear only the eval kit's saved browser state, not its credential file. Then
run the launcher with credential login forced by the eval configuration:

```sh
SBEK_ROOT=/absolute/path/to/eval-checkout \
SBEK_TARGET_URL=https://sessionbuddy-development-2.example.workers.dev \
SBEK_PROVIDER=featherless-claude \
scripts/run_sbek.sh --dry-run
```

After the dry run and configuration check pass, remove `--dry-run` and pass the
desired area, scenario, turn-limit, resume, or other supported eval CLI flags
unchanged. `scripts/run_sbek.sh` forwards those flags to the eval launcher.


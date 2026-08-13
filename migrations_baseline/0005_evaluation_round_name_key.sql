-- Make the round-name rule race-free.
--
-- Two organizers submitting at once could both pass an application-level
-- "is this name taken?" read and both insert, because a read-then-write is not
-- atomic. The one-open-round rule next to it has never had that problem: it is
-- a partial unique index, so the database decides. This gives the name rule the
-- same footing.
--
-- The comparison key is stored rather than computed in the index because the
-- application folds case with Python's str.casefold(), which is Unicode-aware.
-- SQLite's lower() is ASCII-only, so an index over lower(name) would disagree
-- with the application about names like "ROUND" spelled with non-ASCII letters.
-- Writing the key at write time keeps one definition of "the same name".
--
-- The backfill below is that one exception: it can only use SQL's ASCII lower(),
-- so a pre-existing row whose name carries non-ASCII case gets an approximate
-- key. That is deliberate and bounded. The index is a backstop against the
-- concurrent-insert race; the application guard still compares real names with
-- casefold() and is what produces the actionable 409. Neither layer is
-- redundant, and any legacy row re-saved through the draft editor is rewritten
-- with the exact key.

ALTER TABLE evaluation_rounds ADD COLUMN name_key TEXT;

-- Step 1: backfill every existing row. Whitespace is collapsed the way the
-- application collapses it, so "Round  1" and "Round 1" share a key; the
-- repeated two-space replacements halve each run in turn, which covers far more
-- consecutive spaces than a 200-character name can hold.
UPDATE evaluation_rounds
   SET name_key = lower(trim(replace(replace(replace(replace(replace(replace(
                    replace(replace(replace(replace(replace(
                      name,
                    char(9),' '), char(10),' '), char(13),' '),
                    '  ',' '),'  ',' '),'  ',' '),'  ',' '),
                    '  ',' '),'  ',' '),'  ',' '),'  ',' ')))
 WHERE name_key IS NULL;

-- Step 2: resolve historical duplicates before the constraint can refuse them.
-- Only draft and open rounds are in scope, matching the index below: a closed
-- round keeps its name in the history and does not reserve it.
--
-- The loser of each collision is renamed rather than deleted or hidden. A round
-- owns assignments, evaluations and decisions; silently dropping one, or leaving
-- it outside the index with a NULL key, would trade a visible duplicate for an
-- invisible one. Ordering by (created_at_ms, id) makes the choice of winner
-- deterministic and repeatable.
--
-- The suffix is the row's own id, not its rank among the collisions. A rank
-- reads better and is wrong: " (2)" is a name humans type, so a loser renamed to
-- "Round (2)" could land on a round already called exactly that, and the
-- truncation needed to stay inside the name CHECK could push two different long
-- names onto the same one. Either way step 4 aborts AFTER this statement has
-- rewritten names. A primary key cannot collide with another row's, so this
-- statement cannot manufacture the duplicate the migration exists to remove.
-- 160 + ' (' + 36-character id + ')' stays within the 1..200 name CHECK.
UPDATE evaluation_rounds AS r
   SET name = substr(r.name, 1, 160) || ' (' || r.id || ')'
 WHERE r.status IN ('draft','open')
   AND EXISTS (
         SELECT 1 FROM evaluation_rounds e
          WHERE e.organization_id = r.organization_id
            AND e.event_id = r.event_id
            AND e.status IN ('draft','open')
            AND e.name_key = r.name_key
            AND (e.created_at_ms < r.created_at_ms
                 OR (e.created_at_ms = r.created_at_ms AND e.id < r.id))
       );

-- Step 3: re-derive the key for the rows step 2 renamed. Reading name again
-- rather than editing the key in place keeps name and name_key from drifting.
UPDATE evaluation_rounds
   SET name_key = lower(trim(replace(replace(replace(replace(replace(replace(
                    replace(replace(replace(replace(replace(
                      name,
                    char(9),' '), char(10),' '), char(13),' '),
                    '  ',' '),'  ',' '),'  ',' '),'  ',' '),
                    '  ',' '),'  ',' '),'  ',' '),'  ',' ')))
 WHERE status IN ('draft','open');

-- Step 4: the constraint itself, scoped exactly like uq_evaluation_rounds_event_open.
-- A NULL key cannot collide in SQLite, so a row the backfill somehow missed
-- degrades to the previous behaviour instead of failing the migration.
CREATE UNIQUE INDEX uq_evaluation_rounds_live_name
  ON evaluation_rounds(organization_id,event_id,name_key)
  WHERE status IN ('draft','open');

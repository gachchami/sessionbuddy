-- Give every evaluation round an explicit version contract.
--
-- Draft saves read the round, its memberships and its assignments before they
-- write. Nothing tied those reads to the writes: two organizers editing the same
-- draft could each build a diff against the original matrix and both batches
-- would apply, merging into a matrix neither requested. A save racing Open or
-- Close could also modify children after the round had stopped being a draft,
-- because only the parent UPDATE checked status.
--
-- The fix is optimistic concurrency backed by the repository's established
-- write-guard pattern (the same one submissions already use):
--
-- * `evaluation_rounds.version` is the token every editor carries. Mutations
--   send the version they read; the write's UPDATE carries
--   `AND version=?n AND status='draft'` (or the statuses each endpoint allows),
--   and bumps `version = version + 1`.
-- * `evaluation_round_write_guards` records `changes()` from that UPDATE
--   immediately after it. Its `CHECK (applied_changes = 1)` aborts the whole
--   D1 batch when the UPDATE matched zero rows -- the caller raced a winner --
--   rolling back every membership, assignment, audit, activity, notification
--   and idempotency statement that followed it. A losing request leaves no
--   residue.
--
-- The backfill is the DEFAULT clause itself: SQLite applies a constant default
-- to existing rows during ADD COLUMN, so every pre-existing round deterministically
-- starts at version 1 and no row can be left NULL. Clients that have not been
-- rebuilt to send a version are refused with an actionable 409 by the API layer;
-- nothing here guesses a version on their behalf.
--
-- The guard table deliberately has no foreign key, matching
-- submission_write_guards: the insert must never fail for referential reasons,
-- it exists solely to turn the preceding UPDATE's row count into a constraint.

ALTER TABLE evaluation_rounds ADD COLUMN version INTEGER NOT NULL DEFAULT 1
  CHECK (version >= 1);

CREATE TABLE evaluation_round_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  round_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL
);

CREATE INDEX idx_evaluation_round_write_guards_round
  ON evaluation_round_write_guards(round_id);

-- ONE-OFF PATCH. Do NOT put this file in migrations_baseline/.
--
-- 0001_baseline.sql already creates these tables, so the migration runner would apply
-- both and abort with "table already exists" on any fresh install. This file exists for
-- one situation only: a database restored from a backup taken BEFORE round membership
-- existed -- e.g. the ABS-S1 backup currently deployed to sessionbuddy-development --
-- which cannot be recreated from the baseline without losing its eval personas.
--
-- Additive on purpose. It does not repoint evaluation_assignments' foreign keys at these
-- tables; that needs SQLite's 12-step table rebuild and is not safe against live data.
-- The consequence: on a patched database the "an assignment references existing membership"
-- rule is enforced only by the application, not by the schema. Fresh installs from the
-- folded baseline keep the stronger guarantee.
--
-- Verified against a database already holding assignments: rows untouched, foreign_key_check
-- clean, membership reconstructed exactly.

CREATE TABLE evaluation_round_submissions (
  round_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'removed')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  PRIMARY KEY (round_id, submission_id),
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE RESTRICT
);

CREATE TABLE evaluation_round_evaluators (
  round_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'removed')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  PRIMARY KEY (round_id, evaluator_user_id),
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (evaluator_user_id) REFERENCES users(id) ON DELETE RESTRICT
);

-- Backfill. The pairs already exist, so membership is recoverable exactly.
-- A proposal stays active even when every one of its assignments was revoked: it is still
-- in the round, it has simply lost its reviewers -- which is the needs_reassignment state,
-- not removal from the round.
INSERT INTO evaluation_round_submissions
  (round_id, submission_id, organization_id, event_id, status, created_at_ms, updated_at_ms)
SELECT a.round_id, a.submission_id, a.organization_id, a.event_id, 'active',
       MIN(a.created_at_ms), MAX(a.updated_at_ms)
  FROM evaluation_assignments a
 GROUP BY a.round_id, a.submission_id, a.organization_id, a.event_id;

-- A reviewer whose assignments were ALL revoked was removed from the pool (by an organizer
-- or by declaring a conflict on every one), so their membership is reconstructed as removed.
INSERT INTO evaluation_round_evaluators
  (round_id, evaluator_user_id, organization_id, event_id, status, created_at_ms, updated_at_ms)
SELECT a.round_id, a.evaluator_user_id, a.organization_id, a.event_id,
       CASE WHEN SUM(CASE WHEN a.status != 'revoked' THEN 1 ELSE 0 END) > 0
            THEN 'active' ELSE 'removed' END,
       MIN(a.created_at_ms), MAX(a.updated_at_ms)
  FROM evaluation_assignments a
 GROUP BY a.round_id, a.evaluator_user_id, a.organization_id, a.event_id;

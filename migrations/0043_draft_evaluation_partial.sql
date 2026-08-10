-- Allow partial draft evaluations: rating and recommendation become nullable
-- while drafting; finalization still requires both. Adds the rating range
-- check the original table lacked.
PRAGMA foreign_keys = ON;

CREATE TABLE evaluations_rebuild (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  assignment_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 0 AND 10),
  recommendation TEXT CHECK (
    recommendation IS NULL OR length(recommendation) BETWEEN 1 AND 80
  ),
  internal_comment TEXT NOT NULL CHECK (length(internal_comment) <= 5000),
  state TEXT NOT NULL CHECK (state IN ('draft', 'final')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  finalized_at_ms INTEGER,
  criterion_scores_json TEXT NOT NULL DEFAULT '{}'
  CHECK (json_valid(criterion_scores_json)),
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (assignment_id) REFERENCES evaluation_assignments(id) ON DELETE RESTRICT,
  UNIQUE (assignment_id),
  CHECK ((state = 'final') = (finalized_at_ms IS NOT NULL)),
  CHECK (state != 'final' OR (rating IS NOT NULL AND recommendation IS NOT NULL))
);

INSERT INTO evaluations_rebuild
  (id, organization_id, event_id, round_id, assignment_id, evaluator_user_id,
   rating, recommendation, internal_comment, state, version,
   created_at_ms, updated_at_ms, finalized_at_ms, criterion_scores_json)
SELECT
  id, organization_id, event_id, round_id, assignment_id, evaluator_user_id,
  rating, recommendation, internal_comment, state, version,
  created_at_ms, updated_at_ms, finalized_at_ms, criterion_scores_json
FROM evaluations;

DROP TABLE evaluations;
ALTER TABLE evaluations_rebuild RENAME TO evaluations;

CREATE INDEX idx_evaluations_round
  ON evaluations(round_id, state, updated_at_ms DESC);

CREATE TRIGGER validate_evaluation_scope_insert
BEFORE INSERT ON evaluations
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_assignments a
  WHERE a.id=NEW.assignment_id AND a.round_id=NEW.round_id
    AND a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
    AND a.evaluator_user_id=NEW.evaluator_user_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation scope mismatch');
END;

CREATE TRIGGER validate_evaluation_scope_update
BEFORE UPDATE OF organization_id,event_id,round_id,assignment_id,evaluator_user_id
ON evaluations
WHEN NOT EXISTS (
  SELECT 1 FROM evaluation_assignments a
  WHERE a.id=NEW.assignment_id AND a.round_id=NEW.round_id
    AND a.organization_id=NEW.organization_id AND a.event_id=NEW.event_id
    AND a.evaluator_user_id=NEW.evaluator_user_id
)
BEGIN
  SELECT RAISE(ABORT, 'evaluation scope mismatch');
END;

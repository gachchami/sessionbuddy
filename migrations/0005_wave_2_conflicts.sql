PRAGMA foreign_keys = ON;

CREATE TABLE evaluation_conflicts (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  assignment_id TEXT NOT NULL,
  evaluator_user_id TEXT NOT NULL,
  conflict_type TEXT NOT NULL CHECK (conflict_type IN ('speaker_relationship', 'same_company', 'financial', 'other')),
  explanation TEXT NOT NULL CHECK (length(explanation) BETWEEN 1 AND 1000),
  declared_at_ms INTEGER NOT NULL,
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (assignment_id) REFERENCES evaluation_assignments(id) ON DELETE RESTRICT,
  FOREIGN KEY (evaluator_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (assignment_id)
);

CREATE INDEX idx_evaluation_conflicts_round
  ON evaluation_conflicts(organization_id, event_id, round_id, declared_at_ms DESC);

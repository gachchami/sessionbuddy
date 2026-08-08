PRAGMA foreign_keys = ON;

CREATE TABLE submission_decisions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
  internal_reason TEXT NOT NULL CHECK (length(internal_reason) <= 2000),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  decided_by_user_id TEXT NOT NULL,
  decided_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (round_id) REFERENCES evaluation_rounds(id) ON DELETE RESTRICT,
  FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE RESTRICT,
  FOREIGN KEY (decided_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (round_id, submission_id)
);

CREATE INDEX idx_submission_decisions_round
  ON submission_decisions(organization_id, event_id, round_id, decided_at_ms DESC);

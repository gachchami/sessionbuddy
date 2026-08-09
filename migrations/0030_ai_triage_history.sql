PRAGMA foreign_keys = ON;

CREATE TABLE ai_triage_results (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 200),
  score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 10),
  recommendation TEXT NOT NULL CHECK(length(recommendation) BETWEEN 1 AND 80),
  rationale TEXT NOT NULL CHECK(length(rationale) BETWEEN 1 AND 4000),
  generated_by_user_id TEXT NOT NULL,
  generated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,submission_id)
    REFERENCES submissions(organization_id,event_id,id) ON DELETE CASCADE,
  FOREIGN KEY (generated_by_user_id) REFERENCES users(id)
);

CREATE INDEX idx_ai_triage_submission_recent
  ON ai_triage_results(organization_id,event_id,submission_id,generated_at_ms DESC,id DESC);

PRAGMA foreign_keys = ON;

CREATE TABLE submission_contributors (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 200),
  email TEXT NOT NULL CHECK(length(email) BETWEEN 3 AND 320),
  normalized_email TEXT NOT NULL CHECK(length(normalized_email) BETWEEN 3 AND 320),
  role TEXT NOT NULL DEFAULT 'co_speaker' CHECK(role='co_speaker'),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,submission_id)
    REFERENCES submissions(organization_id,event_id,id) ON DELETE CASCADE,
  UNIQUE (submission_id,normalized_email)
);

CREATE INDEX idx_submission_contributors_submission
  ON submission_contributors(organization_id,event_id,submission_id,display_name,id);


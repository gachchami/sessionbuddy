PRAGMA foreign_keys = ON;

ALTER TABLE submissions ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1);

CREATE INDEX idx_submissions_form_owner_recent
  ON submissions(form_id,submitter_user_id,status,updated_at_ms DESC,id DESC);


PRAGMA foreign_keys = ON;

ALTER TABLE submissions ADD COLUMN speaker_email TEXT NOT NULL DEFAULT '';
ALTER TABLE submissions ADD COLUMN submitter_user_id TEXT REFERENCES users(id);
ALTER TABLE submissions ADD COLUMN answers_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(answers_json));

CREATE TABLE submission_drafts (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  program_id TEXT NOT NULL,
  form_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  answers_json TEXT NOT NULL CHECK(json_valid(answers_json)),
  version INTEGER NOT NULL DEFAULT 1 CHECK(version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,program_id)
    REFERENCES programs(organization_id,event_id,id),
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  UNIQUE (form_id,user_id)
);

CREATE INDEX idx_submission_drafts_user
  ON submission_drafts(user_id,updated_at_ms DESC,id DESC);
CREATE INDEX idx_submissions_submitter
  ON submissions(submitter_user_id,submitted_at_ms DESC,id DESC);

PRAGMA foreign_keys = ON;

ALTER TABLE accepted_sessions ADD COLUMN content_status TEXT NOT NULL DEFAULT 'draft'
  CHECK (content_status IN ('draft', 'approved'));
ALTER TABLE accepted_sessions ADD COLUMN version INTEGER NOT NULL DEFAULT 1
  CHECK (version >= 1);

CREATE TABLE session_content_versions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  accepted_session_id TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  abstract TEXT NOT NULL CHECK (length(abstract) BETWEEN 1 AND 5000),
  content_status TEXT NOT NULL CHECK (content_status IN ('draft', 'approved')),
  changed_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id,accepted_session_id)
    REFERENCES accepted_sessions(organization_id,event_id,id) ON DELETE RESTRICT,
  FOREIGN KEY (changed_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id,event_id,id),
  UNIQUE (accepted_session_id,version)
);

CREATE TABLE session_content_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  accepted_session_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK (applied_changes = 1),
  created_at_ms INTEGER NOT NULL
);

CREATE INDEX idx_session_content_history
  ON session_content_versions(accepted_session_id,version DESC);
CREATE INDEX idx_accepted_sessions_public_content
  ON accepted_sessions(organization_id,event_id,content_status,id);

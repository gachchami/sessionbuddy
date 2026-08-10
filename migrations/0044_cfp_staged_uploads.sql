PRAGMA foreign_keys = ON;

-- Staged CFP uploads let an authenticated first-time submitter upload file
-- answers BEFORE any speaker/person/membership rows exist. Rows are keyed to
-- the published form and the uploading user, never to a speaker graph; the
-- speaker graph is created only when create_submission succeeds, at which
-- point staged rows are claimed atomically in the same command batch.
CREATE TABLE cfp_staged_assets (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  form_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('headshot', 'supporting_document')),
  object_key TEXT NOT NULL CHECK (length(object_key) BETWEEN 16 AND 1024),
  original_filename TEXT NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  content_type TEXT NOT NULL CHECK (length(content_type) BETWEEN 1 AND 255),
  byte_size INTEGER NOT NULL CHECK (byte_size > 0),
  checksum_sha256 BLOB NOT NULL CHECK (length(checksum_sha256) = 32),
  upload_token_hash BLOB NOT NULL CHECK (length(upload_token_hash) = 32),
  status TEXT NOT NULL CHECK (
    status IN ('pending_upload', 'uploaded', 'scanning', 'staged', 'rejected', 'claimed')
  ),
  scan_result_code TEXT CHECK (scan_result_code IS NULL OR length(scan_result_code) <= 100),
  claimed_submission_id TEXT,
  claimed_at_ms INTEGER,
  expires_at_ms INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (form_id) REFERENCES call_for_speaker_forms(id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (upload_token_hash),
  UNIQUE (object_key),
  CHECK (expires_at_ms > created_at_ms),
  CHECK (
    status <> 'claimed'
    OR (claimed_submission_id IS NOT NULL AND claimed_at_ms IS NOT NULL)
  ),
  CHECK (claimed_submission_id IS NULL OR status = 'claimed')
);

CREATE INDEX idx_cfp_staged_assets_owner
  ON cfp_staged_assets(form_id, user_id, status, created_at_ms, id);

CREATE INDEX idx_cfp_staged_assets_expiry
  ON cfp_staged_assets(expires_at_ms, id);

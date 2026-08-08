PRAGMA foreign_keys = ON;

CREATE UNIQUE INDEX uq_speaker_tasks_owner_id
  ON speaker_tasks(organization_id, event_id, event_speaker_id, id);

CREATE TABLE speaker_assets (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  submission_id TEXT,
  task_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('headshot', 'slides', 'supporting_document')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, submission_id)
    REFERENCES submissions(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, event_speaker_id, task_id)
    REFERENCES speaker_tasks(organization_id, event_id, event_speaker_id, id)
      ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, event_speaker_id, id)
);

-- COALESCE gives nullable submission/task identifiers ordinary equality
-- semantics, preventing duplicate profile-level slots where both are NULL.
CREATE UNIQUE INDEX uq_speaker_asset_logical_slot
  ON speaker_assets(
    organization_id,
    event_id,
    event_speaker_id,
    COALESCE(submission_id, ''),
    COALESCE(task_id, ''),
    kind
  );

CREATE TABLE speaker_asset_versions (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK (generation >= 1),
  object_key TEXT NOT NULL CHECK (length(object_key) BETWEEN 16 AND 1024),
  original_filename TEXT NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
  content_type TEXT CHECK (content_type IS NULL OR length(content_type) BETWEEN 1 AND 255),
  byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
  checksum_sha256 BLOB CHECK (checksum_sha256 IS NULL OR length(checksum_sha256) = 32),
  scan_state TEXT NOT NULL
    CHECK (scan_state IN (
      'pending_upload', 'uploaded', 'scanning', 'clean', 'rejected', 'superseded'
    )),
  is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
  created_at_ms INTEGER NOT NULL,
  uploaded_at_ms INTEGER,
  scan_started_at_ms INTEGER,
  scanned_at_ms INTEGER,
  scan_result_code TEXT CHECK (scan_result_code IS NULL OR length(scan_result_code) <= 100),
  FOREIGN KEY (organization_id, event_id, event_speaker_id, asset_id)
    REFERENCES speaker_assets(organization_id, event_id, event_speaker_id, id)
      ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id),
  UNIQUE (organization_id, event_id, event_speaker_id, id),
  UNIQUE (asset_id, generation),
  UNIQUE (object_key),
  CHECK (is_current = 0 OR scan_state = 'clean'),
  CHECK (
    (scan_state = 'pending_upload'
      AND uploaded_at_ms IS NULL AND scan_started_at_ms IS NULL AND scanned_at_ms IS NULL
      AND content_type IS NULL AND byte_size IS NULL AND checksum_sha256 IS NULL)
    OR
    (scan_state = 'uploaded'
      AND uploaded_at_ms IS NOT NULL AND scan_started_at_ms IS NULL AND scanned_at_ms IS NULL
      AND content_type IS NOT NULL AND byte_size IS NOT NULL AND checksum_sha256 IS NOT NULL)
    OR
    (scan_state = 'scanning'
      AND uploaded_at_ms IS NOT NULL AND scan_started_at_ms IS NOT NULL AND scanned_at_ms IS NULL
      AND content_type IS NOT NULL AND byte_size IS NOT NULL AND checksum_sha256 IS NOT NULL)
    OR
    (scan_state IN ('clean', 'rejected', 'superseded')
      AND uploaded_at_ms IS NOT NULL AND scan_started_at_ms IS NOT NULL
      AND scanned_at_ms IS NOT NULL AND content_type IS NOT NULL
      AND byte_size IS NOT NULL AND checksum_sha256 IS NOT NULL)
  )
);

CREATE UNIQUE INDEX uq_speaker_asset_current_clean
  ON speaker_asset_versions(asset_id)
  WHERE is_current = 1;

CREATE TRIGGER prevent_speaker_asset_version_identity_update
BEFORE UPDATE OF
  organization_id, event_id, event_speaker_id, asset_id, generation, object_key,
  original_filename
ON speaker_asset_versions
BEGIN
  SELECT RAISE(ABORT, 'asset version identity is immutable');
END;

CREATE TABLE upload_intents (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_speaker_id TEXT NOT NULL,
  asset_version_id TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('create', 'replace')),
  token_hash BLOB NOT NULL CHECK (length(token_hash) = 32),
  expected_content_type TEXT NOT NULL
    CHECK (length(expected_content_type) BETWEEN 1 AND 255),
  expected_byte_size INTEGER NOT NULL CHECK (expected_byte_size >= 0),
  expected_checksum_sha256 BLOB NOT NULL CHECK (length(expected_checksum_sha256) = 32),
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, event_id, event_speaker_id)
    REFERENCES event_speakers(organization_id, event_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, event_speaker_id, asset_version_id)
    REFERENCES speaker_asset_versions(organization_id, event_id, event_speaker_id, id)
      ON DELETE RESTRICT,
  UNIQUE (token_hash),
  CHECK (expires_at_ms > created_at_ms),
  CHECK (consumed_at_ms IS NULL OR consumed_at_ms >= created_at_ms)
);

CREATE INDEX idx_speaker_assets_owner
  ON speaker_assets(
    organization_id, event_id, event_speaker_id, kind, updated_at_ms DESC, id DESC
  );
CREATE INDEX idx_speaker_assets_task
  ON speaker_assets(organization_id, event_id, task_id, kind, id);
CREATE INDEX idx_speaker_asset_versions_current
  ON speaker_asset_versions(organization_id, event_id, asset_id, is_current, generation DESC);
CREATE INDEX idx_speaker_asset_versions_scanner
  ON speaker_asset_versions(scan_state, uploaded_at_ms, id);
CREATE INDEX idx_upload_intents_owner
  ON upload_intents(
    organization_id, event_id, event_speaker_id, created_at_ms DESC, id DESC
  );
CREATE INDEX idx_upload_intents_expiry
  ON upload_intents(expires_at_ms, id)
  WHERE consumed_at_ms IS NULL;

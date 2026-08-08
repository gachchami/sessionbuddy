PRAGMA foreign_keys = ON;

-- Scanner receipts bind to the exact immutable generation and uploaded bytes,
-- not merely to a logical asset or reusable object identifier.
CREATE UNIQUE INDEX uq_speaker_asset_version_scan_identity
  ON speaker_asset_versions(
    organization_id,
    event_id,
    id,
    generation,
    checksum_sha256
  );

CREATE TABLE asset_scan_events (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  asset_version_id TEXT NOT NULL,
  generation INTEGER NOT NULL CHECK (generation >= 1),
  checksum_sha256 BLOB NOT NULL CHECK (length(checksum_sha256) = 32),
  provider_event_id TEXT NOT NULL CHECK (length(provider_event_id) BETWEEN 1 AND 200),
  job_id TEXT NOT NULL CHECK (length(job_id) BETWEEN 1 AND 200),
  verdict TEXT NOT NULL CHECK (verdict IN ('clean', 'malicious', 'error')),
  engine TEXT NOT NULL CHECK (
    length(engine) BETWEEN 1 AND 80
    AND engine NOT GLOB '*[^A-Za-z0-9._:-]*'
  ),
  signature_code TEXT CHECK (
    signature_code IS NULL
    OR (
      length(signature_code) BETWEEN 1 AND 100
      AND signature_code NOT GLOB '*[^A-Za-z0-9._:-]*'
    )
  ),
  received_at_ms INTEGER NOT NULL CHECK (received_at_ms >= 0),
  FOREIGN KEY (
    organization_id,
    event_id,
    asset_version_id,
    generation,
    checksum_sha256
  ) REFERENCES speaker_asset_versions(
    organization_id,
    event_id,
    id,
    generation,
    checksum_sha256
  ) ON DELETE RESTRICT,
  UNIQUE (engine, provider_event_id),
  UNIQUE (engine, job_id, asset_version_id, generation, checksum_sha256)
);

-- Defense in depth for callers that omit the guarded UPDATE pattern: once a
-- replacement generation exists, an older late result cannot become current.
CREATE TRIGGER prevent_old_asset_version_current_insert
BEFORE INSERT ON speaker_asset_versions
WHEN NEW.is_current = 1 AND EXISTS (
  SELECT 1 FROM speaker_asset_versions newer
  WHERE newer.asset_id = NEW.asset_id AND newer.generation > NEW.generation
)
BEGIN
  SELECT RAISE(ABORT, 'newer asset generation exists');
END;

CREATE TRIGGER prevent_old_asset_version_current_update
BEFORE UPDATE OF is_current, scan_state ON speaker_asset_versions
WHEN NEW.is_current = 1 AND EXISTS (
  SELECT 1 FROM speaker_asset_versions newer
  WHERE newer.asset_id = NEW.asset_id AND newer.generation > NEW.generation
)
BEGIN
  SELECT RAISE(ABORT, 'newer asset generation exists');
END;

CREATE INDEX idx_asset_scan_events_version_recent
  ON asset_scan_events(
    organization_id, event_id, asset_version_id, received_at_ms DESC, id DESC
  );
CREATE INDEX idx_asset_scan_events_job
  ON asset_scan_events(engine, job_id, received_at_ms DESC, id DESC);
CREATE INDEX idx_asset_scan_events_verdict_recent
  ON asset_scan_events(verdict, received_at_ms DESC, id DESC);

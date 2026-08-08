PRAGMA foreign_keys = ON;

CREATE TABLE asset_download_grants (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  principal_user_id TEXT NOT NULL,
  asset_version_id TEXT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('speaker_download', 'admin_download')),
  token_hash BLOB NOT NULL CHECK (length(token_hash) = 32),
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id, principal_user_id)
    REFERENCES organization_memberships(organization_id, user_id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id, asset_version_id)
    REFERENCES speaker_asset_versions(organization_id, event_id, id) ON DELETE RESTRICT,
  UNIQUE (token_hash),
  CHECK (expires_at_ms > created_at_ms),
  CHECK (consumed_at_ms IS NULL OR consumed_at_ms >= created_at_ms)
);

CREATE INDEX idx_asset_download_grants_expiry
  ON asset_download_grants(expires_at_ms, id)
  WHERE consumed_at_ms IS NULL;
CREATE INDEX idx_asset_download_grants_principal_recent
  ON asset_download_grants(
    organization_id, event_id, principal_user_id, created_at_ms DESC, id DESC
  );

PRAGMA foreign_keys = ON;

ALTER TABLE speaker_asset_versions
ADD COLUMN version_comment TEXT NOT NULL DEFAULT 'Legacy upload'
CHECK(length(trim(version_comment)) BETWEEN 1 AND 1000);

CREATE INDEX idx_speaker_asset_versions_history
  ON speaker_asset_versions(organization_id,event_id,asset_id,generation DESC,id);

CREATE TRIGGER prevent_asset_version_comment_update
BEFORE UPDATE OF version_comment ON speaker_asset_versions
WHEN NEW.version_comment != OLD.version_comment
BEGIN
  SELECT RAISE(ABORT, 'asset version comment is immutable');
END;

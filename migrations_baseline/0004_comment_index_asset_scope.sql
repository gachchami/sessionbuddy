-- Re-key the speaker asset comment index to match how it is actually queried.
--
-- Every comment list filters by (organization_id, event_id, asset_id) and orders
-- by (created_at_ms, id); none filters by version_id alone. The version-keyed
-- index therefore went unused - the planner fell back to the table's autoindex
-- and built a temp B-tree for the sort - while still costing a write on every
-- insert.

DROP INDEX IF EXISTS idx_speaker_asset_comments_version;

CREATE INDEX idx_speaker_asset_comments_asset
  ON speaker_asset_comments(organization_id,event_id,asset_id,created_at_ms,id);

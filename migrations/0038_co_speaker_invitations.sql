ALTER TABLE submission_contributors ADD COLUMN invitation_status TEXT NOT NULL DEFAULT 'removed'
  CHECK(invitation_status IN ('pending','accepted','declined','removed'));
ALTER TABLE submission_contributors ADD COLUMN invitation_token_hash BLOB;
ALTER TABLE submission_contributors ADD COLUMN invitation_expires_at_ms INTEGER;
ALTER TABLE submission_contributors ADD COLUMN invited_at_ms INTEGER;
ALTER TABLE submission_contributors ADD COLUMN accepted_at_ms INTEGER;
ALTER TABLE submission_contributors ADD COLUMN declined_at_ms INTEGER;
ALTER TABLE submission_contributors ADD COLUMN removed_at_ms INTEGER;
ALTER TABLE submission_contributors ADD COLUMN user_id TEXT REFERENCES users(id);
ALTER TABLE submission_contributors ADD COLUMN invitation_version INTEGER NOT NULL DEFAULT 0
  CHECK(invitation_version >= 0);

CREATE UNIQUE INDEX idx_submission_contributors_invitation_token
  ON submission_contributors(invitation_token_hash)
  WHERE invitation_token_hash IS NOT NULL;
CREATE INDEX idx_submission_contributors_user
  ON submission_contributors(organization_id,event_id,user_id,invitation_status)
  WHERE user_id IS NOT NULL;

CREATE TABLE submission_contributor_invitation_guards (
  id TEXT PRIMARY KEY NOT NULL,
  contributor_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK(applied_changes=1),
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY(contributor_id) REFERENCES submission_contributors(id) ON DELETE CASCADE
);

CREATE TRIGGER validate_submission_contributor_invitation_insert
BEFORE INSERT ON submission_contributors
WHEN (NEW.invitation_status='pending') !=
     (NEW.invitation_token_hash IS NOT NULL AND NEW.invitation_expires_at_ms IS NOT NULL
      AND NEW.invited_at_ms IS NOT NULL)
BEGIN
  SELECT RAISE(ABORT, 'invalid co-speaker invitation state');
END;

CREATE TRIGGER validate_submission_contributor_invitation_update
BEFORE UPDATE OF invitation_status,invitation_token_hash,invitation_expires_at_ms,invited_at_ms
ON submission_contributors
WHEN (NEW.invitation_status='pending') !=
     (NEW.invitation_token_hash IS NOT NULL AND NEW.invitation_expires_at_ms IS NOT NULL
      AND NEW.invited_at_ms IS NOT NULL)
BEGIN
  SELECT RAISE(ABORT, 'invalid co-speaker invitation state');
END;

PRAGMA foreign_keys = ON;

ALTER TABLE authentication_challenges ADD COLUMN user_id TEXT REFERENCES users(id);
ALTER TABLE authentication_challenges ADD COLUMN organization_id TEXT REFERENCES organizations(id);
ALTER TABLE authentication_challenges ADD COLUMN event_id TEXT;
ALTER TABLE authentication_challenges ADD COLUMN invitation_id TEXT;

CREATE TABLE identity_invitations (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  email TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('event_admin','evaluator','speaker')),
  status TEXT NOT NULL CHECK(status IN ('pending','accepted','revoked','expired')),
  invited_by_user_id TEXT NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  accepted_at_ms INTEGER,
  revoked_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id),
  FOREIGN KEY (invited_by_user_id) REFERENCES users(id),
  UNIQUE (organization_id,event_id,normalized_email,role)
);

CREATE INDEX idx_identity_invitations_email
  ON identity_invitations(normalized_email,status,expires_at_ms,id);
CREATE INDEX idx_auth_challenges_token_live
  ON authentication_challenges(token_hash,expires_at_ms) WHERE consumed_at_ms IS NULL;

-- Additive upgrade. Back up retained databases before application; rollback the
-- application, not these tables. Existing event invitations retain their expiry.
CREATE TABLE organization_admin_invitations (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
  email TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  permission TEXT NOT NULL DEFAULT 'manage' CHECK(permission='manage'),
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','revoked','expired')),
  invited_by_user_id TEXT NOT NULL REFERENCES users(id),
  accepted_by_user_id TEXT REFERENCES users(id),
  token_hash BLOB NOT NULL UNIQUE,
  superseded_by_id TEXT REFERENCES organization_admin_invitations(id),
  display_name TEXT NOT NULL DEFAULT '' CHECK(length(display_name)<=200),
  expires_at_ms INTEGER NOT NULL,
  accepted_at_ms INTEGER,
  revoked_at_ms INTEGER,
  declined_at_ms INTEGER,
  expired_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  CHECK(expires_at_ms=created_at_ms+259200000),
  CHECK((status='accepted')=(accepted_at_ms IS NOT NULL)),
  CHECK((status='accepted')=(accepted_by_user_id IS NOT NULL)),
  CHECK((status='revoked')=(revoked_at_ms IS NOT NULL)),
  CHECK((status='expired')=(expired_at_ms IS NOT NULL)),
  CHECK(declined_at_ms IS NULL OR status='revoked'),
  CHECK(superseded_by_id IS NULL OR status='revoked')
);
CREATE UNIQUE INDEX idx_org_admin_invitation_pending
  ON organization_admin_invitations(organization_id,normalized_email) WHERE status='pending';
CREATE INDEX idx_org_admin_invitation_list
  ON organization_admin_invitations(organization_id,created_at_ms DESC,id DESC);
CREATE INDEX idx_org_admin_invitation_recipient
  ON organization_admin_invitations(normalized_email,status,expires_at_ms,id);
CREATE TRIGGER validate_org_admin_invitation_actor_insert
BEFORE INSERT ON organization_admin_invitations
WHEN NOT EXISTS(SELECT 1 FROM organization_memberships
 WHERE organization_id=NEW.organization_id AND user_id=NEW.invited_by_user_id AND status='active')
BEGIN SELECT RAISE(ABORT,'organization invitation actor invalid'); END;
CREATE TRIGGER validate_org_admin_invitation_immutable
BEFORE UPDATE ON organization_admin_invitations
WHEN NEW.expires_at_ms!=OLD.expires_at_ms OR NEW.created_at_ms!=OLD.created_at_ms
 OR NEW.organization_id!=OLD.organization_id OR NEW.normalized_email!=OLD.normalized_email
 OR NEW.invited_by_user_id!=OLD.invited_by_user_id OR NEW.email!=OLD.email
 OR (NEW.token_hash!=OLD.token_hash AND (OLD.status!='pending' OR NEW.status!='pending'))
 OR (OLD.status!='pending' AND NEW.status!=OLD.status)
BEGIN SELECT RAISE(ABORT,'organization invitation immutable field'); END;
CREATE TRIGGER validate_org_admin_invitation_reissue
BEFORE UPDATE OF superseded_by_id ON organization_admin_invitations
WHEN NEW.superseded_by_id IS NOT NULL AND (NEW.id=NEW.superseded_by_id OR NOT EXISTS(
 SELECT 1 FROM organization_admin_invitations replacement
 WHERE replacement.id=NEW.superseded_by_id AND replacement.organization_id=NEW.organization_id
 AND replacement.normalized_email=NEW.normalized_email))
BEGIN SELECT RAISE(ABORT,'organization invitation replacement mismatch'); END;
CREATE TABLE organization_admin_invitation_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  invitation_id TEXT NOT NULL REFERENCES organization_admin_invitations(id) ON DELETE CASCADE,
  applied_changes INTEGER NOT NULL CONSTRAINT org_admin_invitation_write_guard CHECK(applied_changes=1),
  created_at_ms INTEGER NOT NULL
);
CREATE TABLE organization_admin_invitation_responses (
  idempotency_id TEXT PRIMARY KEY NOT NULL REFERENCES idempotency_records(id) ON DELETE CASCADE,
  response_json TEXT NOT NULL CHECK(json_valid(response_json))
);
ALTER TABLE authentication_challenges ADD COLUMN organization_admin_invitation_id TEXT
 REFERENCES organization_admin_invitations(id)
 CHECK(organization_admin_invitation_id IS NULL OR invitation_id IS NULL);
CREATE INDEX idx_org_admin_verification ON authentication_challenges
 (organization_admin_invitation_id,consumed_at_ms,expires_at_ms);
CREATE TRIGGER validate_org_admin_challenge_insert
BEFORE INSERT ON authentication_challenges
WHEN NEW.organization_admin_invitation_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM organization_admin_invitations i
 WHERE i.id=NEW.organization_admin_invitation_id AND i.organization_id=NEW.organization_id
 AND i.normalized_email=NEW.normalized_email AND NEW.event_id IS NULL
 AND NEW.invitation_id IS NULL AND NEW.user_id IS NULL
 AND NEW.purpose='verify_email' AND NEW.provisioning_context='invitation'
 AND NEW.expires_at_ms<=i.expires_at_ms
 AND NEW.expires_at_ms<=NEW.created_at_ms+900000)
BEGIN SELECT RAISE(ABORT,'organization verification scope mismatch'); END;
CREATE TRIGGER validate_org_admin_challenge_binding_immutable
BEFORE UPDATE ON authentication_challenges
WHEN OLD.organization_admin_invitation_id IS NOT NULL AND (
 NEW.organization_admin_invitation_id IS NOT OLD.organization_admin_invitation_id
 OR NEW.token_hash IS NOT OLD.token_hash OR NEW.normalized_email IS NOT OLD.normalized_email
 OR NEW.organization_id IS NOT OLD.organization_id OR NEW.expires_at_ms IS NOT OLD.expires_at_ms
 OR NEW.created_at_ms IS NOT OLD.created_at_ms)
BEGIN SELECT RAISE(ABORT,'organization verification binding immutable'); END;
CREATE TRIGGER validate_org_admin_challenge_update
BEFORE UPDATE ON authentication_challenges
WHEN NEW.organization_admin_invitation_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM organization_admin_invitations i
 WHERE i.id=NEW.organization_admin_invitation_id AND i.organization_id=NEW.organization_id
 AND i.normalized_email=NEW.normalized_email AND NEW.event_id IS NULL
 AND NEW.invitation_id IS NULL AND NEW.user_id IS NULL
 AND NEW.purpose='verify_email' AND NEW.provisioning_context='invitation'
 AND NEW.expires_at_ms<=i.expires_at_ms
 AND NEW.expires_at_ms<=NEW.created_at_ms+900000)
BEGIN SELECT RAISE(ABORT,'organization verification scope mismatch'); END;

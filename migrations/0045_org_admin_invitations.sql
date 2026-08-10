PRAGMA foreign_keys = ON;
-- The rebuild below drops and recreates identity_invitations inside this
-- migration. D1 applies migration files transactionally and does not allow
-- disabling foreign keys; defer enforcement to the end of the transaction so
-- any live rows referencing the table (now or added by future schema) cannot
-- fail the mid-rebuild state. Plain SQLite treats this as a no-op outside a
-- transaction, where the statement ordering alone is already consistent.
PRAGMA defer_foreign_keys = ON;

-- Make organization_admin invitable. Previously the only writer of that role
-- was the one-shot bootstrap, leaving every deployment with exactly one
-- organization admin forever. Invitations stay anchored to an event (the page
-- they are issued from); acceptance of an organization_admin invitation
-- grants the organization-wide role rather than an event membership.
-- SQLite cannot alter a CHECK constraint, so rebuild the table. The
-- authentication-challenge scope triggers reference this table, so they are
-- dropped for the rebuild and recreated verbatim afterwards.

DROP TRIGGER validate_auth_challenge_scope_insert;
DROP TRIGGER validate_auth_challenge_scope_update;

CREATE TABLE identity_invitations_next (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  email TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('organization_admin','event_admin','evaluator','speaker')),
  status TEXT NOT NULL CHECK(status IN ('pending','accepted','revoked','expired')),
  invited_by_user_id TEXT NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  accepted_at_ms INTEGER,
  revoked_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  display_name TEXT NOT NULL DEFAULT '' CHECK(length(display_name) <= 200),
  job_title TEXT NOT NULL DEFAULT '' CHECK(length(job_title) <= 200),
  company TEXT NOT NULL DEFAULT '' CHECK(length(company) <= 200),
  FOREIGN KEY (organization_id,event_id) REFERENCES events(organization_id,id),
  FOREIGN KEY (invited_by_user_id) REFERENCES users(id),
  UNIQUE (organization_id,event_id,normalized_email,role)
);

INSERT INTO identity_invitations_next
  (id,organization_id,event_id,normalized_email,email,role,status,invited_by_user_id,
   expires_at_ms,accepted_at_ms,revoked_at_ms,created_at_ms,updated_at_ms,
   display_name,job_title,company)
SELECT id,organization_id,event_id,normalized_email,email,role,status,invited_by_user_id,
       expires_at_ms,accepted_at_ms,revoked_at_ms,created_at_ms,updated_at_ms,
       display_name,job_title,company
FROM identity_invitations;

DROP TABLE identity_invitations;
ALTER TABLE identity_invitations_next RENAME TO identity_invitations;

CREATE INDEX idx_identity_invitations_email
  ON identity_invitations(normalized_email,status,expires_at_ms,id);
CREATE INDEX idx_identity_invitations_event_recent
  ON identity_invitations(organization_id,event_id,created_at_ms DESC,id DESC);

CREATE TRIGGER validate_identity_invitation_actor_insert
BEFORE INSERT ON identity_invitations
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.invited_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'invitation actor scope mismatch');
END;

CREATE TRIGGER validate_identity_invitation_actor_update
BEFORE UPDATE OF organization_id,invited_by_user_id ON identity_invitations
WHEN NOT EXISTS (
  SELECT 1 FROM organization_memberships m
  WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.invited_by_user_id
    AND m.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'invitation actor scope mismatch');
END;

CREATE TRIGGER validate_identity_invitation_state_insert
BEFORE INSERT ON identity_invitations
WHEN NEW.expires_at_ms <= NEW.created_at_ms
  OR ((NEW.status='accepted') != (NEW.accepted_at_ms IS NOT NULL))
  OR ((NEW.status='revoked') != (NEW.revoked_at_ms IS NOT NULL))
BEGIN
  SELECT RAISE(ABORT, 'invitation state invalid');
END;

CREATE TRIGGER validate_identity_invitation_state_update
BEFORE UPDATE OF status,expires_at_ms,accepted_at_ms,revoked_at_ms,created_at_ms
ON identity_invitations
WHEN NEW.expires_at_ms <= NEW.created_at_ms
  OR ((NEW.status='accepted') != (NEW.accepted_at_ms IS NOT NULL))
  OR ((NEW.status='revoked') != (NEW.revoked_at_ms IS NOT NULL))
BEGIN
  SELECT RAISE(ABORT, 'invitation state invalid');
END;

CREATE TRIGGER validate_auth_challenge_scope_insert
BEFORE INSERT ON authentication_challenges
WHEN (NEW.event_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM events e
        WHERE e.id=NEW.event_id AND e.organization_id=NEW.organization_id
      ))
  OR (NEW.user_id IS NOT NULL AND NEW.organization_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM organization_memberships m
        WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id
          AND m.status='active'
      ))
  OR (NEW.invitation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM identity_invitations i
        WHERE i.id=NEW.invitation_id AND i.organization_id=NEW.organization_id
          AND i.event_id=NEW.event_id AND i.normalized_email=NEW.normalized_email
      ))
BEGIN
  SELECT RAISE(ABORT, 'authentication challenge scope mismatch');
END;

CREATE TRIGGER validate_auth_challenge_scope_update
BEFORE UPDATE OF user_id,organization_id,event_id,invitation_id,normalized_email
ON authentication_challenges
WHEN (NEW.event_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM events e
        WHERE e.id=NEW.event_id AND e.organization_id=NEW.organization_id
      ))
  OR (NEW.user_id IS NOT NULL AND NEW.organization_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM organization_memberships m
        WHERE m.organization_id=NEW.organization_id AND m.user_id=NEW.user_id
          AND m.status='active'
      ))
  OR (NEW.invitation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM identity_invitations i
        WHERE i.id=NEW.invitation_id AND i.organization_id=NEW.organization_id
          AND i.event_id=NEW.event_id AND i.normalized_email=NEW.normalized_email
      ))
BEGIN
  SELECT RAISE(ABORT, 'authentication challenge scope mismatch');
END;

-- Record an invitee's explicit decline without rebuilding identity_invitations.
-- The table is a RESTRICT parent of speaker_tasks and accepted-session
-- participants, so D1 cannot safely DROP it while those rows exist. Persist a
-- decline as the already-supported revoked state plus this marker; API models
-- derive "declined" only when the marker is present. Organizer revocation
-- remains revoked because it leaves declined_at_ms NULL.
-- revoked_at_ms records when the invitation left the pending state; actor
-- attribution belongs to the accompanying audit event, never this timestamp.
--
-- Back up every data-bearing database before application. Rollback is an
-- application rollback: the added nullable column and guard table are safe for
-- the previous application to ignore. Validate with PRAGMA foreign_key_check.

ALTER TABLE identity_invitations ADD COLUMN declined_at_ms INTEGER;

CREATE TRIGGER validate_identity_invitation_decline_insert
BEFORE INSERT ON identity_invitations
WHEN NEW.declined_at_ms IS NOT NULL
 AND (NEW.status!='revoked' OR NEW.revoked_at_ms IS NULL)
BEGIN
  SELECT RAISE(ABORT, 'declined invitation state invalid');
END;

CREATE TRIGGER validate_identity_invitation_decline_update
BEFORE UPDATE OF status,declined_at_ms,revoked_at_ms ON identity_invitations
WHEN NEW.declined_at_ms IS NOT NULL
 AND (NEW.status!='revoked' OR NEW.revoked_at_ms IS NULL)
BEGIN
  SELECT RAISE(ABORT, 'declined invitation state invalid');
END;

CREATE TABLE identity_invitation_write_guards (
  id TEXT PRIMARY KEY NOT NULL,
  invitation_id TEXT NOT NULL,
  applied_changes INTEGER NOT NULL CHECK(applied_changes=1),
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY(invitation_id) REFERENCES identity_invitations(id) ON DELETE CASCADE
);

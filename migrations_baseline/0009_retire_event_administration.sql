-- Retire the unsupported event-scoped organizer model and the grant levels
-- that have no organization-scoped permissions. Event ownership-transfer
-- convenience rows and event-scoped grants are removed; authoritative audit
-- events remain, and no active authorization fact may depend on retired data.

-- Run every refusal check before changing schema or data. DROP IF EXISTS makes
-- a failed manual application restartable without weakening either check.
DROP TABLE IF EXISTS event_creator_backfill_guard;
CREATE TABLE event_creator_backfill_guard (
  valid INTEGER NOT NULL CHECK(valid=1)
);

INSERT INTO event_creator_backfill_guard(valid)
SELECT CASE WHEN EXISTS (
  SELECT 1
  FROM events event_row
  WHERE NOT EXISTS (
    SELECT 1 FROM owned_resources owned
    WHERE owned.id=event_row.id
      AND owned.resource_type='event'
      AND owned.created_by_user_id IS NOT NULL
  )
) THEN 0 ELSE 1 END;

DROP TABLE IF EXISTS event_transfer_audit_guard;
CREATE TABLE event_transfer_audit_guard (
  valid INTEGER NOT NULL CHECK(valid=1)
);

INSERT INTO event_transfer_audit_guard(valid)
SELECT CASE WHEN EXISTS (
  SELECT 1
  FROM resource_ownership_transfers transfer
  JOIN owned_resources owned ON owned.id=transfer.resource_id
  WHERE owned.resource_type='event'
    AND NOT EXISTS (
      SELECT 1 FROM audit_events audit
      WHERE audit.action='resource_ownership.transfer'
        AND audit.target_type='event'
        AND audit.target_id=transfer.resource_id
        AND audit.event_id=transfer.resource_id
        AND audit.actor_user_id=transfer.transferred_by_user_id
        AND audit.occurred_at_ms=transfer.transferred_at_ms
        AND json_extract(audit.metadata_json,'$.previous_owner_user_id')=transfer.from_user_id
        AND json_extract(audit.metadata_json,'$.new_owner_user_id')=transfer.to_user_id
    )
) THEN 0 ELSE 1 END;

DROP TABLE event_creator_backfill_guard;
DROP TABLE event_transfer_audit_guard;

CREATE TABLE event_administration_retirement_clock (
  retired_at_ms INTEGER NOT NULL
);
INSERT INTO event_administration_retirement_clock(retired_at_ms)
VALUES(CAST(unixepoch('now') AS INTEGER) * 1000);

ALTER TABLE events ADD COLUMN created_by_user_id TEXT
  REFERENCES users(id) ON DELETE RESTRICT;

UPDATE events
SET created_by_user_id=(
  SELECT owned.created_by_user_id
  FROM owned_resources owned
  WHERE owned.id=events.id AND owned.resource_type='event'
)
WHERE created_by_user_id IS NULL;

UPDATE users
SET authorization_version = authorization_version + 1,
    updated_at_ms = MAX(
      updated_at_ms + 1,
      (SELECT retired_at_ms FROM event_administration_retirement_clock)
    )
WHERE EXISTS (
        SELECT 1 FROM event_memberships membership
        WHERE membership.user_id=users.id AND membership.status='active'
          AND membership.role='event_admin'
      )
   OR EXISTS (
        SELECT 1 FROM resource_access_grants grant_row
        WHERE grant_row.user_id=users.id AND grant_row.status='active'
          AND (
            grant_row.permission IN ('view','edit')
            OR EXISTS (SELECT 1 FROM events event_row WHERE event_row.id=grant_row.resource_id)
          )
      )
   OR EXISTS (
        SELECT 1 FROM identity_invitations invitation
        WHERE invitation.normalized_email=users.normalized_email
          AND invitation.status='pending' AND invitation.role='event_admin'
      );

UPDATE identity_invitations
SET status='revoked',
    revoked_at_ms=MAX(
      updated_at_ms + 1,
      (SELECT retired_at_ms FROM event_administration_retirement_clock)
    ),
    updated_at_ms=MAX(
      updated_at_ms + 1,
      (SELECT retired_at_ms FROM event_administration_retirement_clock)
    ),
    accepted_at_ms=NULL
WHERE status='pending' AND role='event_admin';

-- A previously delivered invitation may still have an unconsumed magic link.
-- Revoke the invitation and its bearer challenge in the same migration.
UPDATE authentication_challenges
SET consumed_at_ms=MAX(
  created_at_ms + 1,
  (SELECT retired_at_ms FROM event_administration_retirement_clock)
)
WHERE consumed_at_ms IS NULL
  AND invitation_id IN (
    SELECT id FROM identity_invitations
    WHERE role='event_admin' AND status='revoked'
  );

UPDATE event_memberships
SET status='revoked',
    revoked_at_ms=MAX(
      updated_at_ms + 1,
      (SELECT retired_at_ms FROM event_administration_retirement_clock)
    ),
    updated_at_ms=MAX(
      updated_at_ms + 1,
      (SELECT retired_at_ms FROM event_administration_retirement_clock)
    ),
    version=version+1
WHERE status='active' AND role='event_admin';

UPDATE audit_events
SET metadata_json=json_set(
  metadata_json,
  '$.reason',
  (
    SELECT transfer.reason
    FROM resource_ownership_transfers transfer
    WHERE transfer.resource_id=audit_events.target_id
      AND transfer.transferred_by_user_id=audit_events.actor_user_id
      AND transfer.transferred_at_ms=audit_events.occurred_at_ms
      AND transfer.from_user_id=json_extract(
        audit_events.metadata_json,'$.previous_owner_user_id'
      )
      AND transfer.to_user_id=json_extract(
        audit_events.metadata_json,'$.new_owner_user_id'
      )
    LIMIT 1
  )
)
WHERE action='resource_ownership.transfer'
  AND target_type='event'
  AND event_id=target_id
  AND EXISTS (
    SELECT 1 FROM resource_ownership_transfers transfer
    WHERE transfer.resource_id=audit_events.target_id
      AND transfer.transferred_by_user_id=audit_events.actor_user_id
      AND transfer.transferred_at_ms=audit_events.occurred_at_ms
      AND transfer.from_user_id=json_extract(
        audit_events.metadata_json,'$.previous_owner_user_id'
      )
      AND transfer.to_user_id=json_extract(
        audit_events.metadata_json,'$.new_owner_user_id'
      )
      AND transfer.reason IS NOT NULL
  );

DELETE FROM resource_ownership_transfers
WHERE resource_id IN (
  SELECT id FROM owned_resources WHERE resource_type='event'
);

-- Event grants are retired authorization state, not the authoritative audit
-- trail. Existing resource_access_grant.* audit events remain; legacy grant
-- rows are deliberately deleted because their FK would preserve event
-- ownership rows and falsely advertise a supported authority model.
DELETE FROM resource_access_grants
WHERE resource_id IN (
  SELECT id FROM owned_resources WHERE resource_type='event'
);

UPDATE resource_access_grants
SET status='revoked',
    revoked_at_ms=MAX(
      updated_at_ms + 1,
      (SELECT retired_at_ms FROM event_administration_retirement_clock)
    ),
    updated_at_ms=MAX(
      updated_at_ms + 1,
      (SELECT retired_at_ms FROM event_administration_retirement_clock)
    ),
    revoked_by_user_id=granted_by_user_id,
    version=version+1
WHERE status='active'
  AND permission IN ('view','edit');

DELETE FROM owned_resources WHERE resource_type='event';

DROP TABLE event_administration_retirement_clock;

CREATE TRIGGER require_event_creator_insert
BEFORE INSERT ON events
WHEN NEW.created_by_user_id IS NULL
BEGIN
  SELECT RAISE(ABORT,'event creator is required');
END;

CREATE TRIGGER event_creator_is_immutable
BEFORE UPDATE OF created_by_user_id ON events
WHEN NEW.created_by_user_id IS NOT OLD.created_by_user_id
BEGIN
  SELECT RAISE(ABORT,'event creator is immutable');
END;

CREATE TRIGGER prevent_event_owned_resource_insert
BEFORE INSERT ON owned_resources
WHEN NEW.resource_type='event'
BEGIN
  SELECT RAISE(ABORT,'events are owned by their organization');
END;

CREATE TRIGGER prevent_event_owned_resource_update
BEFORE UPDATE OF resource_type ON owned_resources
WHEN NEW.resource_type='event'
BEGIN
  SELECT RAISE(ABORT,'events are owned by their organization');
END;

CREATE TRIGGER prevent_retired_invitation_role_insert
BEFORE INSERT ON identity_invitations
WHEN NEW.role='event_admin'
BEGIN
  SELECT RAISE(ABORT,'event_admin invitations are retired');
END;

CREATE TRIGGER prevent_retired_invitation_role_update
BEFORE UPDATE OF role,status ON identity_invitations
WHEN NEW.role='event_admin'
BEGIN
  SELECT RAISE(ABORT,'event_admin invitations are retired');
END;

CREATE TRIGGER prevent_retired_event_membership_insert
BEFORE INSERT ON event_memberships
WHEN NEW.role='event_admin'
BEGIN
  SELECT RAISE(ABORT,'event_admin memberships are retired');
END;

CREATE TRIGGER prevent_retired_event_membership_update
BEFORE UPDATE OF role,status ON event_memberships
WHEN NEW.role='event_admin'
BEGIN
  SELECT RAISE(ABORT,'event_admin memberships are retired');
END;

CREATE TRIGGER enforce_organization_manage_grant_insert
BEFORE INSERT ON resource_access_grants
WHEN NEW.permission!='manage'
  OR NOT EXISTS (
    SELECT 1 FROM owned_resources owned
    JOIN organizations organization_row ON organization_row.id=owned.id
    WHERE owned.id=NEW.resource_id AND owned.resource_type='organization'
  )
BEGIN
  SELECT RAISE(ABORT,'only organization manage grants are supported');
END;

CREATE TRIGGER enforce_organization_manage_grant_update
BEFORE UPDATE OF resource_id,permission,status ON resource_access_grants
WHEN NEW.status='active' AND (
  NEW.permission!='manage'
  OR NOT EXISTS (
    SELECT 1 FROM owned_resources owned
    JOIN organizations organization_row ON organization_row.id=owned.id
    WHERE owned.id=NEW.resource_id AND owned.resource_type='organization'
  )
)
BEGIN
  SELECT RAISE(ABORT,'only organization manage grants are supported');
END;

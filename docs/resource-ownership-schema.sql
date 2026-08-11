-- SessionBuddy resource-ownership schema proposal
--
-- Status: design contract. The compatible implementation is included in the
-- canonical migrations_baseline/0001_baseline.sql schema. It reuses the existing
-- user_roles, session_active_roles, organizations, events,
-- event_speakers, and evaluation assignment tables instead of creating the
-- illustrative *_v2 tables below.
--
-- Model:
--   1. A persona selects a workspace: organizer, reviewer, or speaker.
--   2. Every administratively managed resource has one immutable creator and
--      one owner. Initially, owner_user_id = created_by_user_id.
--   3. Access for another user is an explicit, revocable grant on that exact
--      resource. Access does not implicitly flow from an organization to its
--      events.
--   4. Speaker and reviewer relationships are domain assignments, not admin
--      roles and not entries in the account role switcher.
--
-- IDs are application-generated UUIDs. Times are UTC Unix epoch milliseconds.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Identity and account personas
-- ---------------------------------------------------------------------------

-- Existing users table shown only as a referenced contract:
-- users(id, email, normalized_email, status, authorization_version, ...)

CREATE TABLE user_personas_v2 (
  user_id TEXT NOT NULL,
  persona TEXT NOT NULL CHECK (persona IN ('organizer', 'reviewer', 'speaker')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  PRIMARY KEY (user_id, persona),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CHECK (updated_at_ms >= created_at_ms),
  CHECK ((status = 'revoked') = (revoked_at_ms IS NOT NULL)),
  CHECK (status = 'active' OR is_default = 0)
);

CREATE UNIQUE INDEX uq_user_personas_v2_default
  ON user_personas_v2(user_id)
  WHERE is_default = 1 AND status = 'active';

CREATE INDEX idx_user_personas_v2_active
  ON user_personas_v2(user_id, persona)
  WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- Owned resources
-- ---------------------------------------------------------------------------

-- A registry gives ownership and delegation one strongly typed source of
-- truth. Resource-specific tables retain their own domain fields and reference
-- this registry. created_by_user_id is immutable; owner_user_id may only change
-- through a separate, audited ownership-transfer workflow.
CREATE TABLE owned_resources (
  id TEXT PRIMARY KEY NOT NULL,
  resource_type TEXT NOT NULL CHECK (
    resource_type IN (
      'organization',
      'event',
      'program',
      'form',
      'session',
      'track',
      'label',
      'message_template'
    )
  ),
  created_by_user_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER,
  FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  CHECK (updated_at_ms >= created_at_ms),
  CHECK ((status = 'archived') = (archived_at_ms IS NOT NULL))
);

CREATE INDEX idx_owned_resources_owner
  ON owned_resources(owner_user_id, resource_type, status, updated_at_ms DESC);

CREATE INDEX idx_owned_resources_creator
  ON owned_resources(created_by_user_id, resource_type, created_at_ms DESC);

-- Prevent ordinary updates from rewriting history. A future ownership transfer
-- changes owner_user_id, never created_by_user_id.
CREATE TRIGGER owned_resources_creator_is_immutable
BEFORE UPDATE OF created_by_user_id ON owned_resources
WHEN NEW.created_by_user_id != OLD.created_by_user_id
BEGIN
  SELECT RAISE(ABORT, 'resource creator is immutable');
END;

-- Resource-specific records use the same ID as their owned_resources row.
-- Keeping organization_id on an event is classification/navigation, not an
-- automatic authorization inheritance rule.
CREATE TABLE organizations_v2 (
  id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  FOREIGN KEY (id) REFERENCES owned_resources(id) ON DELETE RESTRICT
);

CREATE TABLE events_v2 (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  starts_at_ms INTEGER NOT NULL,
  ends_at_ms INTEGER NOT NULL,
  time_zone TEXT NOT NULL,
  location TEXT,
  delivery_mode TEXT NOT NULL
    CHECK (delivery_mode IN ('in_person', 'virtual', 'hybrid')),
  description TEXT,
  CHECK (ends_at_ms >= starts_at_ms),
  FOREIGN KEY (id) REFERENCES owned_resources(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id) REFERENCES organizations_v2(id) ON DELETE RESTRICT
);

-- ---------------------------------------------------------------------------
-- Explicit resource delegation
-- ---------------------------------------------------------------------------

-- These are permissions, not personas. They never appear in the account role
-- switcher. A grant applies to exactly one resource and never cascades to child
-- resources. For example, managing an organization does not automatically
-- grant management of every event classified under it.
CREATE TABLE resource_access_grants (
  id TEXT PRIMARY KEY NOT NULL,
  resource_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  permission TEXT NOT NULL CHECK (permission IN ('view', 'edit', 'manage')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
  granted_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  revoked_by_user_id TEXT,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  FOREIGN KEY (resource_id) REFERENCES owned_resources(id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (granted_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (revoked_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (resource_id, user_id, permission),
  CHECK (updated_at_ms >= created_at_ms),
  CHECK (
    (status = 'active' AND revoked_at_ms IS NULL AND revoked_by_user_id IS NULL)
    OR
    (status = 'revoked' AND revoked_at_ms IS NOT NULL AND revoked_by_user_id IS NOT NULL)
  )
);

CREATE INDEX idx_resource_access_grants_user_active
  ON resource_access_grants(user_id, resource_id, permission)
  WHERE status = 'active';

CREATE INDEX idx_resource_access_grants_resource_active
  ON resource_access_grants(resource_id, permission, user_id)
  WHERE status = 'active';

-- Owners already have full authority. Duplicating that authority as a grant is
-- forbidden so the access screen has one unambiguous source for ownership.
CREATE TRIGGER resource_access_grant_not_for_owner_insert
BEFORE INSERT ON resource_access_grants
WHEN EXISTS (
  SELECT 1 FROM owned_resources resource
  WHERE resource.id = NEW.resource_id AND resource.owner_user_id = NEW.user_id
)
BEGIN
  SELECT RAISE(ABORT, 'resource owner does not need an access grant');
END;

CREATE TRIGGER resource_access_grant_not_for_owner_update
BEFORE UPDATE OF resource_id, user_id, status ON resource_access_grants
WHEN NEW.status = 'active' AND EXISTS (
  SELECT 1 FROM owned_resources resource
  WHERE resource.id = NEW.resource_id AND resource.owner_user_id = NEW.user_id
)
BEGIN
  SELECT RAISE(ABORT, 'resource owner does not need an access grant');
END;

-- ---------------------------------------------------------------------------
-- Ownership transfer
-- ---------------------------------------------------------------------------

-- Transfers are explicit audit records. Application policy must require the
-- current owner, step-up authentication, CSRF/origin checks, and an atomic
-- update of owned_resources.owner_user_id plus this record.
CREATE TABLE resource_ownership_transfers (
  id TEXT PRIMARY KEY NOT NULL,
  resource_id TEXT NOT NULL,
  from_user_id TEXT NOT NULL,
  to_user_id TEXT NOT NULL,
  transferred_by_user_id TEXT NOT NULL,
  reason TEXT CHECK (reason IS NULL OR length(reason) <= 1000),
  transferred_at_ms INTEGER NOT NULL,
  FOREIGN KEY (resource_id) REFERENCES owned_resources(id) ON DELETE RESTRICT,
  FOREIGN KEY (from_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (to_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (transferred_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  CHECK (from_user_id != to_user_id)
);

CREATE INDEX idx_resource_ownership_transfers_resource
  ON resource_ownership_transfers(resource_id, transferred_at_ms DESC);

-- ---------------------------------------------------------------------------
-- Domain assignments: not administrative permissions
-- ---------------------------------------------------------------------------

-- A speaker assignment says that a person participates in an event/session.
-- It does not allow that person to administer the event. Existing people and
-- event_speakers tables can continue to hold profile/onboarding information.
CREATE TABLE session_speaker_assignments_v2 (
  id TEXT PRIMARY KEY NOT NULL,
  event_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  speaker_role TEXT NOT NULL CHECK (speaker_role IN ('speaker', 'co_speaker')),
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'withdrawn')),
  assigned_by_user_id TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  withdrawn_at_ms INTEGER,
  FOREIGN KEY (event_id) REFERENCES events_v2(id) ON DELETE RESTRICT,
  FOREIGN KEY (session_id) REFERENCES owned_resources(id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (session_id, user_id),
  CHECK ((status = 'withdrawn') = (withdrawn_at_ms IS NOT NULL))
);

-- A reviewer assignment grants access only to the named submission/review work.
-- It is narrower than a resource manage/edit/view grant and remains governed by
-- evaluation conflict and final-decision rules.
CREATE TABLE reviewer_assignments_v2 (
  id TEXT PRIMARY KEY NOT NULL,
  event_id TEXT NOT NULL,
  submission_id TEXT NOT NULL,
  reviewer_user_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'assigned'
    CHECK (status IN ('assigned', 'accepted', 'declined', 'completed', 'revoked')),
  assigned_by_user_id TEXT NOT NULL,
  assigned_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  FOREIGN KEY (event_id) REFERENCES events_v2(id) ON DELETE RESTRICT,
  FOREIGN KEY (reviewer_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  FOREIGN KEY (assigned_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (submission_id, reviewer_user_id),
  CHECK ((status = 'revoked') = (revoked_at_ms IS NOT NULL))
);

-- ---------------------------------------------------------------------------
-- Authorization contract (implemented in server policy, not by the UI)
-- ---------------------------------------------------------------------------
--
-- can_view(resource, user):
--   resource.owner_user_id = user.id
--   OR an active grant exists with view, edit, or manage
--
-- can_edit(resource, user):
--   resource.owner_user_id = user.id
--   OR an active grant exists with edit or manage
--
-- can_manage(resource, user):
--   resource.owner_user_id = user.id
--   OR an active grant exists with manage
--
-- Speaker/reviewer screens additionally require the matching active assignment.
-- Persona selection narrows the workspace; it never creates resource access.
-- Child-resource access is always evaluated on the child resource itself.

-- Example for the requested organizer who is also a speaker:
--   user_personas_v2: organizer (default), speaker
--   owned_resources: events created by the user list that user as creator/owner
--   resource_access_grants: none for owned events; explicit manage rows only for
--                           other users invited to manage a particular event
--   session_speaker_assignments_v2: rows only for sessions where the user speaks
--
-- Consequently, organization_admin and event_admin are not account personas in
-- this proposal. Their useful behavior is represented by ownership or an
-- explicit per-resource `manage` grant.

PRAGMA foreign_keys = ON;

CREATE TABLE organizations (
  id TEXT PRIMARY KEY NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER
);

CREATE TABLE users (
  id TEXT PRIMARY KEY NOT NULL,
  email TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'suspended', 'deleted')),
  email_verified_at_ms INTEGER,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  authorization_version INTEGER NOT NULL DEFAULT 1 CHECK (authorization_version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  deleted_at_ms INTEGER
);

CREATE UNIQUE INDEX uq_users_normalized_email ON users(normalized_email);

CREATE TABLE organization_memberships (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('organization_admin', 'member')),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, user_id),
  UNIQUE (organization_id, user_id, id)
);

CREATE TABLE events (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  starts_at_ms INTEGER NOT NULL,
  ends_at_ms INTEGER NOT NULL,
  time_zone TEXT NOT NULL,
  location TEXT,
  delivery_mode TEXT NOT NULL CHECK (delivery_mode IN ('in_person', 'virtual', 'hybrid')),
  description TEXT,
  logo_object_key TEXT,
  accent_color TEXT,
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER,
  CHECK (ends_at_ms >= starts_at_ms),
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  UNIQUE (organization_id, id)
);

CREATE TABLE programs (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  status TEXT NOT NULL CHECK (status IN ('draft', 'open', 'closed', 'archived')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  archived_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, id)
);

CREATE TABLE event_memberships (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('event_admin', 'evaluator', 'speaker')),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  FOREIGN KEY (organization_id, event_id)
    REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, user_id)
    REFERENCES organization_memberships(organization_id, user_id) ON DELETE RESTRICT,
  UNIQUE (organization_id, event_id, user_id, role)
);

CREATE TABLE authentication_challenges (
  id TEXT PRIMARY KEY NOT NULL,
  normalized_email TEXT NOT NULL,
  token_hash BLOB NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('sign_in', 'verify_email')),
  provisioning_context TEXT NOT NULL CHECK (provisioning_context IN ('invitation', 'submission', 'existing_user')),
  redirect_path TEXT NOT NULL,
  requested_ip_hash BLOB,
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  UNIQUE (token_hash)
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL,
  token_hash BLOB NOT NULL,
  csrf_secret_hash BLOB NOT NULL,
  authorization_version INTEGER NOT NULL CHECK (authorization_version >= 1),
  created_at_ms INTEGER NOT NULL,
  last_seen_at_ms INTEGER NOT NULL,
  idle_expires_at_ms INTEGER NOT NULL,
  absolute_expires_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  revoke_reason TEXT,
  rotated_from_session_id TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (rotated_from_session_id) REFERENCES sessions(id) ON DELETE SET NULL,
  UNIQUE (token_hash),
  CHECK (idle_expires_at_ms <= absolute_expires_at_ms)
);

CREATE TABLE idempotency_records (
  id TEXT PRIMARY KEY NOT NULL,
  principal_key TEXT NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  route_key TEXT NOT NULL,
  idempotency_key_hash BLOB NOT NULL,
  request_fingerprint BLOB NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('in_progress', 'completed')),
  response_status INTEGER,
  response_resource_type TEXT,
  response_resource_id TEXT,
  created_at_ms INTEGER NOT NULL,
  completed_at_ms INTEGER,
  expires_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id) REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (principal_key, route_key, idempotency_key_hash),
  CHECK ((state = 'completed') = (completed_at_ms IS NOT NULL))
);

CREATE TABLE outbox_messages (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  topic TEXT NOT NULL,
  payload_version INTEGER NOT NULL CHECK (payload_version >= 1),
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  deduplication_key TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  available_at_ms INTEGER NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  claimed_at_ms INTEGER,
  claim_expires_at_ms INTEGER,
  published_at_ms INTEGER,
  last_error_code TEXT,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id) REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  UNIQUE (topic, deduplication_key)
);

CREATE TABLE audit_events (
  id TEXT PRIMARY KEY NOT NULL,
  organization_id TEXT,
  event_id TEXT,
  actor_user_id TEXT,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('user', 'system', 'anonymous')),
  action TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT,
  result TEXT NOT NULL CHECK (result IN ('succeeded', 'denied', 'failed')),
  reason_code TEXT,
  correlation_id TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(metadata_json)),
  occurred_at_ms INTEGER NOT NULL,
  FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
  FOREIGN KEY (organization_id, event_id) REFERENCES events(organization_id, id) ON DELETE RESTRICT,
  FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE RESTRICT
);

CREATE INDEX idx_org_memberships_user_active
  ON organization_memberships(user_id, organization_id) WHERE status = 'active';
CREATE INDEX idx_event_memberships_user_active
  ON event_memberships(user_id, organization_id, event_id, role) WHERE status = 'active';
CREATE INDEX idx_events_org_status_updated
  ON events(organization_id, status, updated_at_ms DESC, id DESC);
CREATE INDEX idx_programs_event_status_updated
  ON programs(organization_id, event_id, status, updated_at_ms DESC, id DESC);
CREATE INDEX idx_auth_challenges_expiry ON authentication_challenges(expires_at_ms);
CREATE INDEX idx_sessions_user_active
  ON sessions(user_id, absolute_expires_at_ms) WHERE revoked_at_ms IS NULL;
CREATE INDEX idx_sessions_idle_expiry
  ON sessions(idle_expires_at_ms) WHERE revoked_at_ms IS NULL;
CREATE INDEX idx_idempotency_expiry ON idempotency_records(expires_at_ms);
CREATE INDEX idx_outbox_dispatch
  ON outbox_messages(available_at_ms, id) WHERE published_at_ms IS NULL;
CREATE INDEX idx_outbox_event_recent
  ON outbox_messages(organization_id, event_id, created_at_ms DESC, id DESC);
CREATE INDEX idx_audit_event_time
  ON audit_events(organization_id, event_id, occurred_at_ms DESC, id DESC);
CREATE INDEX idx_audit_target_time
  ON audit_events(organization_id, target_type, target_id, occurred_at_ms DESC, id DESC);

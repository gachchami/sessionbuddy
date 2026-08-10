PRAGMA foreign_keys = ON;

CREATE TABLE user_roles (
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('organizer','reviewer','speaker')),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  revoked_at_ms INTEGER,
  PRIMARY KEY (user_id,role),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CHECK(updated_at_ms >= created_at_ms),
  CHECK((status='revoked') = (revoked_at_ms IS NOT NULL))
);

CREATE INDEX idx_user_roles_active
  ON user_roles(user_id,status,role);

CREATE TABLE session_active_roles (
  session_id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('organizer','reviewer','speaker')),
  selected_at_ms INTEGER NOT NULL,
  FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
  FOREIGN KEY (user_id,role) REFERENCES user_roles(user_id,role) ON DELETE CASCADE
);

CREATE INDEX idx_session_active_roles_user
  ON session_active_roles(user_id,session_id);

CREATE TRIGGER validate_session_active_role_user_insert
BEFORE INSERT ON session_active_roles
WHEN NOT EXISTS (
  SELECT 1 FROM sessions s
  JOIN user_roles r ON r.user_id=s.user_id AND r.role=NEW.role
  WHERE s.id=NEW.session_id AND s.user_id=NEW.user_id
    AND s.revoked_at_ms IS NULL AND r.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'active role is not available for session');
END;

CREATE TRIGGER validate_session_active_role_user_update
BEFORE UPDATE OF session_id,user_id,role ON session_active_roles
WHEN NOT EXISTS (
  SELECT 1 FROM sessions s
  JOIN user_roles r ON r.user_id=s.user_id AND r.role=NEW.role
  WHERE s.id=NEW.session_id AND s.user_id=NEW.user_id
    AND s.revoked_at_ms IS NULL AND r.status='active'
)
BEGIN
  SELECT RAISE(ABORT, 'active role is not available for session');
END;

CREATE TRIGGER remove_revoked_active_role
AFTER UPDATE OF status ON user_roles
WHEN NEW.status='revoked'
BEGIN
  DELETE FROM session_active_roles
  WHERE user_id=NEW.user_id AND role=NEW.role;
END;

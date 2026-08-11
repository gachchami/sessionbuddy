PRAGMA foreign_keys = ON;

ALTER TABLE user_roles ADD COLUMN is_default INTEGER NOT NULL DEFAULT 0
  CHECK(is_default IN (0,1));

UPDATE user_roles
SET is_default=1
WHERE status='active'
  AND role=(
    SELECT candidate.role FROM user_roles candidate
    WHERE candidate.user_id=user_roles.user_id AND candidate.status='active'
    ORDER BY CASE candidate.role
      WHEN 'organizer' THEN 1 WHEN 'reviewer' THEN 2 ELSE 3 END
    LIMIT 1
  );

CREATE UNIQUE INDEX idx_user_roles_one_default
  ON user_roles(user_id) WHERE is_default=1;

CREATE TRIGGER validate_default_account_role_insert
BEFORE INSERT ON user_roles
WHEN NEW.is_default=1 AND NEW.status!='active'
BEGIN
  SELECT RAISE(ABORT, 'default role must be active');
END;

CREATE TRIGGER validate_default_account_role_update
BEFORE UPDATE OF is_default ON user_roles
WHEN NEW.is_default=1 AND NEW.status!='active'
BEGIN
  SELECT RAISE(ABORT, 'default role must be active');
END;

CREATE TRIGGER replace_revoked_default_account_role
AFTER UPDATE OF status ON user_roles
WHEN NEW.status='revoked' AND NEW.is_default=1
BEGIN
  UPDATE user_roles SET is_default=0,updated_at_ms=NEW.updated_at_ms
  WHERE user_id=NEW.user_id AND role=NEW.role;
  UPDATE user_roles SET is_default=1,updated_at_ms=NEW.updated_at_ms
  WHERE user_id=NEW.user_id AND role=(
    SELECT role FROM user_roles
    WHERE user_id=NEW.user_id AND status='active'
    ORDER BY CASE role WHEN 'organizer' THEN 1 WHEN 'reviewer' THEN 2 ELSE 3 END
    LIMIT 1
  );
END;

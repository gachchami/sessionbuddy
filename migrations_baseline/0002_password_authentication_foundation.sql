PRAGMA foreign_keys = ON;

CREATE TABLE password_credentials (
  user_id TEXT PRIMARY KEY NOT NULL,
  verifier_phc TEXT NOT NULL
    CHECK(length(verifier_phc) BETWEEN 32 AND 1024)
    CHECK(verifier_phc GLOB '$argon2id$*' OR verifier_phc GLOB '$pbkdf2-sha256$*'),
  pepper_version INTEGER NOT NULL CHECK(pepper_version >= 1),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK(status IN ('active','reset_required','disabled')),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  last_verified_at_ms INTEGER,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CHECK(updated_at_ms >= created_at_ms),
  CHECK(last_verified_at_ms IS NULL OR last_verified_at_ms >= created_at_ms)
);

CREATE TABLE password_authentication_state (
  user_id TEXT PRIMARY KEY NOT NULL,
  consecutive_failures INTEGER NOT NULL DEFAULT 0
    CHECK(consecutive_failures BETWEEN 0 AND 100),
  first_failure_at_ms INTEGER,
  last_failure_at_ms INTEGER,
  blocked_until_ms INTEGER,
  last_success_at_ms INTEGER,
  updated_at_ms INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  CHECK(
    (consecutive_failures = 0 AND first_failure_at_ms IS NULL AND last_failure_at_ms IS NULL)
    OR
    (consecutive_failures > 0 AND first_failure_at_ms IS NOT NULL
      AND last_failure_at_ms IS NOT NULL AND last_failure_at_ms >= first_failure_at_ms)
  ),
  CHECK(blocked_until_ms IS NULL OR last_failure_at_ms IS NOT NULL),
  CHECK(last_success_at_ms IS NULL OR last_success_at_ms <= updated_at_ms)
);

CREATE TABLE password_recovery_challenges (
  id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL,
  normalized_email TEXT NOT NULL,
  token_hash BLOB NOT NULL CHECK(length(token_hash) = 32),
  purpose TEXT NOT NULL CHECK(purpose IN ('password_setup','password_reset')),
  requested_ip_hash BLOB CHECK(requested_ip_hash IS NULL OR length(requested_ip_hash) = 32),
  expires_at_ms INTEGER NOT NULL,
  consumed_at_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  UNIQUE (token_hash),
  CHECK(expires_at_ms > created_at_ms),
  CHECK(consumed_at_ms IS NULL OR consumed_at_ms >= created_at_ms)
);

CREATE INDEX idx_password_recovery_user_active
  ON password_recovery_challenges(user_id,purpose,consumed_at_ms,expires_at_ms DESC,id);

CREATE TRIGGER validate_password_recovery_email_insert
BEFORE INSERT ON password_recovery_challenges
WHEN NOT EXISTS (
  SELECT 1 FROM users u
  WHERE u.id=NEW.user_id AND u.normalized_email=NEW.normalized_email
    AND u.status='active' AND u.email_verified_at_ms IS NOT NULL
)
BEGIN
  SELECT RAISE(ABORT, 'password recovery identity mismatch');
END;

CREATE TRIGGER validate_password_recovery_email_update
BEFORE UPDATE OF user_id,normalized_email ON password_recovery_challenges
WHEN NOT EXISTS (
  SELECT 1 FROM users u
  WHERE u.id=NEW.user_id AND u.normalized_email=NEW.normalized_email
    AND u.status='active' AND u.email_verified_at_ms IS NOT NULL
)
BEGIN
  SELECT RAISE(ABORT, 'password recovery identity mismatch');
END;

CREATE TRIGGER password_rotation_requires_authorization_bump
BEFORE UPDATE OF verifier_phc,pepper_version ON password_credentials
WHEN NOT EXISTS (
  SELECT 1 FROM users u
  WHERE u.id=NEW.user_id AND u.authorization_version > (
    SELECT authorization_version FROM sessions s
    WHERE s.user_id=NEW.user_id AND s.revoked_at_ms IS NULL
    ORDER BY s.authorization_version DESC LIMIT 1
  )
)
AND EXISTS (
  SELECT 1 FROM sessions s WHERE s.user_id=NEW.user_id AND s.revoked_at_ms IS NULL
)
BEGIN
  SELECT RAISE(ABORT, 'password rotation requires authorization bump');
END;
